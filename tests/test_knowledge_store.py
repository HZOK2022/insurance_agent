# -*- coding: utf-8 -*-
"""KnowledgeStore(SQLite chunks 事实源)测试:黄金法则 SQLite=事实源,Qdrant=派生(可重建)。"""
import os
import tempfile
import unittest

from app.retrieval.knowledge_store import KnowledgeStore


class KnowledgeStoreTest(unittest.TestCase):
    def setUp(self):
        self.store = KnowledgeStore(os.path.join(tempfile.mkdtemp(), "k.db"))
        self.chunks = [
            {"chunk_id": "尊享e生2025:0", "content": "条款 0 正文",
             "meta": {"doc_id": "尊享e生2025", "version": "v1", "section": "s0", "doc_type": "policy", "source": "x.pdf", "title": "尊享"}},
            {"chunk_id": "尊享e生2025:1", "content": "条款 1 正文",
             "meta": {"doc_id": "尊享e生2025", "version": "v1", "section": "s1", "doc_type": "policy", "source": "x.pdf", "title": "尊享"}},
        ]

    def tearDown(self):
        self.store.close()

    def test_upsert_and_count(self):
        n = self.store.upsert_chunks(self.chunks)
        self.assertEqual(n, 2)
        self.assertEqual(self.store.count(), 2)

    def test_upsert_is_idempotent(self):
        self.store.upsert_chunks(self.chunks)
        self.store.upsert_chunks(self.chunks)
        self.assertEqual(self.store.count(), 2)   # upsert,不重复

    def test_upsert_updates_content(self):
        self.store.upsert_chunks([{"chunk_id": "尊享e生2025:0", "content": "新版正文",
                                   "meta": {"doc_id": "尊享e生2025", "version": "v2", "section": "s0"}}])
        c = self.store.get_chunk("尊享e生2025:0")
        self.assertEqual(c["content"], "新版正文")
        self.assertEqual(c["meta"]["version"], "v2")

    def test_all_chunks_and_get(self):
        self.store.upsert_chunks(self.chunks)
        allc = self.store.all_chunks()
        self.assertEqual(len(allc), 2)
        self.assertIn("chunk_id", allc[0]) and self.assertIn("content", allc[0]) and self.assertIn("meta", allc[0])
        c = self.store.get_chunk("尊享e生2025:1")
        self.assertEqual(c["content"], "条款 1 正文")

    def test_get_missing(self):
        self.assertIsNone(self.store.get_chunk("不存在"))
