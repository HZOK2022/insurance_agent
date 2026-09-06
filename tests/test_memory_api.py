# -*- coding: utf-8 -*-
"""记忆管理面板(P2.3):/api/memory GET/POST/DELETE + 压实 端点测试(TestClient + 容器替换)。"""
from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace

from fastapi.testclient import TestClient

from app import main as appmod
from app.api.services import container
from app.config import Config
from app.memory.store import MemoryStore
from app.session.store import SessionStore


def cfg_with(**kw):
    return replace(Config(), **kw)


class MemoryApiTest(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.path = path
        self.store = SessionStore(path)
        self.mstore = MemoryStore(path)
        self.client = TestClient(appmod.app)
        self._orig_cfg = container.get_cfg
        self._orig_mem = container.get_memory_store
        # 测试态:开发模式(无全局鉴权)+ 记忆开启 + 该临时库;memory_store 指向同一库
        container.get_cfg = lambda: cfg_with(sqlite_path=path, memory_enabled=True,
                                             memory_bucket_limit_chars=2000, api_token="")
        container.get_memory_store = lambda: self.mstore

    def tearDown(self):
        container.get_cfg = self._orig_cfg
        container.get_memory_store = self._orig_mem
        try:
            self.mstore.close()
            self.store.close()
        finally:
            try:
                os.remove(self.path)
            except OSError:
                pass

    # ---- 读 ----
    def test_list_empty_and_seeded(self):
        r = self.client.get("/api/memory")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["user_id"], "u1")
        # 空库:各桶类别空
        self.assertEqual(d["buckets"]["user"], [])
        self.assertEqual(d["buckets"]["cross_session"], [])
        # 种数据后再读
        self.mstore.save("u1", "user", "preference", "称呼", "叫我大哥")
        self.mstore.save("u1", "cross_session", "fact", "fact:免赔额", "尊享e生免赔额1万")
        d2 = self.client.get("/api/memory").json()
        self.assertEqual(len(d2["buckets"]["user"]), 1)
        self.assertEqual(len(d2["buckets"]["cross_session"]), 1)
        self.assertIn("<user_memory>", d2["frames"]["user"])

    def test_list_session_bucket_only_with_session_id(self):
        sid = self.store.create_session(user_id="u1")["id"]
        self.mstore.save("u1", "session", "instruction", "范围", "本会话基于尊享e生2025", source_session_id=sid)
        d = self.client.get("/api/memory").json()
        self.assertNotIn("session", d["buckets"])   # 未传 session_id 不含会话桶
        d2 = self.client.get("/api/memory?session_id=" + sid).json()
        self.assertEqual(len(d2["buckets"]["session"]), 1)
        self.assertIn("<session_memory>", d2["frames"]["session"])

    def test_disabled_returns_empty(self):
        orig = container.get_cfg
        container.get_cfg = lambda: cfg_with(sqlite_path=self.path, memory_enabled=False, api_token="")
        try:
            d = self.client.get("/api/memory").json()
            self.assertFalse(d["enabled"])
            self.assertEqual(d["buckets"], {})
        finally:
            container.get_cfg = orig

    # ---- 写 ----
    def test_save_routes_by_target(self):
        r = self.client.post("/api/memory", json={"target": "cross_session", "category": "fact",
                                                  "key": "fact:免赔额", "content": "尊享e生免赔额1万"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["bucket"], "cross_session")
        self.assertTrue(self.mstore.list_active("u1", bucket="cross_session"))

    def test_save_rejects_bad_category(self):
        r = self.client.post("/api/memory", json={"target": "user", "category": "fact", "key": "k", "content": "c"})
        self.assertEqual(r.status_code, 400)

    def test_save_session_requires_session_id(self):
        r = self.client.post("/api/memory", json={"target": "session", "category": "instruction",
                                                  "key": "范围", "content": "本会话基于X"})
        self.assertEqual(r.status_code, 400)

    def test_delete_forgets(self):
        self.mstore.save("u1", "cross_session", "lesson", "lesson:x", "旧结论")
        r = self.client.delete("/api/memory?target=cross_session&key=lesson:x")
        self.assertEqual(r.json()["ok"], True)
        self.assertFalse(self.mstore.search("u1", "旧结论", top_k=4))

    def test_compact_over_limit(self):
        orig = container.get_cfg
        container.get_cfg = lambda: cfg_with(sqlite_path=self.path, memory_enabled=True,
                                             memory_bucket_limit_chars=300, api_token="")
        try:
            for i in range(12):
                self.client.post("/api/memory", json={"target": "cross_session", "category": "fact",
                                                      "key": f"f:{i}", "content": "z" * 200})
            # 累计 2400 字,桶内压回 ≤ 300(自压实);再手动压实返回 0 条归档(已在阈值内)
            self.assertLessEqual(self.mstore.count_chars("u1", bucket="cross_session"), 300)
            r = self.client.post("/api/memory/compact?target=cross_session")
            self.assertEqual(r.json()["ok"], True)
        finally:
            container.get_cfg = orig


if __name__ == "__main__":
    unittest.main()
