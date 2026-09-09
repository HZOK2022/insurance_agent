# -*- coding: utf-8 -*-
"""trace # 直达(①)+ 步级 LLM 计量事件 llm_call(②)的存储层/注册表测试。

覆盖:
- events 注册表:llm_call 类型已注册、payload 校验(必需字段/可选键/缺字段拒绝);
- 一轮 = 一次真实 LLM 调用 → 该轮每步落一条 llm_call(token 增量/ttft/耗时),turn 级 usage 不变;
- store.trace_events:由轮级 trace_id(=turn_start 的 seq)取该轮切片、轮内任意事件 seq 锚到所在轮、
  不存在的 seq 返回 None。
"""
from __future__ import annotations

import os
import tempfile
import types
import unittest

from app.loop.agent_loop import AgentLoop
from app.session import events as evmod
from app.session.events import make_event
from app.session.store import SessionStore


def make_cfg(**kw):
    d = dict(max_steps_per_turn=6, max_retrieve_per_turn=5, deepseek_model="fake")
    d.update(kw)
    return types.SimpleNamespace(**d)


class TwoStepLLM:
    """两步:step1 tool-call(ttft 2100, 10/5 tok),step2 answer(ttft 900, 20/15 tok)。"""
    def __init__(self):
        self.calls = 0

    def chat_stream(self, messages, json_mode=False, tools=None, model=None):
        self.calls += 1
        if self.calls == 1:
            yield {"kind": "reasoning", "delta": "先查", "block_index": 0, "ttft_ms": 2100}
            yield {"kind": "text", "delta": "查中", "block_index": 1}
            yield {"kind": "tool-call", "delta": '{"query":"重疾"}', "block_index": 2, "name": "search_knowledge", "call_id": "c1"}
            yield {"kind": "usage", "delta": "", "block_index": None, "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        else:
            yield {"kind": "text", "delta": "答案", "block_index": 0, "ttft_ms": 900}
            yield {"kind": "usage", "delta": "", "block_index": None, "usage": {"prompt_tokens": 20, "completion_tokens": 15}}


class SingleStepLLM:
    def chat_stream(self, messages, json_mode=False, tools=None, model=None):
        yield {"kind": "text", "delta": "你好", "block_index": 0, "ttft_ms": 300}
        yield {"kind": "usage", "delta": "", "block_index": None, "usage": {"prompt_tokens": 7, "completion_tokens": 3}}


class _LoopBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = SessionStore(os.path.join(self.dir, "t.db"))
        self.sid = self.store.create_session("u1")["id"]

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass

    def make_loop(self, llm, tools=None):
        def emit(t, p):
            ev = make_event(t, p)
            seq = self.store.append(self.sid, t, p)
            ev["seq"] = seq
            return ev
        def handler(args, start_idx=0):
            return {"content": "[1] 条款", "reference": [{"chunk_id": "doc:1", "content": "x", "doc_id": "doc",
                                                          "version": "v1", "section": "s", "source": "src", "score": 0.9}]}
        tools = tools if tools is not None else {"search_knowledge": {
            "schema": {"type": "function", "function": {"name": "search_knowledge",
                                                        "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                                                                       "required": ["query"]}}},
            "handler": handler}}
        present = lambda text, refs: ([{"t": "p", "text": text}], [])
        return AgentLoop(llm, "系统", tools, present, make_cfg(), emit=emit)

    def run_turn(self, llm):
        return list(self.make_loop(llm).turn(self.sid, "你好"))


class LlmCallRegistryTest(unittest.TestCase):
    def test_llm_call_registered_in_known_types(self):
        self.assertIn("llm_call", evmod.known_types())

    def test_llm_call_validator_keeps_optional_and_required(self):
        ev = make_event("llm_call", {"step": 1, "model": "m", "prompt_tokens": 10,
                                     "completion_tokens": 5, "ttft_ms": 100, "run_ms": 800,
                                     "tokens_per_second": 6.2})
        p = ev["payload"]
        self.assertEqual(p["step"], 1)
        self.assertEqual(p["prompt_tokens"], 10)
        self.assertEqual(p["run_ms"], 800)
        # 可选键缺省时不在 payload(与 usage 同款:原样丢弃 None)
        ev2 = make_event("llm_call", {"step": 2, "model": "m", "prompt_tokens": 1, "completion_tokens": 1})
        self.assertNotIn("ttft_ms", ev2["payload"])
        self.assertNotIn("run_ms", ev2["payload"])

    def test_llm_call_missing_required_rejected(self):
        with self.assertRaises(ValueError):
            make_event("llm_call", {"model": "m", "prompt_tokens": 1})


class LlmCallPerStepTest(_LoopBase):
    def test_single_step_emits_one_llm_call(self):
        self.run_turn(SingleStepLLM())
        calls = [r["payload"] for r in self.store.read(self.sid) if r["type"] == "llm_call"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["prompt_tokens"], 7)
        self.assertEqual(calls[0]["completion_tokens"], 3)
        self.assertEqual(calls[0]["model"], "fake")
        self.assertEqual(calls[0]["step"], 1)

    def test_two_steps_each_step_has_own_llm_call_and_usage_totals_unchanged(self):
        evs = self.run_turn(TwoStepLLM())
        calls = [e["payload"] for e in evs if e["type"] == "llm_call"]
        self.assertEqual(len(calls), 2)
        # 每步自己的 token 增量 + 该步首 token 时延(step1 ttft 2100 / step2 ttft 900)
        self.assertEqual([c["step"] for c in calls], [1, 2])
        self.assertEqual([c["prompt_tokens"] for c in calls], [10, 20])
        self.assertEqual([c["completion_tokens"] for c in calls], [5, 15])
        self.assertEqual([c["ttft_ms"] for c in calls], [2100, 900])
        for c in calls:
            self.assertIn("run_ms", c)
            self.assertIsInstance(c["run_ms"], int)
            self.assertGreaterEqual(c["run_ms"], 0)
        # turn 级 usage 仍是累计汇总(成本/token 报表口径不变)
        usage = next(e["payload"] for e in evs if e["type"] == "usage")
        self.assertEqual(usage["prompt_tokens"], 30)
        self.assertEqual(usage["completion_tokens"], 20)
        # 事件序:每步 step_start → llm_call(工具步在 tool_call 之前)→ step_end
        types_ = [e["type"] for e in evs]
        self.assertEqual(types_[-1], "turn_end")
        i1 = types_.index("step_start", 0)
        self.assertIn("llm_call", types_[i1:types_.index("step_end", i1)])


class TraceEventsSliceTest(_LoopBase):
    """store.trace_events:trace # 直达 —— 由轮级 trace_id(或轮内任意事件 seq)取该轮事件切片。"""

    def test_slice_by_second_turn_trace_id_only_contains_that_turn(self):
        # 两轮:第一轮 1 步,第二轮 2 步 → 事件数/seq 不同,可精确验证切片边界
        self.run_turn(SingleStepLLM())
        self.run_turn(TwoStepLLM())
        starts = [e["seq"] for e in self.store.read(self.sid) if e["type"] == "turn_start"]
        self.assertEqual(len(starts), 2)
        # 第二轮 trace_id = 第二个 turn_start 的 seq
        res = self.store.trace_events(starts[1])
        self.assertIsNotNone(res)
        self.assertEqual(res["session_id"], self.sid)
        self.assertEqual(res["trace_id"], starts[1])
        self.assertTrue(res["events"])
        seqs = [e["seq"] for e in res["events"]]
        self.assertEqual(seqs[0], starts[1])                     # 从该轮 turn_start 起
        self.assertTrue(all(s >= starts[1] for s in seqs))       # 不含第一轮事件
        self.assertEqual([e["type"] for e in res["events"]][-1], "turn_end")
        self.assertEqual(len([e for e in res["events"] if e["type"] == "llm_call"]), 2)  # 该轮 2 次调用

    def test_slice_by_any_event_seq_inside_turn_anchors_to_turn(self):
        self.run_turn(SingleStepLLM())
        self.run_turn(TwoStepLLM())
        evs = self.store.read(self.sid)
        starts = [e["seq"] for e in evs if e["type"] == "turn_start"]
        # 第二轮里一条 tool_call(非 turn_start)也能锚到所在轮
        tool = next(e["seq"] for e in evs if e["type"] == "tool_call")
        res = self.store.trace_events(tool)
        self.assertIsNotNone(res)
        self.assertEqual(res["trace_id"], starts[1])
        # 第一轮里一条 assistant_message 锚到第一轮
        am1 = next(e["seq"] for e in evs if e["type"] == "assistant_message")
        res1 = self.store.trace_events(am1)
        self.assertEqual(res1["trace_id"], starts[0])

    def test_unknown_seq_returns_none(self):
        self.run_turn(SingleStepLLM())
        self.assertIsNone(self.store.trace_events(99999999))
        self.assertIsNone(self.store.trace_events(0))


if __name__ == "__main__":
    unittest.main()
