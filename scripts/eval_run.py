# -*- coding: utf-8 -*-
"""评测编排器:一条命令跑完 L1(LLM-judge 质量层)+ L2(引用层程序化校验)+ 基线归档。

流程:
  ① eval_agent.py  跑 agent → judge 六维打分 → eval_report.json
  ② eval_citation.py  读报告 → 引用四项断言 → docs/eval/cite_report.json
  ③ 两份产物按日期归档到 docs/eval/history/{eval,cite}-YYYY-MM-DD.json

用法:
  python scripts/eval_run.py                    # 全量(72 条,跑 agent 约 30-60 分钟)
  python scripts/eval_run.py --limit 3          # 冒烟:先跑 3 条验证链路
  python scripts/eval_run.py --category kb,refuse
  python scripts/eval_run.py --no-archive       # 不写 history(调试用)

退出码:0=两层全过;1=质量层有失败;2=引用层有失败;3=都失败。
"""
from __future__ import annotations

import argparse
import datetime
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
PY = sys.executable


def _run(script: str, args: list[str]) -> int:
    """跑一个子脚本(同解释器),实时透传输出。"""
    cmd = [PY, os.path.join(HERE, script)] + args
    print(f"\n=== {' '.join([script] + args)} ===", flush=True)
    return subprocess.call(cmd, cwd=PROJ)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--category", default="")
    ap.add_argument("--no-archive", action="store_true")
    a = ap.parse_args()

    # ① 质量层:跑 agent + judge
    agent_args = ["--out", os.path.join(PROJ, "eval_report.json")]
    if a.limit:
        agent_args += ["--limit", str(a.limit)]
    if a.category:
        agent_args += ["--category", a.category]
    rc_agent = _run("eval_agent.py", agent_args)

    # 报告没生成(如评估集为空/脚本崩了)→ 引用层无从校验
    report = os.path.join(PROJ, "eval_report.json")
    if not os.path.isfile(report):
        print("\n[abort] eval_report.json 未生成,跳过引用层。")
        return rc_agent or 1

    # ② 引用层:程序化校验
    rc_cite = _run("eval_citation.py", ["--report", report])

    # ③ 归档基线
    if not a.no_archive:
        today = datetime.date.today().isoformat()
        hist = os.path.join(PROJ, "docs", "eval", "history")
        os.makedirs(hist, exist_ok=True)
        for src, name in [(report, f"eval-{today}.json"),
                          (os.path.join(PROJ, "docs", "eval", "cite_report.json"),
                           f"cite-{today}.json")]:
            if os.path.isfile(src):
                dst = os.path.join(hist, name)
                import shutil
                shutil.copyfile(src, dst)
                print(f"[archived] {name} -> docs/eval/history/")

    rc_agent = 1 if rc_agent else 0
    rc_cite = 2 if rc_cite else 0
    code = rc_agent + rc_cite
    print(f"\n[eval_run done] 质量层={'FAIL' if rc_agent else 'PASS'}"
          f" 引用层={'FAIL' if rc_cite else 'PASS'} (exit={code or 0})")
    return code or 0


if __name__ == "__main__":
    raise SystemExit(main())
