"""知识库事实源(KnowledgeStore):chunks 存 SQLite/MySQL,Qdrant 是派生的向量索引(可从本表重建)。

黄金法则:MySQL(数据库服务器)= 事实源;Qdrant = 可重建的派生索引。SQLite 仅开发/测试回退。
- search_knowledge 仍查 Qdrant(向量检索,快、语义)。
- 本表(chunks)是 canon:重启/丢 Qdrant 时用 scripts/rebuild_qdrant.py 从它重建 Qdrant;
  也承载版本(条款更新=新增 version 行,旧版不失效,铁律 3)。
- 生产(配 db_host)走 MySQL 的 knowledge 库;开发/测试(未配)回退 SQLite(knowledge.db)。
"""
from __future__ import annotations
import json
import os
import sqlite3
from typing import Any

import app.db as dbmod

import logging
logger = logging.getLogger(__name__)
from app.util.time import beijing_now


def _utcnow() -> str:
    """兼容旧调用:统一返回北京时间 YYYY-MM-DD HH:mm:ss。"""
    return beijing_now()


def _isbit(v) -> int:
    """is_valid 取值:缺省/None→1(生效);显式 False/0/'0'/'false'→0;其余非空→1。显式 False 不被缺省吞掉。"""
    if v is None:
        return 1
    if isinstance(v, bool):
        return 1 if v else 0
    return 0 if str(v).strip().lower() in ("0", "false", "no") else 1


