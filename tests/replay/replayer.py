# -*- coding: utf-8 -*-
"""回放器:按序返回录制好的响应(ReplayLLM),并在请求不一致时报错(保证对拍的是同一请求)。

用法:ReplayLLM(records) 作为 llm 传给 AgentLoop,逐次 chat_stream 返回记录的响应;
若某次请求的 messages 与录制不一致,说明 prompt/上下文变了,回放对拍即失败(有意为之)。
"""
from __future__ import annotations


class ReplayLLM:
    def __init__(self, records):
        self.records = list(records)
        self.i = 0
        self.calls: list[list] = []

    def chat_stream(self, messages, json_mode=False, tools=None, model=None):
        if self.i >= len(self.records):
            raise AssertionError(f"回放耗尽:第 {self.i + 1} 次调用无记录")
        rec = self.records[self.i]
        self.i += 1
        self.calls.append(messages)
        if messages != rec.get("messages"):
            raise AssertionError(
                f"回放请求不一致(第 {self.i} 次):prompt/上下文变了?\n"
                f"  期望(录制):{str(rec.get('messages'))[:200]}\n"
                f"  实际(本次):{str(messages)[:200]}")
        for chunk in rec.get("response", []):
            yield chunk
