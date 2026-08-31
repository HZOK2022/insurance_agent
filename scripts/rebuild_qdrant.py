# -*- coding: utf-8 -*-
"""从 SQLite chunks 事实源重建 Qdrant 向量索引(黄金法则:Qdrant 可重建)。

用法: python scripts/rebuild_qdrant.py [--clear]
丢失/清空 Qdrant 后,用本脚本从 knowledge.db(事实源)重灌,不丢数据。
"""
from __future__ import annotations
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import load
from app.retrieval.knowledge_store import KnowledgeStore
from app.retrieval.embedder import Embedder
from app.retrieval.qdrant_store import QdrantStore


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clear", action="store_true", help="重建前清空 Qdrant 集合")
    a = ap.parse_args()
    cfg = load()

    kstore = KnowledgeStore(getattr(cfg, "knowledge_db_path", "data/knowledge.db"))
    chunks = kstore.all_chunks()
    if not chunks:
        print("[rebuild] 事实源(knowledge.db)为空,无可重建")
        return
    if a.clear:
        from qdrant_client import QdrantClient
        QdrantClient(url=cfg.qdrant_url).delete_collection(cfg.qdrant_collection)
    store = QdrantStore(cfg.qdrant_url, cfg.qdrant_collection, cfg.embedding_dim)
    embedder = Embedder(cfg.embedding_model, cfg.embedding_device, cfg.embedding_batch_size)
    B = 64
    for i in range(0, len(chunks), B):
        batch = chunks[i:i + B]
        vecs = embedder.embed([c["content"] for c in batch])
        store.upsert([{"vector": v, "content": c["content"], "meta": c["meta"]}
                      for c, v in zip(batch, vecs)])
    print(f"[rebuild] {len(chunks)} chunks -> '{cfg.qdrant_collection}' (from SQLite fact source)")


if __name__ == "__main__":
    main()
