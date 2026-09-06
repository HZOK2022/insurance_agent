# -*- coding: utf-8 -*-
"""记忆系统(D52,D73 扩展)专测:三桶(用户/跨会话/会话)读写/覆盖/遗忘/检索/桶帧/每桶压缩 + 工具 target + 事件 + 非侵入。"""
from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace
import unittest

from app.session.store import SessionStore
from app.session import events
from app.memory.store import MemoryStore
from app.memory.tools import attach_memory, build_memory_frame, _make_save_handler, _make_forget_handler


def _cfg(**over):
    d = dict(memory_enabled=True, memory_entry_max_chars=500, memory_total_budget_chars=3000,
             memory_total_budget_target_chars=2000, memory_inject_max_tokens=800, memory_search_top_k=4,
             memory_prune_head_chars=200, memory_prune_tail_chars=100, memory_bucket_limit_chars=2000,
             sqlite_path="")
    d.update(over)
    return SimpleNamespace(**d)


def _tmp():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    sstore = SessionStore(path)   # 建 memory_entries 表
    mstore = MemoryStore(path)
    return sstore, mstore, path


class MemoryStoreTest(unittest.TestCase):
    # save(user_id, bucket, type_, key, content, scope=None, ...)
    def test_save_new_and_update_same_key(self):
        sstore, mstore, path = _tmp()
        r1 = mstore.save("agent1", "cross_session", "policy", "policy:保额", "先问已有保障")
        self.assertTrue(r1["is_new"])
        self.assertEqual(r1["bucket"], "cross_session")
        r2 = mstore.save("agent1", "cross_session", "policy", "policy:保额", "先问已有保障和收入")
        self.assertFalse(r2["is_new"])
        self.assertEqual(r2["old_text"], "先问已有保障")   # 覆盖留旧值
        rows = mstore.list_active("agent1", bucket="cross_session", type_="policy")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["content"], "先问已有保障和收入")
        mstore.close()

    def test_user_vs_cross_session_same_key_not_collide(self):
        # 同样是 key=称呼,user 桶(偏好)与 cross_session 桶(策略)不冲突
        sstore, mstore, path = _tmp()
        mstore.save("agent1", "user", "preference", "称呼", "叫我大哥")
        mstore.save("agent1", "cross_session", "lesson", "称呼", "点名时先确认身份")
        u = mstore.list_active("agent1", bucket="user")
        c = mstore.list_active("agent1", bucket="cross_session")
        self.assertEqual(len(u), 1)
        self.assertEqual(len(c), 1)
        self.assertNotEqual(u[0]["content"], c[0]["content"])
        mstore.close()

    def test_session_bucket_scoped_to_session(self):
        sstore, mstore, path = _tmp()
        s1 = sstore.create_session(user_id="agent1")["id"]
        s2 = sstore.create_session(user_id="agent1")["id"]
        mstore.save("agent1", "session", "instruction", "产品范围", "本会话基于尊享e生2025", source_session_id=s1)
        mstore.save("agent1", "session", "instruction", "产品范围", "本会话基于安盛", source_session_id=s2)
        self.assertEqual(len(mstore.list_active("agent1", bucket="session", session_id=s1)), 1)
        self.assertEqual(mstore.list_active("agent1", bucket="session", session_id=s2)[0]["content"], "本会话基于安盛")
        # session 记忆不进入默认持久检索
        hits = mstore.search("agent1", "尊享e生", top_k=4)
        self.assertFalse(any(h["bucket"] == "session" for h in hits))
        mstore.close()

    def test_search_relevance_global_and_own(self):
        sstore, mstore, path = _tmp()
        mstore.save("agent1", "cross_session", "lesson", "lesson:等待期", "客户常混淆等待期与犹豫期")
        mstore.save("agent1", "cross_session", "fact", "fact:免赔额", "尊享e生免赔额1万")
        mstore.save("global", "cross_session", "policy", "policy:既往症", "有既往症须提示如实告知", scope="global")
        hits = mstore.search("agent1", "既往症", top_k=4)   # 命中 global 既往症
        self.assertTrue(any(h["scope"] == "global" for h in hits))
        hits2 = mstore.search("agent1", "等待期", top_k=4)
        self.assertTrue(any("等待期" in h["content"] for h in hits2))
        mstore.close()

    def test_forget_by_bucket_marks_archived(self):
        sstore, mstore, path = _tmp()
        mstore.save("agent1", "cross_session", "lesson", "lesson:x", "旧结论")
        ok = mstore.forget("agent1", "lesson:x", bucket="cross_session")
        self.assertTrue(ok)
        self.assertFalse(mstore.search("agent1", "旧结论", top_k=4))
        mstore.close()

    def test_count_chars_per_bucket(self):
        sstore, mstore, path = _tmp()
        mstore.save("agent1", "user", "preference", "p:a", "x" * 100)
        mstore.save("agent1", "cross_session", "fact", "f:a", "y" * 200)
        self.assertGreaterEqual(mstore.count_chars("agent1", bucket="user"), 100)
        self.assertGreaterEqual(mstore.count_chars("agent1", bucket="cross_session"), 200)
        mstore.close()

    def test_bucket_frame_tags_and_compact(self):
        # 桶帧带标签;超 limit 压到 30%
        sstore, mstore, path = _tmp()
        mstore.save("agent1", "user", "preference", "称呼", "回答前叫我大哥")
        mstore.save("agent1", "cross_session", "lesson", "lesson:等待期", "先区分等待期与犹豫期")
        mstore.save("agent1", "cross_session", "policy", "policy:既往症", "须提示如实告知", scope="global")
        fu = mstore.bucket_frame("agent1", "user", limit_chars=2000)
        self.assertIn("<user_memory>", fu)
        self.assertIn("[preference]", fu)
        fc = mstore.bucket_frame("agent1", "cross_session", limit_chars=2000)
        self.assertIn("<cross_session_memory>", fc)
        self.assertIn("既往症", fc)
        mstore.close()

    def test_compact_bucket_archives_low_priority_keeps_redline(self):
        sstore, mstore, path = _tmp()
        for i in range(12):
            mstore.save("agent1", "cross_session", "policy", f"policy:{i}", "z" * 200)   # 高优,保留
        mstore.save("agent1", "cross_session", "pending", "pending:x", "y" * 200)        # 最低优先,先弃
        mstore.save("agent1", "cross_session", "redline", "redline:a", "x" * 200)        # 红线,永不压
        archived = mstore.compact_bucket("agent1", "cross_session", target_chars=400)
        self.assertTrue(any(a["type"] == "pending" for a in archived), "pending 应最先归档")
        act = mstore.list_active("agent1", bucket="cross_session")
        self.assertTrue(any(a["type"] == "redline" for a in act), "redline 保留")
        mstore.close()


