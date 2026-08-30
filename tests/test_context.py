# -*- coding: utf-8 -*-
"""build_history 单测:恢复跨轮上下文、剥旧 [idx]、跳过 reasoning/过程事件。"""
from __future__ import annotations

import os
import tempfile
import unittest

from app.session import context
from app.session.store import SessionStore


class TestBuildHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.store = SessionStore(self.tmp.name)
        self.sid = "t1"

    def tearDown(self):
        self.store.close()
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def _append(self, type_: str, payload: dict):
        self.store.append(self.sid, type_, payload)

    def test_user_assistant_and_strips_old_idx(self):
        self._append("user_message", {"text": "重疾险责任免除包括哪些?"})
        # 过程性事件:应跳过
        self._append("retrieval", {"query": "重疾险 责任免除",
                                   "chunks": [{"chunk_id": "a:1", "doc_id": "a", "version": "v1",
                                               "section": "", "source": "", "content": "第二十一条...", "score": 0.7}]})
        self._append("assistant_message",
                     {"blocks": [{"t": "p", "text": "责任免除包括故意自伤。[1][2]"}],
                      "citations": [{"idx": 1, "chunk_id": "a:1"}]})
        hist = context.build_history(self.store, self.sid)
        self.assertEqual(len(hist), 2)
        self.assertEqual(hist[0], {"role": "user", "content": "重疾险责任免除包括哪些?"})
        self.assertEqual(hist[1]["role"], "assistant")
        self.assertNotIn("[", hist[1]["content"])          # 旧 [idx] 已剥
        self.assertIn("责任免除包括故意自伤。", hist[1]["content"])

    def test_skips_reasoning_and_narration(self):
        self._append("user_message", {"text": "等待期是多久?"})
        self._append("assistant_narration", {"text": "我先检索等待期条款"})
        self._append("assistant_message",
                     {"blocks": [{"t": "r", "text": "thinking..."}, {"t": "p", "text": "等待期30天 [1]"}],
                      "citations": []})
        hist = context.build_history(self.store, self.sid)
        # 只有 user + assistant(p 块);narration 与 reasoning(r) 不进入历史正文
        self.assertEqual(len(hist), 2)
        self.assertEqual(hist[1]["content"], "等待期30天")

    def test_empty_session_returns_empty(self):
        self.assertEqual(context.build_history(self.store, self.sid), [])


if __name__ == "__main__":
    unittest.main()
