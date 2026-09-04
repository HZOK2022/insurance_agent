# -*- coding: utf-8 -*-
"""工具参数契约校验:参数不合契约必须在进 handler 之前被拦下,并回给模型可操作的诊断。

为什么需要(不是"让报错好看点"这种装饰性需求):
  handler 里普遍是 `(args or {}).get("query") or ""` 这类兜底取值,模型把参数名写错
  (如 q/keyword)不会抛异常,而是带着空值**真的去执行** —— 检索空串照样 embedding、
  照样返回 top_k 最近邻,模型拿到格式完美但语义无关的结果,于是编出带 [idx] 引用的
  错误答案。这种"假成功"比崩溃危险得多(崩溃至少有 tool_error 可收敛)。
  校验的作用就是把这条路径拦下来,并告诉模型"缺了哪个字段、期望什么类型",
  而不是丢一句"调用失败"让模型瞎猜或被迫手算。
"""
from __future__ import annotations

import types
import unittest
from unittest import mock

from app.loop import agent_loop as al
from app.loop.agent_loop import AgentLoop
from app.session.events import make_event

# 与 businesses/insurance.py 的 SEARCH_TOOL 同构(OpenAI function schema)
_SEARCH_SCHEMA = {"type": "function", "function": {
    "name": "search_knowledge",
    "parameters": {"type": "object",
                   "properties": {"query": {"type": "string"}, "category": {"type": "string"}},
                   "required": ["query"]}}}

# 与 businesses/premium.py 的 PREMIUM_TOOL 同构(含 enum,验证取值域也能拦)
_PREMIUM_SCHEMA = {"type": "function", "function": {
    "name": "calculate_premium",
    "parameters": {"type": "object",
                   "properties": {"product": {"type": "string"},
                                  "age": {"type": "integer"},
                                  "deductible": {"type": "string", "enum": ["0元", "1.5万", "3万"]}},
                   "required": ["product", "age"]}}}


def _cfg(**kw):
    d = dict(max_steps_per_turn=6, max_retrieve_per_turn=5, deepseek_model="fake",
             write_tools_approval="auto")
    d.update(kw)
    return types.SimpleNamespace(**d)


