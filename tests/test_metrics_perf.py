# -*- coding: utf-8 -*-
"""观测性能加固测试:
① TTLCache(进程内 TTL 缓存)行为:命中/过期/关闭/清空;
② /api/metrics SQL 侧聚合(低置信/工具失败/降级)与原 Python 逐行口径一致(SQLite 路径);
③ 方言 SQL 失败 → 自动回退 Python 逐行,结果不变(伪装 mysql 触发);
④ TTL 缓存接入端点:命中不重算(追加事件后仍返回旧值),clear 后刷新。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace

from app.api.routers import metrics as metrics_router
from app.api.services import container
from app.config import Config
from app.session.store import SessionStore
from app.util.ttl_cache import TTLCache


def cfg_with(**kw):
    return replace(Config(), **kw)


def _chunk(cid, score=None):
    """合法检索块(score 可省 → 测"有块无分数"的 low_conf 分支)。"""
    c = {"chunk_id": cid, "doc_id": "doc", "version": "v1", "section": "s1",
         "source": "kb", "content": "正文"}
    if score is not None:
        c["score"] = score
    return c


class TTLCacheTest(unittest.TestCase):
    def test_hit_within_ttl(self):
        c = TTLCache()
        calls = []

        def produce():
            calls.append(1)
            return {"v": len(calls)}

        a = c.get_or_put("k", produce, 60)
        b = c.get_or_put("k", produce, 60)
        self.assertEqual(a, b)
        self.assertEqual(len(calls), 1)  # 命中:producer 只执行一次

    def test_expired_recomputes(self):
        c = TTLCache()
        calls = []

        def produce():
            calls.append(1)
            return len(calls)

        self.assertEqual(c.get_or_put("k", produce, 60), 1)
        ts, val = c._store["k"]
        c._store["k"] = (ts - 61, val)   # 把时间戳拨到过期
        self.assertEqual(c.get_or_put("k", produce, 60), 2)
        self.assertEqual(len(calls), 2)

    def test_ttl_zero_bypasses(self):
        c = TTLCache()
        calls = []

        def produce():
            calls.append(1)
            return len(calls)

        c.get_or_put("k", produce, 0)
        c.get_or_put("k", produce, 0)
        self.assertEqual(len(calls), 2)      # 每次都重算
        self.assertEqual(c._store, {})       # 不落缓存

    def test_clear(self):
        c = TTLCache()
        c.get_or_put("k", lambda: 1, 60)
        c.get_or_put("k2", lambda: 2, 60)
        c.clear()
        self.assertEqual(c._store, {})


class GetMetricsSQLTest(unittest.TestCase):
    """/api/metrics 的 SQL 侧聚合:低置信(no score / max<0.3)、空命中、工具失败、降级。"""

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.path = path
        self.store = SessionStore(path)
        self._orig_cfg = container.get_cfg
        self._orig_store = container.get_store
        # ttl=0:默认关闭缓存,测聚合口径本身
        container.get_cfg = lambda: cfg_with(sqlite_path=path, metrics_cache_ttl_seconds=0)
        container.get_store = lambda: self.store
        metrics_router._cache.clear()

    def tearDown(self):
        container.get_cfg = self._orig_cfg
        container.get_store = self._orig_store
        metrics_router._cache.clear()
        try:
            self.store.close()
        finally:
            try:
                os.remove(self.path)
            except OSError:
                pass

    def _fill(self):
        s = "sessmetrics001"
        # ① 高分检索(0.9)→ 不算低置信
        self.store.append(s, "retrieval", {"query": "q1", "chunks": [_chunk("c1", 0.9)]})
        # ② 低分检索(max 0.2 < 0.3)→ 低置信
        self.store.append(s, "retrieval", {"query": "q2", "chunks": [_chunk("c2", 0.05), _chunk("c3", 0.2)]})
        # ③ 有块但全部无 score → 低置信
        self.store.append(s, "retrieval", {"query": "q3", "chunks": [_chunk("c4"), _chunk("c5")]})
        # ④ 空命中 → no_hits(不算低置信)
        self.store.append(s, "retrieval", {"query": "q4", "chunks": []})
        # 工具结果:失败 2 条(ok=false)、成功 1 条、降级 1 条(error=retrieval_unavailable)
        self.store.append(s, "tool_result", {"tool": "search_knowledge", "ok": False, "error": "tool_error"})
        self.store.append(s, "tool_result", {"tool": "calculate_premium", "ok": False})
        self.store.append(s, "tool_result", {"tool": "search_knowledge", "ok": True})
        self.store.append(s, "tool_result", {"tool": "search_knowledge", "ok": False, "error": "retrieval_unavailable"})
        return s

    def test_low_conf_no_hits_tool_failures(self):
        s = self._fill()
        m = metrics_router.get_metrics()
        self.assertEqual(m["retrieval"]["total"], 4)
        self.assertEqual(m["retrieval"]["no_hits"], 1)
        self.assertEqual(m["retrieval"]["low_conf"], 2)          # ②+③;①高分/④空不计
        self.assertEqual(m["tool_failures"], 3)                  # 3 条 ok=false
        self.assertEqual(m["degradations"], 1)                   # 1 条 retrieval_unavailable
        # 样本:全部落在这个会话
        self.assertEqual(m["samples"]["retrieval_low_conf"], [s])
        self.assertEqual(m["samples"]["tool_failures"], [s])
        self.assertEqual(m["samples"]["degradations"], [s])

    def test_fallback_to_python_on_sql_error(self):
        """聚合 SQL 本身失败(模拟老 MySQL 无 JSON_TABLE / 极老 SQLite 无 json_each)→ 回退 Python 逐行,结果一致。"""
        s = self._fill()
        orig_exec = self.store._conn.execute

        def broken_exec(sql, params=()):
            if "json_each" in sql:   # 只炸"库内展开 chunks"那条聚合 SQL
                raise RuntimeError("simulated: json_each/JSON_TABLE unsupported")
            return orig_exec(sql, params)

        self.store._conn.execute = broken_exec
        try:
            m = metrics_router.get_metrics()
        finally:
            self.store._conn.execute = orig_exec
        self.assertEqual(m["retrieval"]["low_conf"], 2)
        self.assertEqual(m["tool_failures"], 3)
        self.assertEqual(m["degradations"], 1)
        self.assertEqual(m["samples"]["retrieval_low_conf"], [s])

    def test_sqlite_sql_matches_python(self):
        """双口径对拍:json_each 库内聚合 vs Python 逐行,计数与样本必须完全一致(SQL 改写不改语义)。"""
        s = self._fill()
        conn = self.store._conn
        c_sql, s_sql = metrics_router._low_conf_sqlite(conn, 0.3)
        c_py, s_py = metrics_router._low_conf_python(conn, 0.3)
        self.assertEqual(c_sql, c_py)
        self.assertEqual(s_sql, s_py)
        self.assertEqual(c_sql, 2)
        self.assertEqual(s_sql, [s])

    def test_low_conf_samples_cap_five(self):
        """样本最多 5 个不同会话(第 6 个低置信会话不进样本,但计数照加)。"""
        for i in range(7):
            sid = f"sesslow{i:03d}"
            self.store.append(sid, "retrieval", {"query": "q", "chunks": [_chunk("c", 0.1)]})
        m = metrics_router.get_metrics()
        self.assertEqual(m["retrieval"]["low_conf"], 7)
        self.assertEqual(len(m["samples"]["retrieval_low_conf"]), 5)


class MetricsCacheIntegrationTest(unittest.TestCase):
    """TTL 缓存接入 /api/metrics:命中不重算;ttl=0 时每次实时。"""

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.path = path
        self.store = SessionStore(path)
        self._orig_cfg = container.get_cfg
        self._orig_store = container.get_store
        container.get_store = lambda: self.store
        metrics_router._cache.clear()

    def tearDown(self):
        container.get_cfg = self._orig_cfg
        container.get_store = self._orig_store
        metrics_router._cache.clear()
        try:
            self.store.close()
        finally:
            try:
                os.remove(self.path)
            except OSError:
                pass

    def test_cached_and_refresh(self):
        container.get_cfg = lambda: cfg_with(sqlite_path=self.path, metrics_cache_ttl_seconds=60)
        self.store.append("scache00001", "retrieval", {"query": "q", "chunks": [_chunk("c", 0.9)]})
        m1 = metrics_router.get_metrics()
        # 缓存窗口内追加新事件 → 仍返回缓存旧值
        self.store.append("scache00001", "retrieval", {"query": "q2", "chunks": []})
        m2 = metrics_router.get_metrics()
        self.assertEqual(m2["retrieval"]["total"], m1["retrieval"]["total"])
        # 清缓存 → 刷新出真实值
        metrics_router._cache.clear()
        m3 = metrics_router.get_metrics()
        self.assertEqual(m3["retrieval"]["total"], m1["retrieval"]["total"] + 1)

    def test_ttl_zero_always_fresh(self):
        container.get_cfg = lambda: cfg_with(sqlite_path=self.path, metrics_cache_ttl_seconds=0)
        self.store.append("sfresh00001", "retrieval", {"query": "q", "chunks": [_chunk("c", 0.9)]})
        m1 = metrics_router.get_metrics()
        self.store.append("sfresh00001", "retrieval", {"query": "q2", "chunks": []})
        m2 = metrics_router.get_metrics()
        self.assertEqual(m2["retrieval"]["total"], m1["retrieval"]["total"] + 1)


if __name__ == "__main__":
    unittest.main()
