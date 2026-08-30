"""search_knowledge 工具:嵌入查询 → (稠密检索 [+ 混合融合]) → 重排 → RetrievalChunk[]。"""
from __future__ import annotations

from app.retrieval.hybrid import fuse_and_pick


def to_chunk(hit: dict, score) -> dict:
    m = hit.get("meta", {})
    return {"chunk_id": hit["chunk_id"], "score": score, "doc_id": m.get("doc_id", ""),
            "version": m.get("version", ""), "section": m.get("section", ""),
            "source": m.get("source", ""), "content": hit.get("content", "")}


def search_knowledge(embedder, store, query: str, top_k: int = 20, top_rerank: int = 3,
                     rerank_fn=None, hybrid=None, hybrid_weight: float = 0.0) -> list[dict]:
    """检索:默认纯稠密;hybrid + hybrid_weight>0 时与 BM25 融合(recall 更大),再重排。

    hybrid: BM25Index(含 chunks_by_id);hybrid_weight: 0=纯稠密,1=纯 BM25。
    """
    qvec = embedder.embed([query])
    if not qvec:
        return []
    use_hybrid = hybrid is not None and hybrid_weight > 0
    pool = top_k * 2 if use_hybrid else top_k          # 混合时扩大稠密候选池,避免融合后被截断丢回调
    dense_hits = store.search(qvec[0], pool)
    if use_hybrid:
        dense_map = {h["chunk_id"]: float(h["score"]) for h in dense_hits}
        dense_by_id = {h["chunk_id"]: h for h in dense_hits}
        bm25_map = dict(hybrid.search(query, top_k * 2))
        fused = fuse_and_pick(dense_map, bm25_map, hybrid_weight, top_k)
        hits = []
        for cid, combined in fused:
            h = dense_by_id.get(cid) or hybrid.chunks_by_id.get(cid)
            if not h:
                continue
            hh = dict(h)
            hh["score"] = combined
            hits.append(hh)
        if not hits:
            hits = dense_hits[:top_k]
    else:
        hits = dense_hits[:top_k]
    if rerank_fn and len(hits) > 1:
        docs = [h["content"] for h in hits]
        res = rerank_fn(query, docs)
        if res:
            hits = [hits[int(i["index"])] for i in sorted(res, key=lambda x: x.get("relevance_score", 0), reverse=True)
                    if int(i["index"]) < len(hits)]
            hits = hits[:top_rerank]
    return [to_chunk(h, h.get("score")) for h in hits]
