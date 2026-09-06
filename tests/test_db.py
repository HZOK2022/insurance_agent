# -*- coding: utf-8 -*-
"""DB 连接层测试:方言判定/占位符/连接(未配 db_host 走 SQLite,配了走 MySQL 逻辑不在此连真库)。"""
from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace

from app import db


def _cfg(**kw):
    d = dict(db_enabled=True, db_host="", db_port=3306, db_user="", db_pass="", db_name="agent",
             knowledge_db_name="", premium_db_name="", sqlite_path="")
    d.update(kw)
    return SimpleNamespace(**d)


class DbDialectTest(unittest.TestCase):
    def test_default_sqlite(self):
        self.assertEqual(db.dial(_cfg()), "sqlite")
        self.assertEqual(db.ph(_cfg()), "?")

    def test_mysql_when_db_host(self):
        c = _cfg(db_host="10.0.0.5")
        self.assertEqual(db.dial(c), "mysql")
        self.assertEqual(db.ph(c), "%s")
        self.assertEqual(db.translate("SELECT * FROM t WHERE a=? AND b=?", c), "SELECT * FROM t WHERE a=%s AND b=%s")

    def test_db_name_fallback(self):
        c = _cfg(knowledge_db_name="kb", premium_db_name="prem")
        self.assertEqual(db.db_name(c, "knowledge"), "kb")
        self.assertEqual(db.db_name(c, "premium"), "prem")
        self.assertEqual(db.db_name(c, "session"), "agent")
        # 未设知识库名 → 回退 db_name
        self.assertEqual(db.db_name(_cfg(), "knowledge"), "agent")

    def test_get_conn_sqlite(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        c = _cfg(sqlite_path=path)
        conn = db.get_conn(c)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO t (v) VALUES (?)", ("hello",))
        row = conn.execute("SELECT v FROM t WHERE id=1").fetchone()
        # sqlite3.Row 支持下标/键访问
        self.assertEqual(row[0], "hello")
        conn.close()
        os.remove(path)


if __name__ == "__main__":
    unittest.main()
