#!/usr/bin/env python3
"""
RAGAS × 自研 judge 交叉验证
============================
目标：用成熟评测框架 RAGAS 的独立打分，校准自研六维 LLM-judge，
     证明自研 judge 与工业标准指标一致（而非"自己评自己"的自嗨）。

数据来源（避免重跑 agent / API / Qdrant）：
  - docs/eval/history/eval-2026-09-07.json  (72 条生产评测产物，含 answer / judge / retrieval_chunk_ids)
  - data/knowledge.db  (chunks 表，按 chunk_id 取正文当 RAGAS 的 retrieved_contexts)

指标选择（受环境约束，见报告）：
  - Faithfulness (RAGAS, 纯 LLM, 0-1)  ←→  自研 judge 的 faithfulness(0-3) / groundedness(0-3) / hallucinate / overclaim
  - AnswerRelevancy / ContextPrecision / ContextRecall 因当前 env 无 embedding 模型、
    且 eval 集 expected 为约束字典而非参考答案文本，本批不在范围内（报告给出下一步）。

LLM：DeepSeek (OpenAI 兼容)，与自研 judge 同款模型 → 公平对比。

用法：
  python scripts/ragas_crossval.py --pilot      # 跑 1 条验证管线
  python scripts/ragas_crossval.py             # 跑全量可解析记录
"""
import argparse
import json
import math
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env():
    env = {}
    p = os.path.join(ROOT, ".env")
    if not os.path.exists(p):
        return env
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def load_eval():
    p = os.path.join(ROOT, "docs/eval/history/eval-2026-09-07.json")
    d = json.load(open(p, encoding="utf-8"))
    return d["results"]


def build_context_lookup():
    db = os.path.join(ROOT, "data/knowledge.db")
    con = sqlite3.connect(db)
    cur = con.cursor()

    def fetch(cid):
        cur.execute("SELECT content FROM chunks WHERE chunk_id=?", (cid,))
        r = cur.fetchone()
        return r[0] if r else None

    return fetch  # 连接保持打开，供整个脚本生命周期使用


def pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return float("nan")
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return float("nan")
    return num / (dx * dy)


