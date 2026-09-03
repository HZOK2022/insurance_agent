# -*- coding: utf-8 -*-
"""中断/停止保留半截(D53 补):手动停止 → assistant_message 带 interrupted + 半截文本;build_history 折入半截。"""
from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace
import unittest

from app.loop.agent_loop import AgentLoop
from app.session.context import build_history
from app.session import events
from app.session.store import SessionStore


def _cfg():
    return SimpleNamespace(max_steps_per_turn=20, context_window=0, max_retrieve_per_turn=5,
                           deepseek_model="x", write_tools_approval="auto",
                           max_history_search_per_turn=1, history_search_top_k=4,
                           tool_result_head_chars=0, tool_result_tail_chars=0)


def _present(answer_text, references):
    return [{"t": "p", "text": answer_text}], []


class _FakeLLM:
    def __init__(self, chunks):
        self._chunks = chunks
        self.produced = {"n": 0}
    def chat_stream(self, *a, **k):
        for c in self._chunks:
            self.produced["n"] += 1
            yield c


class InterruptTest(unittest.TestCase):
    def test_stop_marks_interrupted_with_partial(self):
        chunks = [{"kind": "text", "delta": "你", "block_index": 0},
                  {"kind": "text", "delta": "好", "block_index": 0},
                  {"kind": "text", "delta": "世", "block_index": 0}]
        llm = _FakeLLM(chunks)
        emitted = []
        loop = AgentLoop(llm, "sys", {}, _present, _cfg(),
                         emit=lambda t, p: {"type": t, "payload": p},
                         should_abort=lambda: llm.produced["n"] >= 2)   # 收到第 2 个 chunk 后停
        sid = "sid-test"
        evts = list(loop.turn(sid, "hi"))
        # 找到最终 assistant_message,应带 interrupted 且含半截文本
        amsgs = [e for e in evts if e["type"] == "assistant_message"]
        self.assertTrue(amsgs)
        final_am = amsgs[-1]
        self.assertTrue(final_am["payload"].get("interrupted"))
        txt = "".join(b.get("text", "") for b in final_am["payload"]["blocks"])
        self.assertIn("你", txt)   # 半截保留(至少收到部分 chunk)
        # turn_end reason=interrupted
        ends = [e for e in evts if e["type"] == "turn_end"]
        self.assertTrue(ends)
        self.assertEqual(ends[-1]["payload"].get("reason"), "interrupted")

    def test_normal_completion_has_no_interrupted(self):
        llm = _FakeLLM([{"kind": "text", "delta": "完整回答", "block_index": 0}])
        loop = AgentLoop(llm, "sys", {}, _present, _cfg(),
                         emit=lambda t, p: {"type": t, "payload": p})
        evts = list(loop.turn("s", "hi"))
        am = [e for e in evts if e["type"] == "assistant_message"][-1]
        self.assertFalse(am["payload"].get("interrupted"))

    def test_build_history_embeds_interrupted(self):
        st = SessionStore(tempfile.mktemp(suffix=".db"))
        sid = st.create_session()["id"]
        st.append(sid, "assistant_message", {"blocks": [{"t": "p", "text": "半截回答"}],
                                             "citations": [], "interrupted": True})
        hist = build_history(st, sid)
        self.assertTrue(any(m.get("role") == "assistant" and "半截回答" in str(m.get("content"))
                            for m in hist))

    def test_assistant_message_validator_accepts_interrupted(self):
        v = events.validate("assistant_message", {"blocks": [{"t": "p", "text": "x"}], "citations": [], "interrupted": True})
        self.assertTrue(v.get("interrupted"))
        v2 = events.validate("assistant_message", {"blocks": [{"t": "p", "text": "x"}], "citations": []})
        self.assertNotIn("interrupted", v2)

if __name__ == "__main__":
    unittest.main()
