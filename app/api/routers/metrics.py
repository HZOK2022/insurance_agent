# -*- coding: utf-8 -*-
"""/api/metrics —— 从 events 表聚合运行/质量指标(自建观测,事实源)。SQLite / MySQL 方言自适应。

指标:turns(总数/错误/错误率)、latency_ms(avg/p50/p95)、tokens(prompt/completion + 成本)、
retrieval(总数/no_hits/命中率)、citations(回答数/带引用数/引用率)、models(按模型)。
"""
from __future__ import annotations
import json
from typing import Any

from fastapi import APIRouter

from app.api.services import container

router = APIRouter(prefix="/api", tags=["metrics"])


@router.get("/metrics")
def get_metrics() -> dict[str, Any]:
    store = container.get_store()
    # 复用 app/db 的方言/连接(store._conn 是 app.db.DB 包装);未配 db_host 走 SQLite
    dialect = getattr(store, "_dialect", "sqlite")
    conn = store._conn
    # MySQL 用 JSON_LENGTH / JSON_EXTRACT(路径 $.x);SQLite 用 json_array_length / json_extract
    def jlen(expr: str) -> str:
        return (f"JSON_LENGTH({expr})" if dialect == "mysql" else f"json_array_length({expr})")

    # 预解析 JSON 子表达式(Python 3.11 禁止 f-string 表达式内含反斜杠,故先算好再拼)
    jchunks = jlen("json_extract(payload,'$.chunks')")
    jcites = jlen("json_extract(payload,'$.citations')")

    def one(sql: str, params: tuple = ()):
        r = conn.execute(sql, params).fetchone()
        return r["v"] if r else 0

    total_turns = one("SELECT COUNT(*) v FROM events WHERE type='turn_end'")
    error_turns = one("SELECT COUNT(*) v FROM events WHERE type='turn_end' AND json_extract(payload,'$.reason')='error'")

    lat = [float(r["x"]) for r in conn.execute(
        "SELECT json_extract(payload,'$.elapsed_ms') x FROM events WHERE type='turn_end' "
        "AND json_extract(payload,'$.elapsed_ms') IS NOT NULL").fetchall()]
    lat_s = sorted(lat)
    avg_lat = round(sum(lat) / len(lat), 1) if lat else None
    p50 = round(lat_s[len(lat_s) // 2], 1) if lat_s else None
    p95 = round(lat_s[int(len(lat_s) * 0.95)], 1) if lat_s else None

    pt = one("SELECT COALESCE(SUM(json_extract(payload,'$.prompt_tokens')),0) v FROM events WHERE type='usage'")
    ct = one("SELECT COALESCE(SUM(json_extract(payload,'$.completion_tokens')),0) v FROM events WHERE type='usage'")

    retr = one("SELECT COUNT(*) v FROM events WHERE type='retrieval'")
    no_hits = one(f"SELECT COUNT(*) v FROM events WHERE type='retrieval' AND {jchunks}=0")

    am = one("SELECT COUNT(*) v FROM events WHERE type='assistant_message'")
    am_cite = one(f"SELECT COUNT(*) v FROM events WHERE type='assistant_message' AND {jcites}>0")

    models = {str(r["m"]): int(r["c"]) for r in conn.execute(
        "SELECT json_extract(payload,'$.model') m, COUNT(*) c FROM events WHERE type='usage' GROUP BY 1").fetchall()}

    # —— 扩展信号(需解析 payload,方言无关):重试/降级/护栏拦截/工具失败/检索低置信 ——————
    retries = one("SELECT COUNT(*) v FROM events WHERE type='llm_retry'")
    guard_triggered = one("SELECT COUNT(*) v FROM events WHERE type='guard_triggered'")

    def _payload(row) -> dict:
        p = row["payload"]
        return json.loads(p) if isinstance(p, str) else (p or {})

    # 检索低置信:某次检索"有命中但最高分 < 阈值"或"有块却无分数"→ 记低置信(检索召回弱但不为空)。
    # 空结果(no_hits)单独计;这里只算"非空但很弱"的召回。
    low_conf_thresh = 0.3
    retrieval_low_conf = 0
    for r in conn.execute("SELECT payload FROM events WHERE type='retrieval'").fetchall():
        chunks = (_payload(r).get("chunks") or [])
        if not chunks:
            continue
        scores = [c.get("score") for c in chunks if isinstance(c, dict) and isinstance(c.get("score"), (int, float))]
        if not scores or max(scores) < low_conf_thresh:
            retrieval_low_conf += 1

    # 工具失败(ok=False)+ 依赖降级(retrieval_unavailable = 向量库挂,知识检索降级)
    tool_failures = 0
    degradations = 0
    for r in conn.execute("SELECT payload FROM events WHERE type='tool_result'").fetchall():
        pl = _payload(r)
        if pl.get("ok") is False:
            tool_failures += 1
        if pl.get("error") == "retrieval_unavailable":
            degradations += 1

    # 成本(单价每 1M token;未配价为 0 → 不编造成本)
    pin = float(getattr(container.get_cfg(), "llm_price_input_per_1m", 0) or 0)
    pout = float(getattr(container.get_cfg(), "llm_price_output_per_1m", 0) or 0)
    cost = round((pt * pin + ct * pout) / 1_000_000, 4) if (pin or pout) else None

    return {
        "turns": {"total": total_turns, "error": error_turns,
                  "error_rate": round(error_turns / total_turns, 4) if total_turns else 0},
        "latency_ms": {"avg": avg_lat, "p50": p50, "p95": p95},
        "tokens": {"prompt": pt, "completion": ct, "cost": cost},
        "retrieval": {"total": retr, "no_hits": no_hits,
                      "low_conf": retrieval_low_conf,
                      "hit_rate": round((retr - no_hits) / retr, 4) if retr else 0},
        "citations": {"assistant": am, "with_cite": am_cite,
                      "cite_rate": round(am_cite / am, 4) if am else 0},
        "retries": retries,
        "guard_triggered": guard_triggered,
        "tool_failures": tool_failures,
        "degradations": degradations,
        "models": models,
    }
