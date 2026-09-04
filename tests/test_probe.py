# -*- coding: utf-8 -*-
"""probe 结构探测纯函数测试。"""
from __future__ import annotations
import unittest

from app.retrieval.ingest.probe import probe_text, strip_md_prefix, chunk_probe, build_outline

SAMPLE = """第一部分 总则
第一条 合同构成
本保险合同由保险条款组成。
（一）一般医疗费用
1.住院医疗费用
指住院期间的费用。
第二条 合同成立
第二部分 保险期间
第六条 保险期间
本合同保险期间为一年。
====== 第 2 页 ======
"""


class ProbeTextTest(unittest.TestCase):
    def test_counts(self):
        d = probe_text(SAMPLE)
        self.assertEqual(d["part"], 2)          # 第一部分 / 第二部分
        self.assertEqual(d["article"], 3)       # 三条
        self.assertEqual(d["subitem"], 1)       # （一）
        self.assertEqual(d["numbered"], 1)      # 1.
        self.assertEqual(d["page_noise"], 1)
        self.assertGreater(d["structural_ratio"], 0)

    def test_md_head_levels(self):
        d = probe_text("# 主标题\n## 副标题\n### 三级\n正文\n")
        self.assertEqual(d["md_heads"]["1"], 1)
        self.assertEqual(d["md_heads"]["2"], 1)
        self.assertEqual(d["md_heads"]["3"], 1)


class StripMdPrefixTest(unittest.TestCase):
    def test_strip(self):
        out = strip_md_prefix("## 第一部分 总则\n### 第一条 合同构成\n正文\n")
        self.assertEqual(out.splitlines()[0], "第一部分 总则")
        self.assertEqual(out.splitlines()[1], "第一条 合同构成")
        self.assertEqual(out.splitlines()[2], "正文")


class ChunkProbeTest(unittest.TestCase):
    def test_section_fill(self):
        d = chunk_probe(SAMPLE, doc_type="policy_pdf", chunk_size=1000)
        self.assertGreater(d["chunks"], 0)
        self.assertEqual(d["with_section"], d["chunks"], "结构文本应全带 section")
        self.assertGreaterEqual(d["avg_path_len"], 1)

    def test_strip_md_flag_for_mineru(self):
        # D71:chunk_structured 内部剥行首 #(非 md 键先剥再匹配),因此即使 strip_md=False 也能识别层级;
        # 之前 # 前缀会破坏编号 regex 匹配导致层级丢失(目录↔chunk 失联)。现在两者都应识别。
        md = "## 第一部分 总则\n## 第一条 合同构成\n本款由条款组成。\n"
        raw = chunk_probe(md, doc_type="policy_pdf", chunk_size=1000, strip_md=False)
        stripped = chunk_probe(md, doc_type="policy_pdf", chunk_size=1000, strip_md=True)
        self.assertGreater(raw["with_section"], 0, "chunk_structured 内部剥 # 后应识别层级(不再漏)")
        self.assertGreater(stripped["with_section"], 0, "脱 # 前缀后可识别层级")


class OutlineTest(unittest.TestCase):
    def test_policy_tree_depth_and_pop(self):
        text = "## 第一部分 总则\n## 第一条 合同构成\n正文\n## （一）一般医疗\n## 1.住院\n内容\n## 第二条 合同成立\n"
        out = build_outline(text, "policy_pdf")
        levels = [n["level"] for n in out]
        self.assertEqual(levels, [1, 2, 3, 4, 2], "部分>条>(一)>1. 后回到条级(弹栈)")
        self.assertEqual(out[1]["title"], "第一条 合同构成")

    def test_markdown_heading_levels(self):
        out = build_outline("# A\n## B\n### C\n", "markdown")
        self.assertEqual([n["level"] for n in out], [1, 2, 3])

    def test_plain_text_empty(self):
        self.assertEqual(build_outline("纯文本没有标题\n第二行\n", "policy_pdf"), [])


if __name__ == "__main__":
    unittest.main()
