# -*- coding: utf-8 -*-
"""会话上下文:把事件日志折成"模型可见的对话历史",供多轮上下文。

阶段 A(恢复跨轮上下文):user_message→user,assistant_message→assistant。
- 剥掉历史回答里的 [idx](那是"当轮"的引用编号,喂进下一轮会串)。
- 跳过 reasoning(r 块)、narration、retrieval/tool(过程性,不作为历史正文)。
"""
from __future__ import annotations

import re
from typing import Any

_IDX = re.compile(r"\[\d+\]")


def _strip_idx(text: str) -> str:
    return _IDX.sub("", text).strip()


def _block_text(b: dict) -> str:
    t = b.get("t")
    if t in ("ul", "ol"):
        return "\n".join(str(x) for x in (b.get("items") or []))
    return str(b.get("text", ""))


def _assistant_content(blocks: list) -> str:
    """只取回答块(p/h/ul/ol),跳过 reasoning(r);去掉历史 [idx] 引用标记。"""
    parts: list[str] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        if b.get("t") == "r":
            continue
        txt = _block_text(b)
        if txt:
            parts.append(txt)
    return _strip_idx("\n".join(parts))


def build_history(store: Any, session_id: str) -> list[dict]:
    """按事件序把历史折成对话消息(role user/assistant)。

    应在**当前轮 user_message 写入日志之前**调用,这样历史不含当前问题;
    若已在日志里,可用 current_text 过滤(见 broker 侧)。
    """
    out: list[dict] = []
    for e in store.read(session_id):
        t = e["type"]
        payload = e.get("payload") or {}
        if t == "user_message":
            txt = payload.get("text", "")
            out.append({"role": "user", "content": txt})
        elif t == "assistant_message":
            blocks = payload.get("blocks") or []
            txt = _assistant_content(blocks)
            if txt:
                out.append({"role": "assistant", "content": txt})
    return out
