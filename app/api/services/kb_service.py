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


def list_products(kstore: KnowledgeStore) -> list[str]:
    """按已上传文档聚合的产品名列表(去重、非空),供上传页"产品"下拉框。"""
    return kstore.list_products()


def _in_branch(sec: str, path: str) -> bool:
    """chunk.section(sec)是否属于目录节点分支(path):按 ">" 分段,path 段序列是 sec 段序列的前缀。

    只做"块在节点之下"的单向判断(段级,避免字符串前缀误伤如「投保要求」vs「投保要求书」);
    不反向把"比节点更浅的祖先块"挂进深层节点(否则每个节点都关联到文档根标题那块)。
    合并块(section 停在父级)归其父分支,不重复挂到每个叶子标题。
    """
    from app.retrieval.doc_structure import _norm
    a = [x for x in _norm(sec).split(">") if x]
    b = [x for x in _norm(path).split(">") if x]
    if not a or not b or len(b) > len(a):
        return False
    return all(a[i] == b[i] for i in range(len(b)))


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
    # 归一化 section 列表,便于段级前缀匹配
    norm_secs = [(_idx(cid), _norm(sec)) for cid, sec in secs]
    stack: list[dict] = []
    for n in nodes:
        while stack and stack[-1]["level"] >= n["level"]:
            stack.pop()
        np = _norm(" > ".join([x["title"] for x in stack] + [n["title"]]))
        # 同一分支即关联:块 section 在节点之下(段级前缀;祖先块不反向挂入)
        ids = sorted(i for i, s in norm_secs if s and _in_branch(s, np))
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
    """删除文档:事实源(MySQL/SQLite)与 Qdrant 派生索引必须同步删,任一步失败整体中止,不留半状态。
    顺序:先删易失的派生索引(Qdrant,可重建)——失败则中止并保留事实源,不会出现
    "事实源已删但索引残留仍被检索";成功后删事实源(Qdrant已删无残留,事实源删失败
    也可靠全量 reindex 重建,不丢数据)。Qdrant 服务不可用时索引层本为空、无残留,可安全只删事实源。
    Returns: (ok, chunks_deleted, points_deleted, message)
    """
    import time
    t0 = time.perf_counter()
    dialect = "mysql" if getattr(kstore, "_is_mysql", False) else "sqlite"

    # 0. 先确认事实源存在(决定可删性,避免误报"不存在")
    if kstore.get_document(doc_id) is None:
        return False, 0, 0, f"文档 {doc_id} 不存在"

    # 1. 删Qdrant索引(易失、可重建);失败整体中止,事实源保留,未产生半状态
    points_deleted = 0
    qdrant_ms = 0
    if not qstore.is_down():   # Qdrant可用时才需要删;不可用时索引层本为空,无残留
        _t = time.perf_counter()
        try:
            points_deleted = qstore.delete_by_doc_id(doc_id)
        except Exception as e:
            qdrant_ms = int((time.perf_counter() - _t) * 1000)
            msg = f"删除失败:Qdrant索引删除出错({e});文档未删除,请重试或先全量reindex再删"
            logger.warning("【知识库删除】失败:文档《%s》的Qdrant索引删除出错(err=%r,qdrant耗时=%dms,dialect=%s),已中止未删除——请先全量reindex再删",
                           doc_id, e, qdrant_ms, dialect,
                           extra={"op": "kb.delete_document", "doc_id": doc_id, "stage": "qdrant",
                                  "dialect": dialect, "qdrant_ms": qdrant_ms})
            return False, 0, 0, msg
        qdrant_ms = int((time.perf_counter() - _t) * 1000)
        if points_deleted < 0:
            points_deleted = 0   # Qdrant成功但计数未知,由事实源计数校准

    # 2. 再删事实源(最后删,Qdrant已删,删失败可靠reindex重建,不丢数据)
    try:
        chunks_deleted = kstore.delete_document(doc_id)
    except Exception as e:
        total_ms = int((time.perf_counter() - t0) * 1000)
        msg = f"Qdrant索引已删除,但事实源删除失败: {e} → 请执行全量reindex修复一致性"
        logger.error("【知识库删除】失败:文档《%s》的Qdrant索引已删,但事实源删除出错(err=%r,qdrant=%dms,总=%dms,dialect=%s)——请执行全量reindex修复一致性",
                     doc_id, e, qdrant_ms, total_ms, dialect,
                     extra={"op": "kb.delete_document", "doc_id": doc_id, "stage": "store",
                            "dialect": dialect, "qdrant_ms": qdrant_ms, "total_ms": total_ms})
        return False, 0, 0, msg

    # 3. 标记BM25为脏,下次检索懒重建
    mark_bm25_dirty()
    pt = points_deleted if points_deleted > 0 else chunks_deleted
    total_ms = int((time.perf_counter() - t0) * 1000)
    msg = f"成功删除文档 {doc_id}: {chunks_deleted} chunks 从事实源({dialect}), {pt} points 从Qdrant"
    logger.info("【知识库删除】成功:文档《%s》已删除——事实源删%d块、Qdrant删%d点(dialect=%s,qdrant=%dms,总=%dms)",
                doc_id, chunks_deleted, pt, dialect, qdrant_ms, total_ms,
                extra={"op": "kb.delete_document", "doc_id": doc_id, "chunks_deleted": chunks_deleted,
                       "points_deleted": pt, "dialect": dialect, "qdrant_ms": qdrant_ms, "total_ms": total_ms})
    return True, chunks_deleted, pt, msg


