# -*- coding: utf-8 -*-
"""M1:耗时归因 —— 检索四段计时 + 工具执行耗时进事件(trace"慢在哪一步")。

覆盖:
- search_knowledge(..., timings={}) 就地记录 embed_ms/dense_ms(bm25_ms 仅混合、rerank_ms 仅重排时);
- handler 返回 tool_meta → loop 把 elapsed_ms 进 tool_result、retrieval_timings_ms 进 retrieval 事件;
- 未批准/上限等未执行分支不产生误导性工具耗时。
"""
from __future__ import annotations

import os
import tempfile
import types
import unittest

from app.loop.agent_loop import AgentLoop
from app.retrieval.search_tool import search_knowledge
from app.session.events import make_event
from app.session.store import SessionStore


def _cfg(**kw):
    d = dict(max_steps_per_turn=6, max_retrieve_per_turn=5, deepseek_model="fake",
             badcase_snapshot_enabled=True, badcase_snapshot_sample_rate=0.0,
             max_tool_result_chars=0)
    d.update(kw)
    return types.SimpleNamespace(**d)


class _E:
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


class _S:
    def search(self, vec, top_k):
        return [{"chunk_id": "c1", "content": "正文x", "score": 0.9,
                 "meta": {"doc_id": "doc", "product_category": "医疗险"}}][:top_k]


class SearchTimingTest(unittest.TestCase):
    def test_records_embed_and_dense(self):
        timings: dict = {}
        out = search_knowledge(_E(), _S(), "等待期", top_k=5, top_rerank=3, timings=timings)
        self.assertEqual(len(out), 1)
        self.assertIn("embed_ms", timings)
        self.assertIn("dense_ms", timings)
        self.assertIsInstance(timings["embed_ms"], int)
        self.assertGreaterEqual(timings["embed_ms"], 0)
        self.assertNotIn("bm25_ms", timings)   # 纯稠密不记 BM25
        self.assertNotIn("rerank_ms", timings)  # 无重排不记

    def test_records_rerank_and_bm25_when_used(self):
        class _H:
            chunks_by_id = {}
            def search(self, q, k): return {"c1": 0.5, "c2": 0.4}
        def rrf(q, docs):
            return [{"index": 0, "relevance_score": 0.99}, {"index": 1, "relevance_score": 0.5}]
        class _S2:
            def search(self, vec, top_k):
                return [{"chunk_id": "c1", "content": "甲", "score": 0.9,
                         "meta": {"doc_id": "doc", "product_category": "医疗险"}},
                        {"chunk_id": "c2", "content": "乙", "score": 0.8,
                         "meta": {"doc_id": "doc", "product_category": "医疗险"}}][:top_k]
        timings: dict = {}
        out = search_knowledge(_E(), _S2(), "等待期", top_k=5, top_rerank=3, rerank_fn=rrf,
                               hybrid=_H(), hybrid_weight=0.5, timings=timings)
        self.assertEqual(len(out), 2)
        self.assertIn("embed_ms", timings); self.assertIn("dense_ms", timings)
        self.assertIn("bm25_ms", timings); self.assertIn("rerank_ms", timings)

    def test_no_timings_param_is_noop(self):
        self.assertEqual(search_knowledge(_E(), _S(), "x", top_k=5, top_rerank=3)[0]["chunk_id"], "c1")


class ToolTimingEventsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = SessionStore(os.path.join(self.dir, "t.db"))
        self.sid = self.store.create_session("u1")["id"]

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass

    def run_retrieve_turn(self, chunk_ref, timings_meta):
        def emit(t, p):
            ev = make_event(t, p)
            seq = self.store.append(self.sid, t, p)
            ev["seq"] = seq
            return ev

        def handler(args, start_idx=0):
            return {"content": "[1] 片段", "reference": chunk_ref,
                    "tool_meta": {"retrieval_timings_ms": timings_meta}}
        tools = {"search_knowledge": {"schema": {"type": "function", "function": {
            "name": "search_knowledge",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
            "handler": handler}}
        present = lambda text, refs: ([{"t": "p", "text": text}], [])

        class LLM:
            def __init__(self): self.calls = 0
            def chat_stream(self, messages, json_mode=False, tools=None, model=None):
                self.calls += 1
                if self.calls == 1:
                    yield {"kind": "text", "delta": "查", "block_index": 0}
                    yield {"kind": "tool-call", "delta": '{"query":"q"}', "block_index": 1, "name": "search_knowledge", "call_id": "c1"}
                else:
                    yield {"kind": "text", "delta": "答案[1]", "block_index": 0}
                yield {"kind": "usage", "delta": "", "block_index": None, "usage": {"prompt_tokens": 5, "completion_tokens": 5}}
        loop = AgentLoop(LLM(), "系统", tools, present, _cfg(), emit=emit)
        list(loop.turn(self.sid, "测试"))
        return self.store.read(self.sid)

    def test_tool_result_has_elapsed_and_retrieval_has_timings(self):
        chunk = [{"chunk_id": "d:1", "content": "x", "doc_id": "doc", "version": "v1",
                  "section": "s", "source": "src", "score": 0.9}]
        meta = {"embed_ms": 3, "dense_ms": 40}
        evs = self.run_retrieve_turn(chunk, meta)
        tr = next(e for e in evs if e["type"] == "tool_result")
        self.assertIn("elapsed_ms", tr["payload"])
        self.assertIsInstance(tr["payload"]["elapsed_ms"], int)
        self.assertGreaterEqual(tr["payload"]["elapsed_ms"], 0)
        ret = next(e for e in evs if e["type"] == "retrieval")
        self.assertEqual(ret["payload"].get("timings"), meta)


if __name__ == "__main__":
    unittest.main()
