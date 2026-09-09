# -*- coding: utf-8 -*-
"""RAGAS 交叉验证(实弹):用 ragas 0.4 对自研评测存档跑 Faithfulness,产出对照报告。

定位(与自研评测的关系):
- 自研 judge 六维:DeepSeek 按 rubric 打 0-3 分(软);引用层程序化校验(硬)。
- 本脚本:第三方库 ragas 的 Faithfulness 指标(它自己的 judge LLM,也是 DeepSeek),
  对**同一批 answer + 实际喂给模型的检索上下文**重新打分,与自研 groundedness/faithfulness
  交叉验证,校准"自研 judge 是否偏严/偏松"。

数据来源:
- answer / retrieval_chunk_ids: docs/eval/history/eval-<date>.json(results)
- chunk 原文: 从 KnowledgeStore(all_chunks)按 chunk_id 回查 —— contexts = 当时实际喂给模型的检索块
  (忠实度必须对"模型真实看到的上下文"算,不能拿 gold 当 contexts)

指标选择:
- Faithfulness: 只需要 LLM(answer 拆陈述 + 陈述对上下文的蕴含),DeepSeek 可作 judge;
  不需要 embedding(DeepSeek 无向量接口),故 answer_relevancy/context_precision 等跳过。

用法(在项目根,用 ins_env 解释器,评测依赖装在 ins_env):
    ins_env\python.exe scripts/eval_ragas_cross.py --max 3     # 冒烟
    ins_env\python.exe scripts/eval_ragas_cross.py              # 全量(有检索上下文的 case)
    ins_env\python.exe scripts/eval_ragas_cross.py --date 2026-09-07 --out docs/eval/history/ragas-2026-09-07.json

依赖 .env:DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL(不打印 key)。
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
EVAL_DIR = ROOT / "docs" / "eval"
HISTORY_DIR = EVAL_DIR / "history"
ENV_FILE = ROOT / ".env"


def _load_env() -> dict:
    env = {}
    if not ENV_FILE.exists():
        return env
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _mask_key(k: str) -> str:
    if not k:
        return ""
    return k[:6] + "..." + k[-4:] if len(k) > 12 else "****"


def _load_chunk_map() -> dict:
    """chunk_id -> content:直接读事实源 knowledge 库(SQLite 回退),不拉 container/Qdrant 依赖链。

    本函数只依赖 stdlib + KnowledgeStore(纯 sqlite),让脚本可在 ins_env(评测 venv,
    无 qdrant_client/torch)下运行;生产配 db_host 时请用带 cfg 的版本读取 MySQL。
    """
    from app.retrieval.knowledge_store import KnowledgeStore
    kstore = KnowledgeStore()
    chunks = kstore.all_chunks()
    return {c["chunk_id"]: c.get("content") or "" for c in chunks}



def _load_cases(date: str) -> list:
    p = HISTORY_DIR / f"eval-{date}.json"
    if not p.exists():
        raise SystemExit("找不到 " + str(p) + ";用 --date 指定存档日期")
    data = json.loads(p.read_text(encoding="utf-8"))
    return data.get("results") or data


def _prepare(cases, chunk_map):
    rows = []
    skipped = []
    for c in cases:
        cids = c.get("retrieval_chunk_ids") or []
        answer = (c.get("answer") or "").strip()
        if not cids or not answer:
            skipped.append({"id": c.get("id"), "reason": "no retrieval/answer"})
            continue
        ctxs = []
        missing = []
        for cid in cids:
            t = chunk_map.get(cid)
            if t:
                ctxs.append(t)
            else:
                missing.append(cid)
        if not ctxs:
            skipped.append({"id": c.get("id"), "reason": "no chunk text in store"})
            continue
        rows.append({"id": c.get("id"), "type": c.get("type"), "query": c.get("query") or "",
                     "answer": answer, "contexts": ctxs, "missing": missing})
    return rows, skipped


_CASE_TIMEOUT = 600  # 单条 case 的 faithfulness 最长等待(秒);超时记为 None 并继续

async def _score_one(metric, row, sem):
    from ragas import SingleTurnSample
    sample = SingleTurnSample(user_input=row["query"], response=row["answer"],
                              retrieved_contexts=row["contexts"])
    async with sem:
        try:
            score = await asyncio.wait_for(metric.single_turn_ascore(sample), timeout=_CASE_TIMEOUT)
            return row["id"], float(score)
        except Exception as e:
            print("  [timeout/err]", row["id"], type(e).__name__, str(e)[:120], flush=True)
            return row["id"], None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-09-07")
    ap.add_argument("--max", type=int, default=0, help=">0 时只跑前 N 条(冒烟)")
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--types", default="", help="逗号分隔只跑这些 type(如 kb,calc);空=全部")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    env = _load_env()
    api_key = env.get("DEEPSEEK_API_KEY", "")
    base_url = env.get("DEEPSEEK_BASE_URL", "")
    model = env.get("DEEPSEEK_MODEL", "")
    if not api_key:
        raise SystemExit(".env 缺少 DEEPSEEK_API_KEY(ragas judge 用 DeepSeek)")
    print("judge llm: model=" + str(model) + " base=" + str(base_url) + " key=" + _mask_key(api_key))

    from langchain_openai import ChatOpenAI
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import Faithfulness

    chat = ChatOpenAI(model=model or "deepseek-chat",
                      api_key=api_key,
                      base_url=base_url or "https://api.deepseek.com",
                      timeout=90, max_retries=2, request_timeout=90)
    metric = Faithfulness()
    metric.llm = LangchainLLMWrapper(chat)

    print("loading chunk map from knowledge store ...")
    chunk_map = _load_chunk_map()
    print("chunks loaded: " + str(len(chunk_map)))
    cases = _load_cases(args.date)
    rows, skipped = _prepare(cases, chunk_map)
    if args.types:
        allowed = {x.strip() for x in args.types.split(",") if x.strip()}
        rows = [x for x in rows if x["type"] in allowed]
    if args.max > 0:
        rows = rows[:args.max]
    print("cases total=" + str(len(cases)) + " runnable=" + str(len(rows)) + " skipped=" + str(len(skipped)))
    if not rows:
        raise SystemExit("没有可跑 case")
    for s in skipped:
        print("  skip:", s)

    sem = asyncio.Semaphore(args.concurrency)

    async def runner():
        return await asyncio.gather(*[_score_one(metric, r, sem) for r in rows])

    loop = asyncio.new_event_loop()
    try:
        results = loop.run_until_complete(runner())
    finally:
        loop.close()

    per = []
    failed = []
    for cid, score in results:
        r = next(x for x in rows if x["id"] == cid)
        if score is None:
            failed.append(cid)
            continue
        per.append({"id": cid, "type": r["type"], "faithfulness": round(score, 4)})
    mean = round(sum(p["faithfulness"] for p in per) / len(per), 4) if per else None
    print("== ragas faithfulness per case ==")
    for p in sorted(per, key=lambda x: -x["faithfulness"]):
        print("  " + format(p["faithfulness"], ".3f") + "  " + p["id"] + " (" + p["type"] + ")")
    if failed:
        print("  failed/timeout: " + ", ".join(failed))
    print("mean faithfulness = " + str(mean) + " (n=" + str(len(per)) + ")" + (" failed=" + str(len(failed)) if failed else ""))

    out_path = args.out or str(HISTORY_DIR / ("ragas-" + args.date + ".json"))
    payload = {
        "tool": "ragas", "version": None, "metric": "faithfulness",
        "judge_llm": {"model": model, "base_url": base_url},
        "source": "eval-" + args.date + ".json + knowledge chunks",
        "date": datetime.date.today().isoformat(),
        "n": len(per), "mean_faithfulness": mean,
        "results": per, "skipped": skipped[:200],
    }
    try:
        import ragas as _ragas
        payload["version"] = _ragas.__version__
    except Exception:
        pass
    Path(out_path).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("[done] -> " + out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
