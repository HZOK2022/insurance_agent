# -*- coding: utf-8 -*-
"""/api/metrics —— 从 events 表聚合运行/质量指标(自建观测,事实源)。SQLite / MySQL 方言自适应。

指标:turns(总数/错误/错误率)、latency_ms(avg/p50/p95)、tokens(prompt/completion + 成本)、
retrieval(总数/no_hits/命中率)、citations(回答数/带引用数/引用率)、models(按模型)。

性能(20 用户并发加固):
- 观测三端点(/metrics、/metrics/timeseries、/metrics/anomalies)挂**进程内 TTL 缓存**
  (metrics_cache_ttl_seconds,默认 30s,0=关):观测大盘是派生只读指标,允许短暂过期,
  重复打开页面直接命中,且避免"重算持 DB 锁 1s → 阻塞全体聊天写入"的互相踩踏。
- 低置信计数:SQLite 走 json_each **库内聚合**(不把大 payload 拉回 Python);
  MySQL 走 Python 逐行(8.0.16 的 JSON_TABLE 跑该查询会崩库,见 _low_conf_python 注释)。
- 工具失败/降级计数:纯 json_extract SQL 聚合(两方言同写法,布尔 false 均读成 0)。
"""
from __future__ import annotations
import json
import logging
from typing import Any

from fastapi import APIRouter

from app.api.services import container
from app.observability.metrics import timeseries_metrics, anomaly_report
from app.util.ttl_cache import TTLCache

router = APIRouter(prefix="/api", tags=["metrics"])
_cache = TTLCache()


def _ttl() -> float:
    return float(int(getattr(container.get_cfg(), "metrics_cache_ttl_seconds", 30) or 0))


@router.get("/metrics")
def get_metrics() -> dict[str, Any]:
    return _cache.get_or_put("metrics", _compute_metrics, _ttl())


def _ev_payload(row) -> dict:
    p = row["payload"]
    return json.loads(p) if isinstance(p, str) else (p or {})


def _low_conf_sqlite(conn, thresh: float) -> tuple[int, list[str]]:
    """SQLite:json_each 库内展开 chunks 取 MAX(score)(进程内,无崩库风险)。

    返回 (低置信数, 前 5 个不同会话样本)。"有块但全部无 score" → MAX 为 NULL → 也算低置信。
    """
    rows = conn.execute(
        "SELECT e.session_id sid, MAX(CAST(json_extract(je.value,'$.score') AS REAL)) ms "
        "FROM events e, json_each(COALESCE(json_extract(e.payload,'$.chunks'),'[]')) je "
        "WHERE e.type='retrieval' AND json_array_length(json_extract(e.payload,'$.chunks'))>0 "
        "GROUP BY e.seq, e.session_id").fetchall()
    count = 0
    samples: list[str] = []
    for r in rows:
        ms = r["ms"]
        if ms is None or float(ms) < thresh:
            count += 1
            if r["sid"] and r["sid"] not in samples and len(samples) < 5:
                samples.append(r["sid"])
    return count, samples


def _low_conf_python(conn, thresh: float) -> tuple[int, list[str]]:
    """Python 逐行(原实现口径;MySQL 8.0.16 JSON_TABLE 崩库,故 MySQL 走此路)。"""
    count = 0
    samples: list[str] = []
    for r in conn.execute("SELECT session_id, payload FROM events WHERE type='retrieval'").fetchall():
        chunks = (_ev_payload(r).get("chunks") or [])
        if not chunks:
            continue
        scores = [c.get("score") for c in chunks if isinstance(c, dict) and isinstance(c.get("score"), (int, float))]
        if not scores or max(scores) < thresh:
            count += 1
            if r["session_id"] and r["session_id"] not in samples and len(samples) < 5:
                samples.append(r["session_id"])
    return count, samples