class KnowledgeStore:
    def __init__(self, path: str | None = None, cfg=None):
        self._is_mysql = cfg is not None and dbmod.dial(cfg) == "mysql"
        if self._is_mysql:
            self.path = None
            self.conn = dbmod.DB(dbmod.get_conn(cfg, "knowledge"), cfg)
            self._init_schema()
            return
        # SQLite(开发/测试/未配 db_host)
        if path is None:
            path = dbmod._sqlite_path(cfg, "knowledge") if cfg else "data/knowledge.db"
        d = os.path.dirname(path) if path else None
        if d:
            os.makedirs(d, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _ddl(self) -> str:
        return """
        CREATE TABLE IF NOT EXISTS chunks (
          chunk_id   TEXT PRIMARY KEY,
          doc_id     TEXT,
          version    TEXT,
          section    TEXT,
          doc_type   TEXT,
          source     TEXT,
          title      TEXT,
          product_category TEXT,
          product_name TEXT,
          content    TEXT,
          is_valid   INTEGER DEFAULT 1,
          updated_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id, version);
        CREATE TABLE IF NOT EXISTS documents (
          doc_id          TEXT PRIMARY KEY,
          product_name    TEXT,
          product_category TEXT,
          version         TEXT,
          title           TEXT,
          source          TEXT,
          content_hash    TEXT,
          is_valid        INTEGER DEFAULT 1,
          created_at      TEXT,
          updated_at      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(content_hash);
        CREATE TABLE IF NOT EXISTS doc_structure (
          doc_id   TEXT,
          seq      INTEGER,
          level    INTEGER,
          title    TEXT,
          page     INTEGER,
          parent   TEXT,
          normalized TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_doc_structure ON doc_structure(doc_id);
        """

    def _mysql_ddl(self) -> None:
        """MySQL DDL(建表 + 建索引)。索引不支持 IF NOT EXISTS,靠调用处容错重复(1061)。"""
        stmts = [
            "CREATE TABLE IF NOT EXISTS chunks ("
            "  chunk_id VARCHAR(191) PRIMARY KEY,"
            "  doc_id VARCHAR(191),"
            "  version VARCHAR(64),"
            "  section TEXT,"
            "  doc_type VARCHAR(64),"
            "  source TEXT,"
            "  title TEXT,"
            "  product_category VARCHAR(64),"
            "  product_name VARCHAR(191),"
            "  content LONGTEXT,"
            "  is_valid TINYINT(1) DEFAULT 1,"
            "  updated_at VARCHAR(40))",
            "CREATE INDEX idx_chunks_doc ON chunks(doc_id, version)",
            "CREATE TABLE IF NOT EXISTS documents ("
            "  doc_id VARCHAR(191) PRIMARY KEY,"
            "  product_name VARCHAR(191),"
            "  product_category VARCHAR(64),"
            "  version VARCHAR(64),"
            "  title TEXT,"
            "  source TEXT,"
            "  content_hash VARCHAR(64),"
            "  is_valid TINYINT(1) DEFAULT 1,"
            "  created_at VARCHAR(40),"
            "  updated_at VARCHAR(40))",
            "CREATE INDEX idx_documents_hash ON documents(content_hash)",
            "CREATE TABLE IF NOT EXISTS doc_structure ("
            "  doc_id VARCHAR(191),"
            "  seq INT,"
            "  level INT,"
            "  title TEXT,"
            "  page INT,"
            "  parent TEXT,"
            "  normalized TEXT)",
            "CREATE INDEX idx_doc_structure ON doc_structure(doc_id)",
        ]
        for stmt in stmts:
            try:
                self.conn.execute(stmt)
            except Exception as e:
                if getattr(e, "args", [None])[0] == 1061:
                    continue  # 索引已存在
                raise
        # 增量加列(既有 MySQL 库缺列时补上;全新库已含,幂等)
        cols = dbmod.columns(self.conn, "chunks")
        if "product_category" not in cols:
            self.conn.execute("ALTER TABLE chunks ADD COLUMN product_category VARCHAR(64)")
        if "product_name" not in cols:
            self.conn.execute("ALTER TABLE chunks ADD COLUMN product_name VARCHAR(191)")
        if "updated_at" not in cols:
            self.conn.execute("ALTER TABLE chunks ADD COLUMN updated_at VARCHAR(40)")
        if "is_valid" not in cols:
            self.conn.execute("ALTER TABLE chunks ADD COLUMN is_valid TINYINT(1) DEFAULT 1")
        dcols = dbmod.columns(self.conn, "documents")
        if "is_valid" not in dcols:
            self.conn.execute("ALTER TABLE documents ADD COLUMN is_valid TINYINT(1) DEFAULT 1")
        try:
            self.conn.execute("CREATE INDEX idx_chunks_product ON chunks(product_name)")
        except Exception as e:
            if getattr(e, "args", [None])[0] != 1061:
                raise

    def _sqlite_ensure_columns(self) -> None:
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(chunks)").fetchall()}
        if "product_category" not in cols:
            self.conn.execute("ALTER TABLE chunks ADD COLUMN product_category TEXT")
        if "product_name" not in cols:
            self.conn.execute("ALTER TABLE chunks ADD COLUMN product_name TEXT")
        if "updated_at" not in cols:
            self.conn.execute("ALTER TABLE chunks ADD COLUMN updated_at TEXT")
        if "is_valid" not in cols:
            self.conn.execute("ALTER TABLE chunks ADD COLUMN is_valid INTEGER DEFAULT 1")
        dcols = {r["name"] for r in self.conn.execute("PRAGMA table_info(documents)").fetchall()}
        if "is_valid" not in dcols:
            self.conn.execute("ALTER TABLE documents ADD COLUMN is_valid INTEGER DEFAULT 1")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_product ON chunks(product_name)")

    def _init_schema(self) -> None:
        if self._is_mysql:
            self._mysql_ddl()
        else:
            self.conn.executescript(self._ddl())
            self._sqlite_ensure_columns()
        self.conn.commit()
        # 回填/重归类:按 doc_id(产品名)判定 product_category(类别规则集中在 categories.py)。
        self._reclassify()
        # 回填 documents 表(旧库无 product_name 时以 doc_id 充当;content_hash 按内容计算)。
        self._backfill_documents()
        self.conn.commit()

    def _reclassify(self) -> None:
        """按 DISTINCT doc_id 重算 product_category;幂等(每次 init 对每个 doc 重算)。
        product_name 回填仅针对 documents 表缺行的旧 chunks(交给 _backfill_documents);
        对"显式不关联产品"(documents 已有行且 product_name 为空)不回填,避免覆盖新语义。"""
        from app.retrieval.categories import classify_product_category
        for row in self.conn.execute("SELECT DISTINCT doc_id FROM chunks").fetchall():
            did = row["doc_id"] or ""
            cat = classify_product_category(did)
            self.conn.execute("UPDATE chunks SET product_category=? WHERE doc_id=?", (cat, did))

    def _backfill_documents(self) -> None:
        """旧库迁移:documents 缺行时按 chunks 回填(product_name=doc_id,content_hash=按内容计算)。幂等。"""
        docs = [r["doc_id"] for r in self.conn.execute("SELECT DISTINCT doc_id FROM chunks WHERE doc_id != ''")]
        for did in docs:
            exists = self.conn.execute("SELECT 1 FROM documents WHERE doc_id=?", (did,)).fetchone()
            if exists:
                continue
            rows = self.conn.execute("SELECT content FROM chunks WHERE doc_id=? ORDER BY chunk_id", (did,)).fetchall()
            from app.retrieval.hash_util import content_hash
            h = content_hash([{"content": r["content"]} for r in rows])
            cat = ""
            r0 = self.conn.execute("SELECT product_category, version, source, title FROM chunks WHERE doc_id=? LIMIT 1", (did,)).fetchone()
            if r0:
                cat = r0["product_category"] or ""
                self.conn.execute(
                    "INSERT INTO documents(doc_id, product_name, product_category, version, title, source, content_hash, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (did, did, cat, r0["version"] or "", r0["title"] or "", r0["source"] or "", h, _utcnow(), _utcnow()))
        self.conn.commit()

    def upsert_chunks(self, chunks: list[dict]) -> int:
        """chunks: [{chunk_id, content, meta:{doc_id,version,section,doc_type,source,title,product_name}}]"""
        n = 0
        now = _utcnow()
        for c in chunks:
            cid = c.get("chunk_id") or (c.get("meta") or {}).get("chunk_id")
            if not cid:
                continue
            m = c.get("meta") or {}
            ival = _isbit(m.get("is_valid", 1))
            args = (cid, m.get("doc_id", ""), m.get("version", ""), m.get("section", ""),
                    m.get("doc_type", ""), m.get("source", ""), m.get("title", ""),
                    m.get("product_category", ""), m.get("product_name", ""), c.get("content", ""), ival, now)
            if self._is_mysql:
                self.conn.execute(
                    """INSERT INTO chunks(chunk_id, doc_id, version, section, doc_type, source, title,
                       product_category, product_name, content, is_valid, updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                       ON DUPLICATE KEY UPDATE
                         doc_id=VALUES(doc_id), version=VALUES(version), section=VALUES(section),
                         doc_type=VALUES(doc_type), source=VALUES(source), title=VALUES(title),
                         product_category=VALUES(product_category), product_name=VALUES(product_name),
                         content=VALUES(content), is_valid=VALUES(is_valid), updated_at=VALUES(updated_at)""",
                    args)
            else:
                self.conn.execute(
                    """INSERT INTO chunks(chunk_id, doc_id, version, section, doc_type, source, title, product_category, product_name, content, is_valid, updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(chunk_id) DO UPDATE SET
                         doc_id=excluded.doc_id, version=excluded.version, section=excluded.section,
                         doc_type=excluded.doc_type, source=excluded.source, title=excluded.title,
                         product_category=excluded.product_category, product_name=excluded.product_name,
                         content=excluded.content, is_valid=excluded.is_valid, updated_at=excluded.updated_at""",
                    args)
            n += 1
        self.conn.commit()
        logger.info("【事实源】chunk 写入成功:共 %d 块(dialect=%s)", n, "mysql" if self._is_mysql else "sqlite",
                    extra={"op": "store.upsert_chunks", "count": n})
        return n

    def get_document(self, doc_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
        return dict(row) if row else None

    def find_doc_by_hash(self, hashes, exclude_doc_id: str = "") -> dict | None:
        """全局内容查重(D75):按内容指纹候选(新旧口径并存)找其他文档。命中即完全重复。"""
        hs = [h for h in (hashes or []) if h]
        if not hs:
            return None
        qmarks = ",".join("?" * len(hs))
        args = list(hs)
        sql = f"SELECT doc_id, product_name, title FROM documents WHERE content_hash IN ({qmarks})"
        if exclude_doc_id:
            sql += " AND doc_id != ?"
            args.append(exclude_doc_id)
        row = self.conn.execute(sql, args).fetchone()
        return dict(row) if row else None

    def upsert_document(self, doc: dict) -> None:
        """doc: {doc_id, product_name, product_category, version, title, source, content_hash, is_valid}。整行替换。"""
        now = _utcnow()
        ival = _isbit(doc.get("is_valid", 1))
        args = (doc.get("doc_id", ""), doc.get("product_name", ""), doc.get("product_category", ""),
                doc.get("version", ""), doc.get("title", ""), doc.get("source", ""),
                doc.get("content_hash", ""), ival, now, now)
        if self._is_mysql:
            self.conn.execute(
                """INSERT INTO documents(doc_id, product_name, product_category, version, title, source, content_hash, is_valid, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON DUPLICATE KEY UPDATE
                     product_name=VALUES(product_name), product_category=VALUES(product_category),
                     version=VALUES(version), title=VALUES(title), source=VALUES(source),
                     content_hash=VALUES(content_hash), is_valid=VALUES(is_valid), updated_at=VALUES(updated_at)""",
                args)
        else:
            self.conn.execute(
                """INSERT INTO documents(doc_id, product_name, product_category, version, title, source, content_hash, is_valid, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(doc_id) DO UPDATE SET
                     product_name=excluded.product_name, product_category=excluded.product_category,
                     version=excluded.version, title=excluded.title, source=excluded.source,
                     content_hash=excluded.content_hash, is_valid=excluded.is_valid, updated_at=excluded.updated_at""",
                args)
        self.conn.commit()
        logger.info("【事实源】文档写入成功:doc_id=%s(版本=%s,dialect=%s)",
                    doc.get("doc_id", ""), doc.get("version", ""), "mysql" if self._is_mysql else "sqlite",
                    extra={"op": "store.upsert_document", "doc_id": doc.get("doc_id", "")})

    def next_version(self, product_name: str, title: str) -> str:
        """D97 版本自动序号:以 (product_name, title) 为逻辑组,取组内最大数字版本 +1(首份 v1)。
        产品名或文档名为空(不参与粗判归组)→ 恒 v1。version 格式统一 v{n},杜绝手动五花八门。"""
        product_name = (product_name or "").strip()
        title = (title or "").strip()
        if not product_name or not title:
            return "v1"
        rows = self.conn.execute(
            "SELECT version FROM documents WHERE product_name=? AND title=?", (product_name, title)).fetchall()
        mx = 0
        import re as _re
        for r in rows:
            mm = _re.search(r"v(\d+)", r["version"] or "")
            if mm:
                mx = max(mx, int(mm.group(1)))
        return f"v{mx + 1}"

    def set_document_valid(self, doc_id: str, is_valid: bool) -> tuple[bool, str]:
        """切换文档生效/失效(MySQL 事实源):documents 与所有 chunks 同步置位。幂等。
        Returns: (exists, message);实际调用方负责与 Qdrant 同步(第7条:同生效同失效、同成功同失败)。"""
        drow = self.conn.execute("SELECT 1 FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
        if drow is None:
            return False, f"文档 {doc_id} 不存在"
        ival = 1 if is_valid else 0
        now = _utcnow()
        self.conn.execute("UPDATE documents SET is_valid=?, updated_at=? WHERE doc_id=?", (ival, now, doc_id))
        self.conn.execute("UPDATE chunks SET is_valid=?, updated_at=? WHERE doc_id=?", (ival, now, doc_id))
        self.conn.commit()
        logger.info("【事实源】文档状态更新:doc_id=%s → is_valid=%s", doc_id, is_valid,
                    extra={"op": "store.set_document_valid", "doc_id": doc_id, "is_valid": is_valid})
        return True, ""

    def get_valid_doc_ids(self, is_valid: bool = True) -> set[str]:
        rows = self.conn.execute("SELECT doc_id FROM documents WHERE is_valid=?", (1 if is_valid else 0,)).fetchall()
        return {r["doc_id"] for r in rows}

    def list_products(self) -> list[str]:
        """按已上传文档聚合的产品名列表(去重、非空),供上传页"产品"下拉框。"""
        rows = self.conn.execute(
            "SELECT DISTINCT product_name FROM documents "
            "WHERE product_name IS NOT NULL AND product_name != '' ORDER BY product_name").fetchall()
        return [r["product_name"] for r in rows]

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
        r = self.conn.execute("SELECT COUNT(*) AS c FROM chunks").fetchone()
        return int(r["c"] or 0)

    def list_documents(self, page: int = 1, page_size: int = 50) -> dict:
        """分页列出所有文档，按doc_id聚合，返回每个文档的统计信息。
        Returns: {total, page, page_size, items: [{doc_id, doc_type, product_category, chunk_count, last_updated}]}
        """
        total_row = self.conn.execute("SELECT COUNT(DISTINCT doc_id) AS c FROM chunks WHERE doc_id IS NOT NULL AND doc_id != ''").fetchone()
        total = total_row["c"] if total_row else 0
        if total == 0:
            return {"total": 0, "page": page, "page_size": page_size, "items": []}

        offset = (page - 1) * page_size
        rows = self.conn.execute("""
            SELECT c.doc_id, c.doc_type, c.product_category,
                   COUNT(*) as chunk_count,
                   MAX(c.updated_at) as last_updated,
                   MAX(d.is_valid) as is_valid,
                   MAX(d.product_name) as product_name,
                   COALESCE(NULLIF(MAX(d.title), ''), MAX(c.title)) as title,
                   MAX(d.version) as version,
                   MAX(d.source) as source
            FROM chunks c
            LEFT JOIN documents d ON d.doc_id = c.doc_id
            WHERE c.doc_id IS NOT NULL AND c.doc_id != ''
            GROUP BY c.doc_id, c.doc_type, c.product_category
            ORDER BY MAX(c.updated_at) DESC
            LIMIT ? OFFSET ?
        """, (page_size, offset)).fetchall()

        items = []
        for r in rows:
            items.append({
                "doc_id": r["doc_id"],
                "doc_type": r["doc_type"],
                "product_category": r["product_category"],
                "chunk_count": r["chunk_count"],
                "last_updated": r["last_updated"],
                "is_valid": bool(r["is_valid"]),
                "product_name": r["product_name"] or "",
                "title": r["title"] or "",
                "version": r["version"] or "",
                "source": r["source"] or "",
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
        total_row = self.conn.execute("SELECT COUNT(*) AS c FROM chunks WHERE doc_id = ?", (doc_id,)).fetchone()
        total = total_row["c"] if total_row else 0
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
                "content": content,   # 全文:供「查看」展开/收起
                "content_preview": content_preview,
            })

        return {
            "doc_id": doc_id,
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": items
        }

    def set_doc_structure(self, doc_id: str, nodes: list[dict]) -> int:
        """整树替换该文档的 doc_structure(nodes: [{level,title,page,parent,norm}])。"""
        self.conn.execute("DELETE FROM doc_structure WHERE doc_id=?", (doc_id,))
        n = 0
        for i, nd in enumerate(nodes):
            self.conn.execute(
                "INSERT INTO doc_structure(doc_id, seq, level, title, page, parent, normalized) VALUES(?,?,?,?,?,?,?)",
                (doc_id, i, int(nd.get("level", 1)), nd.get("title", ""),
                 nd.get("page"), nd.get("parent", ""), nd.get("norm", "")))
            n += 1
        self.conn.commit()
        logger.info("【事实源】文档目录已保存:doc_id=%s,共 %d 个节点", doc_id, n,
                    extra={"op": "store.set_doc_structure", "doc_id": doc_id, "nodes": n})
        return n

    def get_doc_structure(self, doc_id: str) -> list[dict]:
        """返回该文档结构树节点列表(按 seq)。"""
        rows = self.conn.execute(
            "SELECT level, title, page, parent FROM doc_structure WHERE doc_id=? ORDER BY seq",
            (doc_id,)).fetchall()
        return [{"level": r["level"], "title": r["title"], "page": r["page"], "parent": r["parent"]}
                for r in rows]

    def chunks_sections(self, doc_id: str) -> list[tuple[str, str]]:
        """返回该文档所有 chunk 的 (chunk_id, section),用于结构节点↔chunk 关联。"""
        rows = self.conn.execute(
            "SELECT chunk_id, section FROM chunks WHERE doc_id=?", (doc_id,)).fetchall()
        return [(r["chunk_id"], r["section"] or "") for r in rows]

    def delete_document(self, doc_id: str) -> int:
        """删除指定文档的所有chunks + doc_structure + documents 行（硬删除）。
        Returns: number of chunks deleted.
        """
        cur = self.conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
        self.conn.execute("DELETE FROM doc_structure WHERE doc_id = ?", (doc_id,))
        self.conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
        self.conn.commit()
        logger.info("【事实源】文档删除成功:doc_id=%s,共删 %d 块", doc_id, cur.rowcount,
                    extra={"op": "store.delete_document", "doc_id": doc_id, "chunks_deleted": cur.rowcount})
        return cur.rowcount

    def close(self) -> None:
        self.conn.close()
