# -*- coding: utf-8 -*-
"""混合检索测试(离线):BM25Index 排序、fuse 加权、search_knowledge 混合路径能找回稠密漏掉的 BM25 命中。"""
import unittest
from app.retrieval.hybrid import tokenize, BM25Index, fuse_and_pick, rrf_fuse_and_pick
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


class RrfFusionTest(unittest.TestCase):
    """RRF:按排名贡献融合,防"dense-only 好块被 min-max 压出候选池"。"""

    def test_rrf_empty_input(self):
        self.assertEqual(rrf_fuse_and_pick({}, {}, top_k=5), [])

    def test_rrf_rank_contribution(self):
        # min-max 缺陷:dense 只命中一个 gold 块 a 时(min==max),归一 collapse 到 0,
        # a 被 bm25 大批高分块挤到候选池最末。RRF 按排名(1/(k+rank))让 a 保住位置。
        dense = {"a": 0.5}
        bm25 = {"c": 9.0, "d": 8.0, "e": 7.0, "f": 6.0, "g": 5.0, "h": 4.0, "i": 3.0}
        mm = [cid for cid, _ in fuse_and_pick(dense, bm25, 0.5, 5)]
        self.assertNotIn("a", mm, "前提:min-max 归一 collapse → dense-only gold 块 a 被挤出候选池")
        rr = [cid for cid, _ in rrf_fuse_and_pick(dense, bm25, k=60, top_k=5)]
        self.assertIn("a", rr, "RRF:仅 dense rank1 的 a 靠排名贡献保住,不被挤出")

    def test_rrf_boost_double_hit(self):
        # 双路都命中的块 RRF 分 > 各自单路 → 应排在单路命中之上(top_k>候选数时)
        dense = {"x": 0.9, "y": 0.1}
        bm25 = {"x": 8.0}          # x 双路;y 仅 dense
        rr = dict(rrf_fuse_and_pick(dense, bm25, k=60, top_k=5))
        self.assertGreater(rr["x"], rr["y"], "双路命中 x 的 RRF 分应高于单路 y")

    def test_rrf_k_monotonic(self):
        # k 越大 RRF 分越小但排序不变(仅平滑)
        dense = {"a": 0.9, "b": 0.1}
        bm25 = {"a": 1.0, "b": 0.5}
        for kk in (10, 60, 100):
            rr = [cid for cid, _ in rrf_fuse_and_pick(dense, bm25, k=kk, top_k=5)]
            self.assertEqual(rr[0], "a")


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

    def test_product_soft_bias(self):
        # D72:点名产品时,该产品块被提到前面(软偏置,不排除其它产品);chunk 带 product_name
        class MixedStore:
            def search(self, vec, top_k):
                return [
                    {"chunk_id": "a1", "content": "产品A 条款", "score": 0.5, "meta": {"doc_id": "A", "product_name": "产品A"}},
                    {"chunk_id": "b1", "content": "产品B 条款", "score": 0.4, "meta": {"doc_id": "B", "product_name": "产品B"}},
                    {"chunk_id": "a2", "content": "产品A 条款2", "score": 0.3, "meta": {"doc_id": "A", "product_name": "产品A"}},
                ][:top_k]
        out = search_knowledge(FakeEmbedder(), MixedStore(), "问题", top_k=5, top_rerank=3, product="产品A")
        ids = [c["chunk_id"] for c in out]
        self.assertEqual(ids[:2], ["a1", "a2"], "点名产品的块应被提到前面")
        self.assertIn("b1", ids, "软偏置不排除其它产品")
        self.assertTrue(all(c.get("product_name") for c in out), "检索块应带产品名")



