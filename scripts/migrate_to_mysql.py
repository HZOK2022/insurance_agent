# -*- coding: utf-8 -*-
"""一次性迁移:把本地 SQLite(data/*.db)存量数据迁到 MySQL(事实源)。迁移后应用改连 MySQL。

黄金法则:MySQL(数据库服务器)= 事实源,SQLite 仅开发/测试回退。本脚本把 SQLite 里的
事实数据(会话/事件/用户/记忆 + 知识 chunks/文档/结构树 + 费率)整表搬到 MySQL 对应库,
目标表用各 store 的 DDL 建好(幂等),再用 INSERT … ON DUPLICATE KEY UPDATE 落数据(可重跑)。

用法(在 rag_env 下,项目根):
    python scripts/migrate_to_mysql.py                # 从 .env 读 MySQL 连接 + data/*.db 源
    python scripts/migrate_to_mysql.py --force        # 目标表已非空时先清空再迁(默认拒绝)
    python scripts/migrate_to_mysql.py --dry-run      # 只统计,不改目标

前置:.env 已配 DB_HOST/DB_PORT/DB_USER/DB_PASS/DB_NAME/KNOWLEDGE_DB_NAME/PREMIUM_DB_NAME;
目标库已创建(也可为空,脚本建表)。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import load  # noqa: E402
import app.db as dbmod  # noqa: E402


# 各库(映射到 SQLite 文件)要搬的表;列名两方言一致(保留字在 DDL/查询里已反引号)
_DB_GROUPS = {
    "session": ["meta", "sessions", "users", "auth_tokens", "events", "memory_entries"],
    "knowledge": ["chunks", "documents", "doc_structure"],
    "premium": ["products", "premium_rates"],
}

# SQLite 文件路径的 cfg 字段(按库类)
_SQLITE_PATH_KEY = {"session": "sqlite_path", "knowledge": "knowledge_db_path", "premium": "premium_db_path"}
_DEFAULT_SQLITE = {"session": "data/agent.db", "knowledge": "data/knowledge.db", "premium": "data/premium.db"}


def _target_count(target, table: str) -> int:
    try:
        r = target.execute(f"SELECT COUNT(*) AS c FROM `{table}`").fetchone()
        return int(r["c"] or 0)
    except Exception:
        return 0  # 表尚未建(不应发生,store 已建);视为 0


def _copy_table(cfg, db_kind: str, table: str, src: sqlite3.Connection, force: bool,
                batch: int = 5000) -> int:
    """把 sqlite 的一张表整搬进 MySQL 同表;已非空且非 --force 则抛错。分批搬,返回插入条数。"""
    target = dbmod.get_db(cfg, db_kind)
    existing = _target_count(target, table)
    if existing and not force:
        raise RuntimeError(
            f"MySQL {db_kind}.`{table}` 已有 {existing} 行;避免覆盖,请用 --force 清空重迁")
    if force and existing:
        target.execute(f"DELETE FROM `{table}`")

    cols = [r["name"] for r in src.execute(f"PRAGMA table_info(`{table}`)").fetchall()]
    if not cols:
        return 0
    qcols = ", ".join(f"`{c}`" for c in cols)
    qmarks = ", ".join(["?"] * len(cols))
    upd = ", ".join(f"`{c}`=VALUES(`{c}`)" for c in cols)
    sql = f"INSERT INTO `{table}` ({qcols}) VALUES ({qmarks}) ON DUPLICATE KEY UPDATE {upd}"
    # 分批读(源只读,sqlite 表扫按 rowid/PK 顺序,OFFSET 稳定),避免几十万事件行全载入内存
    total, offset = 0, 0
    while True:
        rows = src.execute(f"SELECT * FROM `{table}` LIMIT {batch} OFFSET {offset}").fetchall()
        if not rows:
            break
        target.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
        offset += len(rows)
        total += len(rows)
    return total


def _migrate(cfg, db_kind: str, sqlite_path: str, dry_run: bool, force: bool) -> None:
    if not os.path.isfile(sqlite_path):
        print(f"[{db_kind}] 源 {sqlite_path} 不存在,跳过")
        return
    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row
    try:
        for table in _DB_GROUPS[db_kind]:
            try:
                n = src.execute(f"SELECT COUNT(*) AS c FROM `{table}`").fetchone()["c"]
            except sqlite3.OperationalError:
                print(f"[{db_kind}] 源缺 `{table}` 表,跳过")
                continue
            target_n = 0 if dry_run else _target_count(dbmod.get_db(cfg, db_kind), table)
            if dry_run:
                print(f"[{db_kind}] `{table}` 源={n} 目标={target_n} (dry-run, 不写)")
                continue
            if n == 0:
                print(f"[{db_kind}] `{table}` 源为空,跳过")
                continue
            copied = _copy_table(cfg, db_kind, table, src, force)
            print(f"[{db_kind}] `{table}` 迁入 {copied} 行(sqlite={n})")
    finally:
        src.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="目标表已非空时先清空再迁(默认拒绝,防覆盖)")
    ap.add_argument("--dry-run", action="store_true", help="只统计,不写目标")
    ap.add_argument("--db", default=None, help="覆盖 SQLite 源目录/文件前缀;默认从 .env 取各 *_db_path")
    a = ap.parse_args()

    cfg = load()
    if dbmod.dial(cfg) != "mysql":
        print("[跳过] 未配置 DB_HOST(MySQL 未启用),无迁移目标;配置 .env 的 DB_HOST 后重跑。")
        return

    # 建好目标表结构(各 store 构造时幂等建 DDL)
    try:
        from app.session.store import SessionStore
        from app.retrieval.knowledge_store import KnowledgeStore
        from app.businesses.premium import PremiumStore
        SessionStore(cfg=cfg)
        KnowledgeStore(cfg=cfg)
        PremiumStore(cfg=cfg)
        print(f"[prepare] MySQL 目标库 schema 就绪 "
              f"(session={cfg.db_name}, knowledge={cfg.knowledge_db_name or cfg.db_name}, "
              f"premium={cfg.premium_db_name or cfg.db_name})")
    except Exception as e:
        print(f"[fail] 建表/连接 MySQL 失败: {type(e).__name__}: {e}")
        raise

    for db_kind, tables in _DB_GROUPS.items():
        key = _SQLITE_PATH_KEY[db_kind]
        path = a.db or (getattr(cfg, key, "") or _DEFAULT_SQLITE[db_kind])
        _migrate(cfg, db_kind, path, a.dry_run, a.force)

    print("[done] 迁移完成。请确认后把应用切到 MySQL 运行(DB_HOST 已配即生效)。")


if __name__ == "__main__":
    main()
