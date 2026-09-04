# -*- coding: utf-8 -*-
"""在线 embedding(api 后端)测试:mock requests,不碰网络;含 key 复用/顺序/分批/重试。"""
from __future__ import annotations
import unittest
from types import SimpleNamespace
from unittest import mock

from app.retrieval.embedder import Embedder, build_embedder


def _resp(batch_len: int, status: int = 200):
    # data 乱序返回,验证按 index 排序
    data = [{"object": "embedding", "index": j, "embedding": [float(j)] * 4}
            for j in range(batch_len)]
    data.reverse()
    return SimpleNamespace(status_code=status, text="err",
                           json=lambda: {"object": "list", "model": "m", "data": data,
                                         "usage": {"prompt_tokens": 1, "total_tokens": 1}})


def _fake_post_factory(batch_len_by_call):
    calls = {"n": 0}

    def _post(url, headers=None, json=None, timeout=None):
        n = batch_len_by_call[calls["n"]]
        calls["n"] += 1
        return _resp(n)

    return _post


class EmbedderApiTest(unittest.TestCase):
    def setUp(self):
        self.e = Embedder(backend="api", api_key="k", api_model="m", batch_size=2)

    def test_batch_order_and_progress(self):
        # 2+2+1 = 5 条 → 3 次请求
        seq = [2, 2, 1]
        calls: list = []
        with mock.patch("requests.post", side_effect=_fake_post_factory(seq)) as p:
            out = self.e.embed(["x%d" % i for i in range(5)],
                               on_progress=lambda d, t: calls.append((d, t)))
        self.assertEqual(len(out), 5)
        # 每批内按 index 排序后顺序追加(批内 index 归零):批次 [0,1][0,1][0]
        self.assertEqual([v[0] for v in out], [0.0, 1.0, 0.0, 1.0, 0.0])
        self.assertEqual(p.call_count, 3)
        self.assertEqual(calls, [(2, 5), (4, 5), (5, 5)])

    def test_no_key_raises(self):
        e = Embedder(backend="api", api_key="", api_model="m")
        with self.assertRaises(RuntimeError):
            e.embed(["x"])

    def test_retry_then_success(self):
        e = Embedder(backend="api", api_key="k", api_model="m", batch_size=2)
        with mock.patch("app.retrieval.embedder.time.sleep") as slp:
            with mock.patch("requests.post", side_effect=[_resp(2, status=500), _resp(2)]) as p:
                out = e.embed(["a", "b"])
        self.assertEqual(len(out), 2)
        self.assertEqual(p.call_count, 2)
        slp.assert_called()

    def test_build_embedder_key_fallback(self):
        cfg = SimpleNamespace(
            embedding_model="m_path", embedding_device="cpu", embedding_batch_size=8,
            embedding_backend="api", embedding_api_url="", embedding_api_model="BAAI/bge-large-zh-v1.5",
            embedding_api_key="", embedding_api_timeout_seconds=30,
            reranking_external_api_key="sk-silicon",
        )
        e = build_embedder(cfg)
        self.assertEqual(e.backend, "api")
        self.assertEqual(e.api_key, "sk-silicon", "EMBEDDING_API_KEY 留空应复用 rerank 的 key")

    def test_build_embedder_local_default(self):
        cfg = SimpleNamespace(
            embedding_model="m_path", embedding_device="cpu", embedding_batch_size=8,
            embedding_backend="local", embedding_api_url="", embedding_api_model="",
            embedding_api_key="", embedding_api_timeout_seconds=30,
            reranking_external_api_key="",
        )
        # local 会加载模型 → 只验 backend 解析,不触模型(embed 不调用)
        with mock.patch("sentence_transformers.SentenceTransformer"):
            e = build_embedder(cfg)
        self.assertEqual(e.backend, "local")


if __name__ == "__main__":
    unittest.main()
