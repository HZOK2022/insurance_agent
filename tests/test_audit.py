# -*- coding: utf-8 -*-
"""阶段6(⑨ 审计/追溯 + ⑥ 可观测)测试:history_qa 重建问答、export 导出、metrics 聚合(成本/错误/重试/审批)。"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from app.audit.queries import history_qa, export_session, audit_overview
from app.observability.metrics import project_turn_metrics, session_metrics, overall_metrics, estimate_cost, severity_of
from app.session.store import SessionStore


def _chunk(cid):
    return {"chunk_id": cid, "doc_id": "doc", "version": "v1", "section": "s1",
            "source": "kb", "content": "正文", "score": 0.9}


def _fill(store, sid):
    """造 2 个 turn(一个正常、一个"审批+重试+错误")的事件流。"""
    store.append(sid, "user_message", {"text": "重疾险责任免除"})
    store.append(sid, "turn_start", {"turn": 1})
    store.append(sid, "step_start", {"turn": 1, "step": 1})
    store.append(sid, "tool_call", {"tool": "search_knowledge", "args": {"query": "责任免除"}})
    store.append(sid, "retrieval", {"query": "责任免除", "chunks": [_chunk("c1"), _chunk("c2")]})
    store.append(sid, "assistant_chunk", {"kind": "text", "delta": "部分"})
    store.append(sid, "assistant_message",
                 {"blocks": [{"t": "p", "text": "以下为检索到的部分病种，完整清单以条款原文为准"}],
                  "citations": [{"idx": 1, "chunk_id": "c1"}]})
    store.append(sid, "usage", {"model": "deepseek-v4-flash", "prompt_tokens": 100, "completion_tokens": 20,
                                "cost_estimate": None, "ttft_ms": 200, "tokens_per_second": 30})
    store.append(sid, "turn_end", {"turn": 1, "reason": "completed", "elapsed_ms": 1500})

    store.append(sid, "user_message", {"text": "给客户发消息"})
    store.append(sid, "turn_start", {"turn": 2})
    store.append(sid, "approval_request", {"request_id": "r1", "tool": "send_msg", "args": {"to": "cust"}})
    store.append(sid, "llm_retry", {"attempt": 1, "err": "429"})
    store.append(sid, "usage", {"model": "deepseek-v4-flash", "prompt_tokens": 50, "completion_tokens": 10})
    store.append(sid, "turn_end", {"turn": 2, "reason": "error", "elapsed_ms": 800})


class HistoryQaTest(unittest.TestCase):
    def setUp(self):
        self.db = tempfile.mktemp(suffix=".db")
        self.store = SessionStore(self.db)
        self.sid = self.store.create_session("u1")["id"]
        _fill(self.store, self.sid)

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        os.remove(self.db)

    def test_history_qa_reconstructs_pairs(self):
        items = history_qa(self.store, session_id=self.sid)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["question"], "重疾险责任免除")
        self.assertEqual(items[0]["retrievals"], 1)
        self.assertEqual(items[0]["citations"], [{"idx": 1, "chunk_id": "c1"}])
        self.assertFalse(items[0]["error"])
        self.assertEqual(items[0]["prompt_tokens"], 100)
        self.assertEqual(items[1]["question"], "给客户发消息")
        self.assertEqual(items[1]["approvals"], 1)
        self.assertEqual(items[1]["retries"], 1)
        self.assertTrue(items[1]["error"])

    def test_history_qa_filter_user(self):
        items = history_qa(self.store, user_id="u1")
        self.assertEqual(len(items), 2)
        self.assertEqual(history_qa(self.store, user_id="nope"), [])

    def test_export_jsonl_and_csv(self):
        jl = export_session(self.store, self.sid, "jsonl")
        self.assertEqual(len([l for l in jl.splitlines() if l]), 15)  # 两个 turn 共 9+6 条事件
        first = json.loads(jl.splitlines()[0])
        self.assertIn("seq", first)
        self.assertIn("type", first)
        js = export_session(self.store, self.sid, "json")
        self.assertIn('"events"', js)
        csv_txt = export_session(self.store, self.sid, "csv")
        self.assertTrue(csv_txt.startswith("session_id,title,seq,type,ts,payload"))

    def test_audit_overview(self):
        ov = audit_overview(self.store, session_id=self.sid)
        self.assertEqual(ov["sessions"], 1)
        self.assertGreater(ov["events"], 0)
        self.assertIn("turn_end", ov["type_dist"])


class ObservabilityTest(unittest.TestCase):
    def setUp(self):
        self.db = tempfile.mktemp(suffix=".db")
        self.store = SessionStore(self.db)
        self.sid = self.store.create_session("u1")["id"]
        _fill(self.store, self.sid)

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        os.remove(self.db)

    def test_project_turn_metrics(self):
        turns = project_turn_metrics(self.store, self.sid)
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0]["prompt_tokens"], 100)
        self.assertEqual(turns[1]["severity"], "error")
        self.assertEqual(turns[1]["retries"], 1)

    def test_session_metrics_aggregates(self):
        m = session_metrics(self.store, self.sid)
        self.assertEqual(m["turns"], 2)
        self.assertEqual(m["total_tokens"], 180)
        self.assertEqual(m["errors"], 1)
        self.assertEqual(m["retries"], 1)
        self.assertEqual(m["approvals"], 1)
        self.assertIsNone(m["cost"])

    def test_session_metrics_cost_when_priced(self):
        m = session_metrics(self.store, self.sid, price_in_per_1m=1.0, price_out_per_1m=1.0)
        self.assertAlmostEqual(m["cost"], (150 / 1e6) + (30 / 1e6), places=9)

    def test_overall_metrics(self):
        ov = overall_metrics(self.store)
        self.assertEqual(ov["totals"]["sessions"], 1)
        self.assertEqual(ov["totals"]["turns"], 2)
        self.assertEqual(len(ov["per_session"]), 1)
        self.assertEqual(ov["per_session"][0]["session_id"], self.sid)

    def test_estimate_cost_unpriced_is_none(self):
        self.assertIsNone(estimate_cost(0.0, 0.0, 1000, 2000))
        self.assertAlmostEqual(estimate_cost(2.0, 8.0, 1_000_000, 500_000), 2.0 + 4.0, places=9)

    def test_severity(self):
        self.assertEqual(severity_of("completed"), "info")
        self.assertEqual(severity_of("error"), "error")
        self.assertEqual(severity_of(None, ok=False), "error")


class PIIRedactTest(unittest.TestCase):
    def test_redact_pii_masks_patterns(self):
        from app.guardrails.redact import redact_pii
        t = redact_pii("电话13800138000 证件110101199001011234 卡6222021234567890123 邮箱a@b.com")
        self.assertNotIn("13800138000", t)
        self.assertNotIn("110101199001011234", t)
        self.assertNotIn("6222021234567890123", t)
        self.assertNotIn("a@b.com", t)
        self.assertIn("手机号***", t)
        self.assertIn("证件号***", t)

    def test_export_session_redacts_payload(self):
        db = tempfile.mktemp(suffix=".db")
        store = SessionStore(db)
        sid = store.create_session("u1")["id"]
        store.append(sid, "user_message", {"text": "我的电话13800138000", "client_time": None})
        out = export_session(store, sid, "jsonl")
        self.assertNotIn("13800138000", out)
        self.assertIn("手机号***", out)
        store.close()
        os.remove(db)


if __name__ == "__main__":
    unittest.main()
