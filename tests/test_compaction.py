# -*- coding: utf-8 -*-
"""阶段 C(compaction)测试:剪枝 -> 保尾压头 -> LLM 摘要替换头部,含折叠持久化。

覆盖:
- 纯函数:select_keep_tail 不拆工具对;build_summary_request 含 §8.3 指令;frame_summary 封装。
- 集成(经 AgentLoop.turn):超窗触发压缩 + compaction_* 入日志 + append-only(原事件未删)
  + 对话为"摘要 + 保留尾";低于阈值不压缩;剪枝自降压。
- build_history 折叠:遇到 compaction_summary 折成一条 system 帧包,跳过被影子的事件。
"""
import os
import tempfile
import types
import unittest

from app.loop.agent_loop import AgentLoop
from app.session.store import SessionStore
from app.session.events import make_event
from app.session import context
from app.compaction import compactor as cpc


def make_cfg(ms=6, mr=5, **kw):
    d = dict(max_steps_per_turn=ms, max_retrieve_per_turn=mr, deepseek_model="fake")
    d.update(kw)
    return types.SimpleNamespace(**d)


TOOLS = {"search_knowledge": {"schema": {"type": "function", "function": {"name": "search_knowledge",
          "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
          "handler": lambda args, start_idx=0: {"content": "[1] (doc:1) 条款", "reference":
                [{"chunk_id": "doc:1", "content": "x", "doc_id": "doc", "version": "v1", "section": "s", "source": "src", "score": 0.9}]}}}


class SummaryLLM:
    """识别"压缩指令"请求(末条=COMPACTION_INSTRUCTION)返回简短摘要;否则返回回答。"""
    def chat_stream(self, messages, json_mode=False, tools=None, model=None):
        if messages and messages[-1].get("content") == cpc.COMPACTION_INSTRUCTION:
            yield {"kind": "text", "delta": "## 已查明的知识与口径\n- 已查A款\n## 下一步\n- 继续,", "block_index": 0}
        else:
            yield {"kind": "text", "delta": "答案", "block_index": 0}
        yield {"kind": "usage", "delta": "", "block_index": None, "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = SessionStore(os.path.join(self.dir, "t.db"))

    def tearDown(self):
        self.store.close()

    def make_loop(self, llm, cfg):
        def emit(t_, p_):
            ev = make_event(t_, p_); self.store.append("s1", t_, p_); return ev
        present = lambda text, refs: ([{"t": "p", "text": text}], [])
        force = lambda refs: ([{"t": "p", "text": "兜底"}], [])
        return AgentLoop(llm, "系统", TOOLS, present, cfg, emit=emit, force_answer=force)


class PieceTest(unittest.TestCase):
    def test_keep_tail_does_not_split_tool_pair(self):
        conv = [
            {"role": "system", "content": "s"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function",
                  "function": {"name": "search_knowledge", "arguments": "{}"}}]},
            {"role": "tool", "content": "T" * 5000, "tool_call_id": "c1", "name": "search_knowledge"},
            {"role": "user", "content": "追问"},
        ]
        k = cpc.select_keep_tail(conv, retain_budget=10000)
        self.assertIn(k, (0, 1))          # 边界落在 tool → 回退到它的 assistant(或 0)
        tail = conv[k:]
        # 工具组(assistant+tool_call 与 tool 结果)必须同侧,不可被拆开
        self.assertTrue(any(m.get("role") == "assistant" and m.get("tool_calls") for m in tail))
        self.assertTrue(any(m.get("role") == "tool" for m in tail))

    def test_summary_request_contains_section_markers(self):
        req = cpc.build_summary_request("sys", [{"role": "user", "content": "问题"}])
        self.assertEqual(req[0], {"role": "system", "content": "sys"})
        self.assertEqual(req[-1]["role"], "user")
        self.assertIn("## 已查明的知识与口径", req[-1]["content"])
        self.assertIn("## 风险与合规红线", req[-1]["content"])
        self.assertIn("## 下一步", req[-1]["content"])

    def test_prune_tool_messages_truncates_content(self):
        conv = [{"role": "tool", "content": "A" * 12000, "tool_call_id": "c1", "name": "search_knowledge"}]
        conv2, pruned = cpc.prune_tool_messages(conv, max_chars=8000, head_chars=4000, tail_chars=1000)
        self.assertTrue(pruned)
        self.assertLess(len(conv2[0]["content"]), 8000)
        self.assertIn("已截断", conv2[0]["content"])
        self.assertEqual(pruned[0]["index"], 0)

    def test_frame_summary_wraps_checkpoint(self):
        f = cpc.frame_summary("## 已查明\n- x")
        self.assertIn("<compacted-summary>", f)
        self.assertIn("</compacted-summary>", f)
        self.assertIn("检查点", f)
        self.assertTrue(f.startswith(cpc.CHECKPOINT_PREAMBLE))


class CompactionTriggerTest(_Base):
    def _seed_long_history(self, pairs=3, per=150):
        for i in range(pairs):
            self.store.append("s1", "user_message", {"text": "问题%d" % i + "字" * per})
            self.store.append("s1", "assistant_message",
                              {"blocks": [{"t": "p", "text": "答" + "话" * per + " [1]"}], "citations": []})

    def _convo(self, seen, text):
        return next(m for m in seen if m and m[-1].get("content") == text)

    def test_over_budget_compacts_and_appends_events(self):
        self._seed_long_history()
        hist = context.build_history(self.store, "s1")
        cfg = make_cfg(context_window=400)
        seen = []
        class LLM:
            def chat_stream(self, messages, json_mode=False, tools=None, model=None):
                seen.append(messages)
                if messages and messages[-1].get("content") == cpc.COMPACTION_INSTRUCTION:
                    yield {"kind": "text", "delta": "## 已查明的知识与口径\n- 已查A款\n", "block_index": 0}
                else:
                    yield {"kind": "text", "delta": "答案", "block_index": 0}
                yield {"kind": "usage", "delta": "", "block_index": None,
                       "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        evs = list(self.make_loop(LLM(), cfg).turn("s1", "现在?", history=hist))
        types_ = [e["type"] for e in evs]
        self.assertIn("compaction_start", types_)
        self.assertIn("compaction_summary", types_)
        self.assertIn("compaction_end", types_)
        rc = next(e["payload"] for e in evs if e["type"] == "request_context")
        self.assertTrue(rc["compression_triggered"])
        # append-only:被压缩的最旧 user_message 仍留在日志
        stored = self.store.read("s1")
        self.assertTrue(any(r["type"] == "user_message" and "问题0" in (r["payload"] or {}).get("text", "") for r in stored))
        # 对话请求:含 system 帧包摘要 + 当前问题在最后
        msgs = self._convo(seen, "现在?")
        self.assertTrue(any(m.get("role") == "system" and "<compacted-summary>" in (m.get("content") or "") for m in msgs))
        self.assertEqual(msgs[-1], {"role": "user", "content": "现在?"})
        # 摘要比被替换区间短 → 压缩成功;正文不含被压掉的最旧问题
        cs = next(e["payload"] for e in evs if e["type"] == "compaction_summary")
        self.assertTrue(cs["shadowed_seqs"])
        self.assertFalse(any("问题0" in (m.get("content") or "") for m in msgs))

    def test_empty_summary_falls_back_to_naive_trim(self):
        self._seed_long_history()
        hist = context.build_history(self.store, "s1")
        cfg = make_cfg(context_window=400)
        class NoSummaryLLM:
            def chat_stream(self, messages, json_mode=False, tools=None, model=None):
                # 摘要请求只回 usage,无 text => 摘要为空
                yield {"kind": "usage", "delta": "", "block_index": None, "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        evs = list(self.make_loop(NoSummaryLLM(), cfg).turn("s1", "现在?", history=hist))
        types_ = [e["type"] for e in evs]
        self.assertIn("compaction_start", types_)       # 先发"压缩中"(摘要前)
        self.assertNotIn("compaction_summary", types_)  # 摘要为空 → 无摘要
        ends = [e["payload"] for e in evs if e["type"] == "compaction_end"]
        self.assertTrue(ends and all(e.get("chars_saved", 0) == 0 for e in ends))  # 失败结束标记
        rc = next(e["payload"] for e in evs if e["type"] == "request_context")
        self.assertTrue(rc["compression_triggered"])   # 回退朴素丢头,最旧历史被丢

    def test_under_budget_no_compaction(self):
        cfg = make_cfg(context_window=100000)
        hist = [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "您好"}]
        seen = []
        class LLM:
            def chat_stream(self, messages, json_mode=False, tools=None, model=None):
                seen.append(messages)
                yield {"kind": "text", "delta": "答", "block_index": 0}
                yield {"kind": "usage", "delta": "", "block_index": None, "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        evs = list(self.make_loop(LLM(), cfg).turn("s1", "追问", history=hist))
        types_ = [e["type"] for e in evs]
        self.assertNotIn("compaction_start", types_)
        self.assertNotIn("compaction_summary", types_)
        msgs = seen[0]
        self.assertTrue(any(m.get("content") == "你好" for m in msgs))   # 历史全保留


class FoldTest(_Base):
    def test_build_history_folds_compaction_summary(self):
        seed = [
            # 最早两轮会被影子
            ("user_message", {"text": "第一问" + "字" * 200}),
            ("assistant_message", {"blocks": [{"t": "p", "text": "第一答" + "话" * 200}], "citations": []}),
            ("user_message", {"text": "第二问" + "字" * 200}),
            ("assistant_message", {"blocks": [{"t": "p", "text": "第二答" + "话" * 200}], "citations": []}),
        ]
        seqs = []
        for t_, p_ in seed:
            seqs.append(self.store.append("s1", t_, p_))
        # 早期 seq 被影子
        shadowed = seqs[0:4]
        self.store.append("s1", "compaction_summary",
                          {"summary": "## 已查明的知识与口径\n- 已查A\n", "shadowed_seqs": shadowed,
                           "shadowed_token_count": 800})
        # 新增一轮(不在影子内)
        self.store.append("s1", "user_message", {"text": "第三问" + "字" * 20})
        self.store.append("s1", "assistant_message", {"blocks": [{"t": "p", "text": "第三答" + "话" * 20}], "citations": []})
        hist = context.build_history(self.store, "s1")
        # 折成:一条 system 帧包摘要 + 最近一轮 user/assistant;被影子的第一二轮不出现
        roles = [m["role"] for m in hist]
        self.assertEqual(roles.count("system"), 1)
        sys_msg = next(m for m in hist if m["role"] == "system")
        self.assertIn("<compacted-summary>", sys_msg["content"])
        self.assertIn("已查明的知识与口径", sys_msg["content"])
        self.assertFalse(any("第一问" in (m.get("content") or "") for m in hist))
        self.assertFalse(any("第二问" in (m.get("content") or "") for m in hist))
        self.assertTrue(any(m.get("content") == "第三问" + "字" * 20 for m in hist))




class MidTurnOverflowTest(_Base):
    """回合中 context-overflow/pressure:回合开始未超 80%,检索/推理中增长跨过阈值 → 立即压缩。"""
    def test_midturn_pressure_compacts_with_reason(self):
        cfg = make_cfg(context_window=1000, compaction_threshold_ratio=0.8, compaction_retain_ratio=0.16,
                       max_tool_result_chars=0)   # 追加不截断,让工具结果把回合中撑过 80%
        class LLM:
            def __init__(self): self.calls = 0
            def chat_stream(self, messages, json_mode=False, tools=None, model=None):
                self.calls += 1
                if messages and messages[-1].get("content") == cpc.COMPACTION_INSTRUCTION:
                    yield {"kind": "text", "delta": "## 已查明的知识与口径\n- 已查A\n## 下一步\n- 继续,", "block_index": 0}
                elif self.calls == 1:
                    yield {"kind": "text", "delta": "先查", "block_index": 0}
                    yield {"kind": "tool-call", "delta": '{"query":"重疾"}', "block_index": 1, "name": "search_knowledge", "call_id": "c1"}
                else:
                    yield {"kind": "text", "delta": "答案", "block_index": 0}
                yield {"kind": "usage", "delta": "", "block_index": None, "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        def handler(args, start_idx=0):
            return {"content": "条款" * 175, "reference": [{"chunk_id": "doc:1", "content": "x", "doc_id": "doc", "version": "v1", "section": "s", "source": "src", "score": 0.9}]}
        tools = {"search_knowledge": {"schema": {"type": "function", "function": {"name": "search_knowledge",
                  "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}, "handler": handler}}
        def emit(t_, p_): ev = make_event(t_, p_); self.store.append("s1", t_, p_); return ev
        hist = [{"role": "user", "content": "问" + "字" * 200}, {"role": "assistant", "content": "答" + "话" * 200}]
        loop = AgentLoop(LLM(), "系统", tools, (lambda t, r: ([{"t": "p", "text": t}], [])), cfg, emit=emit)
        evs = list(loop.turn("s1", "现在?", history=hist))
        starts = [e["payload"] for e in evs if e["type"] == "compaction_start"]
        self.assertTrue(any(s.get("reason") == "pressure" for s in starts), "回合中跨过 80% 应触发 pressure 压缩")
        self.assertTrue(any(e["type"] == "compaction_summary" for e in evs))




class PerTurnCitationTest(_Base):
    """D55 引用编号每轮 turn-local、从 1 起:上下文回答(当轮无检索)不再解析历史 [idx];
    有检索的轮,编号只取决于当轮检出的块(前一轮检索过什么不影响本轮编号)。"""

    def _prior_round(self):
        """在 store 里造一个"上一轮":检索过 c1 + 引用 [1]->c1(旧编号空间)。"""
        self.store.append("s1", "retrieval", {"query": "q", "chunks": [
            {"chunk_id": "c1", "content": "条款X", "doc_id": "d", "version": "v1", "section": "s", "source": "src", "score": 0.9}]})
        self.store.append("s1", "assistant_message", {"blocks": [{"t": "p", "text": "答案是X [1]"}], "citations": [{"idx": 1, "chunk_id": "c1"}]})

    def test_context_only_answer_has_no_citations(self):
        """当轮无检索(纯复述)→ 历史 [idx] 无块可解析 → citations 为空(不再跨轮引用)。"""
        from app.businesses.insurance import present_answer as ins_present
        self._prior_round()
        class LLM:
            def chat_stream(self, messages, json_mode=False, tools=None, model=None):
                yield {"kind": "text", "delta": "复述:答案是X [1]", "block_index": 0}
                yield {"kind": "usage", "delta": "", "block_index": None, "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        def emit(t_, p_): ev = make_event(t_, p_); self.store.append("s1", t_, p_); return ev
        loop = AgentLoop(LLM(), "系统", {}, ins_present, make_cfg(), emit=emit)
        evs = list(loop.turn("s1", "再复述一遍", history=[]))
        am = next(e["payload"] for e in evs if e["type"] == "assistant_message")
        self.assertEqual(am["citations"], [])   # 旧 [1] 不再可解析(上下文回答无角标)

    def test_retrieval_turn_numbering_restarts_from_one(self):
        """上一轮检索过 c1;本轮检出新块 c2 → 本轮 feed/citations 从 [1] 开始指向 c2(不沿用全局编号)。"""
        from app.businesses.insurance import present_answer as ins_present
        from app.businesses.insurance import _format_chunks
        self._prior_round()
        # 本轮:工具返回新块 c2(handler 按 start_idx 从本轮 1 编号)
        def handler(args, start_idx=0):
            chunks = [{"chunk_id": "c2", "content": "条款Y", "doc_id": "d", "version": "v1", "section": "s", "source": "src", "score": 0.9}]
            return {"content": _format_chunks(chunks, start_idx), "reference": chunks}
        tools = {"search_knowledge": {"schema": {"type": "function", "function": {
            "name": "search_knowledge", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
            "handler": handler}}
        class TwoStep:
            def __init__(self): self.calls = 0; self.tool_feed = None
            def chat_stream(self, messages, json_mode=False, tools=None, model=None):
                self.calls += 1
                if self.calls == 1:
                    yield {"kind": "text", "delta": "查", "block_index": 0, "ttft_ms": 10}
                    yield {"kind": "tool-call", "delta": '{"query":"q"}', "block_index": 1, "name": "search_knowledge", "call_id": "c1"}
                else:
                    # 记录本轮喂给模型的检索内容编号(应 [1] 起,而不是接历史全局编号 [2])
                    for m in messages:
                        if m.get("role") == "tool":
                            self.tool_feed = m.get("content", "")
                    yield {"kind": "text", "delta": "本轮结论 [1]", "block_index": 0, "ttft_ms": 10}
                yield {"kind": "usage", "delta": "", "block_index": None, "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        def emit(t_, p_): ev = make_event(t_, p_); self.store.append("s1", t_, p_); return ev
        llm = TwoStep()
        loop = AgentLoop(llm, "系统", tools, ins_present, make_cfg(), emit=emit)
        evs = list(loop.turn("s1", "那Y呢", history=[]))
        self.assertIn("[1] (c2)", llm.tool_feed or "")     # 本轮编号从 1 起
        self.assertNotIn("[2]", llm.tool_feed or "")       # 不沿用会话全局编号
        am = next(e["payload"] for e in evs if e["type"] == "assistant_message")
        self.assertEqual(am["citations"], [{"idx": 1, "chunk_id": "c2"}])


if __name__ == "__main__":
    unittest.main()
