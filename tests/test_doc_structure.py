# -*- coding: utf-8 -*-
"""doc_structure:outline 构建 + knowledge_store 存取测试(不依赖书签)。"""
from __future__ import annotations
import os
import tempfile
import unittest

from app.retrieval.doc_structure import build_from_outline
from app.retrieval.knowledge_store import KnowledgeStore

SAMPLE = "第一部分 总则\n第一条 合同构成\n正文。\n（一）一般医疗\n指费用。\n第二条 合同成立\n正文。\n"


class DocStructureBuildTest(unittest.TestCase):
    def test_from_outline_full_levels_no_page(self):
        nodes = build_from_outline(SAMPLE)
        levels = [n["level"] for n in nodes]
        self.assertEqual(levels, [1, 2, 3, 2], "部分>条>(一)>条(弹栈),紧凑层级")
        self.assertTrue(all(n["page"] is None for n in nodes))
        self.assertEqual(nodes[0]["parent"], "")
        # 第二条 的 parent 应回弹到 第一部分
        art2 = [n for n in nodes if "第二条" in n["title"]][0]
        self.assertEqual(art2["parent"], "第一部分总则")

    def test_outline_detects_md_and_generic(self):
        nodes = build_from_outline("# 标题\n## 二级\n### 三级\n", doc_type="markdown")
        self.assertEqual([n["level"] for n in nodes], [1, 2, 3])
        nodes2 = build_from_outline("1. 背景\n1.1 目标\n正文。\n", doc_type="text")
        self.assertGreaterEqual(len(nodes2), 2)


class KnowledgeStoreStructureTest(unittest.TestCase):
    def setUp(self):
        self.db = os.path.join(tempfile.mkdtemp(), "kn.db")
        self.ks = KnowledgeStore(self.db)

    def tearDown(self):
        self.ks.close()

    def test_list_structure_attaches_chunk_ids(self):
        from app.api.services import kb_service
        chunks = [
            {"chunk_id": "d:0", "content": "x0", "meta": {"chunk_id": "d:0", "doc_id": "d", "section": "第一部分 总则 > 第一条 合同构成", "version": "v1", "doc_type": "policy_pdf", "source": "s", "title": "第一条 合同构成", "product_category": ""}},
            {"chunk_id": "d:1", "content": "x1", "meta": {"chunk_id": "d:1", "doc_id": "d", "section": "第一部分 总则 > 第一条 合同构成", "version": "v1", "doc_type": "policy_pdf", "source": "s", "title": "1.住院", "product_category": ""}},
            {"chunk_id": "d:2", "content": "x2", "meta": {"chunk_id": "d:2", "doc_id": "d", "section": "第一部分 总则 > 第二条 合同成立", "version": "v1", "doc_type": "policy_pdf", "source": "s", "title": "第二条 合同成立", "product_category": ""}},
        ]
        self.ks.upsert_chunks(chunks)
        self.ks.set_doc_structure("d", build_from_outline("第一部分 总则\n第一条 合同构成\n正文。\n第二条 合同成立\n正文。\n"))
        nodes = kb_service.list_structure(self.ks, "d")
        first = [n for n in nodes if "第一条" in n["title"]][0]
        second = [n for n in nodes if "第二条" in n["title"]][0]
        self.assertEqual(first["chunk_ids"], [0, 1])
        self.assertEqual(second["chunk_ids"], [2])

    def test_set_get_delete_structure(self):
        nodes = build_from_outline(SAMPLE)
        self.assertGreater(self.ks.set_doc_structure("d1", nodes), 0)
        got = self.ks.get_doc_structure("d1")
        self.assertEqual(len(got), len(nodes))
        self.assertEqual(got[0]["title"].strip(), "第一部分 总则")
        # 整树替换
        self.ks.set_doc_structure("d1", build_from_outline("新部分\n" * 1))
        # 删除文档连带清结构
        self.assertEqual(self.ks.delete_document("d1"), 0)
        self.assertEqual(self.ks.get_doc_structure("d1"), [])


if __name__ == "__main__":
    unittest.main()