class MemoryToolsTest(unittest.TestCase):
    def test_tool_save_writes_event_and_entry(self):
        sstore, mstore, path = _tmp()
        sid = sstore.create_session(user_id="agent1")["id"]
        h = _make_save_handler(mstore, sstore, _cfg())
        res = h({"target": "cross_session", "category": "policy", "key": "policy:保额", "content": "先问已有保障"},
                session_id=sid)
        self.assertIn("跨会话记忆", res["content"])
        evts = sstore.read(sid)
        self.assertTrue(any(e["type"] == "memory_upsert" and e["payload"].get("key") == "policy:保额" for e in evts))
        self.assertTrue(mstore.list_active("agent1", bucket="cross_session", type_="policy"))
        mstore.close()

    def test_tool_save_user_and_session_target(self):
        sstore, mstore, path = _tmp()
        sid = sstore.create_session(user_id="agent1")["id"]
        h = _make_save_handler(mstore, sstore, _cfg())
        h({"target": "user", "category": "preference", "key": "称呼", "content": "叫我大哥"}, session_id=sid)
        h({"target": "session", "category": "instruction", "key": "产品范围", "content": "本会话基于尊享e生2025"}, session_id=sid)
        self.assertTrue(mstore.list_active("agent1", bucket="user"))
        self.assertTrue(mstore.list_active("agent1", bucket="session", session_id=sid))
        mstore.close()

    def test_tool_save_rejects_bad_category(self):
        sstore, mstore, path = _tmp()
        sid = sstore.create_session(user_id="agent1")["id"]
        h = _make_save_handler(mstore, sstore, _cfg())
        res = h({"target": "user", "category": "fact", "key": "k", "content": "c"}, session_id=sid)
        self.assertIn("不适用", res["content"])
        mstore.close()

    def test_tool_forget(self):
        sstore, mstore, path = _tmp()
        sid = sstore.create_session(user_id="agent1")["id"]
        mstore.save("agent1", "cross_session", "lesson", "lesson:x", "旧结论")
        h = _make_forget_handler(mstore, sstore, _cfg())
        res = h({"target": "cross_session", "key": "lesson:x", "reason": "过时"}, session_id=sid)
        self.assertIn("已遗忘", res["content"])
        mstore.close()

    def test_tool_save_triggers_bucket_compact_on_overflow(self):
        # 某桶超 memory_bucket_limit_chars → 压实到 30%
        sstore, mstore, path = _tmp()
        sid = sstore.create_session(user_id="agent1")["id"]
        cfg = _cfg(memory_bucket_limit_chars=300)
        h = _make_save_handler(mstore, sstore, cfg)
        for i in range(15):
            h({"target": "cross_session", "category": "fact", "key": f"f:{i}", "content": "z" * 100}, session_id=sid)
        # 累计写入 1500+ 字符,但桶内被压回 ≤ 上限 300(压实有效)
        self.assertLessEqual(mstore.count_chars("agent1", bucket="cross_session"), 300)
        # 压实确实发生:存在"桶超上限自动压实"的归档事件
        evts = sstore.read(sid)
        self.assertTrue(any(e["type"] == "memory_archive" and e["payload"].get("reason") == "桶超上限自动压实"
                            for e in evts))
        mstore.close()


