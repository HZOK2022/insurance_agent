# -*- coding: utf-8 -*-
"""DeepSeek LLM client (chat completions via requests, JSON output).

Block protocol (照 dsh llm-deepseek/translate.ts):
  chat_stream yields {"kind": "text"|"reasoning"|"tool-call"|"usage", "delta": str,
                       "block_index": int, "ttft_ms": int|None, "name": str|None,
                       "usage": dict|None}
  A block-start is implied by first delta of a given kind (front-end or loop
  assembler opens the block then). kind=="usage" 携带流式结束时的真实 token
  统计(请求带 stream_options.include_usage),delta 为空。

阶段E(重试/退避):对 429 / 5xx / 网络超时·断连这些**瞬时错误**做**有上限的指数退避重试**;
永久错误(如 400 参数错、401 鉴权)不重试直接抛。每次重试记 llm_retry 事件(on_retry 回调)。
"""
import json
import random
import time

import requests


class StreamChunk:
    __slots__ = ("kind", "delta", "block_index", "ttft_ms", "name", "call_id", "usage")
    def __init__(self, kind, delta, block_index, ttft_ms=None, name=None, call_id=None, usage=None):
        self.kind = kind            # 'text' | 'reasoning' | 'tool-call' | 'usage'
        self.delta = delta          # str: text fragment / reasoning fragment / tool args fragment
        self.block_index = block_index
        self.ttft_ms = ttft_ms
        self.name = name            # tool-call only
        self.call_id = call_id      # tool-call only
        self.usage = usage          # usage only: API 返回的 {prompt_tokens, completion_tokens, ...}
    def to_dict(self):
        d = {"kind": self.kind, "delta": self.delta, "block_index": self.block_index}
        if self.ttft_ms is not None: d["ttft_ms"] = self.ttft_ms
        if self.name is not None: d["name"] = self.name
        if self.call_id is not None: d["call_id"] = self.call_id
        if self.usage is not None: d["usage"] = self.usage
        return d


def _is_retryable(status: int) -> bool:
    """瞬时错误才重试:429 限流、5xx 服务端。永久错误(4xx 非 429)不重试。"""
    return status == 429 or status >= 500


