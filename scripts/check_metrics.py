# -*- coding: utf-8 -*-
"""自建观测·告警检查:从 events 聚合指标,对 错误率 / 延迟 p95 / token 预算 做阈值检查,超标报 ALERT。

用法(用 rag_env 解释器,连 facts 源;配了 DB_* 则连 MySQL,否则 SQLite):
    python scripts/check_metrics.py [--error-rate 0.05] [--p95-ms 20000] [--token-budget 5000000]
退出码:0=全部达标;1=有告警。
"""
from __future__ import annotations

import argparse
import sys

from app.api.routers.metrics import get_metrics


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--error-rate", type=float, default=0.05, help="错误率阈值(0~1),默认 0.05")
    ap.add_argument("--p95-ms", type=int, default=20000, help="延迟 p95 阈值(ms),默认 20000")
    ap.add_argument("--token-budget", type=int, default=5_000_000, help="累计 token 预算,默认 500 万")
    args = ap.parse_args()

    m = get_metrics()
    turns = m["turns"]; lat = m["latency_ms"]; tok = m["tokens"]
    total_tok = int(tok["prompt"] or 0) + int(tok["completion"] or 0)
    p95 = lat.get("p95") or 0

    checks = [
        ("错误率", m["turns"]["error_rate"], args.error_rate),
        ("延迟p95(ms)", p95, args.p95_ms),
        ("累计token", total_tok, args.token_budget),
    ]
    alerts = 0
    print(f"turns={turns}  latency_p95={p95}ms  tokens={tok}  cost={tok.get('cost')}")
    for name, val, thr in checks:
        ok = val <= thr
        if not ok:
            alerts += 1
        print(f"[{'OK' if ok else 'ALERT'}] {name}: {val}  阈值 {thr}")
    return 1 if alerts else 0


if __name__ == "__main__":
    sys.exit(main())
