# -*- coding: utf-8 -*-
"""工具失败(对齐 dsh):抛异常/未知工具 → 一等错误结果(ok=False + error_code + 脱敏 content),loop 继续,不泄漏异常。"""
from __future__ import annotations

import types
import unittest

from app.loop.agent_loop import AgentLoop
from app.session.events import make_event


def _cfg(**kw):
    d = dict(max_steps_per_turn=6, max_retrieve_per_turn=5, deepseek_model="fake", write_tools_approval="auto")
    d.update(kw)
    return types.SimpleNamespace(**d)


class CallLLM:
    """step1: 调指定工具;step2: 回答。name 由构造指定。"""
    def __init__(self, name):
        self.calls = 0
        self.name = name

    def chat_stream(self, messages, json_mode=False, tools=None, model=None):
        self.calls += 1
        if self.calls == 1:
            yield {"kind": "text", "delta": "我要", "block_index": 0, "ttft_ms": 10}
            yield {"kind": "tool-call", "delta": '{"query":"x"}', "block_index": 1, "name": self.name, "call_id": "c1"}
        else:
            yield {"kind": "text", "delta": "基于已有资料回答", "block_index": 0, "ttft_ms": 10}
        yield {"kind": "usage", "delta": "", "block_index": None, "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


def _loop(llm, handler, name="boom"):
    tools = {name: {"schema": {"type": "function", "function": {"name": name, "parameters": {"type": "object", "properties": {}}}},
                    "handler": handler}}
    return AgentLoop(llm, "系统", tools, (lambda t, r: ([{"t": "p", "text": t}], [])),
                     _cfg(), emit=lambda t, p: make_event(t, p))


class ToolErrorTest(unittest.TestCase):
    def test_throwing_tool_returns_iserror_and_sanitized(self):
        def handler(args, start_idx=0):
            raise RuntimeError("boom secret stack detail")
        gen = _loop(CallLLM("boom"), handler).turn("s1", "查一下")
        evs = [ev for ev in gen]
        tr = [e for e in evs if e["type"] == "tool_result"][0]
        self.assertFalse(tr["payload"]["ok"])
        self.assertEqual(tr["payload"]["error"], "tool_error")
        # 喂给模型的 content 是脱敏文案,不泄漏异常细节
        self.assertNotIn("boom", tr["payload"].get("error_context", "") or "")
        # turn 正常结束(loop 继续,模型基于错误结果回答)
        self.assertEqual(evs[-1]["type"], "turn_end")
        self.assertIn("assistant_message", [e["type"] for e in evs])

    def test_unknown_tool_returns_unknown(self):
        # 工具不在注册表 -> loop 不执行任何 handler,直接 unknown_tool
        loop = AgentLoop(CallLLM("nope"), "系统", {}, (lambda t, r: ([{"t": "p", "text": t}], [])),
                         _cfg(), emit=lambda t, p: make_event(t, p))
        tr = [e for e in loop.turn("s1", "查") if e["type"] == "tool_result"][0]
        self.assertFalse(tr["payload"]["ok"])
        self.assertEqual(tr["payload"]["error"], "unknown_tool")

    def test_sanitized_content_does_not_leak_exception(self):
        def handler(args, start_idx=0):
            raise RuntimeError("SECRET_DETAIL_12345")
        gen = _loop(CallLLM("boom"), handler).turn("s1", "查")
        # 检查喂给模型的 tool 消息 content 是否泄漏异常串
        # 通过 assistant 的第二步请求可见(这里用事件 + 一次独立调用验证 content 不含泄露)
        tr = [e for e in gen if e["type"] == "tool_result"][0]
        # tool_result 事件本身只存 payload(ok/error/截断),不含 content;泄漏与否看 conversation 回喂。
        # 用 CallLLM 捕获 messages 校验:
        captured = {}
        class CapLLM(CallLLM):
            def chat_stream(self, messages, **kw):
                captured["msgs"] = messages
                return super().chat_stream(messages, **kw)
        gen2 = _loop(CapLLM("boom"), handler).turn("s1", "查")
        for _ in gen2:
            pass
        tool_msgs = [m for m in captured.get("msgs", []) if m.get("role") == "tool"]
        self.assertTrue(tool_msgs)
        self.assertNotIn("SECRET_DETAIL_12345", tool_msgs[-1]["content"])
        self.assertIn("调用失败", tool_msgs[-1]["content"])


if __name__ == "__main__":
    unittest.main()