def spearman(xs, ys):
    def rank(v):
        # average-rank for ties
        idx = sorted(range(len(v)), key=lambda i: v[i])
        r = [0] * len(v)
        i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[idx[j + 1]] == v[idx[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[idx[k]] = avg
            i = j + 1
        return r

    return pearson(rank(xs), rank(ys))


def cohen_kappa(a, b):
    # a,b: lists of 0/1
    n = len(a)
    if n == 0:
        return float("nan")
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pa = sum(a) / n
    pb = sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    if pe == 1:
        return 1.0
    return (po - pe) / (1 - pe)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true", help="只跑 1 条验证管线")
    ap.add_argument("--limit", type=int, default=0, help="最多处理 N 条（调试用）")
    args = ap.parse_args()

    env = load_env()
    records = load_eval()
    fetch = build_context_lookup()

    # 组装 (record, contexts)
    prepared = []
    missing_ctx_ids = []
    for r in records:
        cids = r.get("retrieval_chunk_ids") or []
        ctxs = []
        for cid in cids:
            c = fetch(cid)
            if c:
                ctxs.append(c)
        if ctxs:
            prepared.append((r, ctxs))
        else:
            missing_ctx_ids.append(r["id"])

    print(f"[info] 评测记录总数={len(records)}  有可解析上下文={len(prepared)}  缺上下文={len(missing_ctx_ids)}")

    if args.pilot:
        prepared = prepared[:1]
    elif args.limit:
        prepared = prepared[: args.limit]

    from openai import OpenAI
    from ragas.llms import llm_factory
    from ragas import SingleTurnSample, EvaluationDataset, evaluate
    from ragas.metrics import Faithfulness

    # 走官方推荐的 llm_factory + OpenAI client 路径，绕过 langchain 1.x 兼容问题
    client = OpenAI(
        api_key=env["DEEPSEEK_API_KEY"],
        base_url=env["DEEPSEEK_BASE_URL"],
    )
    # 注意：RAGAS 用 instructor(Mode.JSON) 抽结构化 claims，deepseek-v4-flash 对该模式
    # 支持不稳（返回非 JSON → IncompleteOutputException → nan），故 RAGAS 固定用 deepseek-chat；
    # 自研 judge 是用 deepseek-v4-flash 生成的。两裁判用不同模型，反而降低同模型回声偏差。
    ragas_model = env.get("RAGAS_MODEL") or "deepseek-chat"
    print(f"[info] RAGAS 使用模型: {ragas_model}（自研 judge 用 {env.get('DEEPSEEK_MODEL')}）", flush=True)
    llm = llm_factory(ragas_model, client=client)
    # 关键修复：ragas 默认 max_tokens=1024，对长保险上下文+claims 抽取常被截断
    # → IncompleteOutputException → 分数变 nan。model_args 是 dict，把 max_tokens 调到 4096 根除截断。
    # （逐条重试由下方 score_one 的 attempts 负责，ragas 自身的 run_config 重试可省。）
    llm.model_args = {**getattr(llm, "model_args", {}), "max_tokens": 4096, "temperature": 0}
    fm = Faithfulness()
    fm.llm = llm

    def score_one(r, ctxs, attempts=4):
        s = SingleTurnSample(
            user_input=r.get("query", ""),
            response=r.get("answer", ""),
            retrieved_contexts=ctxs,
        )
        ds = EvaluationDataset(samples=[s])
        for att in range(attempts):
            try:
                out = evaluate(ds, metrics=[fm])
                sc = out.scores[0].get("faithfulness", float("nan"))
                if sc == sc:  # 非 nan
                    return sc
            except Exception as e:
                print(f"    [retry {att+1}] {type(e).__name__}: {str(e)[:80]}", flush=True)
            time.sleep(2)
        return float("nan")

    print(
        f"[info] 逐条评估 {len(prepared)} 条 RAGAS Faithfulness（调用 DeepSeek，单次失败自动重试）...",
        flush=True,
    )
    faith_scores = []
    meta = []
    for i, (r, ctxs) in enumerate(prepared):
        sc = score_one(r, ctxs)
        faith_scores.append(sc)
        meta.append(r["id"])
        print(f"  [{i+1}/{len(prepared)}] {r['id']}: {sc}", flush=True)

    # 合并自研 judge
    rows = []
    for rid, fs, (r, _ctxs) in zip(meta, faith_scores, prepared):
        j = r.get("judge", {})
        self_faith = float(j.get("faithfulness", 0) or 0)
        self_ground = float(j.get("groundedness", 0) or 0)
        hallucinate = bool(j.get("hallucinate", False))
        overclaim = bool(j.get("overclaim", False))
        rows.append(
            {
                "id": rid,
                "type": r.get("type"),
                "query": r.get("query", ""),
                "ragas_faithfulness": fs,                 # 0-1
                "self_faithfulness": self_faith / 3.0,    # 归一 0-1
                "self_groundedness": self_ground / 3.0,   # 归一 0-1
                "self_hallucinate": hallucinate,
                "self_overclaim": overclaim,
                "self_faithful_binary": not (hallucinate or overclaim),
                "ragas_faithful_binary": fs >= 0.5,
                "reason": j.get("reason", ""),
            }
        )

    # 统计
    fs = [x["ragas_faithfulness"] for x in rows]
    sf = [x["self_faithfulness"] for x in rows]
    sg = [x["self_groundedness"] for x in rows]
    rb = [int(x["ragas_faithful_binary"]) for x in rows]
    sb = [int(x["self_faithful_binary"]) for x in rows]

    stats = {
        "n": len(rows),
        "ragas_faithfulness_mean": sum(fs) / len(fs),
        "self_faithfulness_mean": sum(sf) / len(sf),
        "self_groundedness_mean": sum(sg) / len(sg),
        "pearson_ragas_vs_self_faith": pearson(fs, sf),
        "spearman_ragas_vs_self_faith": spearman(fs, sf),
        "pearson_ragas_vs_self_ground": pearson(fs, sg),
        "spearman_ragas_vs_self_ground": spearman(fs, sg),
        "cohen_kappa_binary": cohen_kappa(rb, sb),
        "binary_agreement": sum(1 for x, y in zip(rb, sb) if x == y) / len(rb),
        "n_ragas_unfaithful": sum(1 for x in rb if x == 0),
        "n_self_unfaithful": sum(1 for x in sb if x == 0),
    }

    # 分歧案例
    disagreements = [
        {
            "id": x["id"],
            "ragas": round(x["ragas_faithfulness"], 3),
            "self_faith": round(x["self_faithfulness"], 3),
            "self_ground": round(x["self_groundedness"], 3),
            "self_hallucinate": x["self_hallucinate"],
            "self_overclaim": x["self_overclaim"],
            "query": x["query"][:80],
            "reason": x["reason"][:160],
        }
        for x in rows
        if x["ragas_faithful_binary"] != x["self_faithful_binary"]
    ]

    out = {
        "meta": {
            "eval_file": "docs/eval/history/eval-2026-09-07.json",
            "llm": env.get("DEEPSEEK_MODEL"),
            "metric": "Faithasfulness (RAGAS) vs self-judge",
            "kb_total_records": len(records),
            "records_with_context": len(prepared),
            "records_missing_context": missing_ctx_ids,
        },
        "stats": stats,
        "disagreements": disagreements,
        "rows": rows,
    }
    os.makedirs(os.path.join(ROOT, "docs/eval"), exist_ok=True)
    json.dump(
        out,
        open(os.path.join(ROOT, "docs/eval/ragas_crossval.json"), "w", encoding="utf-8"),
        ensure_ascii=False,
        indent=2,
    )
    print(f"[done] 写入 docs/eval/ragas_crossval.json  (n={len(rows)})")
    print(f"[stat] Pearson(ragas,self_faith)={stats['pearson_ragas_vs_self_faith']:.3f}  "
          f"Spearman={stats['spearman_ragas_vs_self_faith']:.3f}  "
          f"kappa={stats['cohen_kappa_binary']:.3f}  分歧={len(disagreements)}")
    return out


if __name__ == "__main__":
    main()
