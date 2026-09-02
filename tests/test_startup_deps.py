# -*- coding: utf-8 -*-
"""启动依赖体检测试:绝不抛异常;Qdrant 不可用 → qdrant 记 not-ok;本地 SQLite(agent/knowledge/premium)报 ok。"""
from __future__ import annotations

import os
import tempfile
import types
import unittest

from app.retrieval.qdrant_store import QdrantStore
from app.startup_deps import report_startup_dependencies


def _cfg(d: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        sqlite_path=os.path.join(d, "agent.db"),
        knowledge_db_path=os.path.join(d, "knowledge.db"),
        premium_db_path=os.path.join(d, "premium.db"),
        qdrant_url="http://127.0.0.1:1", qdrant_collection="x", embedding_dim=4,
        qdrant_retry_max_tries=0, qdrant_retry_base_delay_ms=0, qdrant_retry_max_delay_ms=0)


class StartupDepsTest(unittest.TestCase):
    def test_down_qdrant_reported_not_ok_and_no_raise(self):
        d = tempfile.mkdtemp()
        cfg = _cfg(d)
        q = QdrantStore("http://127.0.0.1:1", "x", 4,
                        retry_max_tries=0, retry_base_delay_ms=0, retry_max_delay_ms=0)
        res = report_startup_dependencies(cfg, q)   # 不抛异常是硬性要求
        by_name = {r["name"]: r for r in res}
        self.assertEqual(len(res), 4)
        self.assertFalse(by_name["qdrant"]["ok"])                 # Qdrant 连不上 → not ok
        self.assertTrue(by_name["sqlite:agent.db"]["ok"])         # 会话库(SessionStore 由调用方打开)
        self.assertTrue(by_name["sqlite:knowledge.db"]["ok"])     # 空库也能开(建表 + chunks=0)
        self.assertTrue(by_name["sqlite:premium.db"]["ok"])

    def test_report_without_qstore_marks_unchecked(self):
        res = report_startup_dependencies(_cfg(tempfile.mkdtemp()), None)
        self.assertFalse({r["name"]: r for r in res}["qdrant"]["ok"])


if __name__ == "__main__":
    unittest.main()