def _compute_metrics() -> dict[str, Any]:
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

    # —— 扩展信号(方言无关 SQL):重试/降级/护栏拦截 ——————
    retries = one("SELECT COUNT(*) v FROM events WHERE type='llm_retry'")
    guard_triggered = one("SELECT COUNT(*) v FROM events WHERE type='guard_triggered'")

    def _distinct_sessions(cond: str, limit: int = 5) -> list[str]:
        # 事件表的 session_id = 会话(窗口);轮级 trace_id = 各轮 turn_start 的 seq(见 /api/metrics/anomalies 样本)。
        # 此处样本取会话,供"回放"跳到该会话轨迹;轮级直达由 anomalies 的 sample.trace_id 承担。
        rows = conn.execute(f"SELECT DISTINCT session_id FROM events WHERE {cond} LIMIT {limit}").fetchall()
        return [r["session_id"] for r in rows if r["session_id"]]

    # 检索低置信:某次检索"有命中但最高分 < 阈值"或"有块却无分数"→ 记低置信(召回弱但不为空)。
    # 空结果(no_hits)单独计;这里只算"非空但很弱"的召回。
    # SQL 侧聚合(仅 SQLite json_each):库内展开 chunks 取 MAX(score),每事件只回一行小结果,
    # 不把大 payload(含检索块原文快照)拉回 Python 逐行 json.loads。
    # ⚠ MySQL 刻意不走库内展开:本机 MySQL 8.0.16 实测 JSON_TABLE 该查询直接**崩库**
    # (error log 栈:Table_function_json::fill_result_table 段错误,8.0.17+ 才修复该类 bug),
    # 观测接口绝不允许打死事实源 DB → MySQL 一律 Python 逐行(TTL 缓存兜底,661 行约 300ms/30s)。
    low_conf_thresh = 0.3
    if dialect == "sqlite":
        try:
            retrieval_low_conf, low_conf_samples = _low_conf_sqlite(conn, low_conf_thresh)
        except Exception:
            # json1 扩展不可用等极端情况 → 降级 Python 逐行(语义不变)
            logging.getLogger(__name__).warning("低置信 SQLite json_each 聚合失败,回退 Python 逐行", exc_info=True)
            retrieval_low_conf, low_conf_samples = _low_conf_python(conn, low_conf_thresh)
    else:
        retrieval_low_conf, low_conf_samples = _low_conf_python(conn, low_conf_thresh)

    # 工具失败(ok=False)+ 依赖降级(retrieval_unavailable = 向量库挂,知识检索降级)。
    # 纯 SQL 聚合:ok 是布尔,两方言 json_extract 均把 false 读成 0(true=1,缺失=NULL);
    # 与错误出参日志同口径,样本取前 5 个不同会话。
    _fail = "json_extract(payload,'$.ok')=0"
    _degrade = "json_extract(payload,'$.error')='retrieval_unavailable'"
    tool_failures = one(f"SELECT COUNT(*) v FROM events WHERE type='tool_result' AND {_fail}")
    degradations = one(f"SELECT COUNT(*) v FROM events WHERE type='tool_result' AND {_degrade}")
    tool_fail_samples = _distinct_sessions(f"type='tool_result' AND {_fail}")
    degrade_samples = _distinct_sessions(f"type='tool_result' AND {_degrade}")

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
    def _compute() -> dict[str, Any]:
        store = container.get_store()
        cfg = container.get_cfg()
        pin = float(getattr(cfg, "llm_price_input_per_1m", 0) or 0)
        pout = float(getattr(cfg, "llm_price_output_per_1m", 0) or 0)
        series = timeseries_metrics(store, granularity=granularity,
                                    price_in_per_1m=pin, price_out_per_1m=pout)
        return {"granularity": granularity, "series": series}
    return _cache.get_or_put(f"timeseries:{granularity}", _compute, _ttl())


@router.get("/metrics/anomalies")
def get_anomalies(latency_ms: float | None = None) -> dict[str, Any]:
    """生产异常定位:坏轮按主因分类 + trace_id 样本 + why 提示 + 检索→引用漏斗。

    latency_ms 可选,给定时才把超时轮归为 latency_spike;只读 events(事实源)。
    """
    def _compute() -> dict[str, Any]:
        store = container.get_store()
        cfg = container.get_cfg()
        pin = float(getattr(cfg, "llm_price_input_per_1m", 0) or 0)
        pout = float(getattr(cfg, "llm_price_output_per_1m", 0) or 0)
        return anomaly_report(store, price_in_per_1m=pin, price_out_per_1m=pout,
                              latency_threshold_ms=latency_ms)
    return _cache.get_or_put(f"anomalies:{latency_ms}", _compute, _ttl())