class LLMClient:
    def __init__(self, api_key, base_url="https://api.deepseek.com", model="deepseek-v4-flash",
                 temperature=0.7, max_tokens=32768, timeout=120,
                 max_retries=3, retry_base_delay=0.5, retry_max_delay=8.0):
        self.api_key = api_key; self.base_url = base_url.rstrip("/"); self.model = model
        self.temperature = temperature; self.max_tokens = max_tokens; self.timeout = timeout
        self.max_retries = max_retries          # 额外重试次数(首次之外)
        self.retry_base_delay = retry_base_delay
        self.retry_max_delay = retry_max_delay

    def _retry_sleep(self, retry_no: int) -> None:
        delay = min(self.retry_base_delay * (2 ** (retry_no - 1)) + random.uniform(0, 0.2), self.retry_max_delay)
        time.sleep(delay)

    def _maybe_retry(self, err, retry_no: int, on_retry) -> bool:
        """重试第 retry_no 次(retry_no 从 1 起):一次退避 + 通知 on_retry。返回 True=应重试,False=放弃抛错。"""
        if retry_no <= self.max_retries:
            self._retry_sleep(retry_no)
            if on_retry:
                on_retry({"attempt": retry_no, "err": str(err)})
            return True
        return False

    def chat(self, messages, json_mode=True, tools=None, model=None, on_retry=None):
        body = {"model": model or self.model, "messages": messages,
                "temperature": self.temperature, "max_tokens": self.max_tokens}
        if json_mode: body["response_format"] = {"type": "json_object"}
        if tools: body["tools"] = tools
        retry_no = 0
        while True:
            try:
                r = requests.post(self.base_url + "/chat/completions",
                                  headers={"Authorization": "Bearer " + self.api_key},
                                  json=body, timeout=self.timeout)
            except requests.RequestException as e:
                retry_no += 1
                if self._maybe_retry(e, retry_no, on_retry): continue
                raise RuntimeError(f"LLM API request failed: {e}") from e
            if r.status_code != 200:
                err = f"LLM API error {r.status_code}: {r.text[:500]}"
                if _is_retryable(r.status_code):
                    retry_no += 1
                    if self._maybe_retry(err, retry_no, on_retry): continue
                raise RuntimeError(err)
            d = r.json()
            return d["choices"][0]["message"]["content"], d.get("usage", {})

    def _open_stream(self, body, on_retry=None):
        """建立流式连接并校验状态;对瞬时错误重试。返回状态 200 的响应对象(调用方用 with 消费)。"""
        retry_no = 0
        while True:
            try:
                r = requests.post(self.base_url + "/chat/completions",
                                  headers={"Authorization": "Bearer " + self.api_key, "Accept": "text/event-stream"},
                                  json=body, timeout=self.timeout, stream=True)
                if r.status_code == 200:
                    return r
                err = f"LLM API error {r.status_code}: {r.text[:500]}"
                r.close()
                if _is_retryable(r.status_code):
                    retry_no += 1
                    if self._maybe_retry(err, retry_no, on_retry): continue
                raise RuntimeError(err)
            except requests.RequestException as e:
                retry_no += 1
                if self._maybe_retry(e, retry_no, on_retry): continue
                raise RuntimeError(f"LLM stream connect failed: {e}") from e

    def chat_stream(self, messages, json_mode=False, tools=None, model=None, on_retry=None):
        """流式调用,归一化为 StreamChunk(text/reasoning/tool-call/usage)。

        照 dsh llm-deepseek/serialize.ts + translate.ts:
        - 带 tools 时走原生 tool_calls,并加 thinking:{type:'enabled'}(推理模型工具调用需 thinking 模式),
          temperature 在推理/工具模式下不发(部分部署拒绝);tool_choice 不传(V4 拒绝某些取值)。
        - 逐 chunk 读 delta.reasoning_content / delta.content / delta.tool_calls,为每种 open 一个 block。
        include_usage 让 API 在收尾 chunk 返回真实 token 数,以 kind=="usage" 收尾。
        - 阶段E:建立连接/校验状态对 429/5xx/超时重试;流中途断连(已产出部分 chunk)不重试。
        """
        body = {"model": model or self.model, "messages": messages, "stream": True,
                "stream_options": {"include_usage": True}}
        if json_mode: body["response_format"] = {"type": "json_object"}
        if tools:
            body["tools"] = tools
            body["thinking"] = {"type": "enabled"}   # 照 dsh serialize.ts:推理模式显式开启
        else:
            if self.temperature is not None: body["temperature"] = self.temperature
        if self.max_tokens is not None: body["max_tokens"] = self.max_tokens
        started = time.time(); first = True
        next_index = 0
        reasoning_block = None; text_block = None; tool_blocks = {}
        stream_usage = None
        r = self._open_stream(body, on_retry)
        try:
            with r:
                for raw in r.iter_lines(decode_unicode=True):
                    if not raw or not raw.startswith("data:"): continue
                    data = raw[5:].strip()
                    if data == "[DONE]": break
                    try: obj = json.loads(data)
                    except Exception: continue
                    if obj.get("usage"):
                        stream_usage = obj["usage"]
                    for choice in obj.get("choices") or []:
                        delta = choice.get("delta") or {}
                        reason = delta.get("reasoning_content")
                        if isinstance(reason, str) and reason:
                            if reasoning_block is None:
                                reasoning_block = next_index; next_index += 1
                            if first: ttft = int((time.time() - started) * 1000); first = False
                            else: ttft = None
                            yield StreamChunk("reasoning", reason, reasoning_block, ttft_ms=ttft).to_dict()
                        content = delta.get("content")
                        if isinstance(content, str) and content:
                            if text_block is None:
                                text_block = next_index; next_index += 1
                            if first: ttft = int((time.time() - started) * 1000); first = False
                            else: ttft = None
                            yield StreamChunk("text", content, text_block, ttft_ms=ttft).to_dict()
                        for call in delta.get("tool_calls") or []:
                            idx = call.get("index", 0)
                            if idx not in tool_blocks:
                                tool_blocks[idx] = next_index; next_index += 1
                            frag = (call.get("function") or {}).get("arguments") or ""
                            if first: ttft = int((time.time() - started) * 1000); first = False
                            else: ttft = None
                            yield StreamChunk("tool-call", frag, tool_blocks[idx],
                                              ttft_ms=ttft, name=(call.get("function") or {}).get("name"),
                                              call_id=call.get("id")).to_dict()
            if stream_usage is not None:
                yield StreamChunk("usage", "", None, usage=stream_usage).to_dict()
        except requests.RequestException as e:
            # 流中途断连:已产出部分 chunk,不做重试(重试会重复/丢失已有内容),直接报错。
            raise RuntimeError(f"LLM stream failed mid-way: {e}") from e
