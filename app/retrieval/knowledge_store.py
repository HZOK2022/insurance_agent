# -*- coding: utf-8 -*-
"""知识库事实源(KnowledgeStore):chunks 存 SQLite,Qdrant 是派生的向量索引(可从本表重建)。

黄金法则:SQLite = 事实源;Qdrant = 可重建的派生索引。
- search_knowledge 仍查 Qdrant(向量检索,快、语义)。
- 本表(chunks)是 canon:重启/丢 Qdrant 时用 scripts/rebuild_qdrant.py 从它重建 Qdrant;
  也承载版本(条款更新=新增 version 行,旧版不失效,铁律 3)。
"""
from __future__ import annotations
import json
import os
import sqlite3
from typing import Any


class KnowledgeStore:
    def __init__(self, path: str):
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS chunks (
          chunk_id   TEXT PRIMARY KEY,
          doc_id     TEXT,
          version    TEXT,
          section    TEXT,
          doc_type   TEXT,
          source     TEXT,
          title      TEXT,
          content    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id, version);
        """)
        self.conn.commit()

    def upsert_chunks(self, chunks: list[dict]) -> int:
        """chunks: [{chunk_id, content, meta:{doc_id,version,section,doc_type,source,title}}]"""
        n = 0
        for c in chunks:
            cid = c.get("chunk_id") or (c.get("meta") or {}).get("chunk_id")
            if not cid:
                continue
            m = c.get("meta") or {}
            self.conn.execute(
                """INSERT INTO chunks(chunk_id, doc_id, version, section, doc_type, source, title, content)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(chunk_id) DO UPDATE SET
                     doc_id=excluded.doc_id, version=excluded.version, section=excluded.section,
                     doc_type=excluded.doc_type, source=excluded.source, title=excluded.title,
                     content=excluded.content""",
                (cid, m.get("doc_id", ""), m.get("version", ""), m.get("section", ""),
                 m.get("doc_type", ""), m.get("source", ""), m.get("title", ""), c.get("content", "")))
            n += 1
        self.conn.commit()
        return n

    def all_chunks(self) -> list[dict]:
        """导出全部 chunks({chunk_id, content, meta}),供 BM25 构建 / Qdrant 重建。"""
        out = []
        for row in self.conn.execute("SELECT * FROM chunks"):
            d = dict(row)
            content = d.pop("content", "")
            out.append({"chunk_id": d["chunk_id"], "content": content, "meta": d})
        return out

    def get_chunk(self, chunk_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM chunks WHERE chunk_id=?", (chunk_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        content = d.pop("content", "")
        return {"chunk_id": d["chunk_id"], "content": content, "meta": d}

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    def close(self) -> None:
        self.conn.close()
