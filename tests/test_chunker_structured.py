# -*- coding: utf-8 -*-
"""结构化分块(无 MinerU,自写编号 regex)。"""
from __future__ import annotations
import unittest
from app.retrieval.chunker import chunk_structured, chunk_documents

POLICY = """第一部分 总则
第一条 合同构成
本保险合同由保险条款、投保单组成。
第二条 合同的成立
投保人提出保险申请,经保险人同意承保。
第二部分 保障内容
第六条 保险责任
本合同的保险责任包括七项。
（一）一般医疗及外购药械费用医疗保险金
在保险期间内,被保险人在医院接受治疗的。
"""

MD = """# 投保规则
## 首次投保年龄
出生满30天至70周岁可投保。
## 续保
期内可申请续保。
# 保障内容
## 保险责任
包括一般医疗。
"""


class StructuredChunkTest(unittest.TestCase):
    def test_policy_structure_and_prefix(self):
        items = chunk_structured(POLICY, "policy_pdf", chunk_size=1000)
        self.assertTrue(items)
        # 层级路径(section)里出现"部分 > 条"的内容单元
        art = [it for it in items if "第一条 合同构成" in it["section"]]
        self.assertTrue(art, "应切出'第一条'单元")
        a = art[0]
        self.assertIn("第一条 合同构成", a["content"])
        self.assertIn("本保险合同由保险条款", a["content"])          # 正文跟随所属条
        self.assertIn("第一部分 总则", a["section"])                 # 层级标识:部分>条
        # 第二条、第六条各自成不同层级单元的正文,不互相混入
        self.assertFalse(any("投保人提出保险申请" in it["content"] and "本合同的保险责任包括" in it["content"]
                             for it in items))

    def test_overlong_descends_with_prefix(self):
        long_text = "第一部分 总则\n第一条 合同构成\n" + "本保险合同条款内容说明。" * 200
        items = chunk_structured(long_text, "policy_pdf", chunk_size=120)
        self.assertTrue(items)
        art = [it for it in items if "第一条 合同构成" in it["section"]]
        self.assertTrue(art)
        # 超长→降级成多块,且每块保留层级前缀
        self.assertGreater(len(art), 1)
        self.assertTrue(all("第一条 合同构成" in it["content"] for it in art))

    def test_md_headings(self):
        items = chunk_structured(MD, "markdown", chunk_size=1000)
        self.assertTrue(any("首次投保年龄" in it["section"] for it in items))
        self.assertTrue(any("保障内容" in it["section"] and "保险责任" in it["section"] for it in items))

    def test_doc_type_route_and_fallback(self):
        policy_docs = [{"text": POLICY, "meta": {"doc_type": "policy_docx", "chunk_id": "c"}}]
        out_policy = chunk_documents(policy_docs, chunk_size=1000, text_splitter="structured")
        self.assertTrue(any("第一部分 总则 >" in it["meta"]["section"] for it in out_policy))
        text_docs = [{"text": "纯文本" * 50, "meta": {"doc_type": "text", "chunk_id": "t"}}]
        out_text = chunk_documents(text_docs, chunk_size=50, overlap=0, text_splitter="structured")
        self.assertTrue(out_text)
        self.assertTrue(all(it["meta"]["section"] in ("", None) for it in out_text))


if __name__ == "__main__":
    unittest.main()
