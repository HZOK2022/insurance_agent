# -*- coding: utf-8 -*-
"""回放测试基建(录制→回放对拍):改 prompt/条款/工具 schema 时无 key 回归。

断言:录制一次运行 → 存成 JSONL → 用 ReplayLLM 重放同一请求、返回同一响应 → agent 输出一致。
若重放时请求不一致(如 prompt 变了),ReplayLLM 会抛错 => 对拍失败(有意为之)。
"""
from __future__ import annotations

import os
import tempfile
import types
import unittest

from app.loop.agent_loop import AgentLoop
from app.session.events import make_event
from tests.replay.recorder import Recorder
from tests.replay.replayer import ReplayLLM


def make_cfg(**kw):
    d = dict(max_steps_per_turn=6, max_retrieve_per_turn=5, deepseek_model="fake")
    d.update(kw)
    return types.SimpleNamespace(**d)


class AnswerLLM:
    def chat_stream(self, messages, json_mode=False, tools=None, model=None):
        yield {"kind": "text", "delta": "答", "block_index": 0}
        yield {"kind": "usage", "delta": "", "block_index": None, "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


def run(llm, cfg=None):
    cfg = cfg or make_cfg()
    emits = []
    def emit(t, p): ev = make_event(t, p); emits.append(ev); return ev
    loop = AgentLoop(llm, "系统", {}, (lambda t, r: ([{"t": "p", "text": t}], [])), cfg, emit=emit)
    return list(loop.turn("s1", "你好")), emits


class ReplayTest(unittest.TestCase):
    def test_record_then_replay_same_answer_and_request_header(self):
        # 1) 录制
        rec = Recorder(AnswerLLM())
        evs1, _ = run(rec)
        am1 = next(e["payload"] for e in evs1 if e["type"] == "assistant_message")
        # request_header 快照事件
        rh = next(e["payload"] for e in evs1 if e["type"] == "request_header")
        self.assertEqual(rh["system_len"], len("系统"))
        self.assertEqual(rh["history_len"], 0)   # 单轮无历史
        self.assertGreaterEqual(rh["window"], 0)
        # 2) 存 / 载(临时 JSONL)
        tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False); tmp.close()
        rec.save(tmp.name)
        r2 = Recorder(None); r2.load(tmp.name)
        os.unlink(tmp.name)
        self.assertEqual(len(r2.records), len(rec.records))
        # 3) 回放:同一请求应得到同一响应 → assistant_message 一致
        evs2, _ = run(ReplayLLM(r2.records))
        am2 = next(e["payload"] for e in evs2 if e["type"] == "assistant_message")
        self.assertEqual(am1, am2)

    def test_replay_detects_request_change(self):
        # 改了系统提示词 => 请求(messages)不同 => ReplayLLM 应抛错
        rec = Recorder(AnswerLLM()); run(rec)
        emits = []
        def emit(t, p): ev = make_event(t, p); emits.append(ev); return ev
        loop = AgentLoop(ReplayLLM(rec.records), "系统改了", {}, (lambda t, r: ([{"t": "p", "text": t}], [])), make_cfg(), emit=emit)
        evs = list(loop.turn("s1", "你好"))
        # ReplayLLM 检测到请求不一致 → 抛 AssertionError → 循环 try/except 捕获,记为 reason=error(即"对拍失败")
        te = next(e["payload"] for e in evs if e["type"] == "turn_end")
        self.assertEqual(te["reason"], "error")


if __name__ == "__main__":
    unittest.main()