class ScoreLadderTest(unittest.TestCase):
    """A3:检索前序(dense/BM25/融合/rerank 分)落库到每块,供 trace 展示为何召回/排这块。"""

    def test_chunks_carry_ladder_scores(self):
        idx = BM25Index(CORPUS)

        def rerank_fn(query, docs):
            out = []
            for i, d in enumerate(docs):
                out.append({"index": i, "relevance_score": 0.9 if "投保" in d else 0.2})
            return out

        out = search_knowledge(FakeEmbedder(), DenseMissesB1Store(), "投保年龄",
                               top_k=5, top_rerank=3, hybrid=idx, hybrid_weight=0.5, rerank_fn=rerank_fn)
        self.assertTrue(out, "应有检索结果")
        for c in out:
            self.assertIn("dense_score", c)
            self.assertIn("fused_score", c)
            self.assertIn("rerank_score", c)
        # b1 是稠密漏掉、BM25 命中的块 → dense_score 应缺省,但 bm25_score 有值
        b1 = next((c for c in out if c.get("chunk_id") == "b1"), None)
        self.assertIsNotNone(b1, "混合应收回 b1")
        self.assertIsNone(b1.get("dense_score"), "b1 稠密未命中 → dense_score 应为 None")
        self.assertIsNotNone(b1.get("bm25_score"), "b1 BM25 命中 → bm25_score 有值")
        self.assertIsNotNone(b1.get("fused_score"), "b1 应有融合分")
        # rerank 后"投保"相关度最高者应居首
        self.assertEqual(out[0]["chunk_id"], "b1", "rerank 相关度最高者应居首")

    def test_no_rerank_keeps_dense(self):
        out = search_knowledge(FakeEmbedder(), DenseMissesB1Store(), "投保年龄",
                               top_k=5, top_rerank=3, hybrid=BM25Index(CORPUS), hybrid_weight=0.0)
        self.assertTrue(out)
        c0 = out[0]
        self.assertIn("dense_score", c0)              # 纯稠密:dense_score 在
        self.assertIsNone(c0.get("rerank_score"))  # 无 rerank → rerank_score 为 None

class RrfWiringTest(unittest.TestCase):
    """D82:search_knowledge 默认走 RRF 并落四路 rank;fusion=weighted 可切回 min-max 对比。

    判据靠**分数区间**而非具体值:RRF 分 = Σ1/(k+rank),k=60 时上限 2/61≈0.0328;
    min-max 归一加权落在 [0,1]。两者量级差一个数量级,足以区分走了哪条路。
    """

    def _run(self, **kw):
        return search_knowledge(FakeEmbedder(), DenseMissesB1Store(), "投保年龄",
                                top_k=5, top_rerank=3, hybrid=BM25Index(CORPUS),
                                hybrid_weight=0.5, **kw)

    def _b1(self, out):
        return next(c for c in out if c["chunk_id"] == "b1")

    def test_default_fusion_is_rrf(self):
        b1 = self._b1(self._run())
        self.assertGreater(b1["fused_score"], 0, "RRF 按排名给分,b1(BM25 第1)应有分")
        self.assertLess(b1["fused_score"], 0.04, "RRF 分上限 2/(60+1)≈0.0328")

    def test_weighted_fusion_switchable(self):
        b1 = self._b1(self._run(fusion="weighted"))
        self.assertGreater(b1["fused_score"], 0.04, "min-max 归一落在 [0,1],量级远大于 RRF 分")

    def test_chunks_carry_four_ranks(self):
        out = self._run()
        for c in out:
            for k in ("dense_rank", "bm25_rank", "fused_rank", "rerank_rank"):
                self.assertIn(k, c, f"检索块必须带 {k}(缺则前端阶梯无法显示)")
        by = {c["chunk_id"]: c for c in out}
        # b1:稠密漏召 → dense_rank 为 None;靠 BM25 第 1 进入融合
        self.assertIsNone(by["b1"]["dense_rank"], "b1 稠密未命中 → dense_rank 应为 None")
        self.assertEqual(by["b1"]["bm25_rank"], 1, "b1 是 BM25 第 1")
        # d1:稠密第 1 + BM25 第 2 → 双路命中 RRF 叠加最高 → 融合第 1
        self.assertEqual(by["d1"]["dense_rank"], 1)
        self.assertEqual(by["d1"]["fused_rank"], 1, "双路命中的块 RRF 分最高")

    def test_rerank_rank_shows_displacement(self):
        """rank 的核心价值:fused→rerank 的位移一眼可读(分数表达不出"提了 1 位")。"""

        def rerank_fn(query, docs):
            return [{"index": i, "relevance_score": 0.9 if "投保" in d else 0.2}
                    for i, d in enumerate(docs)]

        out = self._run(rerank_fn=rerank_fn)
        top = out[0]
        self.assertEqual(top["chunk_id"], "b1", "rerank 相关度最高者应居首")
        self.assertEqual(top["fused_rank"], 2, "融合时第 2")
        self.assertEqual(top["rerank_rank"], 1, "重排后提到第 1")


if __name__ == "__main__":
    unittest.main()
