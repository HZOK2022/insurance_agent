# -*- coding: utf-8 -*-
"""knowledge.db / premium.db → MySQL 迁移(会话库迁移见 migrate_sqlite_to_mysql.py)。

项目三库:
  agent.db     (已迁,见另一脚本)
  knowledge.db → chunks / doc_structure / documents (MySQL 同库同表名)
  premium.db   → products / premium_rates           (MySQL 同库同表名)

策略:清空目标表 → 显式按 SQLite 列序 INSERT(保留 id/PK)→ 行数对齐校验。
注:products/premium_rates 显式带 id 插入,MySQL AUTO_INCREMENT 计数会自动抬到 max(id)+1,无冲突。

用法:
  python scripts/migrate_kb_premium_to_mysql.py            # 迁全部 5 张表
  python scripts/migrate_kb_premium_to_mysql.py --dry-run  # 只看不动
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import time

import pymysql

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)

# (SQLite 文件, 表名列表)
SOURCES = [
    (os.path.join(PROJ, "data", "knowledge.db"), ["chunks", "doc_structure", "documents"]),
    (os.path.join(PROJ, "data", "premium.db"), ["products", "premium_rates"]),
]

BATCH = 500


def _load_env(env_path: str) -> dict:
    out: dict[str, str] = {}
    if not os.path.isfile(env_path):
        return out
    for line in open(env_path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _mysql_conn(env: dict) -> pymysql.connections.Connection:
    if not env.get("DB_HOST"):
        raise SystemExit("未配 DB_HOST,先填 .env")
    return pymysql.connect(
        host=env["DB_HOST"],
        port=int(env.get("DB_PORT") or 3306),
        user=env.get("DB_USER") or "root",
        password=env.get("DB_PASS") or "",
        database=env.get("DB_NAME") or "insurance_agent",
        charset="utf8mb4",
        autocommit=False,
    )


def _cols(scon, table: str) -> list[str]:
    return [r[1] for r in scon.execute(f"PRAGMA table_info({table})").fetchall()]


def _copy(scon, mcon, table: str, dry: bool) -> dict:
    cols = _cols(scon, table)
    n = scon.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    if n == 0:
        return {"table": table, "rows": 0, "sec": 0.0}
    placeholders = ",".join(["%s"] * len(cols))
    col_list = ",".join(f"`{c}`" for c in cols)
    insert_sql = f"INSERT INTO `{table}` ({col_list}) VALUES ({placeholders})"
    t0 = time.time()
    done = 0
    offset = 0
    with mcon.cursor() as cur:
        while True:
            rows = scon.execute(
                f"SELECT {','.join(cols)} FROM {table} LIMIT ? OFFSET ?",
                (BATCH, offset)).fetchall()
            if not rows:
                break
            payload = [tuple(r[c] for c in cols) for r in rows]
            if not dry:
                cur.executemany(insert_sql, payload)
            done += len(payload)
            offset += BATCH
            if done % 5000 == 0:
                print(f"    {table}: {done}/{n} ...")
    if not dry:
        mcon.commit()
    return {"table": table, "rows": n, "sec": round(time.time() - t0, 2)}


def _verify(scon, mcon, table: str) -> dict:
    s = scon.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    with mcon.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM `{table}`")
        m = cur.fetchone()[0]
    return {"table": table, "sqlite": s, "mysql": m, "ok": s == m}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    env = _load_env(os.path.join(PROJ, ".env"))
    mcon = _mysql_conn(env)
    targets: list[str] = []
    for _, tables in SOURCES:
        targets.extend(tables)

    print(f"== knowledge.db + premium.db → MySQL({env.get('DB_HOST')}/{env.get('DB_NAME')}) ==")
    print(f"   目标表: {targets}")
    if args.dry_run:
        print("   模式: DRY-RUN(不写)")

    print("\n[1/3] 清空目标表")
    if not args.dry_run:
        with mcon.cursor() as cur:
            for t in targets:
                cur.execute(f"TRUNCATE TABLE `{t}`")
        mcon.commit()
    else:
        print("  [dry-run 跳过 TRUNCATE]")

    print("\n[2/3] 复制")
    for sqlite_path, tables in SOURCES:
        print(f"  源: {os.path.basename(sqlite_path)}")
        scon = sqlite3.connect(sqlite_path)
        scon.row_factory = sqlite3.Row
        try:
            for t in tables:
                print(f"    -> {t}")
                r = _copy(scon, mcon, t, args.dry_run)
                print(f"       {r['rows']} rows in {r['sec']}s")
        finally:
            scon.close()

    print("\n[3/3] 行数对齐校验")
    failed = []
    for sqlite_path, tables in SOURCES:
        scon = sqlite3.connect(sqlite_path)
        try:
            for t in tables:
                v = _verify(scon, mcon, t)
                print(f"  {v['table']:16s} sqlite={v['sqlite']:>6} mysql={v['mysql']:>6}  {'OK' if v['ok'] else 'FAIL'}")
                if not v["ok"]:
                    failed.append(t)
        finally:
            scon.close()
    mcon.close()
    if failed:
        print(f"\n!! 行数不一致: {failed}")
        return 1
    print("\nOK:knowledge + premium 全部迁入 MySQL。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
