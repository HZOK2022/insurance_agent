# -*- coding: utf-8 -*-
"""Answer Relevancy 评估(零 gold,贴合 RAGAS 标准口径)。

逻辑:对"已给的 assistant 回答",让 LLM **不看原问题**,仅凭回答反推出 N 个"这道回答最可能
是在回答哪个问题",再把反推问题与原 query 各自 embed,取夹角余弦均值 → 0~1 的 relevancy。

- 反推问题时**故意不喂原 query**(RAGAS 语义):否则模型会照抄 query,得分恒高,失去判别力。
- 评分阶段只做 embedding 余弦(程序化、可复算),唯一 LLM 调用是"反向生成问题"那一步。
- 不需要 gold——Answer Relevancy 衡量"回答是否真的答到问题上",与引用/召回无关。

用法:
  python scripts/eval_relevancy.py                      # 读 eval_report.json(每条含 query+answer)
  python scripts/eval_relevancy.py --report x.json --n 3 --out docs/eval/relevancy_report.json
输入: eval_agent 输出报告或裸 list,每条含 id + query/user_query + answer。
输出: 每 case relevancy(0~1)/questions + 汇总 mean/std。
"""
from __future__ import annotations
import argparse
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DEF_REPORT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "eval_report.json")
_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEF_OUT = os.path.join(_PROJ, "docs", "eval", "relevancy_report.json")


def cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度(对两向量各自归一,对 embedder 是否已归一不敏感)。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def generate_questions_prompt(answer: str, n: int) -> str:
    """反向生成问题 prompt。只给 answer,不给原 query(防止照抄泄漏)。"""
    return (
        "你是评测助手。下面是保险销售客服助手给客户的一条回答。\n"
        "请只看这条回答的内容,反推出它最可能是在回答客户的哪个问题。\n"
        f"生成 {n} 个这样的问题,互不相同、贴近真实客户会问的话,不要包含回答本身。\n"
        f"只输出 JSON:{{\"questions\":[\"...\",...]}}\n\n"
        f"回答:\n{answer}"
    )


def parse_questions(content: str) -> list[str]:
    """从 LLM 输出解析问题列表;容错(可能带 markdown/额外文本/空)。"""
    if not content:
        return []
    try:
        data = json.loads(content)
    except Exception:
        m = re.search(r"\{.*\}", content, re.S)
        data = json.loads(m.group(0)) if m else {}
    qs = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(qs, list):
        return []
    out = []
    for q in qs:
        if isinstance(q, str) and q.strip():
            out.append(q.strip())
    return out


def _mean(xs: list[float]) -> float | None:
    if not xs:
        return None
    return sum(xs) / len(xs)


def eval_relevancy(embedder, llm, query: str, answer: str, n: int) -> dict | None:
    """单条:反推 N 问 → 各与 query 的余弦均值 → 0~1。answer 空返回 None。"""
    if not answer or not query:
        return None
    content, _usage = llm.chat([{"role": "user", "content": generate_questions_prompt(answer, n)}],
                               json_mode=True)
    questions = parse_questions(content)
    if not questions:
        return {"relevancy": None, "questions": [], "error": "parse_empty", "n": n}
    vecs = embedder.embed([query] + questions)
    if not vecs or len(vecs) < 1:
        return {"relevancy": None, "questions": questions, "error": "embed_empty", "n": n}
    qv = vecs[0]
    sims = [cosine(qv, v) for v in vecs[1:]]
    return {"relevancy": round(_mean(sims), 4) if sims else None,
            "questions": questions, "sims": [round(s, 4) for s in sims], "n": n}


def load_report(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("results", data) if isinstance(data, dict) else data


def _query_of(r: dict) -> str:
    return (r.get("query") or r.get("user_query") or "").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default=_DEF_REPORT)
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=DEF_OUT)
    a = ap.parse_args()

    sys.path.insert(0, _PROJ)
    from app.api.services import container
    embedder = container.get_embedder()
    llm = container.get_llm()

    rows = [r for r in load_report(a.report) if _query_of(r)]
    if a.limit:
        rows = rows[: a.limit]
    if not rows:
        raise SystemExit(f"报告无含 query/answer 的行: {a.report}")
    print(f"cases={len(rows)} n={a.n}")

    vals, per_case, skipped = [], [], 0
    for i, r in enumerate(rows):
        cid = r.get("id") or f"row{i+1}"
        res = eval_relevancy(embedder, llm, _query_of(r), r.get("answer") or "", a.n)
        print(f"[{i+1}/{len(rows)}] {cid}: relevancy={res.get('relevancy') if res else None}")
        if res is None or res.get("relevancy") is None:
            skipped += 1
            if res is None:
                res = {"relevancy": None, "questions": [], "note": "empty_answer"}
        else:
            vals.append(res["relevancy"])
        per_case.append({"id": cid, "query": _query_of(r), **res})

    agg = {"mean": round(_mean(vals), 4) if vals else None,
           "std": (round((sum((v - _mean(vals)) ** 2 for v in vals) / len(vals)) ** 0.5, 4) if vals else None)}
    out = {"config": {"n": a.n}, "summary": agg, "count": len(per_case), "skipped": skipped, "cases": per_case}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[summary] mean={agg['mean']} std={agg['std']} skipped={skipped}")
    print(f"[done] -> {a.out}")


if __name__ == "__main__":
    main()