# -*- coding: utf-8 -*-
"""显式"停止"通道:app/loop/abort.py(置位)+ AgentLoop 三个检查点 + POST /api/sessions/{sid}/abort。

为什么要这套测试(以及为什么必须有显式通道):
  前端只 abort() 掉 SSE 是不够的 —— Starlette 的 StreamingResponse 对同步生成器走
  iterate_in_threadpool,实测版本不会在客户端断开时 close() 底层生成器,GeneratorExit
  可能永不触发,后端这一轮会跑完(白烧 token),前端只是"假停"。故停止走显式置位,
  由 loop 在 step 边界 / 每个 chunk 后 / 每个工具前自查。

停止的契约(本文件逐条锁死):
  ① 真的停下:置位后不再产出新的 assistant_chunk;
  ② 仍落库:turn_end(reason=interrupted) 写进 SQLite(事实源);
  ③ 仍推送:客户端没断流,终结事件必须 yield 出去(不能像 GeneratorExit 那样吞掉);
  ④ 不丢回答:本 step 已流出的正文保留为 assistant_message,不退化成"生成失败";
  ⑤ 不自伤:中止回调自己抛异常时,当"没停"处理,回合照常跑完。
"""
from __future__ import annotations

import os
import tempfile
import types
import unittest
from dataclasses import replace

from fastapi.testclient import TestClient

from app import main as appmod
from app.api.services import container
from app.businesses.insurance import present_answer as ins_present
from app.config import Config
from app.loop.abort import begin, clear, is_set, request, reserve
from app.loop.agent_loop import AgentLoop
from app.session.events import make_event
from app.session.store import SessionStore


def make_cfg(ms=6, mr=5):
    return types.SimpleNamespace(max_steps_per_turn=ms, max_retrieve_per_turn=mr, deepseek_model="fake")


class LongStreamLLM:
    """一个 step 内吐 n 个 text chunk(模拟长回答);on_chunk(i) 在第 i 个 chunk 产出前回调(用来置停止位)。"""

    def __init__(self, n: int = 10, on_chunk=None):
        self.n, self.on_chunk = n, on_chunk

    def chat_stream(self, messages, json_mode=False, tools=None, model=None):
        for i in range(self.n):
            if self.on_chunk:
                self.on_chunk(i)
            yield {"kind": "text", "delta": f"字{i}", "block_index": 1}
        yield {"kind": "usage", "delta": "", "block_index": None,
               "usage": {"prompt_tokens": 1, "completion_tokens": self.n}}


class AbortRegistryTest(unittest.TestCase):
    """置位原语生命周期(reserve→begin→request→clear):进程内、易失、按会话隔离。

    竞态保护(2026-09-03 实测发现并修复):
      - reserve 必须在 prompt 路由 handler 同步段执行(SSE 生成器要等客户端首拉才跑,
        abort 若早于生成器内的 begin 到达会置位丢失 → turn 白跑 46s);
      - request 找不到登记 → 无效 abort,返回 False 且不创建 —— 否则残留位会误停下一轮。
    """

    def tearDown(self):
        for s in ("ra", "rb"):
            clear(s)

    def test_request_without_reserve_is_noop_and_no_residue(self):
        """无登记(没有进行中/刚预约的回合)→ 无效 abort:返回 False,且不残留(不误停下一轮)。"""
        clear("ra")
        self.assertFalse(request("ra"))
        reserve("ra")                          # 下一条消息的回合正常预约
        self.assertFalse(is_set("ra"))         # 未被上一步误置位
        clear("ra")

    def test_reserve_then_request_sets_flag(self):
        reserve("ra")
        self.assertTrue(request("ra"))
        self.assertTrue(is_set("ra"))
        clear("ra")

    def test_reserve_clears_stale_flag_for_new_turn(self):
        """同一会话串行回合:新一轮 prompt 的 reserve 清掉旧 turn 遗留/无效的置位。"""
        reserve("ra"); request("ra")           # 旧 turn 的 abort(或无效 abort)
        reserve("ra")                          # 新回合(prompt 串行,旧 turn 已作废)
        self.assertFalse(is_set("ra"))
        clear("ra")

    def test_instant_abort_before_begin_is_respected(self):
        """用户在 LLM 首 token 前就点了停止:begin 尊重已置位,不清 → 首检查点立即停。"""
        reserve("ra"); request("ra")
        begin("ra")
        self.assertTrue(is_set("ra"))
        clear("ra")

    def test_normal_flow_reserve_begin_request(self):
        reserve("ra"); begin("ra")
        self.assertFalse(is_set("ra"))         # 未点停止前是 False
        self.assertTrue(request("ra"))
        self.assertTrue(is_set("ra"))
        clear("ra")

    def test_clear_removes_entry_then_request_is_noop(self):
        reserve("ra"); request("ra"); clear("ra")
        self.assertFalse(is_set("ra"))
        self.assertFalse(request("ra"))        # 回合已结束回收 → 之后的 abort 无效

    def test_sessions_are_isolated(self):
        reserve("ra"); reserve("rb"); request("ra")
        self.assertTrue(is_set("ra"))
        self.assertFalse(is_set("rb"))
        clear("ra"); clear("rb")


