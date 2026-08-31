# -*- coding: utf-8 -*-
"""阶段5(写审批)测试:写工具触发 approval_request → 人工 approval_decision(批准可带改后参数 / 拒绝) → 执行或不执行。"""
from __future__ import annotations

import types
import unittest

from app.loop.agent_loop import AgentLoop
from app.guardrails.approval import ApprovalCenter
from app.session.events import make_event


def make_cfg(**kw):
    d = dict(max_steps_per_turn=6, max_retrieve_per_turn=5, deepseek_model="fake",
             write_tools_approval="manual")
    d.update(kw)
    return types.SimpleNamespace(**d)


class WriteLLM:
    """step1: 调用写工具 send_msg;step2: 回答。"""
    def __init__(self): self.calls = 0
    def chat_stream(self, messages, json_mode=False, tools=None, model=None):
        self.calls += 1
        if self.calls == 1:
            yield {"kind": "text", "delta": "我要发送", "block_index": 0, "ttft_ms": 10}
            yield {"kind": "tool-call", "delta": '{"to":"cust","msg":"hi"}', "block_index": 1, "name": "send_msg", "call_id": "c1"}
        else:
            yield {"kind": "text", "delta": "已处理", "block_index": 0, "ttft_ms": 10}
        yield {"kind": "usage", "delta": "", "block_index": None, "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


def make_loop(center, handler_calls, write_tool=True):
    def handler(args, start_idx=0):
        handler_calls.append(args)
        return {"content": f"已发送到 {args.get('to')}", "reference": None}
    tools = {"send_msg": {"schema": {"type": "function", "function": {"name": "send_msg",
              "parameters": {"type": "object", "properties": {"to": {"type": "string"}, "msg": {"type": "string"}}, "required": ["to"]}}},
              "handler": handler, "write": write_tool}}
    def emit(t, p): return make_event(t, p)
    return AgentLoop(WriteLLM(), "系统", tools, (lambda t, r: ([{"t": "p", "text": t}], [])),
                     make_cfg(), emit=emit, approval=center)


class ApprovalGateTest(unittest.TestCase):
    def test_approve_with_edited_args_executes(self):
        center = ApprovalCenter()
        calls = []
        gen = make_loop(center, calls).turn("s1", "给客户发消息")
        evs = []
        for ev in gen:
            evs.append(ev)
            if ev["type"] == "approval_request":
                rid = ev["payload"]["request_id"]
                self.assertEqual(ev["payload"]["tool"], "send_msg")
                # 改参数后批准
                self.assertTrue(center.decide(rid, "approve", edited_args={"to": "改后客户", "msg": "改后内容"}))
        # 工具被执行,且用的是"改后参数"(approval_decision 事件由审批 API 端点发出,不在本循环事件里)
        self.assertEqual(calls, [{"to": "改后客户", "msg": "改后内容"}])
        self.assertEqual([e["type"] for e in evs][-1], "turn_end")

    def test_reject_does_not_execute(self):
        center = ApprovalCenter()
        calls = []
        gen = make_loop(center, calls).turn("s1", "给客户发消息")
        evs = []
        for ev in gen:
            evs.append(ev)
            if ev["type"] == "approval_request":
                self.assertTrue(center.decide(ev["payload"]["request_id"], "reject", reason="不必要"))
        self.assertEqual(calls, [])                       # 拒绝 → 不执行
        # 拒绝消息进入 conversation(role=tool),agent 据此继续;turn 正常结束
        self.assertEqual([e["type"] for e in evs][-1], "turn_end")
        self.assertIn("tool_result", [e["type"] for e in evs])

    def test_read_tool_not_gated(self):
        center = ApprovalCenter()
        calls = []
        gen = make_loop(center, calls, write_tool=False).turn("s1", "算一下")
        evs = list(gen)
        self.assertEqual([e["type"] for e in evs].count("approval_request"), 0)   # 读工具不触发


if __name__ == "__main__":
    unittest.main()
