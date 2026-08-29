# -*- coding: utf-8 -*-
"""DeepSeek LLM client (chat completions via requests, JSON output).

Block protocol (照 dsh llm-deepseek/translate.ts):
  chat_stream yields {"kind": "text"|"reasoning"|"tool-call", "delta": str,
                       "block_index": int, "ttft_ms": int|None, "name": str|None}
  A block-start is implied by first delta of a given kind (front-end or loop
  assembler opens the block then).
"""
import json
import time

import requests


class StreamChunk:
    __slots__ = ("kind", "delta", "block_index", "ttft_ms", "name", "call_id")
    def __init__(self, kind, delta, block_index, ttft_ms=None, name=None, call_id=None):
        self.kind = kind            # 'text' | 'reasoning' | 'tool-call'
        self.delta = delta          # str: text fragment / reasoning fragment / tool args fragment
        self.block_index = block_index
        self.ttft_ms = ttft_ms
        self.name = name            # tool-call only
        self.call_id = call_id      # tool-call only
    def to_dict(self):
        d = {"kind": self.kind, "delta": self.delta, "block_index": self.block_index}
        if self.ttft_ms is not None: d["ttft_ms"] = self.ttft_ms
        if self.name is not None: d["name"] = self.name
        if self.call_id is not None: d["call_id"] = self.call_id
        return d


class LLMClient:
    def __init__(self, api_key, base_url="https://api.deepseek.com", model="deepseek-v4-flash",
                 temperature=0.7, max_tokens=32768, timeout=120):
        self.api_key=api_key; self.base_url=base_url.rstrip("/"); self.model=model
        self.temperature=temperature; self.max_tokens=max_tokens; self.timeout=timeout

    def chat(self, messages, json_mode=True):
        body={"model":self.model,"messages":messages,"temperature":self.temperature,"max_tokens":self.max_tokens}
        if json_mode: body["response_format"]={"type":"json_object"}
        r=requests.post(self.base_url+"/chat/completions",
                        headers={"Authorization":"Bearer "+self.api_key}, json=body, timeout=self.timeout)
        r.raise_for_status(); d=r.json()
        return d["choices"][0]["message"]["content"], d.get("usage", {})

    def chat_stream(self, messages, json_mode=False):
        """流式调用,归一化为 StreamChunk(text/reasoning/tool-call)。

        照 dsh llm-deepseek/translate.ts:逐 chunk 读 delta.reasoning_content / delta.content /
        delta.tool_calls,为每种 open 一个 block(index 递增),yield 对应 delta。
        """
        body={"model":self.model,"messages":messages,"temperature":self.temperature,"max_tokens":self.max_tokens,"stream":True}
        if json_mode: body["response_format"]={"type":"json_object"}
        started=time.time(); first=True
        next_index=0
        # block accumulators keyed by kind (reasoning/text) and tool index
        reasoning_block=None; text_block=None; tool_blocks={}
        t0=time.time()
        try:
            with requests.post(self.base_url+"/chat/completions",
                               headers={"Authorization":"Bearer "+self.api_key,"Accept":"text/event-stream"},
                               json=body, timeout=self.timeout, stream=True) as r:
                r.raise_for_status()
                for raw in r.iter_lines(decode_unicode=True):
                    if not raw or not raw.startswith("data:"): continue
                    data=raw[5:].strip()
                    if data=="[DONE]": break
                    try: obj=json.loads(data)
                    except Exception: continue
                    for choice in obj.get("choices") or []:
                        delta=choice.get("delta") or {}
                        # reasoning
                        reason=delta.get("reasoning_content")
                        if isinstance(reason,str) and reason:
                            if reasoning_block is None:
                                reasoning_block=next_index; next_index+=1
                            if first:
                                ttft=int((time.time()-started)*1000); first=False
                            else: ttft=None
                            yield StreamChunk("reasoning", reason, reasoning_block, ttft_ms=ttft).to_dict()
                        # text
                        content=delta.get("content")
                        if isinstance(content,str) and content:
                            if text_block is None:
                                text_block=next_index; next_index+=1
                            if first:
                                ttft=int((time.time()-started)*1000); first=False
                            else: ttft=None
                            yield StreamChunk("text", content, text_block, ttft_ms=ttft).to_dict()
                        # tool calls
                        for call in delta.get("tool_calls") or []:
                            idx=call.get("index", 0)
                            if idx not in tool_blocks:
                                tool_blocks[idx]=next_index; next_index+=1
                            frag=(call.get("function") or {}).get("arguments") or ""
                            if first:
                                ttft=int((time.time()-started)*1000); first=False
                            else: ttft=None
                            yield StreamChunk("tool-call", frag, tool_blocks[idx],
                                              ttft_ms=ttft, name=(call.get("function") or {}).get("name"),
                                              call_id=call.get("id")).to_dict()
        except requests.RequestException as e:
            raise RuntimeError(f"LLM stream failed: {e}") from e
