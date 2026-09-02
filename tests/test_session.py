import os, tempfile, time, unittest
from app.session import events, store


class RegistryTest(unittest.TestCase):
    def test_unknown_type_rejected_on_validate(self):
        with self.assertRaises(events.UnknownEventError):
            events.validate("bogus_type", {})

    def test_known_types(self):
        for t in ("user_message", "retrieval", "assistant_chunk", "assistant_message",
                  "tool_call", "tool_result", "approval_request", "approval_decision",
                  "usage", "turn_start", "turn_end"):
            self.assertIn(t, events.known_types())

    def test_missing_field_rejected(self):
        with self.assertRaises(ValueError):
            events.validate("user_message", {})

    def test_retrieval_chunk_validation(self):
        good = {"query": "q", "chunks": [{"chunk_id": "c", "score": 0.9, "doc_id": "d",
                                          "version": "v", "section": "s", "source": "src", "content": "x"}]}
        events.validate("retrieval", good)
        with self.assertRaises(ValueError):
            events.validate("retrieval", {"query": "q", "chunks": [{"chunk_id": "c"}]})  # 缺字段


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "t.db")
        self.s = store.SessionStore(self.path)

    def tearDown(self):
        self.s.close()

    def test_append_and_read_roundtrip(self):
        seq = self.s.append("s1", "user_message", {"text": "你好", "client_time": None})
        self.assertEqual(seq, 1)  # 首个事件 seq=1
        rows = self.s.read("s1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["seq"], 1)
        self.assertEqual(rows[0]["payload"]["text"], "你好")

    def test_second_seq_increments(self):
        self.s.append("s1", "user_message", {"text": "a", "client_time": None})
        seq2 = self.s.append("s1", "user_message", {"text": "b", "client_time": None})
        self.assertEqual(seq2, 2)

    def test_after_seq_filter(self):
        self.s.append("s1", "user_message", {"text": "a", "client_time": None})
        self.s.append("s1", "user_message", {"text": "b", "client_time": None})
        rows = self.s.read("s1", after_seq=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["seq"], 2)

    def test_list_sessions_orders_by_latest_activity(self):
        a = self.s.create_session("u1")["id"]
        b = self.s.create_session("u1")["id"]
        c = self.s.create_session("u1")["id"]
        # 给 c 加事件 -> c 应冒到最前
        self.s.append(c, "user_message", {"text": "hi", "client_time": None})
        order1 = [x["id"] for x in self.s.list_sessions()]
        self.assertEqual(order1[0], c)
        # 再给 a 加更晚的事件 -> a 冒到最前;b(无事件)排最后
        self.s.append(a, "user_message", {"text": "later", "client_time": None})
        order2 = [x["id"] for x in self.s.list_sessions()]
        self.assertEqual(order2[0], a)
        self.assertEqual(order2[1], c)
        self.assertEqual(order2[-1], b)

    def test_new_empty_session_sorts_first(self):
        """新建的会话(还没发消息)必须排在列表第一位,而不是被压到最后。

        旧实现 COALESCE(MAX(seq),0) 会把无事件会话排到末尾,与"新建即在第一位"相悖。
        """
        a = self.s.create_session("u1")["id"]
        self.s.append(a, "user_message", {"text": "旧会话", "client_time": None})
        time.sleep(0.01)  # 跨过毫秒边界,保证 created_at 严格晚于上一条事件 ts
        b = self.s.create_session("u1")["id"]  # 刚新建,还没发消息
        order = [x["id"] for x in self.s.list_sessions()]
        self.assertEqual(order[0], b, f"新建会话应置顶,实际顺序={order}")

    def test_prune_removes_never_messaged_session(self):
        """没发过消息的会话被硬删;发过消息的保留。"""
        empty = self.s.create_session("u1")["id"]
        used = self.s.create_session("u1")["id"]
        self.s.append(used, "user_message", {"text": "hi", "client_time": None})
        self.assertEqual(self.s.prune_empty_sessions(keep_id=""), 1)
        ids = [x["id"] for x in self.s.list_sessions()]
        self.assertNotIn(empty, ids)
        self.assertIn(used, ids)

    def test_prune_keeps_active_session(self):
        """keep_id 豁免:正在看的新会话不会被自己清掉。"""
        active = self.s.create_session("u1")["id"]
        other = self.s.create_session("u1")["id"]
        self.s.prune_empty_sessions(keep_id=active)
        ids = [x["id"] for x in self.s.list_sessions()]
        self.assertIn(active, ids)
        self.assertNotIn(other, ids)

    def test_repeated_new_session_keeps_only_one(self):
        """连点多次"新建":只保留最新那一个空会话(前端 newSession 的 keep 语义)。"""
        first = self.s.create_session("u1")["id"]
        second = self.s.create_session("u1")["id"]
        self.s.prune_empty_sessions(keep_id=second)
        ids = [x["id"] for x in self.s.list_sessions()]
        self.assertEqual(ids, [second])
        self.assertNotIn(first, ids)

    def test_prune_does_not_touch_events(self):
        """硬删只删 sessions 行,不触碰事件历史(append-only 铁律)。"""
        used = self.s.create_session("u1")["id"]
        self.s.append(used, "user_message", {"text": "hi", "client_time": None})
        self.s.prune_empty_sessions(keep_id="")
        self.assertEqual(len(self.s.read(used)), 1)

    def test_reconcile_dangling_turns_closes_unfinished_turn(self):
        sid = self.s.create_session("u1")["id"]
        self.s.append(sid, "turn_start", {"turn": 1})
        self.s.append(sid, "user_message", {"text": "重疾险", "client_time": None})
        self.s.append(sid, "step_start", {"turn": 1, "step": 1})
        self.s.append(sid, "assistant_chunk", {"kind": "reasoning", "delta": "思考中", "block_index": 0})
        # 模拟进程崩溃:无 step_end / assistant_message / turn_end
        fixed = self.s.reconcile_dangling_turns()
        self.assertEqual(fixed, 1)
        evs = self.s.read(sid)
        types = [e["type"] for e in evs]
        self.assertIn("assistant_message", types)
        self.assertIn("turn_end", types)
        te = next(e for e in evs if e["type"] == "turn_end")
        self.assertEqual(te["payload"]["reason"], "interrupted")

    def test_reconcile_skips_completed_turn(self):
        sid = self.s.create_session("u1")["id"]
        self.s.append(sid, "turn_start", {"turn": 1})
        self.s.append(sid, "user_message", {"text": "hi", "client_time": None})
        self.s.append(sid, "assistant_message", {"blocks": [{"t": "p", "text": "答"}], "citations": []})
        self.s.append(sid, "turn_end", {"turn": 1, "reason": "completed"})
        fixed = self.s.reconcile_dangling_turns()
        self.assertEqual(fixed, 0)
        self.assertEqual(len([e for e in self.s.read(sid) if e["type"] == "assistant_message"]), 1)

    def test_reconcile_keeps_existing_answer(self):
        sid = self.s.create_session("u1")["id"]
        self.s.append(sid, "turn_start", {"turn": 1})
        self.s.append(sid, "assistant_message", {"blocks": [{"t": "p", "text": "已答"}], "citations": []})
        # 有回答但缺 turn_end(进程在写 turn_end 前死)
        fixed = self.s.reconcile_dangling_turns()
        self.assertEqual(fixed, 1)
        types = [e["type"] for e in self.s.read(sid)]
        self.assertEqual(types.count("assistant_message"), 1)  # 不重复补回答
        self.assertEqual(types.count("turn_end"), 1)


    def test_load_unknown_type_rejected(self):
        self.s.append("s1", "user_message", {"text": "a", "client_time": None})
        # 直接注入一个未注册类型的事件(模拟损坏/未来版本的日志)
        self.s._conn.execute("INSERT INTO events (session_id,type,ts,payload) VALUES ('s1','bogus','now','{}')")
        self.s._conn.commit()
        with self.assertRaises(events.UnknownEventError):
            store.SessionStore(self.path)

    def test_schema_version_mismatch_rejected(self):
        self.s._conn.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
        self.s._conn.commit()
        with self.assertRaises(RuntimeError):
            store.SessionStore(self.path)

    def test_append_only_no_update_delete_api(self):
        # append-only 铁律:不暴露任何改写事件日志的方法
        self.assertFalse(hasattr(self.s, "update"))
        self.assertFalse(hasattr(self.s, "delete"))
        methods = [m for m in dir(self.s) if not m.startswith("_")]
        self.assertNotIn("update", methods)
        self.assertNotIn("delete", methods)

    def test_no_sql_update_delete_on_events(self):
        # 静态守卫:store.py 源码不得出现 UPDATE/DELETE 针对 events 表;只 append
        with open(os.path.join(os.path.dirname(store.__file__), "store.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("UPDATE events", src)
        self.assertNotIn("DELETE FROM events", src)


if __name__ == "__main__":
    unittest.main()