class MemoryNonInvasiveTest(unittest.TestCase):
    def test_event_types_registered(self):
        kt = events.known_types()
        self.assertIn("memory_upsert", kt)
        self.assertIn("memory_archive", kt)
        self.assertIn("memory_injected", kt)

    def test_attach_memory_adds_tools_and_system(self):
        sstore, mstore, path = _tmp()
        cfg = _cfg(sqlite_path=path)
        base = {"system": "业务SYSTEM", "tools": {"search_knowledge": {"schema": {}, "handler": lambda *a: {}}}}
        b = attach_memory(base, sstore, cfg)
        self.assertIn("memory_system", b)
        self.assertIn("memory_save", b["tools"])
        self.assertIn("memory_search", b["tools"])
        self.assertIn("memory_forget", b["tools"])
        self.assertEqual(b["system"], "业务SYSTEM")   # 业务 SYSTEM 原样保留(非侵入)
        mstore.close()

    def test_build_memory_frame_includes_three_buckets(self):
        sstore, mstore, path = _tmp()
        cfg = _cfg(sqlite_path=path)
        sid = sstore.create_session(user_id="agent1")["id"]
        mstore.save("agent1", "user", "preference", "称呼", "叫我大哥")
        mstore.save("agent1", "cross_session", "lesson", "lesson:x", "旧结论")
        mstore.save("agent1", "session", "instruction", "产品范围", "本会话基于尊享e生2025", source_session_id=sid)
        b = attach_memory({"system": "s", "tools": {}}, sstore, cfg)
        frame = build_memory_frame(b, sstore, sid, cfg)
        self.assertIsNotNone(frame)
        self.assertIn("<user_memory>", frame)
        self.assertIn("<cross_session_memory>", frame)
        self.assertIn("<session_memory>", frame)
        self.assertIn("叫我大哥", frame)
        mstore.close()


if __name__ == "__main__":
    unittest.main()
