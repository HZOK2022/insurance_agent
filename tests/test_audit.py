# -*- coding: utf-8 -*-
"""阶段6(⑨ 审计/追溯 + ⑥ 可观测)测试:history_qa 重建问答、export 导出、metrics 聚合(成本/错误/重试/审批)。"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from app.audit.queries import history_qa, export_session, audit_overview
from app.observability.metrics import project_turn_metrics, session_metrics, overall_metrics, timeseries_metrics, anomaly_report, estimate_cost, severity_of
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


class TimeseriesTest(unittest.TestCase):
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

    def test_buckets_aggregate(self):
        for gran in ("hour", "day"):
            s = timeseries_metrics(self.store, granularity=gran)
            # 同一测试时间内的两个 turn 落在同一桶(跨小时/天边界概率可忽略)
            nz = [x for x in s if x["turns"] > 0]
            self.assertGreaterEqual(len(nz), 1)
            sum_ = lambda k: sum(int(x[k] or 0) for x in nz)
            self.assertEqual(sum_("turns"), 2)
            self.assertEqual(sum_("errors"), 1)
            self.assertEqual(sum_("prompt_tokens"), 150)
            self.assertEqual(sum_("completion_tokens"), 30)
            self.assertEqual(sum_("total_tokens"), 180)
            self.assertEqual(sum_("retries"), 1)
            self.assertEqual(sum_("cost"), 0.0)  # 未配单价 → 每桶 cost 为 None,sum 为 0
            if len(nz) == 1:  # 正常单桶:验证 p95(延迟 [800,1500] 的 p95≈第 0 个)
                self.assertEqual(nz[0]["p95_latency_ms"], 800)

    def test_cost_when_priced(self):
        s = timeseries_metrics(self.store, granularity="hour", price_in_per_1m=1.0, price_out_per_1m=1.0)
        nz = [x for x in s if x["turns"] > 0]
        self.assertGreaterEqual(len(nz), 1)
        cost = sum((x["cost"] or 0.0) for x in nz)
        self.assertAlmostEqual(cost, (150 / 1e6) + (30 / 1e6), places=9)

    def test_invalid_granularity_falls_back(self):
        s = timeseries_metrics(self.store, granularity="week")  # 非法 → hour
        self.assertGreaterEqual(len(s), 1)

    def test_empty_store(self):
        db2 = tempfile.mktemp(suffix=".db")
        st = SessionStore(db2)
        self.assertEqual(timeseries_metrics(st), [])
        st.close()
        os.remove(db2)



class AnomalyReportTest(unittest.TestCase):
    def setUp(self):
        self.db = tempfile.mktemp(suffix=".db")
        self.store = SessionStore(self.db)
        self.sid = self.store.create_session("u1")["id"]

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        os.remove(self.db)

    def _turn(self, *, retrieval=None, cite_count=0, reason="completed", elapsed_ms=1000, tool_fail=False, guard=False):
        st = self.store
        st.append(self.sid, "turn_start", {"turn": 1})
        if guard:
            st.append(self.sid, "guard_triggered", {"kind": "injection"})
        if retrieval is not None:
            st.append(self.sid, "retrieval", {"query": "q", "chunks": retrieval})
        st.append(self.sid, "assistant_message", {"blocks": [{"t": "p", "text": "a"}],
                                                  "citations": [{"idx": i, "chunk_id": "c%d" % i} for i in range(1, cite_count + 1)]})
        if tool_fail:
            st.append(self.sid, "tool_result", {"ok": False, "tool": "send_msg", "error_code": "tool_error"})
        st.append(self.sid, "turn_end", {"reason": reason, "elapsed_ms": elapsed_ms})

    def _low_chunk(self, score):
        return {"chunk_id": "c1", "doc_id": "doc", "version": "v1", "section": "s1",
                "source": "kb", "content": "正文", "score": score}

    def test_error_turn_classified(self):
        _fill(self.store, self.sid)
        r = anomaly_report(self.store)
        self.assertEqual(r["summary"]["total_turns"], 2)
        self.assertEqual(r["summary"]["anomalies"], 1)
        self.assertEqual(r["categories"]["error"]["count"], 1)
        self.assertIn(self.sid, r["categories"]["error"]["samples"])

    def test_low_conf_retrieval(self):
        self._turn(retrieval=[self._low_chunk(0.2)], cite_count=1)
        r = anomaly_report(self.store)
        self.assertEqual(r["categories"]["retrieval_low_conf"]["count"], 1)
        self.assertTrue(any("0.20" in h for h in r["categories"]["retrieval_low_conf"]["hints"]))

    def test_retrieval_empty(self):
        self._turn(retrieval=[], cite_count=0, reason="completed")
        r = anomaly_report(self.store)
        self.assertEqual(r["categories"]["retrieval_empty"]["count"], 1)

    def test_answer_not_cited(self):
        # 检索了(高分)但回答一个引用都没有 → answer_not_cited
        self._turn(retrieval=[self._low_chunk(0.9)], cite_count=0)
        r = anomaly_report(self.store)
        self.assertEqual(r["categories"]["answer_not_cited"]["count"], 1)

    def test_cross_turn_no_retrieval(self):
        # 无检索却带引用 → cross_turn_no_retrieval
        self._turn(retrieval=None, cite_count=2)
        r = anomaly_report(self.store)
        self.assertEqual(r["categories"]["cross_turn_no_retrieval"]["count"], 1)

    def test_tool_failure(self):
        self._turn(retrieval=[self._low_chunk(0.9)], cite_count=1, tool_fail=True)
        r = anomaly_report(self.store)
        self.assertEqual(r["categories"]["tool_failure"]["count"], 1)

    def test_funnel_aggregates(self):
        self._turn(retrieval=[self._low_chunk(0.9), self._low_chunk(0.8)], cite_count=2)
        r = anomaly_report(self.store)
        f = r["funnel"]
        self.assertEqual(f["with_retrieval_turns"], 1)
        self.assertEqual(f["retrieval_total"], 1)  # 一次检索事件
        self.assertEqual(f["cited_turns"], 1)
        self.assertEqual(f["cited_rate"], 1.0)
        self.assertEqual(r["categories"]["error"]["count"], 0)  # 本轮正常

    def test_guard_triggered(self):
        self._turn(retrieval=[self._low_chunk(0.9)], cite_count=1, guard=True)
        r = anomaly_report(self.store)
        self.assertEqual(r["categories"]["guard_triggered"]["count"], 1)

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
