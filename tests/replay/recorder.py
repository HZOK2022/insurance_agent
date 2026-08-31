# -*- coding: utf-8 -*-
"""回放录制器:包装一个 LLM,把每次 chat_stream 的 (messages, response) 记录成 JSONL。

供"改 prompt/条款/工具 schema"时无 key 回归:先录制一次真实/假 LLM 的运行,
再在改动后用 ReplayLLM 重放同样的请求、返回同样的响应,对拍 agent 输出是否一致。
"""
from __future__ import annotations

import json
import os
from typing import Iterable


class Recorder:
    """包装 llm,记录每次请求 (messages) 与其流式响应 (response chunk 列表)。"""

    def __init__(self, llm):
        self.llm = llm
        self.records: list[dict] = []

    def chat_stream(self, messages, json_mode=False, tools=None, model=None):
        resp = list(self.llm.chat_stream(messages, json_mode=json_mode, tools=tools, model=model))
        self.records.append({"messages": messages, "response": resp})
        for c in resp:
            yield c

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            for r in self.records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def load(self, path: str) -> None:
        with open(path, encoding="utf-8") as f:
            self.records = [json.loads(line) for line in f if line.strip()]
