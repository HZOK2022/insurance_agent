# -*- coding: utf-8 -*-
"""录制回放 fixture:用真实 LLM + 真实检索跑一轮知识问答,把 (messages, response) 存成 JSONL。

之后改 prompt/检索参数/工具 schema,用 tests/replay/ReplayLLM 重放:
请求一致 → 响应必然一致 → agent 事件序列/引用对拍,零 API 成本回归。

用法(rag_env):
  python scripts/record_replay_fixture.py                        # 默认录 1 条知识问答
  python scripts/record_replay_fixture.py --out my_fixture.jsonl

产物:tests/replay/fixtures/<name>.jsonl(Recorder 格式,每行 {messages, response})
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, PROJ)

from app.api.services import container                       # noqa: E402
from app.api.services.agent_service import run_prompt        # noqa: E402
from tests.replay.recorder import Recorder                   # noqa: E402

FIXTURE_DIR = os.path.join(PROJ, "tests", "replay", "fixtures")

# 录制的对话(选一条走全链路的:检索+引用+生成,覆盖 RAG 主路径)
SCRIPT = [
    "尊享e生2025的等待期是多少天?",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(FIXTURE_DIR, "kb_qa_basic.jsonl"))
    a = ap.parse_args()

    store = container.get_store()
    real_llm = container.get_llm()
    bundle = container.get_insurance_bundle()
    rec = Recorder(real_llm)

    sid = store.create_session("replay-fix")["id"]
    print(f"session={sid} 录制 {len(SCRIPT)} 条提问(真实 LLM + 真实检索)...")
    events_summary = []
    try:
        for q in SCRIPT:
            n_cite, answer_head = 0, ""
            for ev in run_prompt(store, rec, bundle, sid, q):
                t = ev.get("type"); p = ev.get("payload") or {}
                if t == "assistant_message":
                    n_cite = len(p.get("citations") or [])
                    blocks = p.get("blocks") or []
                    answer_head = str(blocks[0].get("text", ""))[:60] if blocks else ""
                elif t == "retrieval":
                    pass
            events_summary.append({"q": q, "citations": n_cite, "answer_head": answer_head})
            print(f"  [ok] {q!r} cites={n_cite} answer={answer_head!r}")
    finally:
        store.delete_session(sid)

    os.makedirs(FIXTURE_DIR, exist_ok=True)
    rec.save(a.out)
    print(f"\n录制完成: {len(rec.records)} 次 LLM 调用 -> {a.out}")
    print("回放用法: ReplayLLM(records) 替代 llm 传给 run_prompt;请求不一致会 AssertionError(即回归信号)。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
