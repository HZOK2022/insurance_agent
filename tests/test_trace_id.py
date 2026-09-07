# -*- coding: utf-8 -*-
"""M0:轮级 trace_id(= 每轮 turn_start 事件的全局 seq)。

对齐共识命名:session_id = 窗口(分组);trace_id = 一次执行/一轮 = 该轮 turn_start 的 seq。

覆盖:
- agent_service 风格的 emit 把落库 seq 挂回事件(SSE/live 轨迹可用);
- 同一会话两轮:usage 事件的 trace_id 与各自 turn_start 的 seq 一一对应(唯一、可区分);
- 坏轮(错误)→ badcase_snapshot 携带该轮 trace_id;
- history_qa 每项带 trace_id,且"turn_start 先于 user_message"(真实)与"user_message 先于 turn_start"(旧数据)两种顺序都锚到正确轮;
- events 校验器:usage 可选 trace_id;badcase 透传 trace_id。
"""
from __future__ import annotations

import os
import tempfile
import types
import unittest

from app.audit.queries import history_qa
from app.loop.agent_loop import AgentLoop
from app.session.events import make_event
from app.session.store import SessionStore


def _cfg(**kw):
    d = dict(max_steps_per_turn=6, max_retrieve_per_turn=5, deepseek_model="fake",
             badcase_snapshot_enabled=True, badcase_snapshot_sample_rate=0.0)
    d.update(kw)
    return types.SimpleNamespace(**d)


class AnswerLLM:
    """单步直接回答。"""
    def chat_stream(self, messages, json_mode=False, tools=None, model=None):
        yield {"kind": "text", "delta": "答案", "block_index": 0}
        yield {"kind": "usage", "delta": "", "block_index": None, "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


class ErrLLM:
    """流完一段后抛错 → turn_end=error → 落 badcase_snapshot。"""
    def chat_stream(self, messages, json_mode=False, tools=None, model=None):
        yield {"kind": "text", "delta": "部分", "block_index": 0}
        raise RuntimeError("boom")


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = SessionStore(os.path.join(self.dir, "t.db"))
        self.sid = self.store.create_session("u1")["id"]

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass

    def make_emit(self, sid):
        # 与 agent_service.run_prompt 的 emit 同口径:落库后把全局 seq 挂回事件
        def emit(type_, payload):
            ev = make_event(type_, payload)
            seq = self.store.append(sid, type_, payload)
            ev["seq"] = seq
            return ev
        return emit

    def make_loop(self, llm, sid):
        emit = self.make_emit(sid)
        present = lambda text, refs: ([{"t": "p", "text": text}], [])
        return AgentLoop(llm, "系统", {}, present, _cfg(), emit=emit)


class TraceIdEventsTest(_Base):
    def test_two_turns_have_distinct_usage_trace_id_matching_turn_start_seq(self):
        loop = self.make_loop(AnswerLLM(), self.sid)
        list(loop.turn(self.sid, "第一问"))
        list(loop.turn(self.sid, "第二问"))
        evs = self.store.read(self.sid)
        turn_starts = [e for e in evs if e["type"] == "turn_start"]
        usages = [e for e in evs if e["type"] == "usage"]
        self.assertEqual(len(turn_starts), 2)
        self.assertEqual(len(usages), 2)
        # usage.trace_id 与该轮 turn_start 的 seq 一一对应,且两轮不同
        used = [u["payload"].get("trace_id") for u in usages]
        self.assertEqual(len(set(used)), 2, f"两轮 usage trace_id 应不同: {used}")
        self.assertIsInstance(used[0], int)
        self.assertEqual(set(used), {ts["seq"] for ts in turn_starts})
        # emit 事件本身带 seq(SSE 可用)
        self.assertTrue(all("seq" in e for e in evs))

    def test_bad_turn_badcase_carries_trace_id(self):
        loop = self.make_loop(ErrLLM(), self.sid)
        list(loop.turn(self.sid, "会炸的问题"))
        evs = self.store.read(self.sid)
        ts = next(e for e in evs if e["type"] == "turn_start")
        bad = [e for e in evs if e["type"] == "badcase_snapshot"]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["payload"].get("trace_id"), ts["seq"])
        te = next(e for e in evs if e["type"] == "turn_end")
        self.assertEqual(te["payload"]["reason"], "error")


class HistoryQaTraceIdTest(_Base):
    def _two_turns(self, turn_first: bool):
        """造两轮。turn_first=True=真实顺序(turn_start 先于 user_message);
        False=旧/测试数据顺序(user_message 先于 turn_start)。"""
        def push(question, reason):
            if turn_first:
                self.store.append(self.sid, "turn_start", {"turn": 1})
            self.store.append(self.sid, "user_message", {"text": question})
            if not turn_first:
                self.store.append(self.sid, "turn_start", {"turn": 1})
            self.store.append(self.sid, "turn_end", {"turn": 1, "reason": reason})
        push("问题A", "completed")
        push("问题B", "completed")

    def test_trace_id_distinct_and_matches_turn_start_seq_real_order(self):
        self._two_turns(turn_first=True)
        items = history_qa(self.store, session_id=self.sid)
        self.assertEqual([it["question"] for it in items], ["问题A", "问题B"])
        seqs = [e["seq"] for e in self.store.read(self.sid) if e["type"] == "turn_start"]
        self.assertEqual(len(seqs), 2)
        ids = [it["trace_id"] for it in items]
        self.assertEqual(ids, seqs)
        self.assertEqual(len(set(ids)), 2)

    def test_trace_id_matches_turn_start_seq_user_message_first(self):
        # 旧数据顺序:user_message 在 turn_start 之前(history_qa 仍应锚到正确轮)
        self._two_turns(turn_first=False)
        items = history_qa(self.store, session_id=self.sid)
        seqs = [e["seq"] for e in self.store.read(self.sid) if e["type"] == "turn_start"]
        ids = [it["trace_id"] for it in items]
        self.assertEqual(ids, seqs)
        self.assertEqual(len(set(ids)), 2)


class TraceIdValidatorTest(unittest.TestCase):
    def test_usage_validator_keeps_optional_trace_id(self):
        ev = make_event("usage", {"model": "m", "prompt_tokens": 1, "completion_tokens": 1, "trace_id": 42})
        self.assertEqual(ev["payload"].get("trace_id"), 42)
        ev2 = make_event("usage", {"model": "m", "prompt_tokens": 1, "completion_tokens": 1})
        self.assertNotIn("trace_id", ev2["payload"])   # 不带该键的事件原样

    def test_badcase_validator_passes_trace_id(self):
        ev = make_event("badcase_snapshot", {"reason": "error", "model": "m", "trace_id": 7,
                                             "system": "s", "conversation": [], "completion": ""})
        self.assertEqual(ev["payload"]["trace_id"], 7)


if __name__ == "__main__":
    unittest.main()