def ingest_text(
    ingester: Ingester,
    text: str,
    meta: dict,
    chunk_size: int | None = None,
    overlap: int | None = None,
    text_splitter: str | None = None,
    chunk_max_tokens: int | None = None,
    min_heading_level: int | None = None,
) -> tuple[bool, dict, str]:
    """摄取文本文档。
    Returns: (ok, result, message)
    """
    import time
    t0 = time.perf_counter()
    try:
        result = ingester.ingest_text(text, meta, chunk_size=chunk_size,
                                      overlap=overlap, text_splitter=text_splitter,
                                      chunk_max_tokens=chunk_max_tokens,
                                      min_heading_level=min_heading_level)
        doc_id = result.get("doc_id", "")
        chunks_written = int(result.get("chunks_written", 0) or 0)
        elapsed = int((time.perf_counter() - t0) * 1000)
        _ex = {"op": "kb.ingest_text", "doc_id": doc_id, "chunks_written": chunks_written, "elapsed_ms": elapsed}
        if result.get("duplicate"):
            logger.info("【知识库上传】检测到重复:已存在相同内容文档《%s》,未重复入库(耗时=%dms)", doc_id, elapsed, extra=_ex)
            return False, result, result.get("message", "内容与已有文档完全相同")
        if result.get("error"):
            # 服务不可用/索引失败已回滚:失败+明确提示,不标记 BM25 脏
            logger.warning("【知识库上传】失败:文档《%s》未入库(err=%s,耗时=%dms)", doc_id, result.get("error"), elapsed, extra=_ex)
            return False, result, result.get("message", "上传失败")
        if chunks_written == 0:
            logger.warning("【知识库上传】切块后为空,文档《%s》未写入(耗时=%dms)", doc_id, elapsed, extra=_ex)
            return False, result, f"文档 {doc_id} 切块后为空，未写入"
        # 标记BM25为脏，下次检索懒重建
        mark_bm25_dirty()
        logger.info("【知识库上传】成功:文档《%s》已入库,共%d块(耗时=%dms)",
                    doc_id, chunks_written, elapsed, extra=_ex)
        return True, result, f"成功摄取文档 {doc_id}: {chunks_written} chunks"
    except Exception as e:
        elapsed = int((time.perf_counter() - t0) * 1000)
        logger.exception("【知识库上传】异常:err=%r(耗时=%dms)", e, elapsed,
                         extra={"op": "kb.ingest_text", "elapsed_ms": elapsed})
        return False, {}, f"摄取失败: {e}"




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
    min_heading_level: int | None = None,
) -> tuple[bool, dict, str, str]:
    """摄取本地文件(parser: auto|mineru|markitdown|pdfplumber|native)。
    on_progress(stage, done, total) 透传 Ingester(嵌入进度上报)。
    Returns: (ok, result, message, parser)"""
    import time
    t0 = time.perf_counter()
    try:
        result = ingester.ingest_file(file_path, category, parser, on_progress,
                                      chunk_size=chunk_size, overlap=overlap, text_splitter=text_splitter,
                                      chunk_max_tokens=chunk_max_tokens,
                                      min_heading_level=min_heading_level,
                                      product_name=product_name)
        doc_id = result.get("doc_id", "")
        chunks_written = int(result.get("chunks_written", 0) or 0)
        elapsed = int((time.perf_counter() - t0) * 1000)
        _ex = {"op": "kb.ingest_file", "doc_id": doc_id, "chunks_written": chunks_written,
               "parser": parser, "elapsed_ms": elapsed}
        if result.get("duplicate"):
            logger.info("【文件上传】检测到重复:已存在相同内容文档《%s》,未重复入库(parser=%s,耗时=%dms)", doc_id, parser, elapsed, extra=_ex)
            return False, result, result.get("message", "内容与已有文档完全相同"), parser
        if result.get("error"):
            logger.warning("【文件上传】失败:文档《%s》未入库(parser=%s,err=%s,耗时=%dms)", doc_id, parser, result.get("error"), elapsed, extra=_ex)
            return False, result, result.get("message", "上传失败"), parser
        if not doc_id or chunks_written == 0:
            logger.warning("【文件上传】解析为空/不支持(parser=%s,file=%s,耗时=%dms)", parser, file_path, elapsed, extra=_ex)
            return False, result, f"解析为空/不支持(parser={parser}): {file_path}", parser
        mark_bm25_dirty()
        logger.info("【文件上传】成功:文档《%s》已入库,共%d块(parser=%s,耗时=%dms)",
                    doc_id, chunks_written, parser, elapsed, extra=_ex)
        return True, result, f"成功摄取 {doc_id}(parser={parser}): {chunks_written} chunks", parser
    except Exception as e:
        elapsed = int((time.perf_counter() - t0) * 1000)
        logger.exception("【文件上传】异常:parser=%s,err=%r(耗时=%dms)", parser, e, elapsed,
                         extra={"op": "kb.ingest_file", "parser": parser, "elapsed_ms": elapsed})
        return False, {}, f"摄取失败: {e}", parser