class _LoopBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = SessionStore(os.path.join(self.dir, "t.db"))

    def tearDown(self):
        self.store.close()

    def make_loop(self, llm, should_abort=None):
        def emit(t, p):
            ev = make_event(t, p)
            self.store.append("s1", t, p)
            return ev
        tools = {"search_knowledge": {"schema": {"type": "function", "function": {
            "name": "search_knowledge", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
            "handler": lambda a, s=0: {"content": "[1] 条款片段", "reference": None}}}
        return AgentLoop(llm, "SYS", tools, ins_present, make_cfg(), emit=emit, should_abort=should_abort)

    @staticmethod
    def answer_text(evs) -> str:
        am = next((e for e in evs if e["type"] == "assistant_message"), None)
        if not am:
            return ""
        return "".join(b.get("text", "") for b in (am["payload"].get("blocks") or []))


class LoopAbortTest(_LoopBase):
    def test_abort_mid_stream_stops_and_still_yields_turn_end(self):
        """契约 ①②③④:停得下、落库、推给客户端、已流出的正文不丢。"""
        flag = {"on": False}
        llm = LongStreamLLM(n=10, on_chunk=lambda i: flag.__setitem__("on", True) if i == 2 else None)
        evs = list(self.make_loop(llm, should_abort=lambda: flag["on"]).turn("s1", "问题"))

        chunks = [e for e in evs if e["type"] == "assistant_chunk"]
        self.assertEqual(len(chunks), 3)                       # ① 置位后立刻停(第 3 个 chunk 之后)
        self.assertIn("turn_end", [e["type"] for e in evs])    # ③ 终结事件推给客户端(客户端没断流)
        te = next(e for e in evs if e["type"] == "turn_end")
        self.assertEqual(te["payload"]["reason"], "interrupted")
        # ② 落库一致(SQLite 是事实源)
        stored = [r for r in self.store.read("s1") if r["type"] == "turn_end"]
        self.assertEqual(stored[-1]["payload"]["reason"], "interrupted")
        # ④ 已流出的部分回答保留,未产出的不出现
        text = self.answer_text(evs)
        self.assertIn("字0", text)
        self.assertIn("字2", text)
        self.assertNotIn("字9", text)

    def test_abort_before_first_step_emits_stop_note(self):
        """回合刚起步就停:不应有 step_start,给一条明确的停止说明而不是"生成失败"。"""
        evs = list(self.make_loop(LongStreamLLM(n=3), should_abort=lambda: True).turn("s1", "问题"))
        self.assertNotIn("step_start", [e["type"] for e in evs])
        te = next(e for e in evs if e["type"] == "turn_end")
        self.assertEqual(te["payload"]["reason"], "interrupted")
        self.assertIn("已手动停止", self.answer_text(evs))

    def test_abort_does_not_change_normal_completion(self):
        """回归:没置位 → 与改动前完全一致(reason=completed,回答完整)。"""
        evs = list(self.make_loop(LongStreamLLM(n=4), should_abort=lambda: False).turn("s1", "问题"))
        te = next(e for e in evs if e["type"] == "turn_end")
        self.assertEqual(te["payload"]["reason"], "completed")
        self.assertIn("字3", self.answer_text(evs))

    def test_no_abort_callback_behaves_as_before(self):
        """回归:不传 should_abort(旧调用方/既有测试)→ 行为不变。"""
        evs = list(self.make_loop(LongStreamLLM(n=2)).turn("s1", "问题"))
        self.assertEqual(next(e for e in evs if e["type"] == "turn_end")["payload"]["reason"], "completed")

    def test_abort_callback_error_is_ignored(self):
        """契约 ⑤:中止回调自己炸了,当"没停"处理 —— 停止通道不能反过来搞崩回合。"""
        def boom():
            raise RuntimeError("abort registry exploded")
        evs = list(self.make_loop(LongStreamLLM(n=2), should_abort=boom).turn("s1", "问题"))
        self.assertEqual(next(e for e in evs if e["type"] == "turn_end")["payload"]["reason"], "completed")


class AbortRouteTest(unittest.TestCase):
    """POST /api/sessions/{sid}/abort:接口可用、置位生效、无进行中回合时无害。"""

    def setUp(self):
        self.orig_cfg = container.get_cfg
        container.get_cfg = lambda: replace(Config(), api_token="")   # 关鉴权,单测只测路由本身
        self.client = TestClient(appmod.app)
        appmod._rate._hits.clear()

    def tearDown(self):
        container.get_cfg = self.orig_cfg
        clear("sx"); clear("sy")

    def test_abort_route_sets_flag(self):
        reserve("sx")                          # prompt 路由会在 StreamingResponse 前 reserve
        r = self.client.post("/api/sessions/sx/abort")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        self.assertTrue(is_set("sx"))

    def test_abort_route_without_active_turn_is_harmless(self):
        clear("sy")
        r = self.client.post("/api/sessions/sy/abort")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["ok"])
        self.assertFalse(is_set("sy"))


if __name__ == "__main__":
    unittest.main()
