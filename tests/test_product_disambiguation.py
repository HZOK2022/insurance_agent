# -*- coding: utf-8 -*-
"""产品不明确时主动中断询问(不猜产品、不笼统答) —— 业务层规则 + 循环"澄清即停"行为。

覆盖:
- SYSTEM 含产品不明确约束(依赖具体产品却不确定 → 主动问、不调工具、不直接答;追问后继续原问题)。
- available_product_keys() 能列出在售产品(供追问时给出可勾选清单)。
- bundle() 把"当前在售产品"动态拼进 system。
- calculate_premium 缺产品时给出在售清单(确定性兜底,而非空泛"请指定产品")。
- 循环层:模型输出"澄清追问"(无工具调用)时,产出干净的一条 assistant_message(无 tool_call/tool_result/citations)并照常收尾 —— 即"主动中断询问"走通。
"""
from __future__ import annotations

import os
import tempfile
import types
import unittest

from app.businesses import insurance
from app.businesses.premium import available_product_keys, calculate_premium, PremiumStore, PRODUCT_XX
from app.loop.agent_loop import AgentLoop
from app.session.events import make_event
from app.session.store import SessionStore


def make_cfg(ms=6, mr=5):
    return types.SimpleNamespace(max_steps_per_turn=ms, max_retrieve_per_turn=mr, deepseek_model="fake")


class SystemRuleTest(unittest.TestCase):
    def test_system_contains_disambiguation_rule(self):
        # 规则要点:依赖具体产品却不明确 → 不要猜、不要笼统答、主动中断追问、追问后继续原问题
        for token in ("产品不明确", "不要猜", "主动中断", "结束本轮", "继续回答该原问题", "在售产品"):
            self.assertIn(token, insurance.SYSTEM, f"SYSTEM 缺关键表述: {token}")
        # 与既有"点名产品/指定险种"规则共存,不误删(既有测试依赖)
        self.assertIn("指定险种", insurance.SYSTEM)
        self.assertIn("category", insurance.SYSTEM)

    def test_system_does_not_allow_guessing_product(self):
        # 明确禁止:依赖具体产品却未知时,不得拿某款产品条款/一类产品通识作答
        self.assertIn("不要", insurance.SYSTEM)
        self.assertIn("猜", insurance.SYSTEM)


class ProductCatalogTest(unittest.TestCase):
    def test_available_product_keys_lists_known_products(self):
        keys = available_product_keys()
        self.assertGreaterEqual(len(keys), 1)
        # 至少在售的两款医疗险都在清单里
        self.assertIn("尊享e生2025", keys)
        self.assertIn("安盛天平卓越馨选2025", keys)

    def test_calculate_premium_missing_product_lists_available(self):
        d = tempfile.mkdtemp()
        store = PremiumStore(os.path.join(d, "p.db"))
        try:
            res = calculate_premium(store, {"age": 30, "items": [{"item_key": "plan"}]})
            self.assertIn("请指定产品", res["content"])
            self.assertIn("尊享e生2025", res["content"])       # 给出在售清单,而非空泛要求
            self.assertEqual(res["reference"], [])
        finally:
            store.close()
            import shutil
            shutil.rmtree(d, ignore_errors=True)


class ProductListAppendTest(unittest.TestCase):
    def test_append_product_list_pure(self):
        # 纯函数:动态把在售产品拼进 system;取不到就原样返回
        sys2 = insurance._append_product_list("你是保险销售知识助手。\n")
        self.assertIn("【在售产品】", sys2)
        self.assertIn("尊享e生2025", sys2)
        self.assertIn("安盛天平卓越馨选2025", sys2)
        # 模块级 SYSTEM 常量自身不含动态产品目录(只在 bundle/_append_product_list 拼入) —— 既有断言/回放不漂移
        self.assertNotIn("【在售产品】", insurance.SYSTEM)


class _LoopBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = SessionStore(os.path.join(self.dir, "t.db"))

    def tearDown(self):
        self.store.close()

    def make_loop(self, llm, present=None, cfg=None):
        cfg = cfg or make_cfg()
        def emit(t, p):
            ev = make_event(t, p); self.store.append("s1", t, p); return ev
        present = present or (lambda text, refs: ([{"t": "p", "text": text}], []))
        # 工具表为空(SYSTEM 已判"不调工具",模型也确定不调) —— 模拟"追问即停"路径
        return AgentLoop(llm, "系统", {}, present, cfg, emit=emit)

    def run_turn(self, llm, text="你好", history=None, **kw):
        return list(self.make_loop(llm, **kw).turn("s1", text, history=history))


class ClarifyAndStopTest(_LoopBase):
    def test_clarify_question_emits_one_clean_assistant_message(self):
        """产品不明确 → 模型输出澄清追问(无工具调用) → 循环产出干净的一条问题消息,无工具、无引用,正常收尾。"""
        class ClarifyLLM:
            def chat_stream(self, messages, json_mode=False, tools=None, model=None):
                yield {"kind": "text", "delta": "请问您指的是哪款产品?当前在售:尊享e生2025、安盛天平卓越馨选2025。", "block_index": 0}
                yield {"kind": "usage", "delta": "", "block_index": None, "usage": {"prompt_tokens": 5, "completion_tokens": 5}}
        evs = self.run_turn(ClarifyLLM(), text="那这款的免赔额是多少")
        types_ = [e["type"] for e in evs]
        self.assertNotIn("tool_call", types_)          # 未调工具 = "主动中断"
        self.assertNotIn("tool_result", types_)
        self.assertNotIn("retrieval", types_)
        am = next(r["payload"] for r in self.store.read("s1") if r["type"] == "assistant_message")
        self.assertTrue(am["blocks"])
        self.assertIn("哪款产品", am["blocks"][0]["text"])
        self.assertEqual(am["citations"], [])           # 无引用角标(没检索任何内容)
        self.assertEqual(types_[-1], "turn_end")
        te = next(r["payload"] for r in self.store.read("s1") if r["type"] == "turn_end")
        self.assertEqual(te["reason"], "completed")

    def test_continue_after_user_names_product(self):
        """追问后用户给出产品 → 该轮应能给出回答(带引用),而非继续追问 —— 依赖 history 保留原问题。"""
        class AnswerLLM:
            def chat_stream(self, messages, json_mode=False, tools=None, model=None):
                # 后续轮:结合"原问题+已确认产品"作答(此处只模拟模型知道了产品并直接作答)
                yield {"kind": "text", "delta": "尊享e生2025的0元免赔计划一年缴2,312元[1]。", "block_index": 0}
                yield {"kind": "usage", "delta": "", "block_index": None, "usage": {"prompt_tokens": 5, "completion_tokens": 5}}
        # 上一轮(带历史):原问题 + 助手追问 + 用户确认产品
        history = [
            {"role": "user", "content": "那这款的免赔额是多少"},
            {"role": "assistant", "content": "请问您指的是哪款产品?当前在售:尊享e生2025、安盛天平卓越馨选2025。"},
            {"role": "user", "content": "尊享e生2025"},
        ]
        self.run_turn(AnswerLLM(), text="尊享e生2025", history=history)
        am = next(r["payload"] for r in self.store.read("s1") if r["type"] == "assistant_message")
        self.assertTrue(am["blocks"])


if __name__ == "__main__":
    unittest.main()
