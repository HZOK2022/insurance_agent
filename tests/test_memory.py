# -*- coding: utf-8 -*-
"""跨会话记忆(D52)专测:MemoryStore 读写/覆盖/遗忘/检索/注入帧 + 工具 handler + 事件注册 + 非侵入开关。"""
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
             memory_prune_head_chars=200, memory_prune_tail_chars=100, memory_consolidate_min_interval=600,
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
    def test_save_new_and_update_same_key(self):
        sstore, mstore, path = _tmp()
        r1 = mstore.save("agent1", "user", "policy", "policy:保额", "先问已有保障")
        self.assertTrue(r1["is_new"])
        self.assertIsNone(r1["old_text"])
        r2 = mstore.save("agent1", "user", "policy", "policy:保额", "先问已有保障和收入")
        self.assertFalse(r2["is_new"])
        self.assertEqual(r2["old_text"], "先问已有保障")   # 覆盖留旧值
        rows = mstore.list_active("agent1", type_="policy")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["content"], "先问已有保障和收入")
        mstore.close()

    def test_search_relevance_global_and_own(self):
        sstore, mstore, path = _tmp()
        mstore.save("agent1", "user", "lesson", "lesson:等待期", "客户常混淆等待期与犹豫期")
        mstore.save("agent1", "user", "fact", "fact:免赔额", "尊享e生免赔额1万")
        mstore.save("global", "global", "policy", "policy:既往症", "有既往症须提示如实告知")
        hits = mstore.search("agent1", "既往症", top_k=4)   # 命中 global 既往症(验证 global 可见)
        self.assertTrue(any(h["scope"] == "global" for h in hits))
        hits2 = mstore.search("agent1", "等待期", top_k=4)           # 命中自己的
        self.assertTrue(any("等待期" in h["content"] for h in hits2))
        mstore.close()

    def test_forget_marks_archived_and_no_longer_search(self):
        sstore, mstore, path = _tmp()
        mstore.save("agent1", "user", "lesson", "lesson:x", "旧结论")
        ok = mstore.forget("agent1", "lesson:x")
        self.assertTrue(ok)
        hits = mstore.search("agent1", "旧结论", top_k=4)
        self.assertFalse(hits)
        mstore.close()

    def test_count_chars(self):
        sstore, mstore, path = _tmp()
        mstore.save("agent1", "user", "fact", "fact:a", "x" * 100)
        mstore.save("agent1", "user", "fact", "fact:b", "y" * 200)
        self.assertGreaterEqual(mstore.count_chars("agent1"), 300)
        mstore.close()

    def test_inject_frames_redline_first_and_budget(self):
        sstore, mstore, path = _tmp()
        mstore.save("global", "global", "redline", "redline:既往症", "有既往症须提示如实告知并建议人工核保")
        mstore.save("agent1", "user", "preference", "pref:风格", "回答要简洁,先结论后细节" * 60)  # 超单条
        frame = mstore.inject_frames("agent1", inject_tokens=800, entry_max=200, prune_head=100, prune_tail=50)
        self.assertIsNotNone(frame)
        self.assertIn("既往症", frame)
        self.assertIn("[preference]", frame)   # 偏好也注入
        mstore.close()


    def test_consolidate_archives_low_priority_and_protects_redline(self):
        sstore, mstore, path = _tmp()
        mstore.save("agent1", "user", "redline", "redline:a", "x" * 100)   # 红线,永不压
        mstore.save("agent1", "user", "pending", "pending:a", "y" * 100)   # 最低优先,先弃
        mstore.save("agent1", "user", "fact", "fact:a", "z" * 100)
        archived = mstore.consolidate("agent1", target_chars=150)
        self.assertTrue(any(a["type"] == "pending" for a in archived))     # pending 先弃
        act = mstore.list_active("agent1")
        self.assertTrue(any(a["type"] == "redline" for a in act))          # redline 保留
        self.assertLessEqual(mstore.count_chars("agent1"), 150)            # 压回目标(redline 100 保留)
        mstore.close()


class MemoryToolsTest(unittest.TestCase):
    def test_tool_save_writes_event_and_entry(self):
        sstore, mstore, path = _tmp()
        sid = sstore.create_session(user_id="agent1")["id"]
        cfg = _cfg()
        h = _make_save_handler(mstore, sstore, cfg)
        res = h({"key": "policy:保额", "type": "policy", "scope": "user", "content": "先问已有保障"},
                session_id=sid)
        self.assertIn("已新增", res["content"])
        # memory_upsert 事件落库
        evts = sstore.read(sid)
        self.assertTrue(any(e["type"] == "memory_upsert" for e in evts))
        # 表里有条目
        self.assertTrue(mstore.list_active("agent1", type_="policy"))
        mstore.close()

    def test_tool_forget(self):
        sstore, mstore, path = _tmp()
        sid = sstore.create_session(user_id="agent1")["id"]
        mstore.save("agent1", "user", "lesson", "lesson:x", "旧结论")
        h = _make_forget_handler(mstore, sstore, _cfg())
        res = h({"key": "lesson:x", "reason": "过时"}, session_id=sid)
        self.assertIn("已遗忘", res["content"])
        mstore.close()


    def test_tool_save_triggers_consolidate_on_overflow(self):
        sstore, mstore, path = _tmp()
        sid = sstore.create_session(user_id="agent1")["id"]
        cfg = _cfg(memory_total_budget_chars=250, memory_total_budget_target_chars=150)
        h = _make_save_handler(mstore, sstore, cfg)
        h({"key": "pending:a", "type": "pending", "scope": "user", "content": "y" * 100}, session_id=sid)
        h({"key": "fact:a", "type": "fact", "scope": "user", "content": "z" * 100}, session_id=sid)
        res = h({"key": "pending:b", "type": "pending", "scope": "user", "content": "w" * 100}, session_id=sid)  # 超 250
        self.assertIn("压实归档", res["content"])
        self.assertLessEqual(mstore.count_chars("agent1"), 150)   # 压回目标,低优先先弃
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
        # 业务 SYSTEM 原样保留(非侵入:不改 base["system"])
        self.assertEqual(b["system"], "业务SYSTEM")
        mstore.close()

    def test_build_memory_frame_requires_memory_system(self):
        sstore, mstore, path = _tmp()
        cfg = _cfg(sqlite_path=path)
        sid = sstore.create_session(user_id="agent1")["id"]
        b = attach_memory({"system": "s", "tools": {}}, sstore, cfg)
        frame = build_memory_frame(b, sstore, sid, cfg)
        self.assertIsNotNone(frame)
        self.assertIn("跨会话记忆", frame)
        # 未接入 memory 的 bundle -> None
        self.assertIsNone(build_memory_frame({"system": "s", "tools": {}}, sstore, sid, cfg))
        mstore.close()


if __name__ == "__main__":
    unittest.main()
