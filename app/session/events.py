"""会话事件:类型注册表 + 校验(fail-closed)。参照 dsh core/session 的 SessionEventMap。

铁律:模型可见 ⟺ 已记录。新模型可见输入 = 新事件类型,必须先 register_type。
未注册类型在 validate / 加载时一律拒绝(fail-closed),绝不静默猜测。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

class UnknownEventError(ValueError):
    """日志里出现了未注册的事件类型(拒绝加载)。"""

def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

_EVENT_TYPES: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}


def _req(d: dict, key: str, t: type) -> Any:
    if key not in d or not isinstance(d[key], t):
        raise ValueError(f"payload 缺字段或类型不对: {key!r}")
    return d[key]


def _validate_user_message(p): return {"text": _req(p, "text", str), "client_time": p.get("client_time")}


def _validate_retrieval(p):
    chunks = _req(p, "chunks", list)
    for c in chunks:
        if not isinstance(c, dict):
            raise ValueError("chunk 必须是对象")
        for k in ("chunk_id", "doc_id", "version", "section", "source", "content"):
            if not isinstance(c.get(k), str):
                raise ValueError(f"chunk 缺字符串字段 {k}")
        if not isinstance(c.get("score"), (int, float)):
            raise ValueError("chunk score 非数字")
    return {"query": _req(p, "query", str), "chunks": chunks}


def _validate_assistant_message(p):
    blocks = p.get("blocks")
    if isinstance(blocks, list) and blocks:
        norm = []
        for b in blocks:
            if not isinstance(b, dict):
                continue
            t = b.get("t", "p")
            if t in ("ul", "ol"):
                norm.append({"t": t, "items": [str(x) for x in (b.get("items") or []) if x]})
            else:
                norm.append({"t": "p", "text": str(b.get("text", ""))})
    else:
        norm = [{"t": "p", "text": str(p.get("text", ""))}]
    cites = []
    for c in p.get("citations", []):
        if isinstance(c, dict) and "chunk_id" in c:
            cites.append({"idx": int(c["idx"]), "chunk_id": str(c["chunk_id"])})
    return {"blocks": norm, "citations": cites}


def _validate_assistant_chunk(p): return {"delta": _req(p, "delta", str)}
def _validate_tool_call(p): return {"tool": _req(p, "tool", str), "args": p.get("args")}
def _validate_tool_result(p):
    return {"tool": _req(p, "tool", str), "ok": bool(p.get("ok", False)),
            "result_truncated": bool(p.get("result_truncated", False)), "error": p.get("error")}
def _validate_approval_request(p): return {"tool": _req(p, "tool", str), "args": p.get("args"), "reason": p.get("reason")}
def _validate_approval_decision(p): return {"status": _req(p, "status", str), "decided_by": p.get("decided_by")}
def _validate_usage(p):
    return {"model": _req(p, "model", str), "prompt_tokens": _req(p, "prompt_tokens", int),
            "completion_tokens": _req(p, "completion_tokens", int), "cost_estimate": p.get("cost_estimate")}
def _validate_turn(p): return {}

_EVENT_TYPES.update({
    "user_message": _validate_user_message,
    "retrieval": _validate_retrieval,
    "assistant_message": _validate_assistant_message,
    "assistant_chunk": _validate_assistant_chunk,
    "tool_call": _validate_tool_call,
    "tool_result": _validate_tool_result,
    "approval_request": _validate_approval_request,
    "approval_decision": _validate_approval_decision,
    "usage": _validate_usage,
    "turn_start": _validate_turn,
    "turn_end": _validate_turn,
})


def register_type(name: str, validator: Callable[[dict], dict]) -> None:
    if name in _EVENT_TYPES:
        raise ValueError(f"重复注册事件类型: {name}")
    _EVENT_TYPES[name] = validator


def known_types() -> set[str]:
    return set(_EVENT_TYPES)


def validate(type_: str, payload: dict) -> dict:
    if type_ not in _EVENT_TYPES:
        raise UnknownEventError(f"未知事件类型: {type_}")
    return _EVENT_TYPES[type_](payload)


def make_event(type_: str, payload: dict) -> dict:
    return {"type": type_, "ts": utcnow(), "payload": validate(type_, payload)}