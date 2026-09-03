# -*- coding: utf-8 -*-
"""会话上下文:把事件日志折成"模型可见的对话历史",供多轮上下文。

阶段 A(恢复跨轮上下文):user_message→user,assistant_message→assistant。
- **剥离历史回答里的 [idx] 角标**(D55):引用编号每轮 turn-local、从 1 起,历史编号
  空间与本轮冲突;若保留,模型会在复述时复用旧编号 → 张冠李戴(D43 已实测)。需要回指
  早前内容时,引导模型走 session_history_search 回源原文(按文本内容,不依赖编号)。
- 跳过 reasoning(r 块)、narration、retrieval/tool(过程性,不作为历史正文)。
"""
from __future__ import annotations

import re
from typing import Any

from app.compaction.compactor import frame_summary

_IDX_RE = re.compile(r"\[\d+\]")   # 引用角标 [n]:每轮局部编号,跨轮无意义 → 喂给模型时剥掉


def _block_text(b: dict) -> str:
    t = b.get("t")
    if t in ("ul", "ol"):
        return "\n".join(str(x) for x in (b.get("items") or []))
    return str(b.get("text", ""))


def _assistant_content(blocks: list) -> str:
    """只取回答块(p/h/ul/ol),跳过 reasoning(r);剥掉 [idx] 角标(防跨轮编号混淆,见模块 docstring)。"""
    parts: list[str] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        if b.get("t") == "r":
            continue
        txt = _block_text(b)
        if txt:
            parts.append(txt)
    return _IDX_RE.sub("", "\n".join(parts)).strip()


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
