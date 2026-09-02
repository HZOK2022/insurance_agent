# -*- coding: utf-8 -*-
"""D52 本会话历史检索(session_history_search)单测。

核心断言:
- 会话 id 由系统注入(session_id 参数),handler 只用它读本会话 events —— 结构性杜绝跨会话。
- 排除 build_history 已折入(进上下文)的 seq:只返回被 compaction 影子/裁剪掉的早前原文(增量价值)。
- query 特征重叠打分;无命中返回"无相关早前记录";超长截断;store/session 缺失返回不可用。
"""
from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace
import unittest

from app.session.store import SessionStore
from app.businesses.insurance import _make_history_handler, _query_features, _text_of_blocks


def _tmp_store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = SessionStore(path)
    return st, path


def _cfg():
    return SimpleNamespace(history_search_top_k=4, max_tool_result_chars=0,
                           tool_result_head_chars=0, tool_result_tail_chars=0)


class QueryFeaturesTest(unittest.TestCase):
    def test_english_and_cjk_features(self):
        feats = _query_features("A产品重大疾病 bge")
        self.assertIn("重", feats)
        self.assertIn("疾", feats)
        self.assertIn("bge", feats)  # 英文 2+ 字符词
        feats2 = _query_features("重排")
        self.assertIn("重", feats2)

    def test_no_feature(self):
        self.assertEqual(_query_features(""), set())


class TextOfBlocksTest(unittest.TestCase):
    def test_blocks_to_text(self):
        blocks = [{"t": "p", "text": "第一段"}, {"t": "ul", "items": ["甲", "乙"]}]
        out = _text_of_blocks(blocks)
        self.assertIn("第一段", out)
        self.assertIn("甲", out)


class HistoryHandlerTest(unittest.TestCase):
    def _setup(self):
        st, path = _tmp_store()
        sid = st.create_session()["id"]
        return st, sid, path

    def test_no_store_or_session(self):
        st, sid, path = self._setup()
        h = _make_history_handler(None, _cfg())
        self.assertIn("不可用", h({"query": "x"}, session_id=sid)["content"])
        h2 = _make_history_handler(st, _cfg())
        self.assertIn("不可用", h2({"query": "x"})["content"])  # session_id=None
        st.close()

    def test_retrieves_shadowed_excludes_visible(self):
        st, sid, path = self._setup()
        st.append(sid, "user_message", {"text": "A产品重大疾病包括哪些"})          # seq1,早前
        st.append(sid, "compaction_summary", {"summary": "已查明:A产品重疾清单",
                                              "shadowed_seqs": [1], "shadowed_token_count": 0})  # seq2,影子 seq1
        st.append(sid, "user_message", {"text": "16岁能不能买B产品"})              # seq3,未被影子
        h = _make_history_handler(st, _cfg())
        res = h({"query": "重大疾病"}, session_id=sid)
        self.assertIn("重大疾病", res["content"])
        self.assertIn("A产品", res["content"])
        self.assertNotIn("16岁", res["content"])
        self.assertNotIn("B产品", res["content"])
        st.close()

    def test_no_match(self):
        st, sid, path = self._setup()
        st.append(sid, "user_message", {"text": "保费怎么算"})
        h = _make_history_handler(st, _cfg())
        res = h({"query": "重疾 癌症"}, session_id=sid)
        self.assertIn("无相关早前记录", res["content"])
        st.close()

    def test_does_not_cross_session(self):
        st, sid_a, path = self._setup()
        sid_b = st.create_session()["id"]
        # 各会话都有"被影子"的记录(否则 build_history 折入可见、被排除);用 append 返回的真实 seq
        sa1 = st.append(sid_a, "user_message", {"text": "A产品重大疾病有哪些"})
        st.append(sid_a, "compaction_summary", {"summary": "s", "shadowed_seqs": [sa1], "shadowed_token_count": 0})
        sb1 = st.append(sid_b, "user_message", {"text": "B产品癌症保障"})
        st.append(sid_b, "compaction_summary", {"summary": "s", "shadowed_seqs": [sb1], "shadowed_token_count": 0})
        h = _make_history_handler(st, _cfg())
        res_a = h({"query": "癌症"}, session_id=sid_a)   # 注入会话 A,只应查 A(不跨到 B)
        self.assertIn("无相关早前记录", res_a["content"])  # A 无"癌症"内容,且绝不返回 B 的
        res_b = h({"query": "癌症"}, session_id=sid_b)   # 注入会话 B,命中 B 的"癌症保障"
        self.assertIn("癌症保障", res_b["content"])
        st.close()

    def test_truncation(self):
        st, sid, path = self._setup()
        st.append(sid, "user_message", {"text": "A" * 500})
        cfg = SimpleNamespace(history_search_top_k=4, max_tool_result_chars=200,
                              tool_result_head_chars=100, tool_result_tail_chars=50)
        h = _make_history_handler(st, cfg)
        res = h({"query": "A"}, session_id=sid)
        self.assertLess(len(res["content"]), 500)   # 截断后应显著短于原文(原文 500)
        st.close()


if __name__ == "__main__":
    unittest.main()
