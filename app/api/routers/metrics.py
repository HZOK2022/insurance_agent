# -*- coding: utf-8 -*-
"""/api/metrics —— 从 SQLite events 表聚合的运行/质量指标(可观测)。

指标:turns(总数/错误/错误率)、latency_ms(avg/p50/p95)、tokens(prompt/completion)、
retrieval(总数/no_hits/命中率)、citations(回答数/带引用数/引用率)、models(按模型)。
全部从 append-only 事件日志聚合(事实源),可重放、不需额外存储。
"""
from __future__ import annotations
from typing import Any

from fastapi import APIRouter

from app.api.services import container

router = APIRouter(prefix="/api", tags=["metrics"])


@router.get("/metrics")
def get_metrics() -> dict[str, Any]:
    store = container.get_store()
    conn = store._conn

    def one(sql: str, params: tuple = ()):
        r = conn.execute(sql, params).fetchone()
        return r[0] if r else 0

    total_turns = one("SELECT COUNT(*) FROM events WHERE type='turn_end'")
    error_turns = one("SELECT COUNT(*) FROM events WHERE type='turn_end' AND json_extract(payload,'$.reason')='error'")

    lat = [float(r[0]) for r in conn.execute(
        "SELECT json_extract(payload,'$.elapsed_ms') FROM events WHERE type='turn_end' "
        "AND json_extract(payload,'$.elapsed_ms') IS NOT NULL").fetchall()]
    lat_s = sorted(lat)
    avg_lat = round(sum(lat) / len(lat), 1) if lat else None
    p50 = round(lat_s[len(lat_s) // 2], 1) if lat_s else None
    p95 = round(lat_s[int(len(lat_s) * 0.95)], 1) if lat_s else None

    pt = one("SELECT COALESCE(SUM(json_extract(payload,'$.prompt_tokens')),0) FROM events WHERE type='usage'")
    ct = one("SELECT COALESCE(SUM(json_extract(payload,'$.completion_tokens')),0) FROM events WHERE type='usage'")

    retr = one("SELECT COUNT(*) FROM events WHERE type='retrieval'")
    no_hits = one("SELECT COUNT(*) FROM events WHERE type='retrieval' AND json_array_length(json_extract(payload,'$.chunks'))=0")

    am = one("SELECT COUNT(*) FROM events WHERE type='assistant_message'")
    am_cite = one("SELECT COUNT(*) FROM events WHERE type='assistant_message' AND json_array_length(json_extract(payload,'$.citations'))>0")

    models = {str(m): int(c) for m, c in conn.execute(
        "SELECT json_extract(payload,'$.model') m, COUNT(*) c FROM events WHERE type='usage' GROUP BY m").fetchall()}

    return {
        "turns": {"total": total_turns, "error": error_turns,
                  "error_rate": round(error_turns / total_turns, 4) if total_turns else 0},
        "latency_ms": {"avg": avg_lat, "p50": p50, "p95": p95},
        "tokens": {"prompt": pt, "completion": ct},
        "retrieval": {"total": retr, "no_hits": no_hits,
                      "hit_rate": round((retr - no_hits) / retr, 4) if retr else 0},
        "citations": {"assistant": am, "with_cite": am_cite,
                      "cite_rate": round(am_cite / am, 4) if am else 0},
        "models": models,
    }
