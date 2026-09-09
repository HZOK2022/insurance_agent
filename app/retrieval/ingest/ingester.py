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
from app.retrieval.hash_util import content_hash, text_fingerprint

logger = logging.getLogger(__name__)

_BATCH_SIZE = 64


def _resolve_product(meta: dict) -> str:
    """D75:product_name 是文档的归属列(产品下拉),与 doc_id(内容指纹)完全解耦。
    显式传(含空=不绑定产品)即用;无 key(旧 CLI)视为不绑定。不再回退成 doc_id。"""
    return (meta.get("product_name") or "").strip()


class Ingester:
    """摄取管线:文本/文件 → 切块 → SQLite(事实源) → 嵌入 → Qdrant(派生索引)。"""

    def __init__(self, kstore: KnowledgeStore, qstore: QdrantStore, embedder: Embedder):
        self.kstore = kstore
        self.qstore = qstore
        self.embedder = embedder

    def _duplicate_result(self, doc_id: str, label: str) -> dict:
        return {"chunks_written": 0, "chunks_embedded": 0, "doc_id": doc_id,
                "duplicate": True, "duplicate_of": label,
                "message": f"内容与已有文档《{label}》完全相同,未重复入库"}

    def _precheck(self, doc_id: str, fps: tuple[str, ...]) -> dict | None:
        """D75 判重·写库前拦截(任何存储都不碰):
        ① 主键级:doc_id 已是"原文归一化内容指纹",同 doc_id 已存在 = 完全相同文档 → duplicate 硬拦;
        ② 兜底:用指纹候选(含旧口径 chunks_fp)查其他文档命中 → duplicate(兼容存量旧键,防跨键重复)。
        doc_id=指纹后不存在"同键不同内容",故 conflict/force 覆盖概念已移除——内容不同即指纹不同即新文档。"""
        if doc_id and self.kstore.get_document(doc_id):
            return self._duplicate_result(doc_id, doc_id[:12])
        finder = getattr(self.kstore, "find_doc_by_hash", None)
        dup = finder(fps, exclude_doc_id=doc_id) if finder else None
        if dup:
            label = (dup.get("title") or dup.get("doc_id") or "").strip()
            return self._duplicate_result(doc_id, label or dup.get("doc_id", ""))
        return None

    def ingest_text(self, text: str, meta: dict, on_progress=None,
                    chunk_size: int | None = None, overlap: int | None = None,
                    text_splitter: str | None = None, chunk_max_tokens: int | None = None,
                    min_heading_level: int | None = None) -> dict:
        """摄取原始文本。
        Args:
            text: 文档内容
            meta: 文档元数据,含 product_name(归属列,可空)、title、version、doc_type、product_category 等。
                doc_id 不再由调用方指定——它由 text_fingerprint(text) 派生(内容唯一身份,D75)。
            on_progress: 可选回调(stage, done, total),stage in chunked/embed/qdrant
            chunk_size/overlap/text_splitter/chunk_max_tokens/min_heading_level: 覆盖 config 默认(切块口径集中 config,铁律4);
                传 None 则取 config。
        Returns: {chunks_written, chunks_embedded, doc_id}(判重时另带 duplicate + message)
        """
        meta = dict(meta)
        doc_id = text_fingerprint(text)
        product_name = _resolve_product(meta)
        meta["doc_id"] = doc_id
        meta["product_name"] = product_name
        # D97:上传默认生效(is_valid=True);version 按 (产品名,文档名) 逻辑组自动序号(粗判归组,杜绝手动五花八门)
        meta["is_valid"] = True
        meta["version"] = self.kstore.next_version(product_name, (meta.get("title") or "").strip())
        docs = [{"text": text, "meta": meta}]
        # 切块口径取 config(铁律4:阈值集中 config):结构化路径按 token 预算(chunk_max_tokens),
        # 无结构兜底按 chunk_size 字符滑窗;不再硬编码 512 字符。
        from app.config import load
        cfg = load()
        cs = int(getattr(cfg, "chunk_size", 1000) or 1000) if chunk_size is None else int(chunk_size)
        ov = int(getattr(cfg, "chunk_overlap", 200) or 200) if overlap is None else int(overlap)
        ts = getattr(cfg, "text_splitter", "structured") if text_splitter is None else text_splitter
        mt = (int(chunk_max_tokens) if chunk_max_tokens is not None
              else int(getattr(cfg, "chunk_max_tokens", 0) or 0)) or None
        mhl = (int(min_heading_level) if min_heading_level is not None
               else int(getattr(cfg, "chunk_min_heading_level", 6) or 6))
        chunks = chunk_documents(docs,
                                 chunk_size=cs, overlap=ov,
                                 text_splitter=ts, max_tokens=mt, min_heading_level=mhl)
        if not chunks:
            return {"chunks_written": 0, "chunks_embedded": 0, "doc_id": doc_id}
        # D75 判重:doc_id 即内容指纹,同 doc_id 已存在或指纹匹配他文档 → duplicate 硬拦(不写任何存储)
        dupe = self._precheck(doc_id, (doc_id, content_hash(chunks)))
        if dupe:
            return dupe
        for ch in chunks:
            ch["meta"]["product_name"] = product_name

        # 1. 落 SQLite 事实源
        written = self.kstore.upsert_chunks(chunks)
        if on_progress:
            on_progress("chunked", len(chunks), len(chunks))
        self._write_doc_structure(doc_id, text, meta, None)
        if doc_id:
            doc_title = (meta.get("title") or "").strip() or product_name or "未命名文档"
            self.kstore.upsert_document({"doc_id": doc_id, "product_name": product_name,
                "product_category": meta.get("product_category", ""), "version": meta.get("version", ""),
                "title": doc_title, "source": meta.get("source", ""),
                "content_hash": doc_id, "is_valid": True})
        # 原子性:SQLite 与 Qdrant 同成功同失败 —— Qdrant 未启动则整单失败并回滚
        if self.qstore.is_down():
            logger.warning("Qdrant 未启动,上传中止(未写入)")
            return {"chunks_written": 0, "chunks_embedded": 0, "doc_id": doc_id,
                    "error": "knowledge_base_down", "message": "知识库向量服务(Qdrant)未启动,上传已取消,未保存任何内容;请先启动 Qdrant 再上传"}
        try:
            contents = [c["content"] for c in chunks]
            vectors = self.embedder.embed(contents, on_progress=(
                (lambda d, t, on_progress=on_progress: on_progress("embed", d, t)) if on_progress else None))
            for i in range(0, len(chunks), _BATCH_SIZE):
                batch = chunks[i:i + _BATCH_SIZE]
                vecs = vectors[i:i + _BATCH_SIZE]
                self.qstore.upsert([{"vector": v, "content": c["content"], "meta": c["meta"]}
                                    for c, v in zip(batch, vecs)])
            if on_progress:
                on_progress("qdrant", len(chunks), len(chunks))
        except Exception as e:  # noqa: BLE001
            logger.error("向量索引写入失败,回滚 %s: %s", doc_id, e)
            try:
                self._drop_doc(doc_id)
            except Exception:  # noqa: BLE001
                pass
            return {"chunks_written": 0, "chunks_embedded": 0, "doc_id": doc_id,
                    "error": "index_write_failed", "message": "写入向量索引失败,已回滚(未保存);请检查知识库向量服务后再试"}
        logger.info("ingested doc_id=%s chunks=%d", doc_id, written)
        return {"chunks_written": written, "chunks_embedded": len(chunks), "doc_id": doc_id}

    def write_chunks(self, meta: dict, chunk_items, outline=None, on_progress=None,
                     force: bool = False) -> dict:
        """写入已切好的块(供"预览→确认索引"复用,避免二次解析):赋 chunk_id → SQLite → doc_structure → 嵌入 → Qdrant。
        chunk_items: [{content, section, title}];outline: doc_structure 节点列表(可为 None 则跳过)。
        meta 需带 product_name(归属列,可空)、title、version、doc_type;可带 content_fp(preview 阶段算好的
        原文指纹,commit 路径无原文,doc_id 由它派生;缺省 fallback 到 chunks_fp)。doc_id = 内容唯一身份(D75)。
        判重:doc_id 即内容指纹,同 doc_id 已存在或指纹匹配他文档 → duplicate 硬拦。
        """
        chunks_fp = content_hash(chunk_items)
        text_fp = (meta.get("content_fp") or "").strip()
        doc_id = text_fp or chunks_fp
        product_name = _resolve_product(meta)
        dupe = self._precheck(doc_id, (doc_id, chunks_fp))
        if dupe:
            return dupe
        meta = {k: v for k, v in dict(meta).items() if k != "content_fp"}
        meta["doc_id"] = doc_id
        meta["product_name"] = product_name
        # D97:commit 路径上传默认生效;version 按 (产品名,文档名) 逻辑组自动序号(与 ingest_text 同源)
        meta["is_valid"] = True
        meta["version"] = self.kstore.next_version(product_name, (meta.get("title") or "").strip())
        chunks = []
        for i, it in enumerate(chunk_items):
            m = dict(meta)
            m["section"] = it.get("section") or m.get("section", "")
            m["title"] = it.get("title") or m.get("title", "")
            m["chunk_id"] = f"{doc_id}:{i}" if doc_id else f"chunk:{i}"
            chunks.append({"content": it.get("content", ""), "meta": m})
        if not chunks:
            return {"chunks_written": 0, "chunks_embedded": 0, "doc_id": doc_id}
        written = self.kstore.upsert_chunks(chunks)
        if on_progress:
            on_progress("chunked", len(chunks), len(chunks))
        if outline:
            try:
                self.kstore.set_doc_structure(doc_id, outline)
            except Exception:  # noqa: BLE001
                pass
        # 事实源(SQLite)先落:documents 行
        if doc_id:
            doc_title = (meta.get("title") or "").strip() or product_name or "未命名文档"
            self.kstore.upsert_document({"doc_id": doc_id, "product_name": product_name,
                "product_category": meta.get("product_category", ""), "version": meta.get("version", ""),
                "title": doc_title, "source": meta.get("source", ""),
                "content_hash": doc_id, "is_valid": True})
        # 原子性:数据库(SQLite)与 Qdrant 同成功同失败 —— Qdrant 未启动则整单失败并回滚(不写库、不假进度)
        if self.qstore.is_down():
            logger.warning("Qdrant 未启动,上传中止(未写入)")
            return {"chunks_written": 0, "chunks_embedded": 0, "doc_id": doc_id,
                    "error": "knowledge_base_down", "message": "知识库向量服务(Qdrant)未启动,上传已取消,未保存任何内容;请先启动 Qdrant 再上传"}
        try:
            contents = [c["content"] for c in chunks]
            vectors = self.embedder.embed(contents, on_progress=(
                (lambda d, t, on_progress=on_progress: on_progress("embed", d, t)) if on_progress else None))
            for i in range(0, len(chunks), _BATCH_SIZE):
                batch = chunks[i:i + _BATCH_SIZE]
                vecs = vectors[i:i + _BATCH_SIZE]
                self.qstore.upsert([{"vector": v, "content": c["content"], "meta": c["meta"]}
                                    for c, v in zip(batch, vecs)])
            if on_progress:
                on_progress("qdrant", len(chunks), len(chunks))
        except Exception as e:  # noqa: BLE001
            # 向量索引失败 → 回滚已写入的 SQLite(同成功同失败)
            logger.error("向量索引写入失败,回滚 %s: %s", doc_id, e)
            try:
                self._drop_doc(doc_id)
            except Exception:  # noqa: BLE001
                pass
            return {"chunks_written": 0, "chunks_embedded": 0, "doc_id": doc_id,
                    "error": "index_write_failed", "message": "写入向量索引失败,已回滚(未保存);请检查知识库向量服务后再试"}
        logger.info("wrote chunks doc_id=%s chunks=%d", doc_id, written)
        return {"chunks_written": written, "chunks_embedded": len(chunks), "doc_id": doc_id}

    def _drop_doc(self, doc_id: str) -> None:
        """同名产品重摄:先清旧 chunks/structure/Qdrant(干净替换,防残留旧块)。容忍失败。"""
        try:
            self.kstore.delete_document(doc_id)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.qstore.delete_by_doc_id(doc_id)
        except Exception:  # noqa: BLE001
            pass

    def _write_doc_structure(self, doc_id: str, text: str, meta: dict, file_path: str | None) -> None:
        """写 doc_structure:pfd 优先读内嵌书签(带页码),否则正则 outline。"""
        from app.retrieval.doc_structure import build_from_outline
        nodes: list[dict] = []
        # 只按正则 outline 建结构树(全层级,含（一）/1./(1));不读内嵌书签(其仅 1~2 级,层级不全)。
        # file_path 保留形参,不再使用。
        try:
            nodes = build_from_outline(text or "", doc_type=meta.get("doc_type", "policy_pdf"))
        except Exception:  # noqa: BLE001
            nodes = []
        if nodes and doc_id:
            self.kstore.set_doc_structure(doc_id, nodes)

    def ingest_file(self, file_path: str, category: str = "", backend: str | None = None,
                     on_progress=None, chunk_size: int | None = None, overlap: int | None = None,
                     text_splitter: str | None = None, chunk_max_tokens: int | None = None,
                     min_heading_level: int | None = None,
                     product_name: str | None = None) -> dict:
        """摄取单个文件。
        backend: None=取 config PARSER_BACKEND | auto(回退链)| mineru | markitdown | pdfplumber | native(仅 docx/xlsx)。
        on_progress: 可选回调(stage, done, total),见 ingest_text。
        chunk_size/overlap/text_splitter/chunk_max_tokens/min_heading_level: 覆盖 config 切块口径。
        product_name: 归属列(产品下拉);None=不绑定。doc_id 由内容指纹派生(D75),与产品无关。
        Returns: {chunks_written, chunks_embedded, doc_id}(判重时另带 duplicate + message)
        """
        if not is_supported(file_path):
            logger.warning("unsupported file: %s", file_path)
            return {"chunks_written": 0, "chunks_embedded": 0, "doc_id": ""}

        docs = build_docs(file_path, category, backend)
        if not docs:
            logger.warning("parse empty/unsupported backend=%s file=%s", backend, file_path)
            return {"chunks_written": 0, "chunks_embedded": 0, "doc_id": ""}

        if product_name is not None:
            # 显式传参(含空串=不绑定产品,D75)才写 key;None=旧 CLI 调用,走"不绑定"语义
            docs[0]["meta"]["product_name"] = product_name
        result = self.ingest_text(docs[0]["text"], docs[0]["meta"], on_progress,
                                  chunk_size=chunk_size, overlap=overlap, text_splitter=text_splitter,
                                  chunk_max_tokens=chunk_max_tokens, min_heading_level=min_heading_level)
        _doc = result.get("doc_id", "")
        if _doc and not (result.get("duplicate") or result.get("error")):
            # 文件路径已知:pdf 读内嵌书签(带页码),覆盖 ingest_text 的正则 outline(键=解析后的 doc_id)
            self._write_doc_structure(_doc, docs[0]["text"], docs[0]["meta"], file_path)
        return result
    def ingest_directory(self, dir_path: str, limit: int | None = None, category: str = "",
                         backend: str | None = None, on_progress=None,
                         min_heading_level: int | None = None) -> dict:
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
            r = self.ingest_file(f, category, backend, on_progress, min_heading_level=min_heading_level)
            if r["doc_id"]:
                result["docs"].append(r)
                result["total_chunks"] += r["chunks_written"]
        logger.info("ingested directory %s: %d files, %d chunks", dir_path, result["total_files"], result["total_chunks"])
        return result

    def full_reindex(self, on_progress=None) -> dict:
        """全量重建 Qdrant:从 SQLite 事实源读取所有 chunks → 重新嵌入 → 写入 Qdrant。
        遵循黄金法则:SQLite 是事实源,Qdrant 是可重建的派生索引。
        on_progress: 可选回调(stage, done, total)。
        """
        all_chunks = self.kstore.all_chunks()
        if not all_chunks:
            return {"total_chunks": 0, "embedded": 0, "message": "知识库为空,无需重建"}

        # 清空 Qdrant 集合
        self.qstore.delete_collection()
        # 重新创建集合(delete_collection 会删除集合,需要重新 ensure)
        self.qstore._ensure()

        # 分批嵌入 + 写入
        contents = [c["content"] for c in all_chunks]
        vectors = self.embedder.embed(contents, on_progress=(
            (lambda d, t, on_progress=on_progress: on_progress("embed", d, t)) if on_progress else None))
        for i in range(0, len(all_chunks), _BATCH_SIZE):
            batch = all_chunks[i:i + _BATCH_SIZE]
            vecs = vectors[i:i + _BATCH_SIZE]
            self.qstore.upsert([{"vector": v, "content": c["content"], "meta": c["meta"]}
                                for c, v in zip(batch, vecs)])
        if on_progress:
            on_progress("qdrant", len(all_chunks), len(all_chunks))

        n = len(all_chunks)
        logger.info("full reindex done: %d chunks -> Qdrant", n)
        return {"total_chunks": n, "embedded": n, "message": f"已重建 {n} 个 chunks 的向量索引"}