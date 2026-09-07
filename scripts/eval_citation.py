# -*- coding: utf-8 -*-
"""引用层程序化校验(铁律 3 硬指标,L2 层)——消费 eval_agent.py 的报告,零 LLM。

输入:eval_report.json(eval_agent 跑完的产物,每条含 answer/citations/retrieval_chunk_ids)。
对每条用例做四项确定性断言:
  ① 角标完整性:answer 中每个 [n] 都能在 citations 里找到 idx==n 的项(无越界/缺位)
  ② 检索一致性:被引用的 chunk_id 必须落在该轮 retrieval_chunk_ids 集合内(不是凭空引用)
  ③ chunk 真实性:chunk_id 能从知识库取到原文(未被篡改/未越权)
  ④ must_cite 期望:期望带引用的用例,回答必须含角标

用法:
  python scripts/eval_citation.py                          # 读 eval_report.json
  python scripts/eval_citation.py --report my_report.json  # 指定报告
  python scripts/eval_citation.py --out cite_report.json

输出:docs/eval/cite_report.json(每用例的 ok/issues + 汇总),退出码 0=全过 / 1=有失败。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, PROJ)

from app.observability.metrics import classify_retrieval_failure  # noqa: E402

# 角标 [n]
CITE_RE = re.compile(r"\[(\d+)\]")


def load_report(path: str) -> list[dict]:
    if not os.path.isfile(path):
        raise SystemExit(f"报告不存在: {path}\n先跑 scripts/eval_agent.py 生成。")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    # 兼容 {"results": [...]} 与裸 list 两种形态
    return data.get("results", data) if isinstance(data, dict) else data


def _kstore():
    """知识库只读句柄(走 app.db 方言:MySQL/SQLite 自适应)。"""
    from app.config import load as load_cfg
    from app.retrieval.knowledge_store import KnowledgeStore
    return KnowledgeStore(cfg=load_cfg())


def check_one(r: dict, kstore, chunk_cache: dict) -> dict:
    """对单条结果做四项断言,返回 {id, ok, issues, n_cited, n_invalid, ...}。"""
    out: dict[str, Any] = {
        "id": r.get("id"), "type": r.get("type"),
        "ok": True, "issues": [],
        "n_cited": 0, "n_out_of_retrieval": 0, "n_missing_chunk": 0,
        "retrieval_failure": None,
    }
    answer = r.get("answer") or ""
    citations = r.get("citations") or []
    retrieved = set(r.get("retrieval_chunk_ids") or [])

    # citations 按 idx 建索引(结构 [{idx, chunk_id}],见 assistant_message 事件)
    by_idx = {c.get("idx"): c for c in citations if isinstance(c, dict)}

    # ① 角标完整性
    idxs = [int(m.group(1)) for m in CITE_RE.finditer(answer)]
    out["n_cited"] = len(idxs)
    for n in idxs:
        c = by_idx.get(n)
        if c is None:
            out["issues"].append(f"角标 [{n}] 无对应 citation(idx 集合={sorted(by_idx)})")
            out["ok"] = False
            continue
        cid = c.get("chunk_id")
        if not cid:
            out["issues"].append(f"角标 [{n}] 的 citation 缺 chunk_id")
            out["ok"] = False
            continue
        # ② 检索一致性:被引 chunk 必须在该轮检索集内
        if retrieved and cid not in retrieved:
            out["issues"].append(f"角标 [{n}] chunk={cid} 不在本轮检索集({len(retrieved)} 个)")
            out["n_out_of_retrieval"] += 1
            out["ok"] = False
        # ③ chunk 真实性:能取到原文
        if cid not in chunk_cache:
            try:
                row = kstore.get_chunk(cid)
                chunk_cache[cid] = (row.get("content") or "") if row else None
            except Exception as e:  # noqa: BLE001
                chunk_cache[cid] = f"<error {e}>"
        content = chunk_cache[cid]
        if not content:
            out["issues"].append(f"角标 [{n}] chunk={cid} 在知识库中不存在/为空")
            out["n_missing_chunk"] += 1
            out["ok"] = False

    # ④ must_cite 期望
    if (r.get("expected") or {}).get("must_cite") and not idxs:
        out["issues"].append("期望 must_cite=true 但回答无任何角标")
        out["ok"] = False
    # ⑤ 检索失败根因(M2):仅当用例带黄金标注 relevant_chunk_ids 才判(漏召/排序截断 vs 模型没用)。
    # 无 gold 时生产在线由 anomaly_report 的 answer_not_cited 覆盖,这里不做猜测。
    gold = (r.get("expected") or {}).get("relevant_chunk_ids") or []
    if gold:
        cited_ids = [c.get("chunk_id") for c in citations if isinstance(c, dict) and c.get("chunk_id")]
        cls = classify_retrieval_failure(list(retrieved), cited_ids, gold)
        if cls:
            out["retrieval_failure"] = cls["kind"]
            out["issues"].append(f"根因: {cls['kind']} —— {cls['reason']}")
            out["ok"] = False
    if not out["issues"]:
        out["issues"] = []
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default=os.path.join(PROJ, "eval_report.json"),
                    help="eval_agent.py 的输出报告(默认 eval_report.json)")
    ap.add_argument("--out", default=os.path.join(PROJ, "docs", "eval", "cite_report.json"))
    a = ap.parse_args()

    results = load_report(a.report)
    if not results:
        print("报告为空,无可校验条目。")
        return 0

    kstore = _kstore()
    chunk_cache: dict[str, Any] = {}
    checks = []
    for r in results:
        c = check_one(r, kstore, chunk_cache)
        checks.append(c)
        flag = "OK  " if c["ok"] else "FAIL"
        print(f"  [{flag}] {str(c['id']):16s} cites={c['n_cited']:>2d}  issues={len(c['issues'])}")

    issue_counter = Counter(
        i.split("(")[0].split("集")[0][:20] for c in checks for i in c["issues"]
    )
    summary = {
        "total": len(checks),
        "passed": sum(1 for c in checks if c["ok"]),
        "failed": sum(1 for c in checks if not c["ok"]),
        "total_citations": sum(c["n_cited"] for c in checks),
        "out_of_retrieval": sum(c["n_out_of_retrieval"] for c in checks),
        "missing_chunk": sum(c["n_missing_chunk"] for c in checks),
        "issue_types": dict(issue_counter),
        "source_report": a.report,
    }
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "results": checks}, f, ensure_ascii=False, indent=1)

    print(f"\n引用层校验: {summary['passed']}/{summary['total']} passed"
          f" | 角标总数={summary['total_citations']}"
          f" | 检索集外={summary['out_of_retrieval']}"
          f" | chunk缺失={summary['missing_chunk']}")
    print(f"-> {a.out}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
