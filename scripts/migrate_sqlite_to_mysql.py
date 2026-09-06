# -*- coding: utf-8 -*-
"""SQLite → MySQL 全量迁移脚本。

适用场景:.env 切到 MySQL 后,把本地 SQLite 上的历史数据(events/sessions/users 等)
一次性覆盖到 MySQL。DB_HOST=127.0.0.1 / DB_USER=root / DB_NAME=insurance_agent。

策略:清空目标表 → 按 SQLite 行序批量 INSERT → 抽检验证。
只迁 agent.db 范围内的 7 张表;knowledge.db / premium.db 是独立库/独立流程,不在此脚本范围。

表清单:
  auth_tokens, events, memory_entries, meta, sessions, users
  (含 SQLite 内部表 sqlite_sequence 不迁。)

用法:
  python scripts/migrate_sqlite_to_mysql.py             # 跑迁移
  python scripts/migrate_sqlite_to_mysql.py --dry-run   # 只看会跑什么
  python scripts/migrate_sqlite_to_mysql.py --skip-events  # events 大表(50万)耗时,先跳
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time

import pymysql

# 走 .env 拿 MySQL 配置
HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)


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


# 清空顺序必须按"被引用先清":events 引用 sessions(无 FK 但逻辑上);memory 引用 users/sessions
TABLES = ["auth_tokens", "events", "memory_entries", "meta", "sessions", "users"]
SQLITE_PATH = os.path.join(PROJ, "data", "agent.db")
SQLITE_SKIP_SQLITE_SEQUENCE = True
BATCH = 500


def _sqlite_conn():
    if not os.path.isfile(SQLITE_PATH):
        raise SystemExit(f"SQLite 不存在: {SQLITE_PATH}")
    c = sqlite3.connect(SQLITE_PATH)
    c.row_factory = sqlite3.Row
    return c


def _mysql_conn(env: dict) -> pymysql.connections.Connection:
    cfg = {k: env.get(k, "") for k in
           ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASS", "DB_NAME")}
    if not cfg["DB_HOST"]:
        raise SystemExit("未配 DB_HOST,先填 .env")
    return pymysql.connect(
        host=cfg["DB_HOST"],
        port=int(cfg["DB_PORT"] or 3306),
        user=cfg["DB_USER"] or "root",
        password=cfg["DB_PASS"] or "",
        database=cfg["DB_NAME"],
        charset="utf8mb4",
        autocommit=False,
    )


def _table_columns(con, table: str) -> list[str]:
    """读 SQLite 表的所有列(PRAGMA);顺序即为 INSERT 列顺序。"""
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


def _truncate(mcon, tables: list[str], dry: bool) -> None:
    with mcon.cursor() as cur:
        for t in tables:
            if dry:
                print(f"  [dry] TRUNCATE TABLE {t}")
            else:
                cur.execute(f"TRUNCATE TABLE {t}")
    mcon.commit()


def _copy_table(scon, mcon, table: str, dry: bool) -> dict:
    cols = _table_columns(scon, table)
    n = scon.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    if n == 0:
        return {"table": table, "rows": 0, "batches": 0, "sec": 0.0}

    placeholders = ",".join(["%s"] * len(cols))
    col_list = ",".join(f"`{c}`" for c in cols)
    insert_sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"

    t0 = time.time()
    batched = 0
    offset = 0
    with scon, mcon.cursor() as cur:
        while True:
            rows = scon.execute(
                f"SELECT {','.join(cols)} FROM {table} LIMIT ? OFFSET ?",
                (BATCH, offset),
            ).fetchall()
            if not rows:
                break
            payload = [tuple(r[c] for c in cols) for r in rows]
            if dry:
                if offset == 0:
                    print(f"  [dry] sample: {payload[0]!r}")
            else:
                cur.executemany(insert_sql, payload)
            batched += len(payload)
            offset += BATCH
            if not dry and batched % (BATCH * 5) == 0:
                print(f"    {table}: {batched}/{n} ...")
    if not dry:
        mcon.commit()
    return {"table": table, "rows": n, "batches": (n + BATCH - 1) // BATCH,
            "sec": round(time.time() - t0, 2)}


def _verify(scon, mcon, tables: list[str]) -> list[dict]:
    out = []
    for t in tables:
        s = scon.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        with mcon.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {t}")
            m = cur.fetchone()[0]
        out.append({"table": t, "sqlite": s, "mysql": m, "ok": s == m})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印会跑什么,不动数据")
    ap.add_argument("--skip-events", action="store_true", help="跳过 events 大表(默认会迁)")
    ap.add_argument("--skip", default="", help="逗号分隔额外要跳的表,如 'meta,memory_entries'")
    args = ap.parse_args()

    env = _load_env(os.path.join(PROJ, ".env"))
    scon = _sqlite_conn()
    mcon = _mysql_conn(env)

    targets = list(TABLES)
    if args.skip_events and "events" in targets:
        targets.remove("events")
    for t in [x.strip() for x in args.skip.split(",") if x.strip()]:
        if t in targets:
            targets.remove(t)

    print(f"== 计划:SQLite({SQLITE_PATH}) → MySQL({env.get('DB_HOST')}/{env.get('DB_NAME')}) ==")
    print(f"   目标表: {targets}")
    if args.dry_run:
        print("   模式: DRY-RUN(不写)")

    print("\n[1/4] 清空目标表")
    if args.dry_run:
        print("  [dry-run 跳过 TRUNCATE]")
    else:
        _truncate(mcon, targets, args.dry_run)

    print("\n[2/4] 复制")
    rep = []
    for t in targets:
        print(f"  -> {t}")
        if args.dry_run:
            cols = _table_columns(scon, t)
            n = scon.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            sample = scon.execute(f"SELECT {','.join(cols)} FROM {t} LIMIT 1").fetchone()
            print(f"  [dry] {t} rows={n} sample={tuple(sample[c] for c in cols)!r}")
            rep.append({"table": t, "rows": n, "batches": 0, "sec": 0.0})
        else:
            rep.append(_copy_table(scon, mcon, t, args.dry_run))

    print("\n[3/4] 抽检行数")
    verify = _verify(scon, mcon, targets)
    print(f"  {'table':24s} {'sqlite':>10s} {'mysql':>10s}  ok")
    for r in verify:
        print(f"  {r['table']:24s} {r['sqlite']:>10d} {r['mysql']:>10d}  {r['ok']}")

    print("\n[4/4] 复制报告")
    print(f"  {'table':24s} {'rows':>8s} {'batches':>8s} {'sec':>8s}")
    for r in rep:
        print(f"  {r['table']:24s} {r['rows']:>8d} {r['batches']:>8d} {r['sec']:>8.2f}")

    failed = [r for r in verify if not r["ok"]]
    scon.close()
    mcon.close()
    if failed:
        print(f"\n!! 行数不一致: {[r['table'] for r in failed]}")
        return 1
    print("\nOK:全部表行数对齐。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
