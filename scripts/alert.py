# -*- coding: utf-8 -*-
"""阈值告警(从 events 聚合)。

D40 决策:不引 Prometheus/OTel/Sentry,自建轻量,告警 = 一个 Python 脚本
通过 app.db 抽象读 events 聚合最近 N 小时,跨阈值则打印带 trace_id 的告警
(可被 cron / nssm / 任务计划调用)。

events 表实际 schema(读 agent.db 校对过;生产 MySQL 表名 `events` 同结构):
  seq, session_id, type, ts(ISO8601 字符串), payload(JSON 字符串)

可调阈值(CLI 或环境变量):
  --error-rate 0.05        # 含 error 的事件 / 总事件 > 5% 告警
  --latency-p95 30000      # 总 turn 时长 P95(ms) > 30s 告警
  --daily-token 500000     # 当天 prompt+completion token > 50 万告警
  --window-hours 1         # 聚合窗口(默认 1h)

输出:stdout 一行 JSON,字段 {window_h, metrics, alerts: [{rule, value, threshold, ...}]}
退出码: 0=无告警, 1=有告警, 2=配置/数据错误
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, PROJ)

from app.config import load as load_cfg                     # noqa: E402
from app.db import get_db, DB                               # noqa: E402

# MySQL events 表里 ts 是 DATETIME(ISO 字符串可比);本脚本按 UTC ISO 比较
# SQLite 同 .env 没 db_host 时走本地 agent.db
DB_KIND = "session"  # events 跟会话/事件/记忆同一库


def _since_iso(window_h: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=window_h)).strftime("%Y-%m-%dT%H:%M:%S")


def _rows(db: DB, sql: str, params: tuple = ()):
    """统一取行:mysql 走 cursor.fetchall();sqlite 走 conn.execute().fetchall()。"""
    cur = db.execute(sql, params)
    if db._dialect == "mysql":
        rows = cur.fetchall()
        # DictCursor: 已经是 dict;SSDictCursor 类似。统一为 list[dict]
        if rows and not isinstance(rows[0], dict):
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in rows]
        return rows
    return cur.fetchall()


def _scalar(db: DB, sql: str, params: tuple = ()) -> int:
    rows = _rows(db, sql, params)
    if not rows:
        return 0
    first = rows[0]
    if isinstance(first, dict):
        v = next(iter(first.values()))
    else:
        v = first[0]
    try:
        return int(v or 0)
    except Exception:
        return 0


def _agg(window_h: float) -> dict:
    cfg = load_cfg()
    db: DB = get_db(cfg, DB_KIND)
    try:
        since = _since_iso(window_h)
        # events 表名固定为 events(db_kind=session 的同一库)
        total = _scalar(db, "SELECT count(*) FROM events WHERE ts>=?", (since,))
        errors = _scalar(db,
                         "SELECT count(*) FROM events WHERE ts>=? AND "
                         "(type='tool_error' OR lower(payload) LIKE ?)",
                         (since, '%"error"%'))
        rows = _rows(db,
                     "SELECT session_id, min(ts) AS s, max(ts) AS e "
                     "FROM events WHERE ts>=? GROUP BY session_id", (since,))
        durs: list[float] = []
        for r in rows:
            try:
                s = r.get("s") if isinstance(r, dict) else r[1]
                e = r.get("e") if isinstance(r, dict) else r[2]
                if not s or not e:
                    continue
                t0 = datetime.fromisoformat(s); t1 = datetime.fromisoformat(e)
                durs.append((t1 - t0).total_seconds() * 1000.0)
            except Exception:
                continue
        durs.sort()
        p95 = durs[int(len(durs) * 0.95)] if durs else 0
        t_rows = _rows(db,
                       "SELECT payload FROM events WHERE ts>=? AND type='assistant_message'",
                       (since,))
        total_tokens = 0
        for r in t_rows:
            try:
                obj = json.loads(r.get("payload") if isinstance(r, dict) else r[0])
                total_tokens += int(obj.get("prompt_tokens") or 0) + int(obj.get("completion_tokens") or 0)
            except Exception:
                pass
        last_errs = _rows(db,
                          "SELECT session_id, payload FROM events WHERE ts>=? AND "
                          "(type='tool_error' OR lower(payload) LIKE ?) "
                          "ORDER BY ts DESC LIMIT ?", (since, '%"error"%', 5))
        trace_ids: list[dict] = []
        for r in last_errs:
            sid = r.get("session_id") if isinstance(r, dict) else r[0]
            p = r.get("payload") if isinstance(r, dict) else r[1]
            try:
                obj = json.loads(p or "{}")
                trace_ids.append({"session": sid, "trace": obj.get("trace_id")})
            except Exception:
                trace_ids.append({"session": sid, "trace": None})
        return {
            "window_h": window_h, "since": since, "dialect": db._dialect,
            "total_events": total, "errors": errors,
            "error_rate": (errors / total) if total else 0.0,
            "p95_turn_ms": round(p95, 1), "n_turns": len(durs),
            "total_tokens": total_tokens, "last_errors": trace_ids,
        }
    finally:
        try:
            db.close()
        except Exception:
            pass


def _check_and_alert(win: float, err_rate: float, p95: int, daily_tok: int) -> int:
    try:
        a = _agg(win)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"level": "FATAL", "msg": f"{type(e).__name__}: {e}"}), file=sys.stderr)
        return 2
    alerts: list[dict] = []
    if err_rate and a["error_rate"] > err_rate:
        alerts.append({"rule": "error_rate", "value": round(a["error_rate"], 4),
                       "threshold": err_rate, "samples": a["last_errors"]})
    if p95 and a["p95_turn_ms"] > p95:
        alerts.append({"rule": "latency_p95_ms", "value": a["p95_turn_ms"], "threshold": p95})
    if daily_tok and a["total_tokens"] > daily_tok:
        alerts.append({"rule": "total_tokens", "value": a["total_tokens"], "threshold": daily_tok})
    out = {
        "window_h": a["window_h"], "dialect": a["dialect"],
        "metrics": {k: a[k] for k in
                    ("total_events", "errors", "error_rate", "p95_turn_ms", "n_turns", "total_tokens")},
        "alerts": alerts,
    }
    print(json.dumps(out, ensure_ascii=False))
    return 1 if alerts else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-hours", type=float, default=1.0)
    ap.add_argument("--error-rate", type=float,
                    default=float(os.environ.get("ALERT_ERROR_RATE", "0.05")))
    ap.add_argument("--latency-p95", type=int,
                    default=int(os.environ.get("ALERT_P95_MS", "30000")))
    ap.add_argument("--daily-token", type=int,
                    default=int(os.environ.get("ALERT_TOKENS", "500000")))
    a = ap.parse_args()
    return _check_and_alert(a.window_hours, a.error_rate, a.latency_p95, a.daily_token)


if __name__ == "__main__":
    raise SystemExit(main())