def full_reindex(
    kstore: KnowledgeStore,
    ingester: Ingester
) -> tuple[bool, dict, str]:
    """全量重建Qdrant索引: 从SQLite事实源重建。
    Returns: (ok, result, message)
    """
    import time
    t0 = time.perf_counter()
    try:
        result = ingester.full_reindex()
        total = int(result.get("total_chunks", 0) or 0)
        elapsed = int((time.perf_counter() - t0) * 1000)
        logger.info("【索引重建】成功:共重建%d个chunk(耗时=%dms)", total, elapsed,
                    extra={"op": "kb.full_reindex", "total_chunks": total, "elapsed_ms": elapsed})
        if total == 0:
            return True, result, "知识库为空，重建完成(无chunks)"
        return True, result, f"成功重建索引: {total} chunks"
    except Exception as e:
        elapsed = int((time.perf_counter() - t0) * 1000)
        logger.exception("【索引重建】异常:err=%r(耗时=%dms)", e, elapsed,
                         extra={"op": "kb.full_reindex", "elapsed_ms": elapsed})
        return False, {}, f"重建失败: {e}"


def preview_upload(
    file_path: str,
    parser: str = "mineru",
    text_splitter: str | None = None,
    chunk_size: int | None = None,
    overlap: int | None = None,
    chunk_max_tokens: int | None = None,
    min_heading_level: int | None = None,
) -> tuple[bool, dict, str]:
    """单文件单后端"预览切块"(不写库、不发嵌入):解析 + 按指定切块方式切块,返回 outline + chunks。
    供上传页"预览切块→确认并索引"复用,避免二次解析(commit 直接用这里的 chunks/outline)。
    result 另带 content_fp:正文归一化指纹,commit 用它作文档唯一身份 doc_id(D75)。
    Returns: (ok, result, msg);result 含 doc_type/parser/text_splitter/chunk_count/chunk_size/overlap/outline/chunks/content_fp
    """
    from app.config import load as load_cfg
    from app.retrieval.ingest.reader import build_docs, BACKEND_NAMES
    from app.retrieval.doc_structure import build_from_outline
    from app.retrieval.chunker import chunk_documents
    from app.retrieval.hash_util import text_fingerprint
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
    mhl = (int(min_heading_level) if min_heading_level is not None
           else int(getattr(cfg, "chunk_min_heading_level", 6) or 6))
    chunks = chunk_documents([{"text": text, "meta": dict(meta)}],
                             chunk_size=cs, overlap=ov, text_splitter=ts,
                             max_tokens=mt, min_heading_level=mhl)
    outline = []
    if ts == "structured":
        # 仅"结构层级"切分才抽取目录:非 structured(字符/段落)的块无结构 section,目录无意义,
        # 也不写 doc_structure(commit 传 outline 为空 → write_chunks 落空)。
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
        "content_fp": text_fingerprint(text),
    }, ""


