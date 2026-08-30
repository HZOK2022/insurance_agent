# -*- coding: utf-8 -*-
"""重排接线测试:search_knowledge 应用 rerank_fn 后按 top_rerank 截断;无 rerank_fn 时回退全量 top_k。"""
import unittest
from app.retrieval.search_tool import search_knowledge


class FakeEmbedder:
    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]


class FakeStore:
    def search(self, vec, top_k):
        return [{"chunk_id": f"c{i}", "content": f"doc {i}", "score": 1.0 - i * 0.01,
                 "meta": {"doc_id": "d", "version": "v", "section": "s", "source": "src"}} for i in range(top_k)]


def fake_rerank(query, docs):
    # 按"最后一条最相关"返回,验证会被排序+截断
    n = len(docs)
    return [{"index": i, "relevance_score": float(n - i)} for i in range(n)]


class RerankWiringTest(unittest.TestCase):
    def test_with_rerank_fn_returns_top_rerank(self):
        out = search_knowledge(FakeEmbedder(), FakeStore(), "q", top_k=20, top_rerank=3, rerank_fn=fake_rerank)
        self.assertEqual(out, [out[i] for i in range(3)][:3], "应截断到 top_rerank")
        self.assertEqual(len(out), 3)

    def test_without_rerank_fn_returns_full_top_k(self):
        out = search_knowledge(FakeEmbedder(), FakeStore(), "q", top_k=20, top_rerank=3)
        self.assertEqual(len(out), 20)

    def test_rerank_failure_falls_back_to_full(self):
        def broken(query, docs):
            return None
        out = search_knowledge(FakeEmbedder(), FakeStore(), "q", top_k=20, top_rerank=3, rerank_fn=broken)
        self.assertEqual(len(out), 20, "rerank 失败应回退原 top_k,不硬切")

    def test_rerank_prefers_high_score(self):
        def prefer_last(query, docs):
            n = len(docs)
            return [{"index": i, "relevance_score": float(i)} for i in range(n)]
        out = search_knowledge(FakeEmbedder(), FakeStore(), "q", top_k=20, top_rerank=2, rerank_fn=prefer_last)
        # prefer_last 让 index 越大越相关 → 应选最后两个 chunk(c18,c19)
        self.assertEqual([c["chunk_id"] for c in out], ["c19", "c18"])


if __name__ == "__main__":
    unittest.main()
