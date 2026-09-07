# -*- coding: utf-8 -*-
"""可观测(⑥):把会话事件投影为遥测指标(照 dsh session-telemetry 结构,砍掉后端 seam)。

dsh 参照:把 session/event 投影成 ledger 记录 {channel, time, severity, attributes, body},
severity 映射:tool/result.isError、turn/end 错误、agent-error → error;其余 info。
本项目单机单进程,不引入外置 backend / OpenTelemetry / capability seam —— 改为按需从
SQLite events 日志(事实源)聚合派生指标:turn 级 + 会话级 + 全局;成本计量(tokens 从
usage 事件取,单价未配置则成本为 None)。

只读 events,绝不写历史(铁律:events append-only)。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import json

from app.session.store import SessionStore


def severity_of(reason: str | None, ok: bool | None = None) -> str:
    """照 dsh:错误原因 / isError → error;其余 info。"""
    if reason and reason not in ("completed",):
        return "error"
    if ok is False:
        return "error"
    return "info"


def estimate_cost(price_in_per_1m: float, price_out_per_1m: float,
                  prompt_tokens: int, completion_tokens: int) -> float | None:
    """成本(美元)。单价未配置(均 <=0)时返回 None,不与编造的单价计费。"""
    if price_in_per_1m <= 0 and price_out_per_1m <= 0:
        return None
    return (int(prompt_tokens or 0) / 1e6) * price_in_per_1m + (int(completion_tokens or 0) / 1e6) * price_out_per_1m


def project_turn_metrics(store: SessionStore, session_id: str, *,
                         price_in_per_1m: float = 0.0, price_out_per_1m: float = 0.0) -> list[dict]:
    """把一个会话的事件投影成逐 turn 的遥测记录(照 dsh 的事件→记录投影)。"""
    return [turn for _sid, turn in _project_turn_rows(
        _iter_metric_events(store, session_id=session_id), price_in_per_1m, price_out_per_1m)]


# turn 级投影只用这些事件类型(跳过 assistant_chunk / tool_result / request_context 等大 payload 事件,
# 它们不参与指标投影)。与 anomaly_report 同口径:命中类型一次有序扫描,避免按会话 store.read(sid)
# 把全部事件(含大量 assistant_chunk)读进来再 json.loads —— 那是"观测总览"慢的根因(几十万行)。
_METRIC_TYPES = ("turn_start", "user_message", "assistant_message", "step_start", "tool_call",
                 "retrieval", "approval_request", "llm_retry", "usage", "turn_end")


def _iter_metric_events(store: SessionStore, session_id: str | None = None):
    """只取指标相关事件(按 seq 有序),yield (session_id, ev)。payload 解析为 dict。

    session_id 给定 → 该会话;否则全量(按 session_id, seq 排序)。'?' 占位符由 app.db.DB 按方言 translate。
    """
    incl = ",".join("'" + t + "'" for t in _METRIC_TYPES)
    if session_id is not None:
        sql = (f"SELECT session_id, seq, type, ts, payload FROM events "
               f"WHERE type IN ({incl}) AND session_id=? ORDER BY seq")
        rows = store._conn.execute(sql, (session_id,)).fetchall()
    else:
        sql = (f"SELECT session_id, seq, type, ts, payload FROM events "
               f"WHERE type IN ({incl}) ORDER BY session_id, seq")
        rows = store._conn.execute(sql).fetchall()
    for r in rows:
        yield r["session_id"], {"seq": r["seq"], "type": r["type"], "ts": r["ts"],
                                "payload": json.loads(r["payload"]) if isinstance(r["payload"], str) else (r["payload"] or {})}


def _project_turn_rows(rows, price_in_per_1m: float, price_out_per_1m: float):
    """把 (session_id, ev) 有序流投影成逐 turn 记录,yield (session_id, turn)。

    状态机与旧 project_turn_metrics 完全一致,仅把"按会话 read 全部事件"换成"只喂指标相关事件",
    因此聚合结果不变,只是不再读入几十万行 assistant_chunk。仅 turn_end 收尾时才 yield(悬挂 turn 丢弃)。
    """
    cur_sid = None
    cur: dict | None = None
    for sid, ev in rows:
        if sid != cur_sid:
            cur_sid = sid
            cur = None
        t = ev["type"]
        p = ev["payload"] or {}
        if t == "turn_start":
            # trace_id = 该轮 turn_start 的 seq(轮级 trace_id,M0):与 usage/badcase 里的 trace_id 同口径
            cur = {"turn_seq": ev["seq"], "trace_id": ev["seq"], "ts": ev["ts"], "question": None, "answer": None,
                   "citations": [], "model": None, "prompt_tokens": 0, "completion_tokens": 0,
                   "cost": None, "ttft_ms": None, "tps": None, "elapsed_ms": None,
                   "reason": None, "severity": "info", "steps": 0, "tools": 0,
                   "retrievals": 0, "approvals": 0, "retries": 0}
        elif cur is None:
            continue
        elif t == "user_message":
            cur["question"] = p.get("text")
        elif t == "assistant_message":
            cur["answer"] = p.get("blocks") or []
            cur["citations"] = p.get("citations") or []
        elif t == "step_start":
            cur["steps"] += 1
        elif t == "tool_call":
            cur["tools"] += 1
        elif t == "retrieval":
            cur["retrievals"] += 1
        elif t == "approval_request":
            cur["approvals"] += 1
        elif t == "llm_retry":
            cur["retries"] += 1
        elif t == "usage":
            cur["model"] = p.get("model") or cur["model"]
            cur["prompt_tokens"] = p.get("prompt_tokens") or 0
            cur["completion_tokens"] = p.get("completion_tokens") or 0
            cur["cost"] = p.get("cost_estimate")
            cur["ttft_ms"] = p.get("ttft_ms")
            cur["tps"] = p.get("tokens_per_second")
        elif t == "turn_end":
            cur["elapsed_ms"] = p.get("elapsed_ms")
            cur["reason"] = p.get("reason")
            cur["severity"] = severity_of(p.get("reason"))
            if cur["cost"] is None:  # usage 没算就按配置单价估算
                cur["cost"] = estimate_cost(price_in_per_1m, price_out_per_1m,
                                            cur["prompt_tokens"], cur["completion_tokens"])
            yield cur_sid, cur
            cur = None



def _agg(turns: list[dict]) -> dict:
    n = len(turns)
    costs = [t["cost"] for t in turns if t["cost"] is not None]
    return {
        "turns": n,
        "prompt_tokens": sum(int(t["prompt_tokens"] or 0) for t in turns),
        "completion_tokens": sum(int(t["completion_tokens"] or 0) for t in turns),
        "total_tokens": sum(int(t["prompt_tokens"] or 0) for t in turns) + sum(int(t["completion_tokens"] or 0) for t in turns),
        "cost": round(sum(costs), 6) if costs else None,
        "errors": sum(1 for t in turns if t["severity"] == "error"),
        "retries": sum(int(t["retries"] or 0) for t in turns),
        "approvals": sum(int(t["approvals"] or 0) for t in turns),
        "avg_ttft_ms": round(sum(t["ttft_ms"] for t in turns if t["ttft_ms"] is not None) / max(1, sum(1 for t in turns if t["ttft_ms"] is not None))),
        "avg_tps": round(sum(t["tps"] for t in turns if t["tps"] is not None) / max(1, sum(1 for t in turns if t["tps"] is not None)), 1),
    }


def session_metrics(store: SessionStore, session_id: str, *,
                    price_in_per_1m: float = 0.0, price_out_per_1m: float = 0.0) -> dict:
    """某会话的可观测指标汇总。"""
    turns = project_turn_metrics(store, session_id, price_in_per_1m=price_in_per_1m,
                                 price_out_per_1m=price_out_per_1m)
    return {"session_id": session_id, **_agg(turns)}


def overall_metrics(store: SessionStore, *, price_in_per_1m: float = 0.0,
                    price_out_per_1m: float = 0.0) -> dict:
    """全局可观测汇总:遍历会话,聚合成总览。"""
    tot = {"sessions": 0, "turns": 0, "prompt_tokens": 0, "completion_tokens": 0,
           "total_tokens": 0, "cost": 0.0, "errors": 0, "retries": 0, "approvals": 0,
           "avg_ttft_ms": 0, "avg_tps": 0}
    per = []
    for s in (store.list_sessions() or []):
        m = session_metrics(store, s["id"], price_in_per_1m=price_in_per_1m, price_out_per_1m=price_out_per_1m)
        tot["sessions"] += 1
        for k in ("turns", "prompt_tokens", "completion_tokens", "total_tokens", "errors", "retries", "approvals"):
            tot[k] += m[k]
        tot["cost"] += m["cost"] or 0.0
        tot["avg_ttft_ms"] += m["avg_ttft_ms"]
        tot["avg_tps"] += m["avg_tps"]
        per.append({"session_id": s["id"], "title": s.get("title"), "user_id": s.get("user_id"), **m})
    if per:
        n = len(per)
        tot["avg_ttft_ms"] = round(tot["avg_ttft_ms"] / n)
        tot["avg_tps"] = round(tot["avg_tps"] / n, 1)
        tot["cost"] = round(tot["cost"], 6)
    return {"totals": tot, "per_session": per}


def _parse_ts(ts: str | None) -> datetime | None:
    """把 ISO8601(带时区)时间戳解析为 aware datetime;失败返回 None。"""
    if not ts:
        return None
    s = ts
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _bucket_start(dt: datetime, granularity: str) -> datetime:
    if granularity == "day":
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    # 默认 hour
    return dt.replace(minute=0, second=0, microsecond=0)


def timeseries_metrics(store: SessionStore, *, granularity: str = "hour",
                       price_in_per_1m: float = 0.0, price_out_per_1m: float = 0.0) -> list[dict]:
    """按时间分桶聚合:每桶 turns / errors / tokens / cost / 延迟(avg、p95)。

    granularity='hour'|'day'。只从 events(事实源)派生,绝不写历史。空桶按首个与末个
    有数据桶之间的区间补齐(0 值),便于前端画"走势",不漏时空档。
    """
    if granularity not in ("hour", "day"):
        granularity = "hour"

    zero = lambda: {"turns": 0, "errors": 0, "prompt_tokens": 0, "completion_tokens": 0,
                    "cost": 0.0, "lat": [], "retries": 0}
    buckets: dict[datetime, dict] = defaultdict(zero)

    for s in (store.list_sessions() or []):
        for turn in project_turn_metrics(store, s["id"], price_in_per_1m=price_in_per_1m,
                                         price_out_per_1m=price_out_per_1m):
            dt = _parse_ts(turn.get("ts"))
            if dt is None:
                continue
            b = buckets[_bucket_start(dt, granularity)]
            b["turns"] += 1
            if turn.get("severity") == "error":
                b["errors"] += 1
            b["prompt_tokens"] += int(turn.get("prompt_tokens") or 0)
            b["completion_tokens"] += int(turn.get("completion_tokens") or 0)
            if turn.get("cost") is not None:
                b["cost"] += float(turn["cost"])
            if turn.get("elapsed_ms") is not None:
                b["lat"].append(float(turn["elapsed_ms"]))
            b["retries"] += int(turn.get("retries") or 0)

    if not buckets:
        return []
    keys = sorted(buckets.keys())
    step = timedelta(days=1) if granularity == "day" else timedelta(hours=1)

    out: list[dict] = []
    cur = keys[0]
    while cur <= keys[-1]:
        b = buckets[cur]
        lat = sorted(b["lat"])
        avg_lat = round(sum(lat) / len(lat), 1) if lat else None
        p95_lat = round(lat[max(0, int(len(lat) * 0.95) - 1)], 1) if lat else None
        total = b["prompt_tokens"] + b["completion_tokens"]
        out.append({
            "bucket": cur.isoformat(),
            "turns": b["turns"],
            "errors": b["errors"],
            "prompt_tokens": b["prompt_tokens"],
            "completion_tokens": b["completion_tokens"],
            "total_tokens": total,
            "cost": round(b["cost"], 6) if b["cost"] else None,
            "avg_latency_ms": avg_lat,
            "p95_latency_ms": p95_lat,
            "retries": b["retries"],
        })
        cur += step
    return out


# ＝＝ P0 异常定位器(A1 + A2):把每个坏轮分类成可行动原因 + 检索→引用漏斗聚合 ＝＝
# 只读 events(事实源)派生,绝不写历史。分类类别见 _ANOMALY_ORDER。
_ANOMALY_ORDER = ["error", "guard_triggered", "tool_failure", "retrieval_unavailable",
                  "retrieval_empty", "retrieval_low_conf", "cross_turn_no_retrieval",
                  "answer_not_cited", "latency_spike"]
_ANOMALY_LABEL = {
    "error": "LLM/会话错误",
    "guard_triggered": "护栏拦截(注入/PII)",
    "tool_failure": "工具失败",
    "retrieval_unavailable": "向量库不可用(检索降级)",
    "retrieval_empty": "检索 0 命中",
    "retrieval_low_conf": "检索低置信",
    "cross_turn_no_retrieval": "无检索却带引用(跨轮/历史)",
    "answer_not_cited": "检索了但回答0引用",
    "latency_spike": "延迟尖峰",
}


def _classify_turn(t: dict, low_conf_thresh: float, latency_threshold_ms: float | None) -> str | None:
    """按优先级给一轮判一个主因类别(取最先命中的一条)。"""
    if t.get("reason") not in (None, "completed"):
        return "error"
    if t.get("guard"):
        return "guard_triggered"
    if t.get("tool_failures"):
        return "tool_failure"
    if t.get("retrieval_unavailable"):
        return "retrieval_unavailable"
    if t.get("retrieval_empty"):
        return "retrieval_empty"
    if t.get("retrievals") and t.get("retrieval_max") is not None and t["retrieval_max"] < low_conf_thresh:
        return "retrieval_low_conf"
    # 无检索却给了带引用(跨轮/历史引用)——仅依上下文,不可当轮追溯,高危
    if not t.get("retrievals") and t.get("with_cite"):
        return "cross_turn_no_retrieval"
    # 检索了但回答一个引用都没有 → 潜在"检索了却没引用/别处取材"
    if t.get("retrievals") and t.get("assistant") and not t.get("with_cite"):
        return "answer_not_cited"
    if latency_threshold_ms and t.get("elapsed_ms") is not None and t["elapsed_ms"] > latency_threshold_ms:
        return "latency_spike"
    return None


def _turn_hint(t: dict, cat: str) -> str | None:
    if cat == "retrieval_low_conf" and t.get("retrieval_max") is not None:
        return f"检索最高分 {t['retrieval_max']:.2f} < {0.3}"
    if cat == "retrieval_empty":
        return "本轮检索 0 命中"
    if cat == "tool_failure" and t.get("tool_fail_hint"):
        return t["tool_fail_hint"]
    if cat == "answer_not_cited":
        # M2:检索到了(最高分不低)却一个不引用 → "模型没用检索"更明显;给 hint 带分数证据(D79)
        if t.get("retrieval_max") is not None:
            return f"检索 {t.get('retrievals')} 次(最高分 {t['retrieval_max']:.2f})但回答 0 引用 → 更像模型没用检索"
        return f"检索 {t.get('retrievals')} 次但回答 0 引用"
    if cat == "cross_turn_no_retrieval":
        return f"本轮无检索,引用 {t.get('cited_count')} 条(历史/跨轮)"
    if cat == "latency_spike" and t.get("elapsed_ms") is not None:
        return f"耗时 {t['elapsed_ms'] / 1000:.0f}s"
    if cat == "guard_triggered":
        return "护栏拦截(注入/PII)"
    if cat == "retrieval_unavailable":
        return "向量库不可用(知识检索降级)"
    if cat == "error":
        return "LLM/会话错误"
    return None


def anomaly_report(store: SessionStore, *, price_in_per_1m: float = 0.0,
                   price_out_per_1m: float = 0.0, low_conf_thresh: float = 0.3,
                   latency_threshold_ms: float | None = None) -> dict:
    """生产异常定位:坏轮按主因分类 + trace_id 样本 + why 提示 + 检索→引用漏斗。"""
    cats = {c: {"count": 0, "samples": [], "hints": []} for c in _ANOMALY_ORDER}
    funnel = {"answer_turns": 0, "with_retrieval_turns": 0, "retrieval_total": 0,
              "cited_total": 0, "cited_turns": 0, "cited_rate": None}
    total_turns = 0
    # 单次查询只取相关事件类型(跳过 badcase_snapshot / assistant_chunk 等大 payload 事件),
    # 按 session/seq 排好一次性扫描,避免按会话 N 次 read 往返 + 读入大量级 payload。
    # 用 events(事实源)而非 list_sessions:sessions 表可能被软删(deleted=1),events 是 append-only
    # 仍保留,异常定位器必须不漏掉被删会话里的坏轮。
    rows = store._conn.execute(
        "SELECT session_id, seq, type, payload FROM events "
        "WHERE type IN ('turn_start','turn_end','retrieval','assistant_message','tool_result','guard_triggered') "
        "ORDER BY session_id, seq").fetchall()
    cur_sid: object = None
    cur: dict | None = None
    cur_trace: int | None = None   # 当前轮 trace_id = turn_start 的 seq(轮级)
    for row in rows:
        sid = row["session_id"]
        if sid != cur_sid:
            cur_sid = sid
            cur = None
            cur_trace = None
        t = row["type"]
        p = row["payload"] or {}
        if isinstance(p, str):
            try:
                p = json.loads(p)
            except Exception:
                p = {}
            if t == "turn_start":
                cur_trace = row["seq"]
                cur = {"reason": None, "guard": 0, "tool_failures": 0, "tool_fail_hint": None,
                       "retrieval_unavailable": 0, "retrieval_empty": 0, "retrievals": 0,
                       "retrieval_max": None, "assistant": 0, "cited_count": 0, "with_cite": False,
                       "elapsed_ms": None}
            elif cur is None:
                continue
            elif t == "retrieval":
                cur["retrievals"] += 1
                chunks = p.get("chunks") or []
                if p.get("error") == "retrieval_unavailable":
                    cur["retrieval_unavailable"] += 1
                if not chunks:
                    cur["retrieval_empty"] += 1
                else:
                    sc = [c.get("score") for c in chunks
                          if isinstance(c, dict) and isinstance(c.get("score"), (int, float))]
                    if sc:
                        cur["retrieval_max"] = max(cur["retrieval_max"] or 0, max(sc))
            elif t == "assistant_message":
                cur["assistant"] += 1
                cites = p.get("citations") or []
                cur["cited_count"] = max(cur["cited_count"], len(cites))
                if cites:
                    cur["with_cite"] = True
            elif t == "tool_result":
                if p.get("ok") is False:
                    cur["tool_failures"] += 1
                    cur["tool_fail_hint"] = f"工具失败: {p.get('tool')} code={p.get('error_code')}"
            elif t == "guard_triggered":
                cur["guard"] += 1
            elif t == "turn_end":
                cur["elapsed_ms"] = p.get("elapsed_ms")
                cur["reason"] = p.get("reason")
                total_turns += 1
                cat = _classify_turn(cur, low_conf_thresh, latency_threshold_ms)
                if cat:
                    rec = cats[cat]
                    rec["count"] += 1
                    # 样本 = 会话 + 该坏轮的 trace_id(轮级),前端可直达那一轮
                    sample = {"session_id": sid, "trace_id": cur_trace}
                    if all(s.get("session_id") != sid or s.get("trace_id") != cur_trace for s in rec["samples"]) and len(rec["samples"]) < 5:
                        rec["samples"].append(sample)
                    hint = _turn_hint(cur, cat)
                    if hint and len(rec["hints"]) < 5:
                        rec["hints"].append(hint)
                if cur["assistant"]:
                    funnel["answer_turns"] += 1
                if cur["retrievals"]:
                    funnel["with_retrieval_turns"] += 1
                    funnel["retrieval_total"] += cur["retrievals"]
                if cur["with_cite"]:
                    funnel["cited_turns"] += 1
                    funnel["cited_total"] += cur["cited_count"]
                cur = None
    if funnel["with_retrieval_turns"]:
        funnel["cited_rate"] = round(funnel["cited_turns"] / funnel["with_retrieval_turns"], 4)
    anomaly_total = sum(c["count"] for c in cats.values())
    return {
        "summary": {"total_turns": total_turns, "anomalies": anomaly_total},
        "categories": {c: {"count": v["count"], "label": _ANOMALY_LABEL[c],
                           "samples": v["samples"], "hints": v["hints"]} for c, v in cats.items()},
        "funnel": funnel,
    }


def classify_retrieval_failure(retrieved: list[str], cited: list[str], gold: list[str] | None = None) -> dict | None:
    """把"检索→回答"没闭环的坏轮细分为可行动根因(M2)。

    - 有 gold(离线评估标注"正确答案应召回哪些块")时:
      · gold ∩ retrieved 为空       → gold_miss:正确块没进最终返回(漏召,或被 top_k/重排截断)
      · gold ∩ retrieved 非空但 gold ∩ cited 为空 → model_not_used:块已召回却没引用(修 prompt/引用)
    - 无 gold(生产在线):检索有命中但回答零引用 → model_not_used(≈ answer_not_cited 的模型侧细化)。

    retrieved: 本轮最终返回的 chunk_id 列表(retrieval 事件 chunks 的 id);
    cited:     回答引用的 chunk_id 列表;gold: 评估集 per-case 标注;三者均可空。
    返回 {"kind", "reason"} 或 None(已引用到召回块 / 无需判定)。
    """
    rs = set(retrieved or [])
    cs = set(cited or [])
    gs = set(gold or [])
    if gs:
        if not (rs & gs):
            return {"kind": "gold_miss",
                    "reason": "正确块没进最终返回(漏召,或被 top_k/重排截断)"}
        if not (gs & cs):
            return {"kind": "model_not_used",
                    "reason": "正确块已召回但回答未引用(模型没用检索)"}
        return None
    if rs and not cs:
        return {"kind": "model_not_used", "reason": "检索有命中但回答未引用(模型没用检索)"}
    return None
