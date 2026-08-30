# -*- coding: utf-8 -*-
"""app/utils/text.py 的 prune_tool_content 单测:保头尾+标记、严格更短、不误切代理对。"""
from __future__ import annotations

import unittest

from app.utils.text import PRUNE_MARKER, prune_tool_content


class TestPruneToolContent(unittest.TestCase):
    def test_short_returns_none(self):
        self.assertIsNone(prune_tool_content("short", 100, 40, 10))

    def test_long_prunes_head_tail_marker(self):
        content = "A" * 12000
        got = prune_tool_content(content, 8000, 4000, 1000)
        self.assertIsNotNone(got)
        self.assertLess(len(got), len(content))          # 严格更短
        self.assertTrue(got.startswith("A" * 4000))       # 保头
        self.assertTrue(got.endswith("A" * 1000))         # 保尾
        self.assertIn(PRUNE_MARKER, got)                  # 中间标记

    def test_head_marker_tail_exceeds_threshold_returns_none(self):
        # threshold < head+marker+tail → 无法有效剪 → None(防御:不硬切)
        got = prune_tool_content("A" * 9000, 6000, 5000, 3000)
        self.assertIsNone(got)

    def test_does_not_split_surrogate_pairs(self):
        s = "x" * 100 + "😀" * 50 + "y" * 100        # 250 个码点
        got = prune_tool_content(s, 200, 80, 80)
        self.assertIsNotNone(got)
        self.assertFalse(any(0xD800 <= ord(ch) <= 0xDFFF for ch in got))


if __name__ == "__main__":
    unittest.main()
