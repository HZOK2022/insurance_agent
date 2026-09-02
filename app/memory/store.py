# -*- coding: utf-8 -*-
"""跨会话记忆存储层(D52)。memory_entries 表读写;独立于 SessionStore,同 agent.db(SQLite 单写者)。

黄金法则:SQLite=事实源。记忆存 SQLite,不落散文件。表由 SessionStore._ddl 建(见 app/session/store.py),
本模块只负责读写;每次写/忘由工具层追加 memory_upsert/archive 事件到 events 表(可审计)。
"""
from __future__ import annotations

import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# 记忆优先级(高→低):redline 永不压;归档从最低档(pending)开始
_PRIORITY_ORDER = {"pending": 0, "lesson": 1, "fact": 2, "preference": 3, "policy": 4, "redline": 5}


def _features(q: str) -> set[str]:
    """query 检索特征:英文词(>=2 字符)+ 连续中文段逐字。简单重叠打分。"""
    feats: set[str] = set()
    for m in re.findall(r"[A-Za-z0-9]{2,}", q):
        feats.add(m.lower())
    for seg in re.findall(r"[\u4e00-\u9fff]+", q):
        for ch in seg:
            feats.add(ch)
    return feats


def prune_memory_content(content: str, head_chars: int, tail_chars: int) -> str | None:
    """单条记忆剪枝:保头(主题)+ 保尾(关键数字/结论),压中段。超限才剪;不足返回 None。"""
    if head_chars <= 0 or len(content) <= head_chars + tail_chars:
        return None
    return content[:head_chars] + "…(已省略)…" + content[-tail_chars:]


class MemoryStore:
    """memory_entries 表读写。每个操作都 commit(WAL 单写者)。"""

    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")

    def close(self) -> None:
        self._conn.close()

    # ---- 写 / 更新(同 user_id+scope+type+key 覆盖)----
    def save(self, user_id: str, scope: str, type_: str, key: str, content: str,
             confidence: str = "auto", source_session_id: str | None = None,
             source_event_seq: int | None = None) -> dict:
        now = utcnow()
        row = self._conn.execute(
            "SELECT id, content FROM memory_entries WHERE user_id=? AND scope=? AND type=? AND key=?",
            (user_id, scope, type_, key)).fetchone()
        if row:
            old = row["content"]
            self._conn.execute(
                "UPDATE memory_entries SET content=?, confidence=?, updated_at=? WHERE id=?",
                (content, confidence, now, row["id"]))
            self._conn.commit()
            return {"entry_id": row["id"], "is_new": False, "old_text": old, "key": key, "type": type_}
        eid = uuid.uuid4().hex[:12]
        self._conn.execute(
            "INSERT INTO memory_entries (id,user_id,scope,type,key,content,status,confidence,"
            "source_session_id,source_event_seq,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, user_id, scope, type_, key, content, "active", confidence,
             source_session_id, source_event_seq, now, now))
        self._conn.commit()
        return {"entry_id": eid, "is_new": True, "old_text": None, "key": key, "type": type_}

    # ---- 检索 ----
    def search(self, user_id: str, query: str, top_k: int = 4) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id,user_id,scope,type,key,content,status,confidence,updated_at FROM memory_entries "
            "WHERE status='active' AND (scope='global' OR user_id=?)", (user_id,)).fetchall()
        feats = _features(query)
        scored: list[tuple[int, dict]] = []
        for r in rows:
            d = dict(r)
            hit = sum(1 for f in feats if f and f in d["content"])
            if hit:
                scored.append((hit, d))
        scored.sort(key=lambda x: -x[0])
        return [d for _, d in scored[:top_k]]

    # ---- 遗忘(标记,不物理删)----
    def forget(self, user_id: str, key: str, reason: str | None = None, scope: str | None = None) -> bool:
        now = utcnow()
        cur = self._conn.execute(
            "UPDATE memory_entries SET status='archived', updated_at=? "
            "WHERE user_id=? AND key=? AND status='active' AND (scope=? OR ? IS NULL)",
            (now, user_id, key, scope, scope))
        self._conn.commit()
        return cur.rowcount > 0

    # ---- 读 ----
    def list_active(self, user_id: str, scope: str | None = None, type_: str | None = None) -> list[dict]:
        q = "SELECT id,user_id,scope,type,key,content,status,confidence,updated_at FROM memory_entries "             "WHERE status='active' AND (scope='global' OR user_id=?)"
        params: list[Any] = [user_id]
        if scope:
            q += " AND scope=?"
            params.append(scope)
        if type_:
            q += " AND type=?"
            params.append(type_)
        q += " ORDER BY updated_at DESC"
        return [dict(r) for r in self._conn.execute(q, params).fetchall()]

    def count_chars(self, user_id: str) -> int:
        rows = self._conn.execute(
            "SELECT content FROM memory_entries WHERE status='active' AND (scope='global' OR user_id=?)",
            (user_id,)).fetchall()
        return sum(len(r["content"]) for r in rows)

    # ---- 程序级保守压实(D53):按优先级从低到高归档该客服 user 级非 redline,直到总量回到预算 ----
    # global 由主管维护,不程序归档;redline 永不压(安全底线)。
    def consolidate(self, user_id: str, target_chars: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id,type,key,content FROM memory_entries WHERE status='active' "
            "AND user_id=? AND scope='user' AND type!='redline'", (user_id,)).fetchall()
        # 归档顺序 = 优先级从低到高(pending/lesson/fact/preference/policy),同档长条先用
        rows = sorted(rows, key=lambda r: (_PRIORITY_ORDER.get(r["type"], 0), -len(r["content"])))
        total = self.count_chars(user_id)
        archived: list[dict] = []
        for r in rows:
            if total <= target_chars:
                break
            self._conn.execute("UPDATE memory_entries SET status='archived', updated_at=? WHERE id=?",
                               (utcnow(), r["id"]))
            self._conn.commit()
            total -= len(r["content"])
            archived.append({"id": r["id"], "key": r["key"], "type": r["type"]})
        return archived

    # ---- 常驻注入(红线/偏好/口径,按 token 预算取高优)----
    def inject_frames(self, user_id: str, inject_tokens: int, entry_max: int,
                      prune_head: int, prune_tail: int) -> str | None:
        rows = self._conn.execute(
            "SELECT type,key,content FROM memory_entries WHERE status='active' "
            "AND (scope='global' OR user_id=?) AND type IN ('redline','preference','policy') "
            "ORDER BY CASE type WHEN 'redline' THEN 0 WHEN 'policy' THEN 1 ELSE 2 END, updated_at DESC",
            (user_id,)).fetchall()
        parts: list[str] = []
        used = 0
        for r in rows:
            content = r["content"]
            if len(content) > entry_max:
                content = prune_memory_content(content, prune_head, prune_tail) or content
            est = max(1, len(content) // 2)   # 中文粗略 1 字≈0.5 token
            if used + est > inject_tokens:
                continue
            used += est
            parts.append(f"- [{r['type']}] {r['key']}: {content}")
        if not parts:
            return None
        return "【跨会话记忆·常驻(直接遵守,勿复述)】\n" + "\n".join(parts)
