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
from datetime import datetime, timezone
from typing import Any


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class KnowledgeStore:
    def __init__(self, path: str):
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
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
          product_category TEXT,
          content    TEXT,
          updated_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id, version);
        """)
        # 增量迁移:既有库若缺 product_category 列,补上(保险类别,用于区分医疗险/重疾险/意外险)
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(chunks)").fetchall()}
        if "product_category" not in cols:
            self.conn.execute("ALTER TABLE chunks ADD COLUMN product_category TEXT")
        if "updated_at" not in cols:
            self.conn.execute("ALTER TABLE chunks ADD COLUMN updated_at TEXT")
        self.conn.commit()
        # 回填/重归类:按 doc_id(产品名)判定 product_category(类别规则集中在 categories.py)。
        # 幂等:每次 init 都对 DISTINCT doc_id 重算,规则变更后下次打开本表即生效。
        from app.retrieval.categories import classify_product_category
        for row in self.conn.execute("SELECT DISTINCT doc_id FROM chunks").fetchall():
            did = row["doc_id"] or ""
            cat = classify_product_category(did)
            self.conn.execute("UPDATE chunks SET product_category=? WHERE doc_id=? AND product_category IS NOT ?", (cat, did, cat))
        self.conn.commit()

    def upsert_chunks(self, chunks: list[dict]) -> int:
        """chunks: [{chunk_id, content, meta:{doc_id,version,section,doc_type,source,title}}]"""
        n = 0
        now = _utcnow()
        for c in chunks:
            cid = c.get("chunk_id") or (c.get("meta") or {}).get("chunk_id")
            if not cid:
                continue
            m = c.get("meta") or {}
            self.conn.execute(
                """INSERT INTO chunks(chunk_id, doc_id, version, section, doc_type, source, title, product_category, content, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(chunk_id) DO UPDATE SET
                     doc_id=excluded.doc_id, version=excluded.version, section=excluded.section,
                     doc_type=excluded.doc_type, source=excluded.source, title=excluded.title,
                     product_category=excluded.product_category, content=excluded.content,
                     updated_at=excluded.updated_at""",
                (cid, m.get("doc_id", ""), m.get("version", ""), m.get("section", ""),
                 m.get("doc_type", ""), m.get("source", ""), m.get("title", ""),
                 m.get("product_category", ""), c.get("content", ""), now))
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

    def list_documents(self, page: int = 1, page_size: int = 50) -> dict:
        """分页列出所有文档，按doc_id聚合，返回每个文档的统计信息。
        Returns: {total, page, page_size, items: [{doc_id, doc_type, product_category, chunk_count, last_updated}]}
        """
        # 先count total
        total_row = self.conn.execute("SELECT COUNT(DISTINCT doc_id) FROM chunks WHERE doc_id IS NOT NULL AND doc_id != ''").fetchone()
        total = total_row[0] if total_row else 0
        if total == 0:
            return {"total": 0, "page": page, "page_size": page_size, "items": []}

        offset = (page - 1) * page_size
        rows = self.conn.execute("""
            SELECT doc_id, doc_type, product_category,
                   COUNT(*) as chunk_count,
                   MAX(updated_at) as last_updated
            FROM chunks
            WHERE doc_id IS NOT NULL AND doc_id != ''
            GROUP BY doc_id, doc_type, product_category
            ORDER BY MAX(updated_at) DESC
            LIMIT ? OFFSET ?
        """, (page_size, offset)).fetchall()

        items = []
        for r in rows:
            items.append({
                "doc_id": r["doc_id"],
                "doc_type": r["doc_type"],
                "product_category": r["product_category"],
                "chunk_count": r["chunk_count"],
                "last_updated": r["last_updated"]
            })

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": items
        }

    def list_chunks(self, doc_id: str, page: int = 1, page_size: int = 100) -> dict:
        """分页列出指定文档的所有chunks。
        Returns: {doc_id, total, page, page_size, items: [{chunk_id, version, section, title, product_category, content_preview}]}
        """
        total_row = self.conn.execute("SELECT COUNT(*) FROM chunks WHERE doc_id = ?", (doc_id,)).fetchone()
        total = total_row[0] if total_row else 0
        if total == 0:
            return {"doc_id": doc_id, "total": 0, "page": page, "page_size": page_size, "items": []}

        offset = (page - 1) * page_size
        rows = self.conn.execute("""
            SELECT chunk_id, version, section, title, product_category, content
            FROM chunks
            WHERE doc_id = ?
            ORDER BY chunk_id
            LIMIT ? OFFSET ?
        """, (doc_id, page_size, offset)).fetchall()

        items = []
        for r in rows:
            content = r["content"] or ""
            content_preview = content[:200] + ("..." if len(content) > 200 else "")
            items.append({
                "chunk_id": r["chunk_id"],
                "doc_id": doc_id,
                "version": r["version"],
                "section": r["section"],
                "title": r["title"],
                "product_category": r["product_category"],
                "content_preview": content_preview,
                "content": content
            })

        return {
            "doc_id": doc_id,
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": items
        }

    def delete_document(self, doc_id: str) -> int:
        """删除指定文档的所有chunks（硬删除）。
        Returns: number of chunks deleted.
        """
        cur = self.conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
        self.conn.commit()
        return cur.rowcount

    def close(self) -> None:
        self.conn.close()
