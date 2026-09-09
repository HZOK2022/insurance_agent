# -*- coding: utf-8 -*-
"""检索层单品实验(离线,零 LLM):同一批 query+gold,对比不同候选生成配置的召回/精度。

回答两类问题(各自是单因子 A/B,rerank 恒等):
  1) 融合方法:min-max 加权(fuse_and_pick) vs RRF(rrf_fuse_and_pick)
  2) 检索方式:纯稠密(hw=0) vs 混合(hw>0)
对每条 case 量四段:
  - recall@pool      : 融合候选池(top_k)对 gold 的召回
  - recall@top_rerank: 终集(top_rerank,默认无 rerank=融合序前 N)对 gold 的召回
  - gold_hit@top3    : 终集是否含 ≥1 个 gold(直接对应 classify_retrieval_failure 的 gold_miss)
  - precision@top3   : 终集里 gold 占比(塞了多少噪音进 LLM 上下文)
  - mrr@top3         : 第一个 gold 的倒数排名(rerank 排序质量)

gold 来源: case.expected.relevant_chunk_ids。**暂无该标注的 case 被跳过并计数**——
召回/精度必须有 gold 才能算,不能拿实际检索输出当 gold(会自我指认得满分)。

用法:
  python scripts/eval_retrieval.py                    # 全部 arm,输出表
  python scripts/eval_retrieval.py --arms dense,rrf   # 只跑某几组
  python scripts/eval_retrieval.py --top-k 40 --top-rerank 5
  python scripts/eval_retrieval.py --rerank           # 开外排(bge-reranker)再测 top_rerank
"""
from __future__ import annotations
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.retrieval.hybrid import BM25Index, fuse_and_pick, rrf_fuse_and_pick
from app.retrieval.knowledge_store import KnowledgeStore

_EVAL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "eval")
EVAL_SET = os.path.join(_EVAL_DIR, "eval_set.json")
EVAL_EXTRA = os.path.join(_EVAL_DIR, "eval_set_extra.json")

# 候选生成配置(arm 名 -> 构造函数返回 (dense_map, bm25_map, fused_ids))
# 所有 arm 用同一 pool=top_k*2,避免池大小差异干扰对比。


def _arm_candidates(qvec, query, bm25, store, top_k, method):
    pool = top_k * 2
    dense_hits = store.search(qvec, pool)
    dense_map = {h["chunk_id"]: float(h["score"]) for h in dense_hits}
    dense_by_id = {h["chunk_id"]: h for h in dense_hits}
    bm25_map = dict(bm25.search(query, pool))
    if method == "dense":
        # 纯稠密:不融合,直接取 dense top_k(池大小与其他 arm 一致:pool)
        fused = [(cid, float(score)) for cid, score in dense_map.items()]
        fused.sort(key=lambda x: x[1], reverse=True)
        fused = fused[:top_k]
    elif method == "minmax":
        fused = fuse_and_pick(dense_map, bm25_map, 0.5, top_k)
    else:  # "rrf"
        fused = rrf_fuse_and_pick(dense_map, bm25_map, k=60, top_k=top_k)
    # 还原完整 hit dict(含 content,供 rerank;content 缺失用 chunks_by_id 兜底)
    hits = []
    for cid, score in fused:
        h = dense_by_id.get(cid) or bm25.chunks_by_id.get(cid)
        if not h:
            continue
        hh = dict(h)
        hh["score"] = score
        hits.append(hh)
    return hits


def _final_list(query, hits, top_rerank, rerank_fn):
    """融合候选 →(可选 rerank)→ top_rerank 截断,返回排序后的 chunk_id 列表。"""
    if not hits:
        return []
    if rerank_fn and len(hits) > 1:
        res = rerank_fn(query, [h.get("content", "") for h in hits])
        if res:
            order = sorted(res, key=lambda x: x.get("relevance_score", 0), reverse=True)
            hits = [hits[int(i["index"])] for i in order if int(i["index"]) < len(hits)]
    return [h["chunk_id"] for h in hits[:top_rerank]]


