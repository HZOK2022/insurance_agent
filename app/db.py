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


def get_conn(cfg) -> Any:
    """生产:返回 pymysql 连接(应用服务器连数据库服务器);开发/测试:返回 sqlite3 连接(data/agent.db)。"""
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
            database=db_name(cfg, "session"),
            charset="utf8mb4",
            autocommit=True,
        )
    # 开发/测试:SQLite
    import sqlite3
    conn = sqlite3.connect(getattr(cfg, "sqlite_path", "data/agent.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
