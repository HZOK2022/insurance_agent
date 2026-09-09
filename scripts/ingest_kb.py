"""摄取知识文件到 Qdrant(insurance_knowledge)。

用法: python scripts/ingest_kb.py --path <文件|目录> [--clear] [--limit N] [--category 保险类别]
支持 .txt/.md/.pdf/.docx/.xlsx。
"""
from __future__ import annotations
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import load
from app.retrieval.embedder import build_embedder
from app.retrieval.qdrant_store import QdrantStore
from app.retrieval.knowledge_store import KnowledgeStore
from app.retrieval.ingest import Ingester, is_supported, supported_extensions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", required=True)
    ap.add_argument("--clear", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="仅前 N 个文件(测试用)")
    ap.add_argument("--category", default="", help="保险类别(医疗险/重疾险/意外险/…);缺省按 doc_id 关键词判定")
    ap.add_argument("--parser", default="auto",
                    help="pdf/docx/xlsx 解析后端:auto(默认回退链)| mineru | markitdown | pdfplumber | native(docx/xlsx)")
    ap.add_argument("--min-heading-level", type=int, default=0,
                    help="md 结构化切分标题层级门槛(仅 markdown 生效;≤该深度才成块,覆盖 config CHUNK_MIN_HEADING_LEVEL)")
    a = ap.parse_args()

    cfg = load()
    print(f"[cfg] collection={cfg.qdrant_collection} embedding={cfg.embedding_model} chunk={cfg.chunk_size}/{cfg.chunk_overlap}")

    kstore = KnowledgeStore(cfg=cfg)

    # 先删(若 --clear) —— Qdrant 集合 + SQLite 事实源都清,重建
    if a.clear:
        from qdrant_client import QdrantClient
        QdrantClient(url=cfg.qdrant_url).delete_collection(cfg.qdrant_collection)
        kstore.conn.execute("DELETE FROM chunks"); kstore.conn.commit()
        print("[clear] 已删除集合 + SQLite chunks 事实源")

    qstore = QdrantStore(cfg.qdrant_url, cfg.qdrant_collection, cfg.embedding_dim)
    embedder = build_embedder(cfg)
    ingester = Ingester(kstore, qstore, embedder)

    mhl = a.min_heading_level or 0
    if os.path.isfile(a.path):
        if a.limit:
            print("[skip] --limit 仅对目录模式有效,单文件直接全量摄取")
        if not is_supported(a.path):
            print(f"[skip] 不支持的文件格式: {a.path}")
            return
        result = ingester.ingest_file(a.path, a.category, a.parser, min_heading_level=mhl)
        print(f"[done] ingested {result['doc_id']}: {result['chunks_written']} chunks -> SQLite + Qdrant")
    else:
        result = ingester.ingest_directory(a.path, a.limit, a.category, a.parser,
                                           min_heading_level=mhl)
        print(f"[done] 目录 {a.path}: {result['total_files']} 文件 -> {result['total_chunks']} chunks")


if __name__ == "__main__":
    main()