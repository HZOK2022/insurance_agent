# -*- coding: utf-8 -*-
"""阶段E(重试/退避)测试:patching requests.post,验证对 429/5xx/超时重试、永久错误不重试、超上限放弃、on_retry 记录。"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from app.llm.client import LLMClient


class FakeResp:
    def __init__(self, status, lines=None):
        self.status_code = status
        self._lines = lines or []
        self.text = "err body" if status != 200 else ""
    def json(self):
        return {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    def close(self):
        pass
    def iter_lines(self, decode_unicode=True):
        for line in self._lines:
            yield line
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def stream_resp(status, chunks):
    # chunks: list of dict -> "data: {...}" lines, with [DONE]
    lines = ["data: " + json.dumps(c, ensure_ascii=False) for c in chunks] + ["data: [DONE]"]
    return FakeResp(status, lines)


def make_client(**kw):
    return LLMClient("k", max_retries=kw.pop("max_retries", 3), max_tokens=16,
                     retry_base_delay=0.001, retry_max_delay=0.01, **kw)


class RetryTest(unittest.TestCase):
    def _retries(self):
        return []

    def test_chat_retries_on_429_then_succeeds(self):
        seq = iter([FakeResp(429), FakeResp(429), FakeResp(200)])
        with mock.patch("app.llm.client.requests.post", side_effect=lambda *a, **k: next(seq)):
            c = make_client(max_retries=3); retries = []
            content, usage = c.chat([{"role": "user", "content": "hi"}], on_retry=lambda a: retries.append(a))
            self.assertEqual(content, "ok")
            self.assertEqual(len(retries), 2)          # 429 → 429 → 200,重试2次
            self.assertEqual(retries[0]["attempt"], 1)
            self.assertEqual(retries[1]["attempt"], 2)

    def test_chat_stream_retries_on_5xx_then_succeeds(self):
        seq = iter([FakeResp(503), stream_resp(200, [{"choices": [{"delta": {"content": "好"}}]}])])
        with mock.patch("app.llm.client.requests.post", side_effect=lambda *a, **k: next(seq)):
            c = make_client(); got = []
            for ch in c.chat_stream([{"role": "user", "content": "hi"}]):
                if ch.get("kind") == "text":
                    got.append(ch["delta"])
            self.assertEqual("".join(got), "好")

    def test_permanent_400_no_retry(self):
        seq = iter([FakeResp(400)] * 3)
        with mock.patch("app.llm.client.requests.post", side_effect=lambda *a, **k: next(seq)):
            c = make_client(max_retries=3); retries = []
            with self.assertRaises(RuntimeError):
                c.chat([{"role": "user", "content": "hi"}], on_retry=lambda a: retries.append(a))
            self.assertEqual(len(retries), 0)          # 400 永久错误,不重试

    def test_gives_up_after_max_retries(self):
        seq = iter([FakeResp(429)] * 10)
        with mock.patch("app.llm.client.requests.post", side_effect=lambda *a, **k: next(seq)):
            c = make_client(max_retries=3); retries = []
            with self.assertRaises(RuntimeError):
                c.chat([{"role": "user", "content": "hi"}], on_retry=lambda a: retries.append(a))
            self.assertEqual(len(retries), 3)          # 最多重试 3 次 → 放弃


if __name__ == "__main__":
    unittest.main()
