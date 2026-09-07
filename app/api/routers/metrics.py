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
from app.observability.metrics import timeseries_metrics, anomaly_report

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

    def _distinct_sessions(cond: str, limit: int = 5) -> list[str]:
        # 事件表的 session_id = 会话(窗口);轮级 trace_id = 各轮 turn_start 的 seq(见 /api/metrics/anomalies 样本)。
        # 此处样本取会话,供"回放"跳到该会话轨迹;轮级直达由 anomalies 的 sample.trace_id 承担。
        rows = conn.execute(f"SELECT DISTINCT session_id FROM events WHERE {cond} LIMIT {limit}").fetchall()
        return [r["session_id"] for r in rows if r["session_id"]]

    # 检索低置信:某次检索"有命中但最高分 < 阈值"或"有块却无分数"→ 记低置信(检索召回弱但不为空)。
    # 空结果(no_hits)单独计;这里只算"非空但很弱"的召回。
    low_conf_thresh = 0.3
    retrieval_low_conf = 0
    low_conf_samples: list[str] = []
    for r in conn.execute("SELECT session_id, payload FROM events WHERE type='retrieval'").fetchall():
        chunks = (_payload(r).get("chunks") or [])
        if not chunks:
            continue
        scores = [c.get("score") for c in chunks if isinstance(c, dict) and isinstance(c.get("score"), (int, float))]
        if not scores or max(scores) < low_conf_thresh:
            retrieval_low_conf += 1
            if r["session_id"] and r["session_id"] not in low_conf_samples and len(low_conf_samples) < 5:
                low_conf_samples.append(r["session_id"])

    # 工具失败(ok=False)+ 依赖降级(retrieval_unavailable = 向量库挂,知识检索降级)
    tool_failures = 0
    degradations = 0
    tool_fail_samples: list[str] = []
    degrade_samples: list[str] = []
    for r in conn.execute("SELECT session_id, payload FROM events WHERE type='tool_result'").fetchall():
        pl = _payload(r)
        if pl.get("ok") is False:
            tool_failures += 1
            if r["session_id"] and r["session_id"] not in tool_fail_samples and len(tool_fail_samples) < 5:
                tool_fail_samples.append(r["session_id"])
        if pl.get("error") == "retrieval_unavailable":
            degradations += 1
            if r["session_id"] and r["session_id"] not in degrade_samples and len(degrade_samples) < 5:
                degrade_samples.append(r["session_id"])

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
        "samples": {
            "error_turns": _distinct_sessions("type='turn_end' AND json_extract(payload,'$.reason')='error'"),
            "retries": _distinct_sessions("type='llm_retry'"),
            "guard_triggered": _distinct_sessions("type='guard_triggered'"),
            "retrieval_low_conf": low_conf_samples,
            "tool_failures": tool_fail_samples,
            "degradations": degrade_samples,
        },
        "models": models,
    }


@router.get("/metrics/timeseries")
def get_timeseries(granularity: str = "hour") -> dict[str, Any]:
    """按时间分桶聚合(成本/延迟/token 走势)。granularity=hour|day,默认 hour。

    自建观测的"时间序列大盘"数据源;只读 events(事实源),绝不写历史。
    """
    store = container.get_store()
    cfg = container.get_cfg()
    pin = float(getattr(cfg, "llm_price_input_per_1m", 0) or 0)
    pout = float(getattr(cfg, "llm_price_output_per_1m", 0) or 0)
    series = timeseries_metrics(store, granularity=granularity,
                                price_in_per_1m=pin, price_out_per_1m=pout)
    return {"granularity": granularity, "series": series}


@router.get("/metrics/anomalies")
def get_anomalies(latency_ms: float | None = None) -> dict[str, Any]:
    """生产异常定位:坏轮按主因分类 + trace_id 样本 + why 提示 + 检索→引用漏斗。

    latency_ms 可选,给定时才把超时轮归为 latency_spike;只读 events(事实源)。
    """
    store = container.get_store()
    cfg = container.get_cfg()
    pin = float(getattr(cfg, "llm_price_input_per_1m", 0) or 0)
    pout = float(getattr(cfg, "llm_price_output_per_1m", 0) or 0)
    return anomaly_report(store, price_in_per_1m=pin, price_out_per_1m=pout,
                          latency_threshold_ms=latency_ms)
