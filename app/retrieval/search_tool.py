"""search_knowledge 工具:嵌入查询 → (稠密检索 [+ 混合融合]) → 重排 → RetrievalChunk[]。"""
from __future__ import annotations

import logging
import time

from app.guardrails.rag import quarantine_suspicious
from app.retrieval.errors import RetrievalUnavailable
from app.retrieval.hybrid import fuse_and_pick, rrf_fuse_and_pick

logger = logging.getLogger(__name__)


def to_chunk(hit: dict, score) -> dict:
    m = hit.get("meta", {})
    return {"chunk_id": hit["chunk_id"], "score": score, "doc_id": m.get("doc_id", ""),
            "version": m.get("version", ""), "section": m.get("section", ""),
            "source": m.get("source", ""), "doc_type": m.get("doc_type", ""),
            "title": m.get("title", ""), "product_category": m.get("product_category", ""),
            "product_name": m.get("product_name", ""),
            "content": hit.get("content", "")}


def _dt_ok(doc_type: str, include, exclude) -> bool:
    """doc_type 过滤判定:include 给定则只留集合内;exclude 给定则剔除集合内。"""
    if include is not None and doc_type not in include:
        return False
    if exclude is not None and doc_type in exclude:
        return False
    return True


def _filter_doc_types(hits: list[dict], include, exclude) -> list[dict]:
    return [h for h in hits
            if _dt_ok((h.get("meta") or {}).get("doc_type") or "", include, exclude)]