def _metrics(pool_ids: list[str], final_ids: list[str], gold: list[str]) -> dict:
    gs = set(gold)
    pool_set = set(pool_ids)
    final_set = set(final_ids)
    recall_pool = len(pool_set & gs) / len(gs) if gs else None
    recall_final = len(final_set & gs) / len(gs) if gs else None
    gold_hit = bool(final_set & gs)
    precision = len(final_set & gs) / len(final_ids) if final_ids else None
    mrr = 0.0
    for i, cid in enumerate(final_ids):
        if cid in gs:
            mrr = 1.0 / (i + 1)
            break
    return {
        "recall_pool": round(recall_pool, 4) if recall_pool is not None else None,
        "recall_top": round(recall_final, 4) if recall_final is not None else None,
        "gold_hit": gold_hit, "precision_top": round(precision, 4) if precision is not None else None,
        "mrr": round(mrr, 4),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="dense,minmax,rrf", help="逗号分隔:dense|minmax|rrf")
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument("--top-rerank", type=int, default=3)
    ap.add_argument("--rerank", action="store_true", help="开启外排(bge-reranker)再测 top_rerank")
    ap.add_argument("--out", default=os.path.join(_EVAL_DIR, "retrieval_report.json"))
    a = ap.parse_args()

    cases = json.load(open(EVAL_SET, encoding="utf-8"))
    if os.path.isfile(EVAL_EXTRA):
        cases.extend(json.load(open(EVAL_EXTRA, encoding="utf-8")))
    gold_cases = [c for c in cases if (c.get("expected") or {}).get("relevant_chunk_ids")]
    skipped = [c["id"] for c in cases if c not in gold_cases]
    if not gold_cases:
        raise SystemExit(
            "检索层实验需要人工标注相关chunk_id(gold):无 gold 用例。\n"
            "请给 eval_set.json 的 expected.relevant_chunk_ids 补「正确应答该召回哪些块」标注。\n"
            "注意:gold 必须人工确认,不能拿检索实际输出当标签(会自我指认得满分)。")
    print(f"gold cases={len(gold_cases)} (跳过无gold {len(skipped)}: {skipped})")

    from app.api.services import container
    store = container.get_qstore()
    embedder = container.get_embedder()
    kstore = KnowledgeStore(cfg=container.get_cfg())
    bm25 = BM25Index(kstore.all_chunks())

    rerank_fn = None
    if a.rerank:
        cfg = container.get_cfg()
        if cfg.reranking_engine == "external" and cfg.reranking_external_api_key:
            from app.retrieval.reranker import rerank
            def rerank_fn(query, docs):
                return rerank(query, docs, cfg.reranking_external_url,
                              cfg.reranking_external_api_key, cfg.reranking_external_model,
                              top_n=len(docs), timeout=cfg.reranking_external_timeout)
        else:
            print("[warn] --rerank 但有外排未配置,跳过 rerank")

    arms = [x.strip() for x in a.arms.split(",") if x.strip()]
    by_arm = {arm: [] for arm in arms}
    per_case = []
    for i, c in enumerate(gold_cases):
        gold = (c.get("expected") or {}).get("relevant_chunk_ids") or []
        qvec = embedder.embed([c["user_query"]])[0]
        row = {"id": c["id"], "type": c.get("type"), "gold": gold}
        pool_cache = {}
        for arm in arms:
            hits = _arm_candidates(qvec, c["user_query"], bm25, store, a.top_k, arm)
            pool_ids = [h["chunk_id"] for h in hits]
            final_ids = _final_list(c["user_query"], hits, a.top_rerank, rerank_fn)
            m = _metrics(pool_ids, final_ids, gold)
            by_arm[arm].append(m)
            row[arm] = m
        per_case.append(row)
        print(f"[{i+1}/{len(gold_cases)}] {c['id']}: "
              + " ".join(f"{arm}={json.dumps(row[arm], ensure_ascii=False)}" for arm in arms))

    # 汇总:每 arm 的各指标平均(gold_hit/mrr 按全体均值)
    summary = {}
    for arm, rows in by_arm.items():
        agg = {}
        for k in ("recall_pool", "recall_top", "precision_top"):
            vals = [r[k] for r in rows if r[k] is not None]
            agg[k] = round(sum(vals) / len(vals), 4) if vals else None
        agg["gold_hit"] = round(sum(1 for r in rows if r["gold_hit"]) / len(rows), 4)
        agg["mrr"] = round(sum(r["mrr"] for r in rows) / len(rows), 4)
        summary[arm] = agg
    for arm in arms:
        print(f"[summary {arm}] {json.dumps(summary[arm], ensure_ascii=False)}")

    out = {"config": {"arms": arms, "top_k": a.top_k, "top_rerank": a.top_rerank, "rerank": a.rerank},
           "summary": summary, "count": len(gold_cases), "skipped": skipped, "cases": per_case}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[done] -> {a.out}")


if __name__ == "__main__":
    main()