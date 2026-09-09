# -*- coding: utf-8 -*-
"""KnowledgeStore / PremiumStore / MemoryStore 走 MySQL(本地 3306,root 空密码)的往返测试;连不上则 skip。

与 test_session_mysql 同风格:每测用独立 id,断言后清理(events append-only 同理,这里用底层直连删测试行)。
"""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

import app.db as dbmod


def _mysql_cfg():
    host = os.environ.get("MYSQL_HOST", "127.0.0.1")
    # 单库多表形态:knowledge/premium 未拆分(留空),store 经 dbmod 空名回退到 db_name
    return SimpleNamespace(db_enabled=True, db_host=host, db_port=3306, db_user="root", db_pass="",
                           db_name="insurance_agent", sqlite_path="")


class MysqlStoresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            dbmod.get_conn(_mysql_cfg()).close()
        except Exception as e:
            raise unittest.SkipTest(f"本地 MySQL 不可达: {e}")

    def test_knowledge_roundtrip(self):
        from app.retrieval.knowledge_store import KnowledgeStore
        cfg = _mysql_cfg()
        k = KnowledgeStore(cfg=cfg)
        did = "mysql_ks_test"
        cid = did + ":c1"
        try:
            k.upsert_chunks([{"chunk_id": cid, "content": "hello-test",
                              "meta": {"doc_id": did, "version": "v1", "section": "s1",
                                       "doc_type": "policy", "source": "test", "title": "t",
                                       "product_name": did, "product_category": "medical"}}])
            self.assertEqual(k.get_chunk(cid)["content"], "hello-test")
            self.assertIsInstance(k.count(), int)  # 可执行
        finally:
            k.delete_document(did)
            self.assertIsNone(k.get_chunk(cid))
            k.close()

    def test_premium_roundtrip(self):
        from app.businesses.premium import PremiumStore
        cfg = _mysql_cfg()
        p = PremiumStore(cfg=cfg)
        pk = "mysql_premium_test"
        try:
            p.upsert_product(pk, "prod", "doc", "v1", "cov", "rules", {"mandatory": []}, "src")
            p.upsert_rate(pk, "plan", "plan-name", {"deductible": "0yuan"}, 0, 99, 100.0, "yuan/yr", "src", "sec")
            r = p.get_rate(pk, "plan", {"deductible": "0yuan"}, 30)
            self.assertIsNotNone(r)
            self.assertEqual(r["premium"], 100.0)
            self.assertEqual(p.get_product(pk)["key"], pk)
        finally:
            p.conn.execute("DELETE FROM premium_rates WHERE product_key=?", (pk,))
            p.conn.execute("DELETE FROM products WHERE `key`=?", (pk,))
            p.close()

    def test_memory_roundtrip(self):
        from app.memory.store import MemoryStore
        cfg = _mysql_cfg()
        m = MemoryStore(cfg=cfg)
        uid = "mysql_mem_test"
        key = "verify_guard"
        try:
            m.save(uid, "cross_session", "policy", key, "ask insurance first")
            hits = m.search(uid, "insurance", top_k=4, bucket="cross_session")
            self.assertTrue(hits)
            self.assertEqual(hits[0]["content"], "ask insurance first")
            m.forget(uid, key, bucket="cross_session")
            self.assertEqual(m.list_active(uid, bucket="cross_session"), [])
        finally:
            m._conn.execute("DELETE FROM memory_entries WHERE user_id=?", (uid,))
            m.close()


if __name__ == "__main__":
    unittest.main()