def search_knowledge(embedder, store, query: str, top_k: int = 20, top_rerank: int = 3,
                     rerank_fn=None, hybrid=None, hybrid_weight: float = 0.0,
                     category: str | None = None, product: str | None = None,
                     timings: dict | None = None, fusion: str = "rrf",
                     rrf_k: int = 60,
                     include_doc_types=None, exclude_doc_types=None) -> list[dict]:
    """检索:默认纯稠密;hybrid + hybrid_weight>0 时与 BM25 融合(recall 更大),再重排。

    fusion: **rrf**(默认,D82)| weighted。两种均已实现,由 config.hybrid_fusion 决定,可切换对比。
    - rrf: Reciprocal Rank Fusion,按 1/(k+rank) 累加,**只看排名不看分数分布**。相比 weighted
      能避免"dense 只命中一个 gold 块时 min==max 归一 collapse 到 0、被 BM25 大批高分块挤出候选池"。
    - weighted: 旧 min-max 归一加权,保留仅供对比。

    hybrid: BM25Index(含 chunks_by_id);hybrid_weight: 开关 + 权重 —— >0 才启用混合构建;
    数值仅在 fusion=weighted 时参与融合(rrf 按排名融合不需要权重)。
    rrf_k: RRF 平滑常量(默认 60,业界惯例),只影响分值大小、不影响排序单调性。
    category: 可选保险类别(医疗险/重疾险/意外险/…)。软偏置:检索后把同类别块稳定提到前面,
    但不排除其它类别(top_k 不变,类别匹配优先,跨类别仍保留在低优先级)。
    用于用户明确指定险种时圈定检索范围。
    product: 可选产品名(如"尊享e生2025")。软偏置:把该产品的块稳定提到前面(不排除其它),
    使用户明确点名的产品优先命中。
    timings: 可选 dict 出参,就地记录四段耗时(ms):embed_ms / dense_ms / bm25_ms / rerank_ms。
    include_doc_types / exclude_doc_types: **硬过滤**(D91,与 category/product 的软偏置不同)。
    条款检索(search_knowledge)排除 sales_script 防话术污染事实引用;话术检索(search_sales_scripts)
    只留 sales_script。过滤在 dense/BM25 各自产出后立即做,防话术块挤占 rerank 名额。
    """
    _t0 = time.perf_counter()
    qvec = embedder.embed([query])
    if timings is not None:
        timings["embed_ms"] = int((time.perf_counter() - _t0) * 1000)
    if not qvec:
        return []
    use_hybrid = hybrid is not None and hybrid_weight > 0
    pool = top_k * 2 if (use_hybrid or category or product
                         or include_doc_types or exclude_doc_types) else top_k   # 过滤也扩池,防滤后不足 top_k
    try:
        _t0 = time.perf_counter()
        dense_hits = store.search(qvec[0], pool)
        if timings is not None:
            timings["dense_ms"] = int((time.perf_counter() - _t0) * 1000)
    except RetrievalUnavailable:
        # 向量库(稠密)不可用:不做 SQLite 关键词兜底作答(产品决策:残缺而自信的清单比诚实受限更危险)。
        # 注入零检索结果 → 抛 RetrievalUnavailable → _run_tool 记 retrieval_unavailable,LLM 依 SYSTEM 诚实拒答。
        raise
    # doc_type 硬过滤(D91):dense 产出后立即滤,防话术块进入重排/融合挤占名额。
    if include_doc_types is not None or exclude_doc_types is not None:
        dense_hits = _filter_doc_types(dense_hits, include_doc_types, exclude_doc_types)
    # 分数阶梯(A3):记录每块在各阶段原始分值(dense/bm25/fused),供 trace 展示"为何召回/排这块"。
    ladder: dict[str, dict] = {}
    if use_hybrid:
        _t0 = time.perf_counter()
        dense_map = {h["chunk_id"]: float(h["score"]) for h in dense_hits}
        # 四路 rank(D82):RRF 的输入就是排名,前端阶梯展示 rank 比展示 RRF 分可解释得多;
        # 且 bm25-only 块在 dense 里没有位置 —— rank 必须由后端记录,前端算不出来。
        dense_rank = {h["chunk_id"]: i + 1 for i, h in enumerate(dense_hits)}
        dense_by_id = {h["chunk_id"]: h for h in dense_hits}
        _bm25_res = hybrid.search(query, top_k * 2)
        if include_doc_types is not None or exclude_doc_types is not None:
            # doc_type 硬过滤(D91):BM25 产出同样过滤(语料含话术块,不过滤会从融合路混入)。
            _bm25_res = [(cid, s) for cid, s in _bm25_res
                         if _dt_ok(((hybrid.chunks_by_id.get(cid) or {}).get("meta") or {}).get("doc_type") or "",
                                   include_doc_types, exclude_doc_types)]
        bm25_map = dict(_bm25_res)
        bm25_rank = {cid: i + 1 for i, (cid, _s) in enumerate(_bm25_res)}
        if fusion == "weighted":
            fused = fuse_and_pick(dense_map, bm25_map, hybrid_weight, top_k)
        else:
            fused = rrf_fuse_and_pick(dense_map, bm25_map, k=rrf_k, top_k=top_k)
        fused_rank = {cid: i + 1 for i, (cid, _s) in enumerate(fused)}
        if timings is not None:
            timings["bm25_ms"] = int((time.perf_counter() - _t0) * 1000)
        hits = []
        for cid, combined in fused:
            h = dense_by_id.get(cid) or hybrid.chunks_by_id.get(cid)
            if not h:
                continue
            hh = dict(h)
            hh["score"] = combined
            ladder[cid] = {"dense": dense_map.get(cid), "bm25": bm25_map.get(cid), "fused": float(combined),
                           "dense_rank": dense_rank.get(cid), "bm25_rank": bm25_rank.get(cid),
                           "fused_rank": fused_rank.get(cid)}
            hits.append(hh)
        if not hits:
            hits = dense_hits[:top_k]
            ladder = {h["chunk_id"]: {"dense": float(h["score"]), "bm25": None, "fused": None,
                                      "dense_rank": i + 1} for i, h in enumerate(hits)}
    else:
        hits = dense_hits[:top_k]
        ladder = {h["chunk_id"]: {"dense": float(h["score"]), "bm25": None, "fused": None,
                                  "dense_rank": i + 1} for i, h in enumerate(hits)}
    # 重排:记录每块 rerank relevance(不因重排丢失)。重排"前后顺序"可由 fused_score 与 rerank_score 推导。
    rerank_score_by_chunk: dict[str, float] = {}
    if rerank_fn and len(hits) > 1:
        _t0 = time.perf_counter()
        docs = [h["content"] for h in hits]
        res = rerank_fn(query, docs)
        if timings is not None:
            timings["rerank_ms"] = int((time.perf_counter() - _t0) * 1000)
        if res:
            for i in res:
                idx = int(i.get("index"))
                if 0 <= idx < len(hits):
                    rerank_score_by_chunk[hits[idx]["chunk_id"]] = float(i.get("relevance_score", 0))
            order = sorted(res, key=lambda x: x.get("relevance_score", 0), reverse=True)
            hits = [hits[int(i["index"])] for i in order if int(i["index"]) < len(hits)]
            hits = hits[:top_rerank]
            # 重排后位置(D82):与 fused_rank 对比即可看出"重排把它提/降了几位"
            for _i, _h in enumerate(hits):
                _lb2 = ladder.get(_h.get("chunk_id"))
                if _lb2 is not None:
                    _lb2["rerank_rank"] = _i + 1
    chunks = []
    for h in hits:
        c = to_chunk(h, h.get("score"))
        lb = ladder.get(h.get("chunk_id")) or {}
        # 分数阶梯固定四键(缺失用 None):前端 trace 可统一渲染各阶段分值,无值显示"—"。
        c["dense_score"] = round(float(lb["dense"]), 4) if lb.get("dense") is not None else None
        c["bm25_score"] = round(float(lb["bm25"]), 4) if lb.get("bm25") is not None else None
        c["fused_score"] = round(float(lb["fused"]), 4) if lb.get("fused") is not None else None
        rs = rerank_score_by_chunk.get(h.get("chunk_id"))
        c["rerank_score"] = round(rs, 4) if rs is not None else None
        # 四路 rank(D82):缺失为 None → 前端渲染「—」。rank 是整数,不做 round。
        c["dense_rank"] = lb.get("dense_rank")
        c["bm25_rank"] = lb.get("bm25_rank")
        c["fused_rank"] = lb.get("fused_rank")
        c["rerank_rank"] = lb.get("rerank_rank")
        chunks.append(c)
    if product:
        # 软偏置:该产品块稳定提到前面(用户点名产品优先命中)
        same = [c for c in chunks if c.get("product_name") == product]
        rest = [c for c in chunks if c.get("product_name") != product]
        chunks = same + rest
        if not same:
            # 产品名未命中任何块(可能库中无该产品),退化为不按产品过滤,避免返回空
            pass
    if category:
        # 软偏置:同类别块稳定提到前面,不排除其它类别(top_k 不变)
        same = [c for c in chunks if c.get("product_category") == category]
        rest = [c for c in chunks if c.get("product_category") != category]
        chunks = same + rest
    # RAG 投毒防护:隔离疑似"指令式"chunk,不让恶意指令进模型上下文(确定性拦截)
    chunks, _susp = quarantine_suspicious(chunks)
    if _susp:
        logger.warning("rag_quarantined %d chunk(s) (content=指令式,疑似投毒)", len(_susp))
    return chunks
