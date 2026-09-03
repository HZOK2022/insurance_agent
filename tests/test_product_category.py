# -*- coding: utf-8 -*-
"""产品类别(保险类型):字段加到 chunks + to_chunk;区分医疗险/重疾险/意外险。

B 方案:search_knowledge 可选 category —— 检索后把同类别块稳定前置(软偏置),不排除其它类别。
"""
from __future__ import annotations

import os
import tempfile
import unittest

from app.businesses import insurance
from app.retrieval.categories import classify_product_category
from app.retrieval.knowledge_store import KnowledgeStore
from app.retrieval.search_tool import search_knowledge, to_chunk
from app.session.store import SessionStore


class ClassifyTest(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(classify_product_category("安盛天平个人综合住院医疗保险条款"), "医疗险")
        self.assertEqual(classify_product_category("尊享e生2025"), "医疗险")
        self.assertEqual(classify_product_category("某重大疾病保险产品"), "重疾险")
        self.assertEqual(classify_product_category("某意外险产品"), "意外险")
        self.assertEqual(classify_product_category("未知产品"), "其他")


class SearchSchemaTest(unittest.TestCase):
    def test_search_tool_schema_has_category(self):
        props = insurance.SEARCH_TOOL["function"]["parameters"]["properties"]
        self.assertIn("category", props)
        self.assertIn("query", props)
        self.assertEqual(insurance.SEARCH_TOOL["function"]["parameters"]["required"], ["query"])

    def test_system_guidance_mentions_category(self):
        self.assertIn("指定险种", insurance.SYSTEM)
        self.assertIn("category", insurance.SYSTEM)


class KnowledgeStoreCategoryTest(unittest.TestCase):
    def setUp(self):
        self.p = tempfile.mktemp(suffix=".db")
        self.k = KnowledgeStore(self.p)

    def tearDown(self):
        try:
            self.k.close()
        except Exception:
            pass
        os.remove(self.p)

    def _chunk(self, cid, doc, cat=""):
        return {"chunk_id": cid, "content": "正文", "meta": {"doc_id": doc, "version": "v1", "section": "",
                "doc_type": "policy_document", "source": "s", "title": doc, "product_category": cat}}

    def test_upsert_returns_category(self):
        self.k.upsert_chunks([self._chunk("c1", "安盛住院医疗", "医疗险")])
        self.assertEqual(self.k.get_chunk("c1")["meta"]["product_category"], "医疗险")

    def test_backfill_classifies_inferred(self):
        self.k.upsert_chunks([self._chunk("c2", "某重疾险")])
        self.k.conn.commit()
        self.k.close()
        self.k = KnowledgeStore(self.p)
        self.assertEqual(self.k.get_chunk("c2")["meta"]["product_category"], "重疾险")


class ToChunkCategoryTest(unittest.TestCase):
    def test_to_chunk_surfaces_category(self):
        c = to_chunk({"chunk_id": "c", "content": "x",
                      "meta": {"doc_id": "d", "product_category": "重疾险", "title": "t"}}, 0.9)
        self.assertEqual(c["product_category"], "重疾险")



class _FakeEmbedder:
    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]


class _CatStore:
    def search(self, vec, top_k):
        return [
            {"chunk_id": "a", "content": "医疗险A", "score": 0.9, "meta": {"doc_id": "安盛医疗", "product_category": "医疗险"}},
            {"chunk_id": "b", "content": "重疾险B", "score": 0.8, "meta": {"doc_id": "某重疾险", "product_category": "重疾险"}},
            {"chunk_id": "c", "content": "医疗险C", "score": 0.7, "meta": {"doc_id": "尊享e生", "product_category": "医疗险"}},
        ][:top_k]


class SoftCategoryTest(unittest.TestCase):
    def test_soft_category_prioritizes_same_and_keeps_rest(self):
        out = search_knowledge(_FakeEmbedder(), _CatStore(), "推荐", top_k=5, top_rerank=3, category="医疗险")
        ids = [c["chunk_id"] for c in out]
        self.assertEqual(ids[:2], ["a", "c"])
        self.assertIn("b", ids)
        self.assertEqual(out[0]["product_category"], "医疗险")

    def test_no_category_keeps_original_order(self):
        out = search_knowledge(_FakeEmbedder(), _CatStore(), "推荐", top_k=5, top_rerank=3)
        self.assertEqual([c["chunk_id"] for c in out], ["a", "b", "c"])

    def test_category_does_not_exclude_cross_category(self):
        out = search_knowledge(_FakeEmbedder(), _CatStore(), "推荐", top_k=5, top_rerank=3, category="重疾险")
        ids = [c["chunk_id"] for c in out]
        self.assertEqual(ids[0], "b")
        self.assertIn("a", ids)


if __name__ == "__main__":
    unittest.main()
