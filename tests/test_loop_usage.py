# -*- coding: utf-8 -*-
"""agent-loop 核心(app/loop/agent_loop.py)测试(fake LLM,不依赖真实 API)。

覆盖:usage/turn_end 携带 turn 级 ttft_ms 与 tokens_per_second;多步 ttft 取第一步、token 累加;
usage piece 不泄漏;尾端错误/断开仍落终结事件;检索收敛(达 max_retrieve 强制兜底);业务层 present_answer 溯源引用。
"""
import os
import tempfile
import types
import unittest

from app.loop.agent_loop import AgentLoop
from app.session.store import SessionStore
from app.session.events import make_event
from app.businesses.insurance import present_answer as ins_present
from app.businesses.insurance import force_answer as ins_force


def make_cfg(ms=6, mr=5):
    return types.SimpleNamespace(max_steps_per_turn=ms, max_retrieve_per_turn=mr, deepseek_model="fake")


class FakeAnswerLLM:
    """单步直接 answer(无工具):reasoning(ttft 2100)+ text。"""
    def chat_stream(self, messages, json_mode=False, tools=None, model=None):
        yield {"kind": "reasoning", "delta": "思考", "block_index": 0, "ttft_ms": 2100}
        yield {"kind": "text", "delta": "你好", "block_index": 1}
        yield {"kind": "usage", "delta": "", "block_index": None, "usage": {"prompt_tokens": 120, "completion_tokens": 105}}


