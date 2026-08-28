"""search_knowledge 工具:嵌入查询 → 检索 → 重排 → RetrievalChunk[]。"""
from __future__ import annotations


def to_chunk(hit: dict, score) -> dict:
    m = hit.get("meta", {})
    return {"chunk_id": hit["chunk_id"], "score": score, "doc_id": m.get("doc_id", ""),
            "version": m.get("version", ""), "section": m.get("section", ""),
            "source": m.get("source", ""), "content": hit.get("content", "")}


def search_knowledge(embedder, store, query: str, top_k: int = 20, top_rerank: int = 3,
                     rerank_fn=None) -> list[dict]:
    qvec = embedder.embed([query])
    if not qvec:
        return []
    hits = store.search(qvec[0], top_k)
    if rerank_fn and len(hits) > 1:
        docs = [h["content"] for h in hits]
        res = rerank_fn(query, docs)
        if res:
            hits = [hits[int(i["index"])] for i in sorted(res, key=lambda x: x.get("relevance_score", 0), reverse=True)
                    if int(i["index"]) < len(hits)]
            hits = hits[:top_rerank]
    return [to_chunk(h, h["score"]) for h in hits]
