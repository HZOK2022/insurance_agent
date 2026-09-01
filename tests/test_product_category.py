# -*- coding: utf-8 -*-
"""产品类别(保险类型):字段加到 chunks + to_chunk + chunk 注册表;区分医疗险/重疾险/意外险。"""
from __future__ import annotations

import os
import tempfile
import unittest

from app.retrieval.categories import classify_product_category
from app.retrieval.knowledge_store import KnowledgeStore
from app.retrieval.search_tool import to_chunk
from app.session.context import build_chunk_registry
from app.session.store import SessionStore


class ClassifyTest(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(classify_product_category("安盛天平个人综合住院医疗保险条款"), "医疗险")
        self.assertEqual(classify_product_category("尊享e生2025"), "医疗险")
        self.assertEqual(classify_product_category("某重大疾病保险产品"), "重疾险")
        self.assertEqual(classify_product_category("某意外险产品"), "意外险")
        self.assertEqual(classify_product_category("未知产品"), "其他")


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
        m = self.k.get_chunk("c1")["meta"]
        self.assertEqual(m["product_category"], "医疗险")

    def test_backfill_classifies_inferred(self):
        self.k.upsert_chunks([self._chunk("c2", "某重疾险")])
        self.k.conn.commit()
        self.k.close()
        self.k = KnowledgeStore(self.p)
        m = self.k.get_chunk("c2")["meta"]
        self.assertEqual(m["product_category"], "重疾险")

    def test_all_chunks_carries_category(self):
        self.k.upsert_chunks([self._chunk("c3", "尊享e生2025", "医疗险")])
        ac = self.k.all_chunks()
        self.assertEqual(ac[0]["meta"]["product_category"], "医疗险")


class ToChunkCategoryTest(unittest.TestCase):
    def test_to_chunk_surfaces_category(self):
        c = to_chunk({"chunk_id": "c", "content": "x",
                      "meta": {"doc_id": "d", "product_category": "重疾险", "title": "t"}}, 0.9)
        self.assertEqual(c["product_category"], "重疾险")
        self.assertEqual(c["doc_type"], "")
        self.assertEqual(c["title"], "t")

    def test_to_chunk_default_empty(self):
        c = to_chunk({"chunk_id": "c", "content": "x", "meta": {}}, 0.9)
        self.assertEqual(c["product_category"], "")


class RegistryCategoryTest(unittest.TestCase):
    def test_registry_preserves_category(self):
        db = tempfile.mktemp(suffix=".db")
        store = SessionStore(db)
        sid = store.create_session("u1")["id"]
        chunk = {"chunk_id": "c1", "score": 0.9, "doc_id": "安盛住院医疗", "version": "v1", "section": "",
                 "source": "s", "doc_type": "policy_document", "title": "安盛住院医疗", "product_category": "医疗险",
                 "content": "正文"}
        store.append(sid, "retrieval", {"query": "q", "chunks": [chunk]})
        pool, idx_map = build_chunk_registry(store, sid)
        self.assertEqual(pool[0]["product_category"], "医疗险")
        self.assertEqual(idx_map["c1"], 1)
        store.close()
        os.remove(db)


if __name__ == "__main__":
    unittest.main()
