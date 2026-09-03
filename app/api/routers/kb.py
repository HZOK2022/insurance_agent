# -*- coding: utf-8 -*-
"""Knowledge Base management API (admin-only)."""
from __future__ import annotations
from fastapi import APIRouter, Depends

from app.api.schemas.kb import (
    DocumentListResponse, DocumentItem,
    ChunkListResponse, ChunkItem,
    IngestTextRequest, DeleteDocumentResponse,
    ReindexResponse, IngestTextResponse,
)
from app.api.services.container import (
    get_knowledge_store, get_qstore, get_ingester
)
from app.api.services import kb_service

router = APIRouter(prefix="/api/kb", tags=["knowledge-base"])


@router.get("/documents", response_model=DocumentListResponse)
def list_documents(page: int = 1, page_size: int = 50):
    """列出所有文档（分页）"""
    kstore = get_knowledge_store()
    result = kb_service.list_documents(kstore, page, page_size)
    items = [DocumentItem(**item) for item in result["items"]]
    return DocumentListResponse(
        total=result["total"],
        page=result["page"],
        page_size=result["page_size"],
        items=items
    )


@router.get("/documents/{doc_id}/chunks", response_model=ChunkListResponse)
def list_chunks(doc_id: str, page: int = 1, page_size: int = 100):
    """列出指定文档的所有chunks（分页）"""
    kstore = get_knowledge_store()
    result = kb_service.list_chunks(kstore, doc_id, page, page_size)
    items = [ChunkItem(**item) for item in result["items"]]
    return ChunkListResponse(
        doc_id=result["doc_id"],
        total=result["total"],
        page=result["page"],
        page_size=result["page_size"],
        items=items
    )


@router.post("/ingest/text", response_model=IngestTextResponse)
def ingest_text(body: IngestTextRequest):
    """摄取文本文档"""
    ingester = get_ingester()
    meta = {
        "doc_id": body.doc_id,
        "version": body.version,
        "doc_type": body.doc_type,
        "product_category": body.product_category,
        "title": body.title or body.doc_id,
        "source": body.source or f"api-ingest/{body.doc_id}",
    }
    ok, result, msg = kb_service.ingest_text(ingester, body.text, meta)
    return IngestTextResponse(
        ok=ok,
        doc_id=result.get("doc_id", ""),
        chunks_written=result.get("chunks_written", 0),
        chunks_embedded=result.get("chunks_embedded", 0),
        message=msg
    )


@router.delete("/documents/{doc_id}", response_model=DeleteDocumentResponse)
def delete_document(doc_id: str):
    """删除整个文档（SQLite + Qdrant同步删除）"""
    kstore = get_knowledge_store()
    qstore = get_qstore()
    ok, chunks_deleted, points_deleted, msg = kb_service.delete_document(kstore, qstore, doc_id)
    return DeleteDocumentResponse(
        ok=ok,
        doc_id=doc_id,
        chunks_deleted=chunks_deleted,
        points_deleted=points_deleted,
        message=msg
    )


@router.post("/reindex", response_model=ReindexResponse)
def reindex():
    """全量重建Qdrant索引（从SQLite事实源重建）"""
    kstore = get_knowledge_store()
    ingester = get_ingester()
    ok, result, msg = kb_service.full_reindex(kstore, ingester)
    return ReindexResponse(
        ok=ok,
        total_chunks=result.get("total_chunks", 0),
        embedded=result.get("embedded", 0),
        message=msg
    )
