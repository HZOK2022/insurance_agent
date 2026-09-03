# -*- coding: utf-8 -*-
"""摄取核心:Ingester 封装从文本→切块→SQLite→嵌入→Qdrant 的完整管线。
CLI(ingest_kb.py) 与 API 共用此模块,避免逻辑重复。
"""
from __future__ import annotations
import logging
import os
from app.retrieval.chunker import chunk_documents
from app.retrieval.embedder import Embedder
from app.retrieval.knowledge_store import KnowledgeStore
from app.retrieval.qdrant_store import QdrantStore
from app.retrieval.ingest.reader import build_docs, read_text, is_supported, _SUPPORTED_EXTS

logger = logging.getLogger(__name__)

_BATCH_SIZE = 64


class Ingester:
    """摄取管线:文本/文件 → 切块 → SQLite(事实源) → 嵌入 → Qdrant(派生索引)。"""

    def __init__(self, kstore: KnowledgeStore, qstore: QdrantStore, embedder: Embedder):
        self.kstore = kstore
        self.qstore = qstore
        self.embedder = embedder

    def ingest_text(self, text: str, meta: dict) -> dict:
        """摄取原始文本。
        Args:
            text: 文档内容
            meta: 文档元数据,需包含 doc_id,version,title,doc_type,product_category 等
        Returns: {chunks_written, chunks_embedded, doc_id}
        """
        docs = [{"text": text, "meta": meta}]
        chunks = chunk_documents(docs, chunk_size=512, chunk_overlap=64)
        if not chunks:
            return {"chunks_written": 0, "chunks_embedded": 0, "doc_id": meta.get("doc_id", "")}

        # 1. 落 SQLite 事实源
        written = self.kstore.upsert_chunks(chunks)
        # 2. 嵌入 + 写 Qdrant
        vectors = self.embedder.embed([c["content"] for c in chunks])
        for i in range(0, len(chunks), _BATCH_SIZE):
            batch = chunks[i:i + _BATCH_SIZE]
            vecs = vectors[i:i + _BATCH_SIZE]
            self.qstore.upsert([{"vector": v, "content": c["content"], "meta": c["meta"]}
                                for c, v in zip(batch, vecs)])

        doc_id = meta.get("doc_id", "")
        logger.info("ingested doc_id=%s chunks=%d -> SQLite + Qdrant", doc_id, written)
        return {"chunks_written": written, "chunks_embedded": len(chunks), "doc_id": doc_id}

    def ingest_file(self, file_path: str, category: str = "") -> dict:
        """摄取单个文件。
        Returns: {chunks_written, chunks_embedded, doc_id} 或 None(不支持格式)
        """
        if not is_supported(file_path):
            logger.warning("unsupported file: %s", file_path)
            return {"chunks_written": 0, "chunks_embedded": 0, "doc_id": ""}

        docs = build_docs(file_path, category)
        if not docs:
            return {"chunks_written": 0, "chunks_embedded": 0, "doc_id": ""}

        return self.ingest_text(docs[0]["text"], docs[0]["meta"])

    def ingest_directory(self, dir_path: str, limit: int | None = None, category: str = "") -> dict:
        """摄取目录下所有支持的文件。
        Returns: {total_files, total_chunks, docs: [{doc_id, chunks_written}]}
        """
        files = []
        for root, _, fs in os.walk(dir_path):
            for fn in fs:
                if fn.lower().endswith(_SUPPORTED_EXTS):
                    files.append(os.path.join(root, fn))
        if limit:
            files = files[:limit]

        result = {"total_files": len(files), "total_chunks": 0, "docs": []}
        for f in files:
            r = self.ingest_file(f, category)
            if r["doc_id"]:
                result["docs"].append(r)
                result["total_chunks"] += r["chunks_written"]
        logger.info("ingested directory %s: %d files, %d chunks", dir_path, result["total_files"], result["total_chunks"])
        return result

    def full_reindex(self) -> dict:
        """全量重建 Qdrant:从 SQLite 事实源读取所有 chunks → 重新嵌入 → 写入 Qdrant。
        遵循黄金法则:SQLite 是事实源,Qdrant 是可重建的派生索引。
        """
        all_chunks = self.kstore.all_chunks()
        if not all_chunks:
            return {"total_chunks": 0, "embedded": 0, "message": "知识库为空,无需重建"}

        # 清空 Qdrant 集合
        self.qstore.delete_collection()
        # 重新创建集合(delete_collection 会删除集合,需要重新 ensure)
        self.qstore._ensure()

        # 分批嵌入 + 写入
        vectors = self.embedder.embed([c["content"] for c in all_chunks])
        for i in range(0, len(all_chunks), _BATCH_SIZE):
            batch = all_chunks[i:i + _BATCH_SIZE]
            vecs = vectors[i:i + _BATCH_SIZE]
            self.qstore.upsert([{"vector": v, "content": c["content"], "meta": c["meta"]}
                                for c, v in zip(batch, vecs)])

        n = len(all_chunks)
        logger.info("full reindex done: %d chunks -> Qdrant", n)
        return {"total_chunks": n, "embedded": n, "message": f"已重建 {n} 个 chunks 的向量索引"}