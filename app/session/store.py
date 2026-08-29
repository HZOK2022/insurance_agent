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

from . import events

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
          id TEXT PRIMARY KEY, title TEXT, user_id TEXT, created_at TEXT, status TEXT DEFAULT 'active'
        );
        """

    def _ensure_schema(self) -> None:
        self._conn.executescript(self._ddl())
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
        return cur.lastrowid

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

    def list_sessions(self) -> list[dict]:
        rows = self._conn.execute("SELECT id,title,user_id,created_at FROM sessions ORDER BY created_at DESC").fetchall()
        return [dict(r_) for r_ in rows]

    def get_session(self, sid: str) -> dict | None:
        row = self._conn.execute("SELECT id,title,user_id,created_at FROM sessions WHERE id=?", (sid,)).fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        self._conn.close()