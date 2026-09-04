# -*- coding: utf-8 -*-
"""embedder 分批进度回调测试(假模型,不加载 sentence-transformers)。"""
from __future__ import annotations
import unittest

from app.retrieval.embedder import Embedder


class _Vecs:
    def __init__(self, n: int):
        self.n = n

    def tolist(self):
        return [[0.1] * 4 for _ in range(self.n)]


class _FakeModel:
    def encode(self, batch, batch_size=None, normalize_embeddings=None):
        return _Vecs(len(batch))


class EmbedProgressTest(unittest.TestCase):
    def setUp(self):
        self.e = Embedder.__new__(Embedder)
        self.e.model = _FakeModel()
        self.e.batch_size = 2

    def test_batch_callbacks(self):
        calls: list = []
        out = self.e.embed(["x"] * 5, on_progress=lambda d, t: calls.append((d, t)))
        self.assertEqual(len(out), 5)
        self.assertEqual(calls, [(2, 5), (4, 5), (5, 5)], "每批结束回调一次(done,total)")

    def test_empty_no_call(self):
        calls: list = []
        out = self.e.embed([], on_progress=lambda d, t: calls.append((d, t)))
        self.assertEqual(out, [])
        self.assertEqual(calls, [])

    def test_no_progress_still_works(self):
        out = self.e.embed(["a", "b", "c"])
        self.assertEqual(len(out), 3)


if __name__ == "__main__":
    unittest.main()
