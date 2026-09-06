# -*- coding: utf-8 -*-
"""自建观测·告警检查:从 events 聚合指标做阈值检查,超标报 ALERT。

覆盖(自建可观测,事实源 events):
- 错误全记(任何错误轮数)、错误率
- 延迟 p95、累计 token
- 检索空结果率 / 检索低置信率(RAG 问题 80% 出在检索)
- LLM 重试次数、依赖降级数(retrieval_unavailable = 向量库挂)
- 护栏拦截数(guard_triggered,任何一条都要看)
- 工具失败数(tool_result ok=False)

用法(用 rag_env 解释器,连 facts 源;配了 DB_* 则连 MySQL,否则 SQLite):
    python scripts/check_metrics.py [--error-count N] [--error-rate 0.05] ... [--guard-count N]
退出码:0=全部达标;1=有告警。
"""
from __future__ import annotations

import argparse
import sys

from app.api.routers.metrics import get_metrics


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--error-count", type=int, default=1, help="错误轮数阈值(默认 1,有任何错误轮即告警)")
    ap.add_argument("--error-rate", type=float, default=0.05, help="错误率阈值(0~1),默认 0.05")
    ap.add_argument("--p95-ms", type=int, default=20000, help="延迟 p95 阈值(ms),默认 20000")
    ap.add_argument("--token-budget", type=int, default=5_000_000, help="累计 token 预算,默认 500 万")
    ap.add_argument("--retrieve-empty-rate", type=float, default=0.10, help="检索空结果率阈值,默认 0.10")
    ap.add_argument("--retrieve-lowconf-rate", type=float, default=0.30, help="检索低置信率阈值,默认 0.30")
    ap.add_argument("--retry-count", type=int, default=5, help="LLM 重试次数阈值,默认 5")
    ap.add_argument("--degrade-count", type=int, default=1, help="依赖降级数阈值,默认 1(任何降级即告警)")
    ap.add_argument("--guard-count", type=int, default=1, help="护栏拦截数阈值,默认 1(任何一条都要看)")
    ap.add_argument("--tool-fail-count", type=int, default=5, help="工具失败数阈值,默认 5")
    args = ap.parse_args()

    m = get_metrics()
    turns = m["turns"]; lat = m["latency_ms"]; tok = m["tokens"]; retr = m["retrieval"]
    total_tok = int(tok["prompt"] or 0) + int(tok["completion"] or 0)
    p95 = lat.get("p95") or 0
    retr_total = int(retr["total"] or 0)
    empty_rate = (int(retr["no_hits"] or 0) / retr_total) if retr_total else 0
    lowconf_rate = (int(retr["low_conf"] or 0) / retr_total) if retr_total else 0

    # (名称, 值, 阈值, 存在即告警, 对应 samples 键[回放 trace_id])
    checks = [
        ("错误轮数", turns["error"], args.error_count, True, "error_turns"),
        ("错误率", turns["error_rate"], args.error_rate, False, None),
        ("延迟p95(ms)", p95, args.p95_ms, False, None),
        ("累计token", total_tok, args.token_budget, False, None),
        ("检索空结果率", round(empty_rate, 4), args.retrieve_empty_rate, False, None),
        ("检索低置信率", round(lowconf_rate, 4), args.retrieve_lowconf_rate, False, "retrieval_low_conf"),
        ("LLM重试次数", m["retries"], args.retry_count, False, "retries"),
        ("依赖降级数", m["degradations"], args.degrade_count, True, "degradations"),
        ("护栏拦截数", m["guard_triggered"], args.guard_count, True, "guard_triggered"),
        ("工具失败数", m["tool_failures"], args.tool_fail_count, False, "tool_failures"),
    ]

    samples = m.get("samples") or {}
    print(f"turns={turns['total']} err={turns['error']}({turns['error_rate']}) "
          f"p95={p95}ms tokens={total_tok} retr={retr_total}(空={retr['no_hits']}/低置信={retr['low_conf']}) "
          f"retries={m['retries']} degrade={m['degradations']} guard={m['guard_triggered']} toolfail={m['tool_failures']}")

    alerts = 0
    for name, val, thr, exists, skey in checks:
        # exists=True:只要 >0 即告警(错误/降级/护栏"任何一条都必须看");否则值 <= 阈值才算达标
        _ok = (val == 0) if exists else (val <= thr)
        tail = ""
        if skey:
            tra = samples.get(skey) or []
            if tra:
                tail = f"  trace_id={','.join(tra)}"
        if not _ok:
            alerts += 1
            print(f"[ALERT] {name}: {val}  阈值 {thr}{tail}")
        else:
            print(f"[OK] {name}: {val}  阈值 {thr}")
    return 1 if alerts else 0


if __name__ == "__main__":
    sys.exit(main())