def set_document_valid(
    kstore: KnowledgeStore,
    qstore: QdrantStore,
    doc_id: str,
    is_valid: bool,
) -> tuple[bool, str]:
    """切换文档生效/失效(D97)。第7条原子性:MySQL 事实源与 Qdrant 索引必须同生效同失效、
    同成功同失败——任一步失败整体回滚(保留原状态),并标记 BM25 脏触发下次检索重建。
    Returns: (ok, message)"""
    prev = kstore.get_document(doc_id)
    if prev is None:
        return False, f"文档 {doc_id} 不存在"
    prev_valid = bool(prev.get("is_valid"))
    if prev_valid == is_valid:
        return True, f"文档《{doc_id}》已是{'生效' if is_valid else '失效'}状态(无需切换)"
    # 1) 先 MySQL 事实源
    ok, emsg = kstore.set_document_valid(doc_id, is_valid)
    if not ok:
        return False, emsg
    # 2) 同步 Qdrant 索引;失败 → 回滚 MySQL(同成功同失败)
    if qstore.is_down():
        kstore.set_document_valid(doc_id, prev_valid)
        return False, "Qdrant 不可用,已取消切换(状态未改变)"
    try:
        qstore.set_is_valid_by_doc_id(doc_id, is_valid)
    except Exception as e:
        kstore.set_document_valid(doc_id, prev_valid)
        logger.error("【文档状态】失败:Qdrant is_valid同步失败已回滚,文档《%s》状态未改变(err=%s)", doc_id, e)
        return False, f"Qdrant 同步失败,已回滚(状态未改变): {e}"
    mark_bm25_dirty()
    logger.info("【文档状态】成功:文档《%s》已切换为%s", doc_id, "生效" if is_valid else "失效",
                extra={"op": "kb.set_document_valid", "doc_id": doc_id, "is_valid": is_valid})
    return True, f"文档《{doc_id}》已{'生效' if is_valid else '失效'}"


def commit_upload(
    ingester,
    doc_meta: dict,
    chunk_items: list[dict],
    outline: list[dict] | None,
    on_progress=None,
) -> tuple[bool, dict, str]:
    """确认索引:commit 预览得到的 chunks/outline(不再解析)写库 + doc_structure + 嵌入 + Qdrant。
    on_progress(stage, done, total) 透传 write_chunks(嵌入进度)。
    Returns: (ok, result, msg)
    """
    import time
    t0 = time.perf_counter()
    doc_id = str(doc_meta.get("doc_id") or doc_meta.get("title") or "")
    try:
        resp = ingester.write_chunks(doc_meta, chunk_items, outline or None,
                                     on_progress=on_progress)
        doc_id = resp.get("doc_id", "") or doc_id
        written = int(resp.get("chunks_written", 0) or 0)
        embedded = int(resp.get("chunks_embedded", 0) or 0)
        elapsed = int((time.perf_counter() - t0) * 1000)
        _ex = {"op": "kb.commit_upload", "doc_id": doc_id, "chunks_written": written,
               "chunks_embedded": embedded, "elapsed_ms": elapsed}
        if resp.get("duplicate"):
            _t = (doc_meta.get("title") or "").strip() or doc_id
            logger.warning("【知识库上传】检测到重复:已存在相同内容文档《%s》(doc_id=%s),未重复入库(已有块=%d,耗时=%dms)",
                           _t, doc_id, written, elapsed,
                           extra={**_ex, "title": _t})
            return False, resp, resp.get("message", "内容与已有文档完全相同,未重复入库")
        if resp.get("error"):
            logger.warning("【知识库上传】失败:文档《%s》未入库(err=%s,耗时=%dms)", doc_id, resp.get("error"), elapsed, extra=_ex)
            return False, resp, resp.get("message", "上传失败")
        if not written:
            logger.warning("【知识库上传】切块后为空,文档《%s》未写入(耗时=%dms)", doc_id, elapsed, extra=_ex)
            return False, resp, "切块后为空,未写入"
        mark_bm25_dirty()
        _t = (doc_meta.get("title") or "").strip() or doc_id
        logger.info("【知识库上传】成功:文档《%s》已入库,共%d块(已嵌入%d,目录%d节点,耗时=%dms)",
                    _t, written, embedded, len(outline or []), elapsed, extra={**_ex, "title": _t})
        return True, resp, f"成功摄取 {doc_id}: {written} chunks"
    except Exception as e:
        elapsed = int((time.perf_counter() - t0) * 1000)
        logger.exception("【知识库上传】异常:文档《%s》,err=%r(耗时=%dms)", doc_id, e, elapsed,
                         extra={"op": "kb.commit_upload", "doc_id": doc_id, "elapsed_ms": elapsed})
        return False, {}, f"摄取失败: {e}"