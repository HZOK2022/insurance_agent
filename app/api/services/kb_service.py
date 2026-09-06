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


def list_structure(kstore: KnowledgeStore, doc_id: str) -> list[dict]:
    """返回文档目录(结构树节点),并给每个节点附上其子树对应的 chunk 编号列表 chunk_ids。"""
    from app.retrieval.doc_structure import _norm

    def _idx(cid: str) -> int:
        try:
            return int(cid.rsplit(":", 1)[-1])
        except (ValueError, IndexError):
            return 0

    nodes = kstore.get_doc_structure(doc_id)
    secs = kstore.chunks_sections(doc_id)
    # 归一化 section 列表,便于前缀匹配
    norm_secs = [(_idx(cid), _norm(sec)) for cid, sec in secs]
    stack: list[dict] = []
    for n in nodes:
        while stack and stack[-1]["level"] >= n["level"]:
            stack.pop()
        np = _norm(" > ".join([x["title"] for x in stack] + [n["title"]]))
        # 同一分支即关联:块 section 在节点之下(子块挂父节点)或节点在块之下(合并块挂其每个叶子标题)
        ids = sorted(i for i, s in norm_secs if s and (s.startswith(np) or np.startswith(s)))
        n["chunk_ids"] = ids
        stack.append(n)
    return nodes


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
    meta: dict,
    chunk_size: int | None = None,
    overlap: int | None = None,
    text_splitter: str | None = None,
    chunk_max_tokens: int | None = None,
    force: bool = False,
) -> tuple[bool, dict, str]:
    """摄取文本文档。
    Returns: (ok, result, message)
    """
    try:
        result = ingester.ingest_text(text, meta, chunk_size=chunk_size,
                                      overlap=overlap, text_splitter=text_splitter,
                                      chunk_max_tokens=chunk_max_tokens, force=force)
        doc_id = result.get("doc_id", "")
        if result.get("conflict") or result.get("error"):
            # 冲突(Qdrant 未启动/写入失败已回滚):返回失败+明确提示,不标记 BM25 脏
            return False, result, result.get("message", "上传失败")
        chunks_written = result.get("chunks_written", 0)
        if chunks_written == 0:
            return False, result, f"文档 {doc_id} 切块后为空，未写入"
        # 标记BM25为脏，下次检索懒重建
        mark_bm25_dirty()
        msg = f"成功摄取文档 {doc_id}: {chunks_written} chunks"
        return True, result, msg
    except Exception as e:
        msg = f"摄取失败: {e}"
        logger.error(msg, exc_info=True)
        return False, {}, msg




def parse_preview(file_path: str, backends: list[str], chunk_size: int = 512) -> list[dict]:
    """同一文件多后端解析预览(不写库):每路 try_backend → probe_text → chunk_probe(脱#)。
    返回 [{backend, ok, err, chars, lines, md_heads, part, article, subitem, numbered,
          chunks, section_fill_pct, avg_path_len, overlong, excerpt}]"""
    import os
    from app.config import load as load_cfg
    from app.retrieval.ingest.reader import try_backend, BACKEND_NAMES
    from app.retrieval.ingest.probe import probe_text, chunk_probe, build_outline, chunk_preview_list
    # 预览与真实摄取同口径:结构化切块走 config 的 embedding token 预算(防"预览口径≠入库口径")
    _budget = int(getattr(load_cfg(), "chunk_max_tokens", 0) or 0) or None
    ext = os.path.splitext(file_path)[1].lower()
    doc_type = {".pdf": "policy_pdf", ".docx": "policy_docx", ".xlsx": "rate_table"}.get(ext, "policy_document")
    import time
    rows = []
    for b in backends:
        if b not in BACKEND_NAMES or b == "auto":
            continue
        t0 = time.perf_counter()
        ok, text, err = try_backend(file_path, b)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        if not ok:
            rows.append({"backend": b, "ok": False, "err": err, "chars": 0, "lines": 0,
                        "md_heads": 0, "part": 0, "article": 0, "subitem": 0, "numbered": 0,
                        "chunks": 0, "section_fill_pct": 0.0, "avg_path_len": 0.0,
                        "overlong": 0, "excerpt": "", "outline": [], "text": "",
                        "chunks_view": [], "elapsed_ms": elapsed_ms})
            continue
        p = probe_text(text)
        cp = chunk_probe(text, doc_type=doc_type, chunk_size=chunk_size, strip_md=True, max_tokens=_budget)
        excerpt = " ".join((text or "").split())[:220]
        outline = build_outline(text, doc_type=doc_type)
        rows.append({"backend": b, "ok": True, "err": "",
                    "chars": p["chars"], "lines": p["lines"],
                    "md_heads": sum(p["md_heads"].values()),
                    "part": p["part"], "article": p["article"],
                    "subitem": p["subitem"], "numbered": p["numbered"],
                    "chunks": cp["chunks"], "section_fill_pct": cp["section_fill_pct"],
                    "avg_path_len": cp["avg_path_len"], "overlong": cp["overlong"],
                    "excerpt": excerpt,
                    "outline": outline,
                    "text": text[:150000],
                    "chunks_view": chunk_preview_list(text, doc_type=doc_type, chunk_size=chunk_size,
                                                      max_tokens=_budget),
                    "elapsed_ms": elapsed_ms})
    return rows


