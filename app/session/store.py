"""SQLite append-only 会话事件存储。参照 dsh core/session + session-persistence-sqlite。

规则(AGENTS.md):
- WAL + busy_timeout;单写者(agent 服务进程)
- append-only:只 INSERT,绝不 UPDATE / DELETE 历史(不暴露任何改写事件的方法)
- schema 版本 fail-closed:meta 表版本不匹配 → 拒绝启动
- 读到的未注册事件类型 → 拒绝(UnknownEventError)
"""
from __future__ import annotations

import json
import sqlite3

from . import events

SCHEMA_VERSION = 1


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


class SessionStore:
    """SQLite 事实源 on top of 事件日志。只提供 append / read。"""

    def __init__(self, path: str):
        self.path = path
        self._conn = _connect(path)
        self._check_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', '{SCHEMA_VERSION}');
        CREATE TABLE IF NOT EXISTS events (
          seq INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id TEXT NOT NULL,
          type TEXT NOT NULL,
          ts TEXT NOT NULL,
          payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, seq);
        """)
        self._conn.commit()

    def _check_schema(self) -> None:
        cur = self._conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='meta'")
        if cur.fetchone() is None:
            self._init_schema()
            return
        row = self._conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if row is None or int(row["value"]) != SCHEMA_VERSION:
            have = row["value"] if row else "?"
            raise RuntimeError(f"schema 版本不匹配: 库={have} 代码={SCHEMA_VERSION} —— 拒绝启动")
        # fail-closed:日志里出现未注册类型 → 拒绝
        for r in self._conn.execute("SELECT DISTINCT type FROM events").fetchall():
            if r["type"] not in events.known_types():
                raise events.UnknownEventError(f"日志含未注册事件类型: {r['type']}")

    def append(self, session_id: str, type_: str, payload: dict) -> int:
        """追加一条事件(只 INSERT)。返回 seq,即使失败也不改历史。"""
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
        for r in rows:
            if r["type"] not in events.known_types():
                raise events.UnknownEventError(f"未知事件类型: {r['type']}")
            pl = json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"]
            out.append({"seq": r["seq"], "type": r["type"], "ts": r["ts"], "payload": pl})
        return out

    def close(self) -> None:
        self._conn.close()