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

# 三桶:标签 + 每桶 type 优先级(高→低);压缩时先归档低优先级
_BUCKET_TAGS = {"user": "user_memory", "cross_session": "cross_session_memory", "session": "session_memory"}
_BUCKET_PRIO = {
    "user": {"habit": 0, "profile": 1, "preference": 2},
    "cross_session": {"pending": 0, "lesson": 1, "fact": 2, "policy": 3, "redline": 4},
    "session": {"context": 0, "instruction": 1},
}


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

    # bucket: 'user' | 'cross_session' | 'session';session 桶 scope='session:<sid>'
    def _scope_for(self, bucket: str, scope_or_user: str | None, session_id: str | None) -> str:
        if bucket == "user":
            return "user"
        if bucket == "session":
            return "session:" + (session_id or "")
        return scope_or_user or "user"   # cross_session: global(主管)| user(个人)

    # ---- 写 / 更新(同 user_id+bucket+type+key 覆盖;session 桶 + source_session_id)----
    def save(self, user_id: str, bucket: str, type_: str, key: str, content: str,
             scope: str | None = None, confidence: str = "auto", source_session_id: str | None = None,
             source_event_seq: int | None = None) -> dict:
        now = utcnow()
        sc = self._scope_for(bucket, scope, source_session_id)
        row = self._conn.execute(
            "SELECT id, content FROM memory_entries WHERE user_id=? AND bucket=? AND type=? AND key=? AND scope=?",
            (user_id, bucket, type_, key, sc)).fetchone()
        if row:
            old = row["content"]
            self._conn.execute(
                "UPDATE memory_entries SET content=?, confidence=?, updated_at=? WHERE id=?",
                (content, confidence, now, row["id"]))
            self._conn.commit()
            return {"entry_id": row["id"], "is_new": False, "old_text": old, "key": key, "type": type_, "bucket": bucket}
        eid = uuid.uuid4().hex[:12]
        self._conn.execute(
            "INSERT INTO memory_entries (id,user_id,bucket,scope,type,key,content,status,confidence,"
            "source_session_id,source_event_seq,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, user_id, bucket, sc, type_, key, content, "active", confidence,
             source_session_id, source_event_seq, now, now))
        self._conn.commit()
        return {"entry_id": eid, "is_new": True, "old_text": None, "key": key, "type": type_, "bucket": bucket}

    # ---- 检索 ----
    def search(self, user_id: str, query: str, top_k: int = 4, bucket: str | None = None,
               session_id: str | None = None) -> list[dict]:
        q = "SELECT id,user_id,bucket,scope,type,key,content,status,confidence,updated_at FROM memory_entries " \
            "WHERE status='active' AND (scope='global' OR user_id=?)"
        params: list[Any] = [user_id]
        if bucket == "session":
            q += " AND bucket='session' AND source_session_id=?"
            params.append(session_id or "")
        elif bucket:
            q += " AND bucket=?"
            params.append(bucket)
        else:
            q += " AND bucket != 'session'"   # 默认搜持久(user+跨会话),session 靠注入不靠 search
        rows = self._conn.execute(q, params).fetchall()
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
    def forget(self, user_id: str, key: str, reason: str | None = None, bucket: str | None = None,
               session_id: str | None = None) -> bool:
        now = utcnow()
        q = "UPDATE memory_entries SET status='archived', updated_at=? WHERE user_id=? AND key=? AND status='active'"
        params: list[Any] = [now, user_id, key]
        if bucket == "session":
            q += " AND bucket='session' AND source_session_id=?"
            params.append(session_id or "")
        elif bucket:
            q += " AND bucket=?"
            params.append(bucket)
        cur = self._conn.execute(q, params)
        self._conn.commit()
        return cur.rowcount > 0

    # ---- 读(bucket 维度)----
    def list_active(self, user_id: str, bucket: str | None = None, type_: str | None = None,
                    session_id: str | None = None) -> list[dict]:
        q = "SELECT id,user_id,bucket,scope,type,key,content,status,confidence,updated_at FROM memory_entries " \
            "WHERE status='active' AND (scope='global' OR user_id=?)"
        params: list[Any] = [user_id]
        if bucket == "session":
            q += " AND bucket='session' AND source_session_id=?"
            params.append(session_id or "")
        elif bucket:
            q += " AND bucket=?"
            params.append(bucket)
        if type_:
            q += " AND type=?"
            params.append(type_)
        q += " ORDER BY updated_at DESC"
        return [dict(r) for r in self._conn.execute(q, params).fetchall()]

    def count_chars(self, user_id: str, bucket: str | None = None, session_id: str | None = None) -> int:
        q = "SELECT content FROM memory_entries WHERE status='active' AND (scope='global' OR user_id=?)"
        params: list[Any] = [user_id]
        if bucket == "session":
            q += " AND bucket='session' AND source_session_id=?"
            params.append(session_id or "")
        elif bucket:
            q += " AND bucket=?"
            params.append(bucket)
        rows = self._conn.execute(q, params).fetchall()
        return sum(len(r["content"]) for r in rows)

    def bucket_entries(self, user_id: str, bucket: str, session_id: str | None = None,
                       type_: str | None = None) -> list[dict]:
        """取某桶 active 条目,按优先级排序(global/redline 在前,同档长条在后)。"""
        rows = self.list_active(user_id, bucket=bucket, type_=type_, session_id=session_id)
        prio = _BUCKET_PRIO.get(bucket, {})
        def key(r):
            return (0 if r.get("scope") == "global" else 1, -prio.get(r.get("type"), 0), -len(r["content"]))
        return sorted(rows, key=key)

    def compact_bucket(self, user_id: str, bucket: str, target_chars: int, session_id: str | None = None) -> list[dict]:
        """规则压缩某桶:按 type 优先级从低到高归档(redline/global 永不归档)直至回到 target_chars,
        再对留下的超长单条做头尾剪枝。返回归档列表。"""
        entries = self.bucket_entries(user_id, bucket, session_id)
        total = self.count_chars(user_id, bucket=bucket, session_id=session_id)
        prio = _BUCKET_PRIO.get(bucket, {})
        removable = [e for e in entries if e["type"] != "redline" and e.get("scope") != "global"]
        removable.sort(key=lambda e: (prio.get(e["type"], 0), -len(e["content"])))
        archived: list[dict] = []
        for e in removable:
            if total <= target_chars:
                break
            self._conn.execute("UPDATE memory_entries SET status='archived', updated_at=? WHERE id=?",
                               (utcnow(), e["id"]))
            self._conn.commit()
            total -= len(e["content"])
            archived.append({"id": e["id"], "key": e["key"], "type": e["type"]})
        for e in self.bucket_entries(user_id, bucket, session_id):
            if len(e["content"]) > 200:
                trimmed = prune_memory_content(e["content"], 140, 60)
                if trimmed:
                    self._conn.execute("UPDATE memory_entries SET content=? WHERE id=?", (trimmed, e["id"]))
                    self._conn.commit()
        return archived

    def bucket_frame(self, user_id: str, bucket: str, session_id: str | None = None,
                     limit_chars: int = 2000) -> str:
        """构建某桶注入帧:<tag>…- [type] key: content…</tag>;总长超 limit 时先压缩到 30%。"""
        tag = _BUCKET_TAGS.get(bucket, bucket)
        entries = self.bucket_entries(user_id, bucket, session_id)
        if not entries:
            return ""
        total = sum(len(e["content"]) for e in entries)
        if total > limit_chars:
            self.compact_bucket(user_id, bucket, int(limit_chars * 0.3), session_id)   # 压缩到 30%
            entries = self.bucket_entries(user_id, bucket, session_id)
        lines = [f"- [{e['type']}] {e['key']}: {e['content']}" for e in entries]
        return f"<{tag}>\n" + "\n".join(lines) + f"\n</{tag}>"

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
