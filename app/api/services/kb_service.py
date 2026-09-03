# -*- coding: utf-8 -*-
"""Knowledge Base management service (admin-only)."""
from __future__ import annotations
import logging
from app.retrieval.knowledge_store import KnowledgeStore
from app.retrieval.qdrant_store import QdrantStore
from app.retrieval.ingest.ingester import Ingester
from app.businesses.insurance import mark_bm25_dirty
from app.config import Config

logger = logging.getLogger(__name__)


def list_documents(kstore: KnowledgeStore, page: int, page_size: int) -> dict:
    """列出所有文档，分页。"""
    return kstore.list_documents(page=page, page_size=page_size)


def list_chunks(kstore: KnowledgeStore, doc_id: str, page: int, page_size: int) -> dict:
    """列出指定文档的所有chunks，分页。"""
    return kstore.list_chunks(doc_id=doc_id, page=page, page_size=page_size)


def delete_document(
    kstore: KnowledgeStore,
    qstore: QdrantStore,
    doc_id: str
) -> tuple[bool, int, int, str]:
    """删除文档: 先删SQLite，再删Qdrant。
    Returns: (ok, chunks_deleted, points_deleted, message)
    """
    # 1. 删SQLite事实源
    chunks_deleted = kstore.delete_document(doc_id)
    if chunks_deleted == 0:
        return False, 0, 0, f"文档 {doc_id} 不存在"

    # 2. 删Qdrant索引
    try:
        points_deleted = qstore.delete_by_doc_id(doc_id)
        # Qdrant返回-1表示成功但不知道确切条数，用chunks_deleted做估算
        if points_deleted < 0:
            points_deleted = chunks_deleted
        # 3. 标记BM25为脏，下次检索懒重建
        mark_bm25_dirty()
        msg = f"成功删除文档 {doc_id}: {chunks_deleted} chunks 从SQLite, {points_deleted} points 从Qdrant"
        logger.info(msg)
        return True, chunks_deleted, points_deleted, msg
    except Exception as e:
        msg = f"SQLite删除成功({chunks_deleted} chunks)，但Qdrant删除失败: {e} → 请手动执行全量reindex修复一致性"
        logger.error(msg)
        return True, chunks_deleted, 0, msg


def ingest_text(
    ingester: Ingester,
    text: str,
    meta: dict
) -> tuple[bool, dict, str]:
    """摄取文本文档。
    Returns: (ok, result, message)
    """
    try:
        result = ingester.ingest_text(text, meta)
        doc_id = result.get("doc_id", "")
        chunks_written = result.get("chunks_written", 0)
        if chunks_written == 0:
            return False, result, f"文档 {doc_id} 切块后为空，未写入"
        # 标记BM25为脏，下次检索懒重建
        mark_bm25_dirty()
        return True, result, f"成功摄取文档 {doc_id}: {chunks_written} chunks"
    except Exception as e:
        msg = f"摄取失败: {e}"
        logger.error(msg, exc_info=True)
        return False, {}, msg


def full_reindex(
    kstore: KnowledgeStore,
    ingester: Ingester
) -> tuple[bool, dict, str]:
    """全量重建Qdrant索引: 从SQLite事实源重建。
    Returns: (ok, result, message)
    """
    try:
        result = ingester.full_reindex()
        total = result.get("total_chunks", 0)
        if total == 0:
            return True, result, "知识库为空，重建完成(无chunks)"
        return True, result, f"成功重建索引: {total} chunks"
    except Exception as e:
        msg = f"重建失败: {e}"
        logger.error(msg, exc_info=True)
        return False, {}, msg