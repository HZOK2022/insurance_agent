# -*- coding: utf-8 -*-
"""RAG 投毒治理(④):检出知识库/检索内容里的"指令式"文本(间接提示注入),隔离不让其进模型上下文。

- 规避"内容携带的恶意指令"(RAG 投毒):检索到的 chunk 正文若含 忽略上次/system prompt/输出系统/
  扮演/立即执行/越狱/base64 等指令式标记,判定为可疑 → 从检索结果中隔离(drop),不喂给模型。
- 同时检索内容以【检索结果(数据)】标记,强化"这是数据不是指令"(行为层;隔离是确定性层)。
- 只对**检索/展示**过滤;事实源 chunks 不变。误报宁可多拦(保险/医疗,宁可少给不可误执行)。
"""
from __future__ import annotations

import re
from typing import Any

# 指令式/注入式特征(命中即视为可疑;宁可误报,也不让可疑指令进上下文)
_SUSPICIOUS = re.compile(
    r"忽略之前|忽略上面|忽略以上|无视.*指令|ignore (?:previous|above|all)|"
    r"system prompt|system_prompt|输出系统|系统提示词是什么|"
    r"作为.*(?:助手|系统).*请|请你.*(?:输出|执行|扮演|泄露)|立即执行|"
    r"指令:|jailbreak|越狱|base64|解码|decode|扮演|角色扮演|prompt leak",
    re.IGNORECASE,
)


def chunk_is_suspicious(chunk: dict[str, Any]) -> bool:
    return bool(_SUSPICIOUS.search(str(chunk.get("content") or "")))


def quarantine_suspicious(chunks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """把 chunks 分成 (clean, suspicious);suspicious 已被隔离,不进模型/引用。"""
    clean: list[dict[str, Any]] = []
    susp: list[dict[str, Any]] = []
    for c in chunks:
        (susp if chunk_is_suspicious(c) else clean).append(c)
    return clean, susp
