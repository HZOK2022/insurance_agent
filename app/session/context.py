# -*- coding: utf-8 -*-
"""会话上下文:把事件日志折成"模型可见的对话历史",供多轮上下文。

阶段 A(恢复跨轮上下文):user_message→user,assistant_message→assistant。
- 保留历史回答里的 [idx](全局编号下可跨轮复用,供上下文回答继续引用)。
- 跳过 reasoning(r 块)、narration、retrieval/tool(过程性,不作为历史正文)。
"""
from __future__ import annotations

from typing import Any

from app.compaction.compactor import frame_summary


def _block_text(b: dict) -> str:
    t = b.get("t")
    if t in ("ul", "ol"):
        return "\n".join(str(x) for x in (b.get("items") or []))
    return str(b.get("text", ""))


def _assistant_content(blocks: list) -> str:
    """只取回答块(p/h/ul/ol),跳过 reasoning(r);保留 [idx](跨轮引用)。"""
    parts: list[str] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        if b.get("t") == "r":
            continue
        txt = _block_text(b)
        if txt:
            parts.append(txt)
    return "\n".join(parts).strip()


def build_history(store: Any, session_id: str) -> list[dict]:
    """按事件序把历史折成对话消息(role user/assistant/system)。

    阶段 A:user_message->user、assistant_message->assistant(保留 [idx],跳 reasoning/过程事件)。
    阶段 C 持久化:每当遇到 compaction_summary 事件,就把"被它影子(shadowed_seqs)的
    历史事件"折成一条 system 原文帧包(照 dsh frameSummary),并跳过那些被影子的
    user/assistant 事件(append-only:被替换的原文仍在日志,只是不再进模型可见历史)。

    应在**当前轮 user_message 写入日志之前**调用,这样历史不含当前问题。
    返回的消息带 "seq"(对应事件序号),供压缩回指;调用方(loop)构造模型消息时应剥掉。
    """
    events = store.read(session_id)
    shadowed: set[int] = set()
    for e in events:
        if e["type"] == "compaction_summary":
            for s in (e.get("payload") or {}).get("shadowed_seqs") or []:
                shadowed.add(s)
    out: list[dict] = []
    for e in events:
        t = e["type"]
        payload = e.get("payload") or {}
        seq = e.get("seq")
        if seq is not None and seq in shadowed:
            continue
        if t == "user_message":
            out.append({"role": "user", "content": payload.get("text", ""), "seq": seq})
        elif t == "assistant_message":
            blocks = payload.get("blocks") or []
            txt = _assistant_content(blocks)
            if txt:
                out.append({"role": "assistant", "content": txt, "seq": seq})
        elif t == "compaction_summary":
            summ = payload.get("summary") or ""
            if summ:
                out.append({"role": "system", "content": frame_summary(summ), "seq": seq})
    return out


def build_chunk_registry(store: Any, session_id: str):
    """把该会话所有 retrieval 事件的 chunks 按 chunk_id 去重、按首次出现顺序给全局编号 idx=1..N。

    返回 (pool, idx_map):pool=[{chunk_id, content, ...}, ...](全局顺序);idx_map={chunk_id: 全局idx}。
    用途:跨轮引用 —— 上下文回答(当轮无检索)也能把答案里的 [idx] 解析到历史检索过的 chunk,
    从而仍能给出引用角标(铁律3:答复可追溯)。
    """
    pool = []
    idx_map = {}
    for e in store.read(session_id):
        if e["type"] != "retrieval":
            continue
        for c in (e.get("payload") or {}).get("chunks") or []:
            if not isinstance(c, dict):
                continue
            cid = c.get("chunk_id")
            if cid is None or cid in idx_map:
                continue
            idx_map[cid] = len(pool) + 1
            pool.append(c)
    return pool, idx_map