class FakeRetrieveLLM:
    """两步:step1 叙述+原生 tool-call(ttft 2100),step2 回答(ttft 900)。"""
    def __init__(self): self.calls = 0
    def chat_stream(self, messages, json_mode=False, tools=None, model=None):
        self.calls += 1
        if self.calls == 1:
            yield {"kind": "reasoning", "delta": "先查资料", "block_index": 0, "ttft_ms": 2100}
            yield {"kind": "text", "delta": "已查到X,还缺Y,需再查Z", "block_index": 1}
            yield {"kind": "tool-call", "delta": '{"query":"重疾"}', "block_index": 2, "name": "search_knowledge", "call_id": "c1"}
            yield {"kind": "usage", "delta": "", "block_index": None, "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        else:
            yield {"kind": "reasoning", "delta": "足够", "block_index": 0, "ttft_ms": 900}
            yield {"kind": "text", "delta": "答案[1]", "block_index": 1}
            yield {"kind": "usage", "delta": "", "block_index": None, "usage": {"prompt_tokens": 20, "completion_tokens": 15}}


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = SessionStore(os.path.join(self.dir, "t.db"))

    def tearDown(self):
        self.store.close()

    def make_loop(self, llm, cfg=None, present=None, force=None, chunks=None):
        cfg = cfg or make_cfg()
        def emit(t, p): ev = make_event(t, p); self.store.append("s1", t, p); return ev
        def handler(args): return {"content": "[1] 条款片段", "reference": chunks if chunks is not None
                                   else [{"chunk_id": "doc:1", "content": "x", "doc_id": "doc", "version": "v1", "section": "s", "source": "src", "score": 0.9}]}
        tools = {"search_knowledge": {"schema": {"type": "function", "function": {"name": "search_knowledge", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}, "handler": handler}}
        present = present or (lambda text, refs: ([{"t": "p", "text": text}], []))
        force = force or (lambda refs: ([{"t": "p", "text": "已检索多次,未获取完整清单"}], []))
        return AgentLoop(llm, "系统", tools, present, cfg, emit=emit, force_answer=force)

    def run_turn(self, llm, text="你好", **kw):
        return list(self.make_loop(llm, **kw).turn("s1", text))


class LoopMetricsTest(_Base):
    def test_single_step_usage_and_turn_end_carry_ttft_and_tps(self):
        evs = self.run_turn(FakeAnswerLLM())
        types_ = [e["type"] for e in evs]
        self.assertIn("usage", types_); self.assertIn("turn_end", types_)
        usage = next(e["payload"] for e in evs if e["type"] == "usage")
        turn_end = next(e["payload"] for e in evs if e["type"] == "turn_end")
        self.assertEqual(usage["ttft_ms"], 2100); self.assertEqual(turn_end["ttft_ms"], 2100)
        self.assertEqual(usage["prompt_tokens"], 120); self.assertEqual(usage["completion_tokens"], 105)
        self.assertGreater(usage["tokens_per_second"], 0)
        self.assertEqual(turn_end["tokens_per_second"], usage["tokens_per_second"])

    def test_multi_step_ttft_first_step_tokens_summed_with_narration(self):
        evs = self.run_turn(FakeRetrieveLLM(), text="100种重大疾病")
        usage = next(e["payload"] for e in evs if e["type"] == "usage")
        self.assertEqual(usage["ttft_ms"], 2100); self.assertEqual(usage["prompt_tokens"], 30); self.assertEqual(usage["completion_tokens"], 20)
        types_ = [e["type"] for e in evs]
        self.assertIn("tool_call", types_); self.assertIn("retrieval", types_)
        narr = [e["payload"]["delta"] for e in evs if e["type"] == "assistant_chunk" and e["payload"].get("kind") == "text"]
        self.assertTrue(any("还缺Y" in n for n in narr))

    def test_usage_piece_not_leaked_as_assistant_chunk(self):
        chunks = [e for e in self.run_turn(FakeAnswerLLM()) if e["type"] == "assistant_chunk"]
        self.assertTrue(chunks)
        self.assertTrue(all(e["payload"].get("delta") for e in chunks))
        self.assertTrue(all(e["payload"].get("kind") in ("text", "reasoning") for e in chunks))


class LoopTerminalTest(_Base):
    def test_trailing_llm_error_still_records_turn_end_error(self):
        class ErrLLM:
            def chat_stream(self, messages, json_mode=False, tools=None, model=None):
                yield {"kind": "text", "delta": "a", "block_index": 0, "ttft_ms": 10}
                raise RuntimeError("connection reset")
        evs = self.run_turn(ErrLLM())
        types_ = [r["type"] for r in self.store.read("s1")]
        self.assertIn("assistant_message", types_); self.assertIn("usage", types_); self.assertIn("turn_end", types_)
        te = next(r["payload"] for r in self.store.read("s1") if r["type"] == "turn_end")
        self.assertEqual(te["reason"], "error")

    def test_generator_close_records_turn_end_interrupted(self):
        class NormalLLM:
            def chat_stream(self, messages, json_mode=False, tools=None, model=None):
                yield {"kind": "text", "delta": "好", "block_index": 0, "ttft_ms": 5}
                yield {"kind": "usage", "delta": "", "block_index": None, "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        gen = self.make_loop(NormalLLM()).turn("s1", "问题")
        for i, _ in enumerate(gen):
            if i >= 3: break
        gen.close()
        types_ = [r["type"] for r in self.store.read("s1")]
        self.assertIn("turn_end", types_)
        te = next(r["payload"] for r in self.store.read("s1") if r["type"] == "turn_end")
        self.assertEqual(te["reason"], "interrupted")

    def test_assistant_chunk_persisted_before_yield(self):
        class OneChunkLLM:
            def chat_stream(self, messages, json_mode=False, tools=None, model=None):
                yield {"kind": "text", "delta": "x", "block_index": 0, "ttft_ms": 5}
                yield {"kind": "usage", "delta": "", "block_index": None, "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        gen = self.make_loop(OneChunkLLM()).turn("s1", "问题")
        seen = False
        for ev in gen:
            if ev["type"] == "assistant_chunk": seen = True; break
        self.assertTrue(seen)
        self.assertGreaterEqual(len([r for r in self.store.read("s1") if r["type"] == "assistant_chunk"]), 1)
        gen.close()


class BusinessLayerTest(_Base):
    def test_insurance_present_answer_produces_citations(self):
        # 业务层 present_answer 把 [idx] 映射回条款原文(溯源)
        blocks, cites = ins_present("以下病种: 恶性肿瘤[1]", [[{"chunk_id": "条款:24", "content": "恶..."}]])
        self.assertEqual(cites, [{"idx": 1, "chunk_id": "条款:24"}])
        self.assertTrue(blocks)

    def test_present_answer_parses_markdown_into_blocks(self):
        # 业务层把模型 markdown 拆成结构化块:## 标题→h、- 要点→ul、**加粗**/[idx] 保留给前端 inline
        text = "## 一、重大疾病\n\n- 病种A **加粗** [1]\n- 病种B [2]\n\n## 二、等待期\n\n- 30日 [3]"
        blocks, cites = ins_present(text, [[{"chunk_id": "c1", "content": "x"}], [{"chunk_id": "c2", "content": "y"}], [{"chunk_id": "c3", "content": "z"}]])
        self.assertEqual([b["t"] for b in blocks], ["h", "ul", "h", "ul"])
        self.assertIn("**加粗**", blocks[1]["items"][0])
        self.assertIn("30日", blocks[3]["items"][0])
        self.assertEqual(cites, [{"idx": 1, "chunk_id": "c1"}, {"idx": 2, "chunk_id": "c2"}, {"idx": 3, "chunk_id": "c3"}])

    def test_no_idx_citation_completes_with_valid_int_idx(self):
        class CiteNoIdxLLM:
            def __init__(self): self.calls = 0
            def chat_stream(self, messages, json_mode=False, tools=None, model=None):
                self.calls += 1
                if self.calls == 1:
                    yield {"kind": "text", "delta": "先查", "block_index": 0, "ttft_ms": 10}
                    yield {"kind": "tool-call", "delta": '{"query":"重疾"}', "block_index": 1, "name": "search_knowledge", "call_id": "c1"}
                else:
                    yield {"kind": "text", "delta": "答案[1]", "block_index": 0, "ttft_ms": 40}
                yield {"kind": "usage", "delta": "", "block_index": None, "usage": {"prompt_tokens": 5, "completion_tokens": 5}}
        chunks = [{"chunk_id": "d:1", "content": "x", "doc_id": "doc", "version": "v1", "section": "s", "source": "src", "score": 0.9},
                  {"chunk_id": "d:2", "content": "y", "doc_id": "doc", "version": "v1", "section": "s", "source": "src", "score": 0.8}]
        evs = self.run_turn(CiteNoIdxLLM(), present=ins_present, chunks=chunks)
        am = next(r["payload"] for r in self.store.read("s1") if r["type"] == "assistant_message")
        self.assertTrue(am["citations"]); self.assertIsInstance(am["citations"][0]["idx"], int)
        self.assertEqual([e["type"] for e in evs][-1], "turn_end")


class LoopConvergenceTest(_Base):
    def test_retrieve_forever_stops_at_cap_and_force_answers(self):
        class RetrieveForeverLLM:
            def __init__(self): self.calls = 0
            def chat_stream(self, messages, json_mode=False, tools=None, model=None):
                self.calls += 1
                yield {"kind": "text", "delta": "仍不够,再查", "block_index": 0, "ttft_ms": 10}
                yield {"kind": "tool-call", "delta": '{"query":"完整列表"}', "block_index": 1, "name": "search_knowledge", "call_id": "c1"}
                yield {"kind": "usage", "delta": "", "block_index": None, "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        cfg = make_cfg(ms=10, mr=5)
        evs = self.run_turn(RetrieveForeverLLM(), text="100种重大疾病", cfg=cfg, force=ins_force)
        self.assertEqual(sum(1 for e in evs if e["type"] == "tool_call"), cfg.max_retrieve_per_turn)
        am = next(r["payload"] for r in self.store.read("s1") if r["type"] == "assistant_message")
        self.assertTrue(am["blocks"])
        self.assertIn("完整清单", am["blocks"][0]["text"])   # 业务层诚实兜底
        te = next(r["payload"] for r in self.store.read("s1") if r["type"] == "turn_end")
        self.assertEqual(te["reason"], "completed")




class HistoryInjectionTest(_Base):
    """阶段 A:history 应作为跨轮上下文,在第一次模型请求里、当前 user 之前。"""
    def test_history_prepended_before_current_user(self):
        seen = []
        class CaptureLLM:
            def chat_stream(self, messages, json_mode=False, tools=None, model=None):
                seen.append(messages)
                yield {"kind": "text", "delta": "答案", "block_index": 0}
                yield {"kind": "usage", "delta": "", "block_index": None, "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

        history = [
            {"role": "user", "content": "重疾险责任免除包括哪些?"},
            {"role": "assistant", "content": "责任免除包括A、B"},
        ]
        list(self.make_loop(CaptureLLM()).turn("s1", "那等待期呢?", history=history))
        self.assertTrue(seen, "llm 至少被调用一次")
        msgs = seen[0]
        self.assertIn({"role": "user", "content": "重疾险责任免除包括哪些?"}, msgs)
        self.assertIn({"role": "assistant", "content": "责任免除包括A、B"}, msgs)
        self.assertEqual(msgs[-1], {"role": "user", "content": "那等待期呢?"})



class ToolTruncationTest(_Base):
    """阶段 B-1:工具结果超过 max_tool_result_chars → 截断喂模型 content,result_truncated=True,"""
    """reference(原始 chunks)仍完整落 retrieval 事件。"""
    def test_long_tool_result_truncated_and_reference_kept(self):
        long_content = "A" * 12000
        chunks = [{"chunk_id": "doc:1", "content": "x", "doc_id": "doc", "version": "v1",
                  "section": "s", "source": "src", "score": 0.9}]
        def handler(args):
            return {"content": long_content, "reference": chunks}
        tools = {"search_knowledge": {"schema": {"type": "function", "function": {"name": "search_knowledge",
                                        "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                                                        "required": ["query"]}}}, "handler": handler}}
        cfg = types.SimpleNamespace(max_steps_per_turn=6, max_retrieve_per_turn=5, deepseek_model="fake",
                                    max_tool_result_chars=8000, tool_result_head_chars=4000, tool_result_tail_chars=1000)
        def emit(t_, p_):
            ev = make_event(t_, p_); self.store.append("s1", t_, p_); return ev
        loop = AgentLoop(FakeRetrieveLLM(), "系统", tools, ins_present, cfg, emit=emit, force_answer=ins_force)
        evs = list(loop.turn("s1", "100种重大疾病"))
        tr = next((e["payload"] for e in evs if e["type"] == "tool_result"), None)
        self.assertIsNotNone(tr)
        self.assertTrue(tr["result_truncated"])
        # 模型侧 conversation 里 content 被截断
        ret = next((e["payload"] for e in evs if e["type"] == "retrieval"), None)
        self.assertIsNotNone(ret)
        self.assertEqual(ret["chunks"], chunks)   # reference 完整



class WindowBoundTest(_Base):
    """阶段 B:history 超 window 预算时丢最旧、留当前;并落 request_context 事件。"""
    def _capture(self, cfg, history):
        seen = []
        class CapLLM:
            def chat_stream(self, messages, json_mode=False, tools=None, model=None):
                seen.append(messages)
                yield {"kind": "text", "delta": "答", "block_index": 0}
                yield {"kind": "usage", "delta": "", "block_index": None, "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        loop = self.make_loop(CapLLM(), cfg=cfg)
        evs = list(loop.turn("s1", "现在?", history=history))
        return seen, evs

    def test_long_history_trimmed_and_request_context_emitted(self):
        cfg = types.SimpleNamespace(max_steps_per_turn=6, max_retrieve_per_turn=5, deepseek_model="fake", context_window=400)
        history = [
            {"role": "user", "content": "问题" + "字" * 200},
            {"role": "assistant", "content": "答" + "话" * 200},
            {"role": "user", "content": "追问" + "词" * 200},
        ]
        seen, evs = self._capture(cfg, history)
        rc = next((e["payload"] for e in evs if e["type"] == "request_context"), None)
        self.assertIsNotNone(rc)
        self.assertEqual(rc["context_window"], 400)
        self.assertEqual(rc["model"], "fake")
        self.assertGreater(rc["prompt_tokens"], 0)
        msgs = seen[0]
        self.assertEqual(msgs[-1], {"role": "user", "content": "现在?"})   # 当前问题在最后
        self.assertFalse(any("问题" + "字" * 200 in (m.get("content") or "") for m in msgs))   # 最旧历史被丢

    def test_large_window_keeps_all_history(self):
        cfg = types.SimpleNamespace(max_steps_per_turn=6, max_retrieve_per_turn=5, deepseek_model="fake", context_window=100000)
        history = [{"role": "user", "content": "问题" + "字" * 20}, {"role": "assistant", "content": "答" + "话" * 20}]
        seen, _ = self._capture(cfg, history)
        msgs = seen[0]
        self.assertTrue(any(m.get("content") == "问题" + "字" * 20 for m in msgs))   # 历史全保留

if __name__ == "__main__":
    unittest.main()
