# -*- coding: utf-8 -*-
"""检索降级测试(产品决策:向量库不可用 → 注入零检索结果 + LLM 诚实拒答,不做关键词兜底作答)。

- search_knowledge:稠密(Qdrant)不可用 → 一律抛 RetrievalUnavailable(即使有 SQLite BM25 也不兜底作答);
  正常路径返回 chunks(list 语义不变)。
- _run_tool:handler 抛 RetrievalUnavailable → error_code=retrieval_unavailable + 诚实话术(严禁杜撰,不泄漏内部细节)。
"""
from __future__ import annotations

import types
import unittest

from app.loop.agent_loop import AgentLoop
from app.retrieval.errors import RetrievalUnavailable
from app.retrieval.hybrid import BM25Index
from app.retrieval.search_tool import search_knowledge
from app.session.events import make_event

_CHUNKS = [
    {"chunk_id": "a1", "content": "重大疾病保险的责任免除包括故意自伤、酒后驾驶、吸毒等情形。", "meta": {"doc_id": "docA", "version": "v1", "section": "s1", "product_category": "重疾险"}},
    {"chunk_id": "a2", "content": "本合同的免赔额为一万元,按年度累计计算。", "meta": {"doc_id": "docA", "version": "v1", "section": "s2", "product_category": "医疗险"}},
]


class _FakeEmbedder:
    def embed(self, texts):
        return [[0.1] * 4 for _ in texts]


class _DownStore:
    """search 一律抛 RetrievalUnavailable(模拟 Qdrant 挂)。"""

    def search(self, vector, top_k=20):
        raise RetrievalUnavailable("qdrant down")


class _OkStore:
    def search(self, vector, top_k=20):
        c = _CHUNKS[0]
        return [{"chunk_id": c["chunk_id"], "score": 0.9, "content": c["content"], "meta": c["meta"]}]


def _cfg(**kw):
    d = dict(max_steps_per_turn=6, max_retrieve_per_turn=5, deepseek_model="fake", write_tools_approval="auto")
    d.update(kw)
    return types.SimpleNamespace(**d)


class RetrievalUnavailableTest(unittest.TestCase):
    def test_dense_down_raises_even_with_hybrid(self):
        # 产品决策:向量库挂 → 不做 SQLite 关键词兜底作答,一律注入零检索 → 抛 RetrievalUnavailable
        hybrid = BM25Index(_CHUNKS)
        with self.assertRaises(RetrievalUnavailable):
            search_knowledge(_FakeEmbedder(), _DownStore(), "责任免除包括什么", top_k=5, top_rerank=3,
                             hybrid=hybrid, hybrid_weight=0.5)

    def test_dense_down_no_hybrid_raises(self):
        with self.assertRaises(RetrievalUnavailable):
            search_knowledge(_FakeEmbedder(), _DownStore(), "责任免除", top_k=5, top_rerank=3)

    def test_normal_path_returns_chunks(self):
        out = search_knowledge(_FakeEmbedder(), _OkStore(), "责任免除", top_k=5, top_rerank=3)
        self.assertIsInstance(out, list)
        self.assertEqual(out[0]["chunk_id"], "a1")


class RunToolRetrievalUnavailableTest(unittest.TestCase):
    def test_run_tool_returns_retrieval_unavailable_with_honest_content(self):
        def handler(args, start_idx=0):
            raise RetrievalUnavailable("qdrant down")

        tools = {"search_knowledge": {"schema": {"type": "function", "function": {"name": "search_knowledge", "parameters": {"type": "object", "properties": {}}}},
                                      "handler": handler}}
        loop = AgentLoop(None, "系统", tools, (lambda t, r: ([{"t": "p", "text": t}], [])),
                         _cfg(), emit=lambda t, p: make_event(t, p))
        content, ref, ok, code = loop._run_tool("search_knowledge", {"query": "q"}, 0)
        self.assertFalse(ok)
        self.assertEqual(code, "retrieval_unavailable")
        self.assertIn("请稍后重试", content)    # 引导用户稍后重试
        self.assertNotIn("qdrant down", content)  # 不泄漏内部细节
        self.assertIsNone(ref)                    # 注入零检索结果

    def test_generic_error_still_tool_error(self):
        def handler(args, start_idx=0):
            raise RuntimeError("boom")

        tools = {"x": {"schema": {"type": "function", "function": {"name": "x", "parameters": {"type": "object", "properties": {}}}},
                       "handler": handler}}
        loop = AgentLoop(None, "系统", tools, (lambda t, r: ([{"t": "p", "text": t}], [])),
                         _cfg(), emit=lambda t, p: make_event(t, p))
        _c, _r, ok, code = loop._run_tool("x", {}, 0)
        self.assertFalse(ok)
        self.assertEqual(code, "tool_error")


if __name__ == "__main__":
    unittest.main()
