# -*- coding: utf-8 -*-
"""数据库连接层:生产两机(应用服务器连接数据库服务器的 MySQL)与开发/测试(SQLite)的统一入口。

- 事实源:生产用数据库服务器的 MySQL;开发/测试(未配 db_host)回退 SQLite,保证单测不依赖外部服务。
- 单写者:只有应用服务器部署这一个连接写 MySQL(跨进程/跨机器唯一写入方)。
- 用法:store 用 `get_conn(cfg, db_kind)` 取连接;SQL 占位符统一写 `?`,用 `ph(cfg)` 取实际占位符
  (sqlite=`?`, mysql=`%s`),执行前把 SQL 里的 `?` 换成 `ph()`,`?`→`%s` 交给 pymysql。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("insurance.agent")


def dial(cfg) -> str:
    """当前运行方言:配了 db_host → mysql,否则 sqlite。"""
    if (getattr(cfg, "db_host", "") or "").strip():
        return "mysql"
    return "sqlite"


def ph(cfg) -> str:
    """SQL 占位符:sqlite 用 ?,mysql 用 %s。SQL 里一律写 ?,执行前 translate 替换。"""
    return "%s" if dial(cfg) == "mysql" else "?"


def translate(sql: str, cfg) -> str:
    """把 SQL 中的 `?` 占位符换成对应方言的实际占位符(mysql `?`→`%s`)。"""
    if dial(cfg) == "mysql":
        return sql.replace("?", "%s")
    return sql


def db_name(cfg, db_kind: str = "session") -> str:
    """取某类库的库名:会话/事件/记忆用 db_name,知识用 knowledge_db_name,费率用 premium_db_name。"""
    if db_kind == "knowledge":
        return (getattr(cfg, "knowledge_db_name", "") or "").strip() or (getattr(cfg, "db_name", "") or "").strip()
    if db_kind == "premium":
        return (getattr(cfg, "premium_db_name", "") or "").strip() or (getattr(cfg, "db_name", "") or "").strip()
    return (getattr(cfg, "db_name", "") or "").strip()


def _sqlite_path(cfg, db_kind: str = "session") -> str:
    """开发/测试 SQLite 文件路径:会话用 sqlite_path,知识用 knowledge_db_path,费率用 premium_db_path。"""
    if db_kind == "knowledge":
        return (getattr(cfg, "knowledge_db_path", "") or "").strip() or "data/knowledge.db"
    if db_kind == "premium":
        return (getattr(cfg, "premium_db_path", "") or "").strip() or "data/premium.db"
    return (getattr(cfg, "sqlite_path", "") or "").strip() or "data/agent.db"


def get_conn(cfg, db_kind: str = "session") -> Any:
    """生产:返回 pymysql 连接(应用服务器连数据库服务器);开发/测试:返回 sqlite3 连接(对应 *.db)。"""
    if dial(cfg) == "mysql":
        try:
            import pymysql
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("使用 MySQL 需安装 pymysql(pip install pymysql)") from e
        return pymysql.connect(
            host=(getattr(cfg, "db_host", "") or "").strip(),
            port=int(getattr(cfg, "db_port", 3306) or 3306),
            user=(getattr(cfg, "db_user", "") or "").strip(),
            password=(getattr(cfg, "db_pass", "") or ""),
            database=db_name(cfg, db_kind),
            charset="utf8mb4",
            autocommit=True,
            cursorclass=pymysql.cursors.DictCursor,
        )
    # 开发/测试:SQLite
    import sqlite3
    conn = sqlite3.connect(_sqlite_path(cfg, db_kind), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_db(cfg, db_kind: str = "session") -> "DB":
    """统一入口:按方言/库类返回 DB 包装(store 用它建连接)。配了 db_host 走 MySQL,否则 SQLite 兜底。"""
    return DB(get_conn(cfg, db_kind), cfg)


def columns(db: "DB", table: str) -> set[str]:
    """列出某表当前列名(SQLite:PRAGMA table_info;MySQL:information_schema),供增量加列迁移。"""
    if db._dialect == "mysql":
        rows = db.execute(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=?",
            (table,)).fetchall()
        return {r["COLUMN_NAME"].lower() for r in rows}
    rows = db.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


class DB:
    """统一连接适配器:让 store 用同一种 `_conn.execute(sql, params)` 同时支持 sqlite/mysql。

    - sql:一律写 `?` 占位符,执行时按方言 translate(mysql `?`→`%s`)。
    - 行:sqlite3.Row / pymysql DictCursor 都支持 `row["col"]`。
    - mysql autocommit=True;sqlite 靠显式 commit(兼容原逻辑)。
    """

    def __init__(self, conn, cfg):
        self._conn = conn
        self._cfg = cfg
        self._dialect = dial(cfg)
        # pymysql 用 cursor;sqlite3.Connection.execute 也内建 cursor
        self._cursor = conn.cursor() if self._dialect == "mysql" else None

    def translate(self, sql: str) -> str:
        return translate(sql, self._cfg)

    def execute(self, sql: str, params: Any = ()):
        sql2 = translate(sql, self._cfg)
        if self._dialect == "mysql":
            cur = self._conn.cursor()
            cur.execute(sql2, params)
            return cur
        return self._conn.execute(sql2, params)

    def executemany(self, sql: str, seq: Any):
        sql2 = translate(sql, self._cfg)
        if self._dialect == "mysql":
            cur = self._conn.cursor()
            cur.executemany(sql2, seq)
            return cur
        return self._conn.executemany(sql2, seq)

    def commit(self):
        if self._dialect != "mysql":  # mysql autocommit,无谓 commit
            try:
                self._conn.commit()
            except Exception as e:
                logger.warning("db commit 失败: %s", e)

    def rollback(self):
        try:
            self._conn.rollback()
        except Exception as e:
            logger.warning("db rollback 失败: %s", e)

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def __getattr__(self, name: str):
        # 透传给底层连接(sqlite3 常用 attr 如 rowcount/lastrowid 走 cursor;这里兜底)
        return getattr(self._conn, name)

