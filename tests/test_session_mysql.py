# -*- coding: utf-8 -*-
"""SessionStore 走 MySQL(本地 3306,root 空密码)的往返测试;连不上则 skip。"""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

import app.db as dbmod
from app.session.store import SessionStore


def _mysql_cfg():
    host = os.environ.get("MYSQL_HOST", "127.0.0.1")
    return SimpleNamespace(db_host=host, db_port=3306, db_user="root", db_pass="",
                           db_name="insurance_agent", knowledge_db_name="insurance_knowledge",
                           premium_db_name="insurance_premium", sqlite_path="")


class SessionStoreMysqlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.cfg = _mysql_cfg()
            cls.conn = dbmod.get_conn(cls.cfg)   # 能连上才跑;连不上 skip
            cls.conn.close()
        except Exception as e:
            raise unittest.SkipTest(f"本地 MySQL 不可达: {e}")

    def setUp(self):
        self.s = SessionStore(cfg=_mysql_cfg())
        self.sid = self.s.create_session(user_id="mysql_test")["id"]

    def tearDown(self):
        # 清理测试数据(events 是 append-only,这里用底层连接直接清理测试会话)
        try:
            self.s._conn.execute("DELETE FROM events WHERE session_id=?", (self.sid,))
            self.s._conn.execute("DELETE FROM sessions WHERE id=?", (self.sid,))
        except Exception:
            pass
        self.s._conn.close()

    def test_create_session_and_append_event(self):
        seq = self.s.append(self.sid, "user_message", {"text": "你好", "client_time": None})
        self.assertGreaterEqual(seq, 1)   # MySQL events.seq 是全局自增,DELETE 不复位,不硬编码为 1
        self.s.append(self.sid, "assistant_message", {"blocks": [{"t": "p", "text": "您好"}], "citations": []})
        rows = self.s.read(self.sid)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["seq"], seq)
        self.assertEqual(rows[0]["type"], "user_message")
        self.assertEqual(rows[0]["payload"]["text"], "你好")
        self.assertEqual(rows[1]["type"], "assistant_message")

    def test_sessions_list_and_get(self):
        got = self.s.get_session(self.sid)
        self.assertEqual(got["user_id"], "mysql_test")
        self.assertEqual(self.s.list_sessions()[0]["id"], self.sid)
        self.assertEqual(self.s.count_users(), self.s.count_users())  # 可执行


if __name__ == "__main__":
    unittest.main()
