# -*- coding: utf-8 -*-
"""Knowledge Base management API (admin-only)."""
from __future__ import annotations
from fastapi import APIRouter, Depends

from app.api.schemas.kb import (
    DocumentListResponse, DocumentItem,
    ChunkListResponse, ChunkItem,
    IngestTextRequest, DeleteDocumentResponse,
    ReindexResponse, IngestTextResponse,
    StructNode, DocStructureResponse,
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


@router.get("/documents/{doc_id}/structure", response_model=DocStructureResponse)
def doc_structure(doc_id: str):
    """返回该文档的目录(结构树,含页码)。"""
    kstore = get_knowledge_store()
    nodes = kb_service.list_structure(kstore, doc_id)
    return DocStructureResponse(doc_id=doc_id, nodes=[StructNode(**n) for n in nodes])


@router.post("/ingest/text", response_model=IngestTextResponse)
def ingest_text(body: IngestTextRequest):
    """摄取文本文档"""
    ingester = get_ingester()
    product_name = body.product_name
    doc_id = product_name   # 产品名唯一,doc_id=产品名
    meta = {
        "doc_id": doc_id,
        "product_name": product_name,
        "version": body.version,
        "doc_type": body.doc_type,
        "product_category": body.product_category,
        "title": body.title or product_name,
        "source": body.source or f"api-ingest/{product_name}",
    }
    ok, result, msg = kb_service.ingest_text(
        ingester, body.text, meta,
        chunk_size=body.chunk_size, overlap=body.overlap, text_splitter=body.text_splitter,
        force=body.force)
    return IngestTextResponse(
        ok=ok,
        doc_id=result.get("doc_id", ""),
        chunks_written=result.get("chunks_written", 0),
        chunks_embedded=result.get("chunks_embedded", 0),
        message=msg, conflict=bool(result.get("conflict", False))
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

import os
import shutil
import tempfile
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.retrieval.ingest.reader import is_supported, BACKEND_NAMES
from app.api.schemas.kb import (
    DocumentListResponse, DocumentItem,
    ChunkListResponse, ChunkItem,
    IngestTextRequest, DeleteDocumentResponse,
    ReindexResponse, IngestTextResponse,
    ParsePreviewItem, ParsePreviewResponse, FileIngestResponse,
    UploadPreviewResponse, IngestionCommitRequest,
)


def _save_upload(upload: UploadFile) -> str:
    """校验扩展名并把上传文件落临时盘,返回路径(调用方负责清理目录)。"""
    name = os.path.basename(upload.filename or "")
    if not is_supported(name):
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {name or '?'}")
    tmpdir = tempfile.mkdtemp(prefix="kbup_")
    path = os.path.join(tmpdir, name)
    try:
        with open(path, "wb") as f:
            f.write(upload.file.read())
    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise
    return path


def _cleanup(path: str) -> None:
    shutil.rmtree(os.path.dirname(path), ignore_errors=True)




@router.post("/parse/preview", response_model=ParsePreviewResponse)
def parse_preview_upload(
    file: UploadFile = File(...),
    backends: str = Form("markitdown,pdfplumber"),
    chunk_size: int = Form(512),
):
    """同一文件多后端解析预览(不写库,不发嵌入):对比三路解析结果与"脱#→编号regex"切块质量。"""
    path = _save_upload(file)
    names = [b.strip() for b in backends.split(",") if b.strip()]
    names = [b for b in names if b in BACKEND_NAMES and b != "auto"]
    if not names:
        _cleanup(path)
        raise HTTPException(status_code=400, detail="未指定有效后端(mineru/markitdown/pdfplumber/native)")
    try:
        rows = kb_service.parse_preview(path, names, chunk_size)
        return ParsePreviewResponse(
            file_name=os.path.basename(path),
            items=[ParsePreviewItem(**r) for r in rows],
        )
    finally:
        _cleanup(path)

import json
import queue
import threading

from fastapi.responses import StreamingResponse


@router.post("/ingest/file", response_model=None)
def ingest_file_upload_stream(
    file: UploadFile = File(...),
    parser: str = Form("auto"),
    category: str = Form(""),
    text_splitter: str = Form("auto"),
    chunk_size: int = Form(0),
    overlap: int = Form(-1),
    product_name: str = Form(""),
    force: str = Form(""),
):
    """上传文件摄取,SSE 逐帧推进度:chunked/embed(done,total)/qdrant → done|error。"""
    if parser not in BACKEND_NAMES:
        raise HTTPException(status_code=400, detail=f"未知解析后端: {parser}")
    path = _save_upload(file)
    q: "queue.Queue" = queue.Queue()

    def work() -> None:
        try:
            def prog(stage: str, done: int, total: int) -> None:
                q.put({"type": "progress", "stage": stage, "done": done, "total": total})
            ok, result, msg, parser_used = kb_service.ingest_file(
                get_ingester(), path, category, parser, on_progress=prog,
                text_splitter=(None if text_splitter in ("auto", "", "null") else text_splitter),
                chunk_size=(None if chunk_size <= 0 else chunk_size),
                overlap=(None if overlap < 0 else overlap),
                product_name=(product_name.strip() or None),
                force=(str(force).lower() in ("1", "true", "yes")))
            q.put({"type": "done", "ok": ok, "doc_id": result.get("doc_id", ""),
                   "chunks_written": result.get("chunks_written", 0),
                   "chunks_embedded": result.get("chunks_embedded", 0),
                   "parser": parser_used, "message": msg,
                   "conflict": bool(result.get("conflict", False))})
        except Exception as exc:
            q.put({"type": "error", "message": str(exc)})
        finally:
            _cleanup(path)

    threading.Thread(target=work, daemon=True).start()

    def gen():
        while True:
            ev = q.get()
            yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
            if ev.get("type") in ("done", "error"):
                return

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/ingest/preview", response_model=UploadPreviewResponse)
def ingest_preview_upload(
    file: UploadFile = File(...),
    parser: str = Form("mineru"),
    text_splitter: str = Form("structured"),
    chunk_size: int = Form(0),
    overlap: int = Form(-1),
):
    """上传前"预览切块"(不写库、不发嵌入):同一文件按所选解析+切块方式切片,返回目录树 + 切块内容。
    供上传页「预览切块→确认并索引」复用,避免二次解析。"""
    path = _save_upload(file)
    try:
        ok, result, err = kb_service.preview_upload(
            path, parser,
            text_splitter=(None if text_splitter in ("auto", "", "null") else text_splitter),
            chunk_size=(None if chunk_size <= 0 else chunk_size),
            overlap=(None if overlap < 0 else overlap))
        return UploadPreviewResponse(ok=ok, err=err, **{k: result.get(k) for k in (
            "doc_type", "parser", "text_splitter", "chunk_count", "chunk_size",
            "overlap", "outline", "chunks")})
    finally:
        _cleanup(path)


@router.post("/ingest/commit", response_model=None)
def ingest_commit_stream(body: IngestionCommitRequest):
    """确认索引:commit 预览得到的 chunks/outline(不再解析),服务端写库 + doc_structure + 嵌入 + Qdrant,SSE 逐帧进度。"""
    ingester = get_ingester()
    product_name = body.product_name
    doc_id = product_name   # 产品名唯一,doc_id=产品名
    doc_meta = {
        "doc_id": doc_id,
        "product_name": product_name,
        "version": body.version,
        "doc_type": body.doc_type,
        "product_category": body.product_category or "",
        "title": body.title or product_name,
        "source": body.source or f"upload-preview/{product_name}",
    }
    chunk_items = [{"section": c.section, "title": c.title, "content": c.content} for c in body.chunks]
    outline = [{"level": n.level, "title": n.title, "page": n.page, "parent": n.parent} for n in body.outline]
    q: "queue.Queue" = queue.Queue()

    def work() -> None:
        try:
            def prog(stage: str, done: int, total: int) -> None:
                q.put({"type": "progress", "stage": stage, "done": done, "total": total})
            ok, result, msg = kb_service.commit_upload(ingester, doc_meta, chunk_items, outline,
                                                       on_progress=prog, force=body.force)
            q.put({"type": "done", "ok": ok, "doc_id": result.get("doc_id", ""),
                   "chunks_written": result.get("chunks_written", 0),
                   "chunks_embedded": result.get("chunks_embedded", 0), "message": msg,
                   "conflict": bool(result.get("conflict", False))})
        except Exception as exc:
            q.put({"type": "error", "message": str(exc)})

    threading.Thread(target=work, daemon=True).start()

    def gen():
        while True:
            ev = q.get()
            yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
            if ev.get("type") in ("done", "error"):
                return

    return StreamingResponse(gen(), media_type="text/event-stream")

