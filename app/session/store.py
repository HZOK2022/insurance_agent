"""SQLite append-only 会话事件存储 + 会话元数据。参照 dsh core/session + session-persistence-sqlite。

规则(AGENTS.md):
- WAL + busy_timeout;单写者
- events 只 INSERT,绝不 UPDATE / DELETE 历史(不暴露改写方法)
- schema 版本 fail-closed;未注册事件类型 → 拒绝
"""
from __future__ import annotations
import json
import sqlite3
import uuid

from . import events, title

SCHEMA_VERSION = 1


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


class SessionStore:
    """SQLite 事实源。events(append-only)+ sessions(元数据)。"""

    def __init__(self, path: str):
        self.path = path
        self._conn = _connect(path)
        self._ensure_schema()
        self._check_schema()

    def _ddl(self) -> str:
        return f"""
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', '{SCHEMA_VERSION}');
        CREATE TABLE IF NOT EXISTS events (
          seq INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id TEXT NOT NULL, type TEXT NOT NULL, ts TEXT NOT NULL, payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, seq);
        CREATE TABLE IF NOT EXISTS sessions (
          id TEXT PRIMARY KEY, title TEXT, user_id TEXT, created_at TEXT, status TEXT DEFAULT 'active',
          deleted INTEGER DEFAULT 0
        );
        """

    def _ensure_schema(self) -> None:
        self._conn.executescript(self._ddl())
        # 增量迁移:既有库的 sessions 若缺 deleted 列,补上(软删除用;events 仍 append-only)
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "deleted" not in cols:
            self._conn.execute("ALTER TABLE sessions ADD COLUMN deleted INTEGER DEFAULT 0")
        self._conn.commit()

    def _check_schema(self) -> None:
        row = self._conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if row is None or int(row["value"]) != SCHEMA_VERSION:
            have = row["value"] if row else "?"
            raise RuntimeError(f"schema 版本不匹配: 库={have} 代码={SCHEMA_VERSION} —— 拒绝启动")
        for r in self._conn.execute("SELECT DISTINCT type FROM events").fetchall():
            if r["type"] not in events.known_types():
                raise events.UnknownEventError(r["type"])

    # ---- events(append-only) ----
    def append(self, session_id: str, type_: str, payload: dict) -> int:
        ev = events.make_event(type_, payload)
        cur = self._conn.execute(
            "INSERT INTO events (session_id, type, ts, payload) VALUES (?,?,?,?)",
            (session_id, ev["type"], ev["ts"], json.dumps(ev["payload"], ensure_ascii=False)))
        self._conn.commit()
        # 首条用户消息 → 自动生成确定性 fallback 标题(照抄 dsh-session-title fallback 语义)
        if type_ == "user_message":
            self._maybe_title_from_first_message(session_id)
        return cur.lastrowid

    def _maybe_title_from_first_message(self, session_id: str) -> None:
        """若会话标题仍是默认值(未命名),用首条用户消息生成 fallback 标题并更新。"""
        sess = self.get_session(session_id)
        if sess is None:
            return
        if sess.get("title") and sess["title"] != "新会话":
            return  # 已有命名标题,不覆盖(dsh: fallback 只在尚无标题时生成)
        first = title.first_user_text(self.read(session_id))
        if not first:
            return
        gen = title.fallback_session_title(first)
        if gen:
            self._conn.execute("UPDATE sessions SET title=? WHERE id=?", (gen, session_id))
            self._conn.commit()

    def set_title(self, session_id: str, title_text: str) -> dict | None:
        """显式设置会话标题(供用户重命名/强制刷新)。返回更新后的会话或 None。"""
        norm = title.fallback_session_title(title_text, max_bytes=200)
        if not norm:
            return None
        self._conn.execute("UPDATE sessions SET title=? WHERE id=?", (norm, session_id))
        self._conn.commit()
        return self.get_session(session_id)

    def read(self, session_id: str, after_seq: int = 0, limit: int | None = None) -> list[dict]:
        sql = "SELECT seq, type, ts, payload FROM events WHERE session_id=? AND seq>? ORDER BY seq"
        params: list = [session_id, after_seq]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        out = []
        for r_ in rows:
            if r_["type"] not in events.known_types():
                raise events.UnknownEventError(r_["type"])
            pl = json.loads(r_["payload"]) if isinstance(r_["payload"], str) else r_["payload"]
            out.append({"seq": r_["seq"], "type": r_["type"], "ts": r_["ts"], "payload": pl})
        return out

    def last_seq(self, session_id: str) -> int:
        row = self._conn.execute("SELECT MAX(seq) AS m FROM events WHERE session_id=?", (session_id,)).fetchone()
        return int(row["m"] or 0)

    # ---- sessions 元数据 ----
    def create_session(self, user_id: str = "", title: str = "新会话") -> dict:
        sid = uuid.uuid4().hex[:12]
        now = events.utcnow()
        self._conn.execute("INSERT INTO sessions (id,title,user_id,created_at) VALUES (?,?,?,?)",
                           (sid, title, user_id, now))
        self._conn.commit()
        return {"id": sid, "title": title, "user_id": user_id, "created_at": now}

    def delete_sessions_meta(self, ids: list[str]) -> int:
        """仅删除 sessions 元数据记录(不动 events,遵守 events append-only 铁律)。
        返回删除条数。用于清理空会话/测试会话,不触碰事件历史。"""
        cur = self._conn.executemany("DELETE FROM sessions WHERE id=?", [(i,) for i in ids])
        self._conn.commit()
        return cur.rowcount

    def list_sessions(self) -> list[dict]:
        rows = self._conn.execute("SELECT id,title,user_id,created_at FROM sessions WHERE deleted IS NULL OR deleted=0 ORDER BY created_at DESC").fetchall()
        return [dict(r_) for r_ in rows]

    def get_session(self, sid: str) -> dict | None:
        row = self._conn.execute("SELECT id,title,user_id,created_at FROM sessions WHERE id=? AND (deleted IS NULL OR deleted=0)", (sid,)).fetchone()
        return dict(row) if row else None

    def delete_session(self, sid: str) -> bool:
        """软删除会话(标记 deleted=1,不再出现在列表/视图;events 保留,遵守 append-only 铁律)。"""
        cur = self._conn.execute("UPDATE sessions SET deleted=1, status='deleted' WHERE id=? AND (deleted IS NULL OR deleted=0)", (sid,))
        self._conn.commit()
        return cur.rowcount > 0

    def rename_session(self, sid: str, new_title: str) -> dict | None:
        """重命名会话;返回更新后的会话或 None。"""
        norm = title.fallback_session_title(new_title, max_bytes=200)
        if not norm:
            return None
        self._conn.execute("UPDATE sessions SET title=? WHERE id=? AND (deleted IS NULL OR deleted=0)", (norm, sid))
        self._conn.commit()
        return self.get_session(sid)

    def close(self) -> None:
        self._conn.close()
