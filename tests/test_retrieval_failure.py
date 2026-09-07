# -*- coding: utf-8 -*-
"""M2:坏轮根因细分 —— classify_retrieval_failure(漏召/排序截断 vs 模型没用)。

- 有 gold(离线评估标注 relevant_chunk_ids):
  gold 块没进最终返回 → gold_miss(漏召,或被 top_k/重排截断);
  gold 块进了返回但回答没引用 → model_not_used(模型没用检索);
  gold 块被引用 → None(闭环正常)。
- 无 gold(生产在线):检索有命中但回答零引用 → model_not_used。
"""
from __future__ import annotations

import unittest

from app.observability.metrics import classify_retrieval_failure


class ClassifyRetrievalFailureTest(unittest.TestCase):
    def test_gold_block_missing_from_retrieved_is_gold_miss(self):
        r = classify_retrieval_failure(["r1", "r2"], [], ["gold1"])
        self.assertEqual(r["kind"], "gold_miss")
        self.assertIn("漏召", r["reason"])

    def test_gold_retrieved_but_not_cited_is_model_not_used(self):
        r = classify_retrieval_failure(["r1", "gold1"], ["r1"], ["gold1"])
        self.assertEqual(r["kind"], "model_not_used")

    def test_gold_retrieved_but_no_citation_at_all_is_model_not_used(self):
        r = classify_retrieval_failure(["r1", "gold1"], [], ["gold1"])
        self.assertEqual(r["kind"], "model_not_used")

    def test_gold_cited_is_ok(self):
        r = classify_retrieval_failure(["r1", "gold1"], ["gold1"], ["gold1"])
        self.assertIsNone(r)

    def test_partial_gold_cited_is_ok(self):
        r = classify_retrieval_failure(["r1", "gold1", "gold2"], ["gold1"], ["gold1", "gold2"])
        self.assertIsNone(r)

    def test_no_gold_retrieved_not_cited_is_model_not_used(self):
        r = classify_retrieval_failure(["r1", "r2"], [])
        self.assertEqual(r["kind"], "model_not_used")

    def test_no_gold_with_citation_is_none(self):
        self.assertIsNone(classify_retrieval_failure(["r1", "r2"], ["r1"]))

    def test_no_gold_no_retrieval_is_none(self):
        self.assertIsNone(classify_retrieval_failure([], []))


if __name__ == "__main__":
    unittest.main()