def ingest_file(
    ingester: Ingester,
    file_path: str,
    category: str = "",
    parser: str = "auto",
    on_progress=None,
    chunk_size: int | None = None,
    overlap: int | None = None,
    text_splitter: str | None = None,
    chunk_max_tokens: int | None = None,
    product_name: str | None = None,
    force: bool = False,
) -> tuple[bool, dict, str, str]:
    """摄取本地文件(parser: auto|mineru|markitdown|pdfplumber|native)。
    on_progress(stage, done, total) 透传 Ingester(嵌入进度上报)。
    Returns: (ok, result, message, parser)"""
    try:
        result = ingester.ingest_file(file_path, category, parser, on_progress,
                                      chunk_size=chunk_size, overlap=overlap, text_splitter=text_splitter,
                                      chunk_max_tokens=chunk_max_tokens,
                                      product_name=product_name, force=force)
        doc_id = result.get("doc_id", "")
        if result.get("conflict") or result.get("error"):
            return False, result, result.get("message", "上传失败"), parser
        chunks_written = result.get("chunks_written", 0)
        if not doc_id or chunks_written == 0:
            return False, result, f"解析为空/不支持(parser={parser}): {file_path}", parser
        mark_bm25_dirty()
        msg = f"成功摄取 {doc_id}(parser={parser}): {chunks_written} chunks"
        return True, result, msg, parser
    except Exception as e:
        msg = f"摄取失败: {e}"
        logger.error(msg, exc_info=True)
        return False, {}, msg, parser

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


def preview_upload(
    file_path: str,
    parser: str = "mineru",
    text_splitter: str | None = None,
    chunk_size: int | None = None,
    overlap: int | None = None,
    chunk_max_tokens: int | None = None,
) -> tuple[bool, dict, str]:
    """单文件单后端"预览切块"(不写库、不发嵌入):解析 + 按指定切块方式切块,返回 outline + chunks。
    供上传页"预览切块→确认并索引"复用,避免二次解析(commit 直接用这里的 chunks/outline)。
    Returns: (ok, result, msg);result 含 doc_type/parser/text_splitter/chunk_count/chunk_size/overlap/outline/chunks
    """
    from app.config import load as load_cfg
    from app.retrieval.ingest.reader import build_docs, BACKEND_NAMES
    from app.retrieval.doc_structure import build_from_outline
    from app.retrieval.chunker import chunk_documents
    cfg = load_cfg()
    cs = int(getattr(cfg, "chunk_size", 1000) or 1000) if chunk_size is None else int(chunk_size)
    ov = int(getattr(cfg, "chunk_overlap", 200) or 200) if overlap is None else int(overlap)
    ts = (text_splitter or getattr(cfg, "text_splitter", "structured")) or "structured"
    if ts not in ("structured", "character", "paragraph"):
        ts = "structured"
    if parser not in BACKEND_NAMES:
        return False, {}, f"未知解析后端: {parser}"
    # 与摄取同一 reader(build_docs),确保预览的解析结果与"确认索引"真正写库的完全一致(避免二次解析漂移)
    docs = build_docs(file_path, "", parser)
    if not docs:
        return False, {}, "解析为空/不支持(parser=%s)" % parser
    meta = docs[0]["meta"]
    doc_type = meta.get("doc_type", "policy_document")
    text = docs[0]["text"]
    mt = (int(chunk_max_tokens) if chunk_max_tokens is not None
          else int(getattr(cfg, "chunk_max_tokens", 0) or 0)) or None
    chunks = chunk_documents([{"text": text, "meta": dict(meta)}],
                             chunk_size=cs, overlap=ov, text_splitter=ts, max_tokens=mt)
    outline = []
    try:
        outline = build_from_outline(text or "", doc_type)
    except Exception:  # noqa: BLE001
        outline = []
    chunk_items = [{"section": c.get("meta", {}).get("section", ""),
                    "title": c.get("meta", {}).get("title", ""),
                    "content": c.get("content", "")} for c in chunks]
    return True, {
        "doc_type": doc_type, "parser": parser, "text_splitter": ts,
        "chunk_count": len(chunk_items), "chunk_size": cs, "overlap": ov,
        "outline": outline, "chunks": chunk_items,
    }, ""


def commit_upload(
    ingester,
    doc_meta: dict,
    chunk_items: list[dict],
    outline: list[dict] | None,
    on_progress=None,
    force: bool = False,
) -> tuple[bool, dict, str]:
    """确认索引:commit 预览得到的 chunks/outline(不再解析)写库 + doc_structure + 嵌入 + Qdrant。
    on_progress(stage, done, total) 透传 write_chunks(嵌入进度)。force: 同名产品内容不同时强制覆盖。
    Returns: (ok, result, msg)
    """
    try:
        resp = ingester.write_chunks(doc_meta, chunk_items, outline or None,
                                     on_progress=on_progress, force=force)
        doc_id = resp.get("doc_id", "")
        if resp.get("conflict") or resp.get("error"):
            return False, resp, resp.get("message", "上传失败")
        if not resp.get("chunks_written", 0):
            return False, resp, "切块后为空,未写入"
        mark_bm25_dirty()
        msg = f"成功摄取 {doc_id}: {resp.get('chunks_written', 0)} chunks"
        return True, resp, msg
    except Exception as e:
        msg = f"摄取失败: {e}"
        logger.error(msg, exc_info=True)
        return False, {}, msg