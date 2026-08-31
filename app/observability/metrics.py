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
    turns: list[dict] = []
    cur: dict | None = None
    for ev in store.read(session_id):
        t = ev["type"]
        p = ev["payload"] or {}
        if t == "turn_start":
            cur = {"turn_seq": ev["seq"], "ts": ev["ts"], "question": None, "answer": None,
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
            turns.append(cur)
            cur = None
    return turns


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
