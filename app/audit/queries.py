# -*- coding: utf-8 -*-
"""审计/追溯层(⑨):events 日志 = 审计原始数据;这里提供查询视图 + 导出。

架构(architecture-v3 §审计/追溯层构成):
- events 日志 = 审计的原始数据(用户提问、检索片段、回答、引用、工具调用、审批决定全在日志里)
- 查询视图:按客服 / 时间 / 会话检索历史问答
- 导出:审计报表(合规留证,给监管/内审)

只读 events(SQLite),绝不写历史(铁律:events append-only)。
"""
from __future__ import annotations

import csv
import io
import json

from app.guardrails.redact import redact_obj
from app.session.store import SessionStore


def history_qa(store: SessionStore, *, session_id: str | None = None,
               user_id: str | None = None, since: str | None = None,
               until: str | None = None, limit: int = 50) -> list[dict]:
    """按会话/客服检索历史问答。每一条 = 一次「问题 → 回答」,带引用/成本/工具/审批计数。

    按事件序重建:user_message → 本 turn 的各类事件 → assistant_message(回答)。未成对(错误/中断)也保留。
    """
    sids: list[str] = []
    if session_id:
        sids = [session_id]
    else:
        for s in (store.list_sessions() or []):
            if user_id and s.get("user_id") != user_id:
                continue
            sids.append(s["id"])

    out: list[dict] = []
    for sid in sids:
        sess = store.get_session(sid) or {}
        cur: dict | None = None
        for ev in store.read(sid):
            t = ev["type"]
            p = ev["payload"] or {}
            if t == "user_message":
                if cur:
                    out.append(cur)
                cur = {"session_id": sid, "title": sess.get("title"), "user_id": sess.get("user_id"),
                       "ts": ev["ts"], "question": p.get("text"), "answer": None, "citations": [],
                       "model": None, "prompt_tokens": 0, "completion_tokens": 0, "cost": None,
                       "elapsed_ms": None, "reason": None, "retrievals": 0, "approvals": 0,
                       "retries": 0, "error": False}
            elif cur is None:
                continue
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
            elif t == "assistant_message":
                cur["answer"] = p.get("blocks") or []
                cur["citations"] = p.get("citations") or []
            elif t == "turn_end":
                cur["elapsed_ms"] = p.get("elapsed_ms")
                cur["reason"] = p.get("reason")
                if p.get("reason") not in (None, "completed"):
                    cur["error"] = True
                out.append(cur)
                cur = None
        if cur:
            out.append(cur)

    if since or until:
        out = [r for r in out if (not since or (r["ts"] or "") >= since) and (not until or (r["ts"] or "") <= until)]
    return out[:limit]


def _export_rows(store: SessionStore, session_id: str) -> tuple[dict, list[dict]]:
    sess = store.get_session(session_id) or {}
    rows = []
    for e in store.read(session_id):
        rows.append({"session_id": session_id, "title": sess.get("title"),
                     "seq": e["seq"], "type": e["type"], "ts": e["ts"], "payload": e["payload"]})
    return sess, rows


def export_session(store: SessionStore, session_id: str, fmt: str = "jsonl") -> str:
    """导出某会话的完整事件流(审计留证)。fmt: jsonl | json | csv。

    ⚠ 合规:导出前对每个事件 payload 做 PII 脱敏(redact_obj),防止审计报表外泄客户敏感信息。
    """
    sess, rows = _export_rows(store, session_id)
    sess = redact_obj(sess)                                   # session 元数据(如 title 可能含手机号)
    rows = [redact_obj(r) for r in rows]                      # 整行(含 title + payload)脱敏
    if fmt == "json":
        return json.dumps({"session": sess, "events": rows}, ensure_ascii=False, indent=2)
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["session_id", "title", "seq", "type", "ts", "payload"])
        for r in rows:
            w.writerow([r["session_id"], r.get("title") or "", r["seq"], r["type"], r["ts"],
                        json.dumps(r["payload"], ensure_ascii=False)])
        return buf.getvalue()
    # 默认 jsonl(每行一条完整事件,便于后续 ingest)
    return "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)


def audit_overview(store: SessionStore, *, session_id: str | None = None) -> dict:
    """某会话(或全库)的审计概览:事件数、类型分布、问答对数、审批/检索/引用计数。"""
    if session_id:
        rows = _export_rows(store, session_id)[1]
        scope = {"sessions": 1}
    else:
        rows = []
        for s in (store.list_sessions() or []):
            rows.extend(_export_rows(store, s["id"])[1])
        scope = {"sessions": len(store.list_sessions() or [])}
    type_dist: dict[str, int] = {}
    for r in rows:
        type_dist[r["type"]] = type_dist.get(r["type"], 0) + 1
    return {**scope, "events": len(rows), "type_dist": type_dist}
