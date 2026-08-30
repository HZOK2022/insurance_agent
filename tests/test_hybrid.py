# -*- coding: utf-8 -*-
"""混合检索测试(离线):BM25Index 排序、fuse 加权、search_knowledge 混合路径能找回稠密漏掉的 BM25 命中。"""
import unittest
from app.retrieval.hybrid import tokenize, BM25Index, fuse_and_pick
from app.retrieval.search_tool import search_knowledge


class FakeEmbedder:
    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]


CORPUS = [
    {"chunk_id": "d1", "content": "重大疾病保险 恶性肿瘤 心肌梗死 100种", "meta": {"doc_id": "doc", "version": "v1", "section": "s", "source": "src"}},
    {"chunk_id": "d2", "content": "等待期 30日 意外无等待期", "meta": {"doc_id": "doc", "version": "v1", "section": "s", "source": "src"}},
    {"chunk_id": "b1", "content": "投保年龄 出生满30天至59周岁", "meta": {"doc_id": "doc", "version": "v1", "section": "s", "source": "src"}},
]


class DenseMissesB1Store:
    """稠密检索只回 d1,d2,漏掉 b1(模拟稠密召回不全)。"""
    def search(self, vec, top_k):
        return [{"chunk_id": "d1", "content": CORPUS[0]["content"], "score": 0.8, "meta": CORPUS[0]["meta"]},
                {"chunk_id": "d2", "content": CORPUS[1]["content"], "score": 0.3, "meta": CORPUS[1]["meta"]}][:top_k]


class HybridCoreTest(unittest.TestCase):
    def test_tokenize_cjk_unigram_and_words(self):
        toks = tokenize("重大疾病保险 2025版")
        self.assertIn("重大疾病保险", "".join([t for t in toks if len(t) == 1]))  # CJK 每字
        self.assertIn("2025", toks)

    def test_bm25_ranks_relevant_doc_top(self):
        idx = BM25Index(CORPUS)
        hits = idx.search("投保年龄", 3)
        self.assertEqual(hits[0][0], "b1")
        hits2 = idx.search("等待期", 3)
        self.assertEqual(hits2[0][0], "d2")

    def test_fuse_weight_0_is_dense_only(self):
        fused = fuse_and_pick({"a": 0.9, "b": 0.2}, {"b": 5.0}, 0.0, 3)
        self.assertEqual(fused[0][0], "a")

    def test_fuse_weight_1_is_bm25_only(self):
        fused = fuse_and_pick({"a": 0.9}, {"b": 5.0, "a": 0.1}, 1.0, 3)
        self.assertEqual(fused[0][0], "b")

    def test_fuse_half_picks_high_either(self):
        fused = fuse_and_pick({"a": 0.9, "b": 0.2, "c": 0.4},
                              {"c": 8.0, "a": 0.3}, 0.5, 3)
        top_ids = [c for c, _ in fused]
        self.assertIn("c", top_ids)  # bm25 高分 c 应进入融合 topk


class HybridSearchTest(unittest.TestCase):
    def test_hybrid_recovers_bm25_only_chunk(self):
        idx = BM25Index(CORPUS)
        out = search_knowledge(FakeEmbedder(), DenseMissesB1Store(), "投保年龄",
                               top_k=5, top_rerank=3, hybrid=idx, hybrid_weight=0.5)
        ids = [c["chunk_id"] for c in out]
        self.assertIn("b1", ids, "混合应找回稠密漏掉、但 BM25 命中的 b1")

    def test_weight_zero_is_dense_only(self):
        # 不开混合(weight=0)时,只用稠密命中,不会带回 b1
        out = search_knowledge(FakeEmbedder(), DenseMissesB1Store(), "投保年龄",
                               top_k=5, top_rerank=3, hybrid=BM25Index(CORPUS), hybrid_weight=0.0)
        ids = [c["chunk_id"] for c in out]
        self.assertNotIn("b1", ids)


if __name__ == "__main__":
    unittest.main()
