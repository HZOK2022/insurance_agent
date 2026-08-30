# -*- coding: utf-8 -*-
"""解析保费费率 xlsx 到 data/premium.db(尊享e生2025 两张费率表:必选计划 + 加油包)。

用法: python scripts/seed_premium.py --xlsx <费率表.xlsx> [--db data/premium.db]
费率事实源(SQLite)不入 git(data/ 已 ignore);要从 Excel 重建就运行本脚本。
"""
from __future__ import annotations
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import load
from app.businesses.premium import PremiumStore, load_xx2025_xlsx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", required=True, help="尊享e生2025 费率表 .xlsx 路径")
    ap.add_argument("--db", default=None, help="目标 sqlite;默认取 cfg.premium_db_path")
    a = ap.parse_args()

    cfg = load()
    db = a.db or getattr(cfg, "premium_db_path", "data/premium.db")
    store = PremiumStore(db)
    n = load_xx2025_xlsx(store, a.xlsx)
    cnt = store.conn.execute("SELECT COUNT(*) FROM premium_rates").fetchone()[0]
    prod = store.conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    print(f"[seed_premium] loaded {n} rate rows;premium_rates={cnt}, products={prod} → {db}")
    store.close()


if __name__ == "__main__":
    main()