class CallLLM:
    """step1:用给定参数调一次工具;step2:基于结果回答。记录最后一次请求的消息。"""

    def __init__(self, args='{"query":"免赔额"}', name="search_knowledge"):
        self.calls = 0
        self.args = args
        self.name = name
        self.msgs = []

    def chat_stream(self, messages, json_mode=False, tools=None, model=None):
        self.msgs = list(messages)
        self.calls += 1
        if self.calls == 1:
            yield {"kind": "text", "delta": "我来查", "block_index": 0, "ttft_ms": 10}
            yield {"kind": "tool-call", "delta": self.args, "block_index": 1,
                   "name": self.name, "call_id": "c1"}
        else:
            yield {"kind": "text", "delta": "基于已有资料回答", "block_index": 0, "ttft_ms": 10}
        yield {"kind": "usage", "delta": "", "block_index": None,
               "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


def _run(llm, handler, schema=_SEARCH_SCHEMA, name="search_knowledge"):
    """跑一整轮,返回 (事件列表, handler 收到的 args 列表)。"""
    seen = []

    def _h(args, start_idx=0, session_id=None):
        seen.append(args)
        return handler(args, start_idx)

    loop = AgentLoop(llm, "系统", {name: {"schema": schema, "handler": _h}},
                     (lambda t, r: ([{"t": "p", "text": t}], [])),
                     _cfg(), emit=lambda t, p: make_event(t, p))
    evs = list(loop.turn("s1", "查一下免赔额"))
    return evs, seen


def _tool_result(evs):
    return [e for e in evs if e["type"] == "tool_result"][0]["payload"]


def _tool_feed(llm):
    """模型真正看到的最后一条 tool 消息 content。"""
    msgs = [m for m in llm.msgs if m.get("role") == "tool"]
    return msgs[-1]["content"] if msgs else ""


# ---------------------------------------------------------------- 纯函数层
class SchemaExtractionTest(unittest.TestCase):
    def test_extracts_from_openai_function_shape(self):
        self.assertIn("required", al.tool_parameters_schema({"schema": _SEARCH_SCHEMA}))

    def test_extracts_from_bare_parameters_shape(self):
        tool = {"schema": {"parameters": {"type": "object", "properties": {}}}}
        self.assertEqual(al.tool_parameters_schema(tool), {"type": "object", "properties": {}})

    def test_missing_parameters_returns_empty(self):
        self.assertEqual(al.tool_parameters_schema({"schema": {}}), {})
        self.assertEqual(al.tool_parameters_schema({}), {})


# ---------------------------------------------------------------- 校验行为
class ValidateBehaviorTest(unittest.TestCase):
    def test_valid_args_pass(self):
        self.assertIsNone(al.validate_tool_arguments({"schema": _SEARCH_SCHEMA},
                                                     {"query": "免赔额"}))

    def test_missing_required_is_blocked(self):
        msg = al.validate_tool_arguments({"schema": _SEARCH_SCHEMA}, {"category": "医疗险"})
        self.assertIsNotNone(msg)
        self.assertIn("query", msg)

    def test_wrong_type_is_blocked(self):
        msg = al.validate_tool_arguments({"schema": _PREMIUM_SCHEMA},
                                         {"product": "尊享e生2025", "age": "35"})
        self.assertIsNotNone(msg)
        self.assertIn("age", msg)

    def test_enum_violation_is_blocked(self):
        msg = al.validate_tool_arguments({"schema": _PREMIUM_SCHEMA},
                                         {"product": "p", "age": 35, "deductible": "0"})
        self.assertIsNotNone(msg)
        self.assertIn("deductible", msg)

    def test_non_dict_args_is_blocked(self):
        """parse_tool_arguments 解析失败会返回原始字符串,不是 dict。"""
        msg = al.validate_tool_arguments({"schema": _SEARCH_SCHEMA}, "这不是json")
        self.assertIsNotNone(msg)
        self.assertIn("JSON 对象", msg)

    def test_tool_without_parameters_is_not_blocked(self):
        """没声明 parameters 的工具一律放行(校验是加固,不是门槛)。"""
        tool = {"schema": {"type": "function", "function": {"name": "x"}}}
        self.assertIsNone(al.validate_tool_arguments(tool, {"anything": 1}))

    @mock.patch.object(al, "_jsonschema", None)
    def test_missing_jsonschema_degrades_to_pass(self):
        """缺依赖时退化为改动前的行为,绝不让工具集体失效。"""
        self.assertIsNone(al.validate_tool_arguments({"schema": _SEARCH_SCHEMA}, {"q": "x"}))


# ---------------------------------------------------------------- 集成:拦截与回喂
class GuardIntegrationTest(unittest.TestCase):
    def test_wrong_field_name_never_reaches_handler(self):
        """核心场景:参数名写错(历史上真的发生过 query=None)—— 必须拦在 handler 之外。"""
        def handler(args, start_idx=0):
            return {"content": "【检索结果 1】假结果", "reference": []}

        llm = CallLLM(args='{"q":"免赔额"}')
        evs, seen = _run(llm, handler)
        self.assertEqual(seen, [], "参数不合契约时 handler 绝不能被执行")
        tr = _tool_result(evs)
        self.assertFalse(tr["ok"])
        self.assertEqual(tr["error"], "invalid_arguments")

    def test_diagnosis_is_actionable_for_model(self):
        """回给模型的话必须指出错在哪个字段 —— 否则模型只能瞎猜或手算。"""
        def handler(args, start_idx=0):
            return {"content": "x", "reference": []}

        llm = CallLLM(args='{"q":"免赔额"}')
        _run(llm, handler)
        feed = _tool_feed(llm)
        self.assertIn("query", feed)
        self.assertIn("必填", feed)
        self.assertIn("【工具调用失败】", feed)

    def test_valid_args_still_executes_handler(self):
        def handler(args, start_idx=0):
            return {"content": "【检索结果 1】真结果", "reference": []}

        llm = CallLLM(args='{"query":"免赔额"}')
        evs, seen = _run(llm, handler)
        self.assertEqual(seen, [{"query": "免赔额"}])
        tr = _tool_result(evs)
        self.assertTrue(tr["ok"])
        self.assertIsNone(tr["error"])
        self.assertNotIn("【工具调用失败】", _tool_feed(llm))

    def test_invalid_args_still_logged_for_audit(self):
        """铁律1:模型可见⟺已记录。参数被拒也要留在 tool_call 事件里可审计。"""
        def handler(args, start_idx=0):
            return {"content": "x", "reference": []}

        llm = CallLLM(args='{"q":"免赔额"}')
        evs, _ = _run(llm, handler)
        tc = [e for e in evs if e["type"] == "tool_call"][0]["payload"]
        self.assertEqual(tc["args"], {"q": "免赔额"})

    def test_handler_exception_still_maps_to_tool_error(self):
        """校验只是前置关卡,不替换原有的异常分类。"""
        def handler(args, start_idx=0):
            raise RuntimeError("SECRET_DETAIL_12345")

        llm = CallLLM(args='{"query":"免赔额"}')
        evs, _ = _run(llm, handler)
        tr = _tool_result(evs)
        self.assertFalse(tr["ok"])
        self.assertEqual(tr["error"], "tool_error")   # 不是 invalid_arguments
        self.assertNotIn("SECRET_DETAIL_12345", _tool_feed(llm))
        self.assertIn("【工具调用失败】", _tool_feed(llm))

    def test_turn_completes_after_invalid_args(self):
        """参数被拒后 loop 必须继续收敛(模型据此改参数或诚实作答),不能悬挂。"""
        def handler(args, start_idx=0):
            return {"content": "x", "reference": []}

        llm = CallLLM(args='{"q":"免赔额"}')
        evs, _ = _run(llm, handler)
        self.assertEqual(evs[-1]["type"], "turn_end")
        self.assertEqual(evs[-1]["payload"]["reason"], "completed")


# ---------------------------------------------------------------- 真实契约绑定
class RealSchemaTest(unittest.TestCase):
    """用业务层真实的 schema 冒烟:上面的自建 schema 测的是机制,这里确保真实契约

    也真的被解析和拦截。若有人改了 SEARCH_TOOL 的 required/字段,这里会先红。
    (延迟 import:业务模块带 retrieval 依赖链,避免拖慢与此无关的其他测试。)
    """

    @classmethod
    def setUpClass(cls):
        from app.businesses.insurance import SEARCH_TOOL, HISTORY_SEARCH_TOOL
        cls.SEARCH_TOOL = SEARCH_TOOL
        cls.HISTORY_SEARCH_TOOL = HISTORY_SEARCH_TOOL

    def test_real_search_schema_extracts_parameters(self):
        self.assertEqual(al.tool_parameters_schema({"schema": self.SEARCH_TOOL}).get("required"),
                         ["query"])

    def test_real_search_valid_args_pass(self):
        self.assertIsNone(al.validate_tool_arguments({"schema": self.SEARCH_TOOL},
                                                     {"query": "免赔额", "category": "医疗险"}))

    def test_real_search_wrong_field_name_blocked(self):
        """历史真实场景(q 而非 query)在真实 schema 下必须被拦。"""
        msg = al.validate_tool_arguments({"schema": self.SEARCH_TOOL}, {"q": "免赔额"})
        self.assertIsNotNone(msg)
        self.assertIn("query", msg)

    def test_real_history_wrong_type_blocked(self):
        msg = al.validate_tool_arguments({"schema": self.HISTORY_SEARCH_TOOL},
                                         {"query": "前面说的", "past_rounds": "三"})
        self.assertIsNotNone(msg)
        self.assertIn("past_rounds", msg)
        self.assertIn("integer", msg)


if __name__ == "__main__":
    unittest.main()
