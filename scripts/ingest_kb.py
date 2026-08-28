"""摄取知识文件到 Qdrant(insurance_knowledge)。

用法: python scripts/ingest_kb.py --path <文件|目录> [--clear] [--limit N]
支持 .txt/.md(直接文本)与 .pdf(pdfplumber 抽文本)。
"""
from __future__ import annotations
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import load
from app.retrieval.chunker import chunk_documents
from app.retrieval.embedder import Embedder
from app.retrieval.qdrant_store import QdrantStore

_EXTS = (".txt", ".md", ".pdf")


def read_text(path: str):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".txt", ".md"):
        with open(path, encoding="utf-8") as f:
            return f.read()
    if ext == ".pdf":
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
    return None


def build_docs(path: str):
    text = read_text(path)
    if not text:
        return []
    base = os.path.splitext(os.path.basename(path))[0]
    return [{"text": text,
             "meta": {"chunk_id": base, "doc_id": base, "version": "v1", "section": "",
                      "doc_type": "policy_document", "source": path, "title": base}}]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", required=True)
    ap.add_argument("--clear", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="仅前 N 个文件(测试用)")
    a = ap.parse_args()

    cfg = load()
    print(f"[cfg] collection={cfg.qdrant_collection} embedding={cfg.embedding_model} chunk={cfg.chunk_size}/{cfg.chunk_overlap}")

    # 先删(若 --clear),再建 store —— store 的 _ensure 会在缺集合时重建
    if a.clear:
        from qdrant_client import QdrantClient
        QdrantClient(url=cfg.qdrant_url).delete_collection(cfg.qdrant_collection)
        print("[clear] 已删除集合:", cfg.qdrant_collection)
    store = QdrantStore(cfg.qdrant_url, cfg.qdrant_collection, cfg.embedding_dim)

    files = []
    if os.path.isfile(a.path):
        files = [a.path]
    else:
        for root, _, fs in os.walk(a.path):
            for fn in fs:
                if fn.lower().endswith(_EXTS):
                    files.append(os.path.join(root, fn))
    if a.limit:
        files = files[:a.limit]

    docs = []
    for f in files:
        docs += build_docs(f)
    chunks = chunk_documents(docs, cfg.chunk_size, cfg.chunk_overlap)
    print(f"[files] {len(files)} -> [chunks] {len(chunks)}")

    embedder = Embedder(cfg.embedding_model, cfg.embedding_device, cfg.embedding_batch_size)
    B = 64
    i = 0
    while i < len(chunks):
        batch = chunks[i:i + B]
        vecs = embedder.embed([c["content"] for c in batch])
        store.upsert([{"vector": v, "content": c["content"], "meta": c["meta"]}
                      for c, v in zip(batch, vecs)])
        i += B
    print(f"[done] ingested {len(chunks)} chunks -> '{cfg.qdrant_collection}'")


if __name__ == "__main__":
    main()
