# -*- coding: utf-8 -*-
"""D93:检索错误分类 —— 客户端参数错误(代码 bug)与上游服务宕机必须分开。

- 客户端确定性错误(pydantic.ValidationError / ValueError / Qdrant 4xx):
  立即抛 RetrievalClientError,**不重试、不进冷却期**(重试必同样失败,冷却会误诊为"服务宕机")。
- 上游瞬态故障(连接超时/5xx 等):重试,耗尽/冷却期抛 RetrievalUnavailable(服务宕机语义)。
回归:之前两类都被 _retry_call 一网打尽 → 重试 + 冷却 + 误标 retrieval_unavailable,
把一个代码 bug 放大成"持续不可用"且污染"向量库挂"指标。
"""
from __future__ import annotations

import unittest
from unittest import mock

import pydantic
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import MatchExcept

from app.retrieval.errors import RetrievalUnavailable, RetrievalClientError
from app.retrieval.qdrant_store import QdrantStore, _is_client_error


def _store() -> QdrantStore:
    # QdrantClient 构造会尝试连网,用 mock 隔离;_retry_call 本身不依赖真实 client。
    with mock.patch("app.retrieval.qdrant_store.QdrantClient"):
        return QdrantStore("http://x", "col", 1024, retry_max_tries=2)


class IsClientErrorTest(unittest.TestCase):
    def test_validation_error_is_client_error(self):
        # 复现生产 bug:MatchExcept(value=False) 在 1.18.0 下抛 ValidationError
        with self.assertRaises(pydantic.ValidationError):
            MatchExcept(value=False)
        try:
            MatchExcept(value=False)
        except Exception as e:
            self.assertTrue(_is_client_error(e))

    def test_value_error_is_client_error(self):
        self.assertTrue(_is_client_error(ValueError("bad")))

    def test_qdrant_4xx_is_client_error(self):
        class Fake4xx(UnexpectedResponse):
            def __init__(self):
                self.status_code = 400
        self.assertTrue(_is_client_error(Fake4xx()))

    def test_5xx_is_not_client_error(self):
        class Fake5xx(UnexpectedResponse):
            def __init__(self):
                self.status_code = 503
        self.assertFalse(_is_client_error(Fake5xx()))

    def test_generic_exception_is_not_client_error(self):
        self.assertFalse(_is_client_error(ConnectionError("down")))


class RetryCallClassificationTest(unittest.TestCase):
    def test_client_validation_error_is_retrieval_client_error_and_no_cooldown(self):
        s = _store()

        def fn():
            MatchExcept(value=False)  # 真实生产路径:确定性客户端错误

        with self.assertRaises(RetrievalClientError):
            s._retry_call("search", fn)
        self.assertFalse(s.is_down(), "客户端错误不得进入冷却期(否则误诊为服务宕机)")

    def test_transient_error_retries_then_cools_down_as_unavailable(self):
        s = _store()

        def fn():
            raise ConnectionError("qdrant down")

        with self.assertRaises(RetrievalUnavailable):
            s._retry_call("search", fn)
        self.assertTrue(s.is_down(), "上游瞬态故障应进入冷却期(服务宕机语义)")

    def test_cooldown_fast_fails_as_unavailable(self):
        s = _store()
        try:
            s._retry_call("search", lambda: (_ for _ in ()).throw(ConnectionError("down")))
        except RetrievalUnavailable:
            pass
        self.assertTrue(s.is_down())
        # 冷却期内再调用应直接判不可用,不再慢重试
        with self.assertRaises(RetrievalUnavailable):
            s._retry_call("search", lambda: ["ok"])


if __name__ == "__main__":
    unittest.main()
