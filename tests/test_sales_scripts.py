# -*- coding: utf-8 -*-
"""话术库隔离与双工具(D91)测试:
① search_knowledge 的 doc_type 硬过滤(排除式/只留式,dense 路径);
② hybrid(BM25)路径同样过滤(话术块不得从融合路混入条款检索,反之亦然);
③ 业务层:build_tools 注册 search_sales_scripts;条款 handler 排除话术、话术 handler 只留话术;
④ SYSTEM 含话术三条引导;retrieve_tool_names 纳入新工具;话术空命中诚实降级文案。
"""
from __future__ import annotations

import unittest

from app.retrieval.hybrid import BM25Index
from app.retrieval.search_tool import search_knowledge


class FakeEmbedder:
    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]


# 语料:2 条款块(policy)+ 2 话术块(sales_script)
CORPUS = [
    {"chunk_id": "p1", "content": "免赔额 0元 或 5000元 可选 一般医疗 300万",
     "meta": {"doc_id": "产品条款", "version": "v1", "section": "s", "source": "src", "doc_type": "policy_document"}},
    {"chunk_id": "p2", "content": "等待期 30日 意外不受等待期限制",
     "meta": {"doc_id": "产品条款", "version": "v1", "section": "s", "source": "src", "doc_type": "policy_document"}},
    {"chunk_id": "q1", "content": "客户问买计划一还是计划二 先问需求预算年龄体况再给建议",
     "meta": {"doc_id": "产品话术", "version": "v1", "section": "s", "source": "src", "doc_type": "sales_script"}},
    {"chunk_id": "q2", "content": "客户说太贵了 把账拆小 一天一杯奶茶钱换300万兜底",
     "meta": {"doc_id": "通用话术", "version": "v1", "section": "s", "source": "src", "doc_type": "sales_script"}},
]


class MixedStore:
    """稠密检索返回全部 4 块(条款+话术混在一起,模拟共享 collection)。"""
    def search(self, vec, top_k):
        return [{"chunk_id": c["chunk_id"], "content": c["content"], "score": 0.9 - i * 0.1,
                 "meta": c["meta"]} for i, c in enumerate(CORPUS)][:top_k]


class OnlyScriptStore:
    """稠密只回话术块(极端:条款块全部召回失败)。"""
    def search(self, vec, top_k):
        return [{"chunk_id": c["chunk_id"], "content": c["content"], "score": 0.9,
                 "meta": c["meta"]} for c in CORPUS if c["meta"]["doc_type"] == "sales_script"][:top_k]


class DocTypeFilterTest(unittest.TestCase):
    def test_exclude_sales_script_from_knowledge(self):
        """条款检索排除话术:结果只含 policy 块。"""
        res = search_knowledge(FakeEmbedder(), MixedStore(), "免赔额", top_k=4,
                               exclude_doc_types={"sales_script"})
        self.assertTrue(res)
        self.assertTrue(all(c["doc_type"] != "sales_script" for c in res))
        self.assertEqual({c["chunk_id"] for c in res}, {"p1", "p2"})

    def test_include_only_sales_script(self):
        """话术检索只留话术:结果只含 sales_script 块。"""
        res = search_knowledge(FakeEmbedder(), MixedStore(), "怎么回复", top_k=4,
                               include_doc_types={"sales_script"})
        self.assertTrue(res)
        self.assertTrue(all(c["doc_type"] == "sales_script" for c in res))
        self.assertEqual({c["chunk_id"] for c in res}, {"q1", "q2"})

    def test_no_filter_returns_all(self):
        """不传过滤参数:行为与旧版一致(全部返回)。"""
        res = search_knowledge(FakeEmbedder(), MixedStore(), "免赔额", top_k=4)
        self.assertEqual(len(res), 4)

    def test_filter_empty_result_when_no_match(self):
        """排除后为空(条款库只有话术)→ 返回空列表(诚实,不回退)。"""
        res = search_knowledge(FakeEmbedder(), OnlyScriptStore(), "免赔额", top_k=4,
                               exclude_doc_types={"sales_script"})
        self.assertEqual(res, [])

    def test_hybrid_path_filters_both_ways(self):
        """hybrid(BM25)路径:排除式与只留式都生效,话术块不得从融合路混入。"""
        bm25 = BM25Index(CORPUS)
        res_k = search_knowledge(FakeEmbedder(), MixedStore(), "免赔额 计划一", top_k=4,
                                 hybrid=bm25, hybrid_weight=0.5,
                                 exclude_doc_types={"sales_script"})
        self.assertTrue(res_k)
        self.assertTrue(all(c["doc_type"] != "sales_script" for c in res_k))

        res_s = search_knowledge(FakeEmbedder(), MixedStore(), "免赔额 计划一", top_k=4,
                                 hybrid=bm25, hybrid_weight=0.5,
                                 include_doc_types={"sales_script"})
        self.assertTrue(res_s)
        self.assertTrue(all(c["doc_type"] == "sales_script" for c in res_s))


class _Cfg:
    """build_tools 最小配置(避开外部依赖)。"""
    top_k = 4
    top_k_reranker = 3
    reranking_engine = ""
    hybrid_bm25_weight = 0.0
    hybrid_fusion = "rrf"
    hybrid_rrf_k = 60


class SalesScriptToolTest(unittest.TestCase):
    """业务层接线:工具注册 + handler 过滤 + SYSTEM 引导 + retrieve_tool_names。"""

    def setUp(self):
        from app.businesses import insurance
        self.ins = insurance
        self.store = MixedStore()
        self.tools = insurance.build_tools(FakeEmbedder(), self.store, _Cfg(), store=None)

    def test_tool_registered(self):
        self.assertIn("search_knowledge", self.tools)
        self.assertIn("search_sales_scripts", self.tools)
        self.assertEqual(self.tools["search_sales_scripts"]["schema"]["function"]["name"],
                         "search_sales_scripts")

    def test_knowledge_handler_excludes_scripts(self):
        h = self.tools["search_knowledge"]["handler"]
        res = h({"query": "免赔额"})
        refs = res["reference"]
        self.assertTrue(refs)
        self.assertTrue(all(c["doc_type"] != "sales_script" for c in refs))

    def test_script_handler_only_scripts(self):
        h = self.tools["search_sales_scripts"]["handler"]
        res = h({"query": "计划一还是计划二怎么回复"})
        refs = res["reference"]
        self.assertTrue(refs)
        self.assertTrue(all(c["doc_type"] == "sales_script" for c in refs))
        self.assertIn("[1]", res["content"])   # 话术块同样带 [idx] 编号(溯源链路一致)

    def test_script_handler_empty_honest_fallback(self):
        """话术库空命中:返回诚实降级文案,不编造。"""
        h = self.tools["search_sales_scripts"]["handler"]
        res = h({"query": "任何词"})
        # OnlyScriptStore 只回话术;此处用排除式让话术也命中不了 → 模拟空库
        res2 = h({"query": "怎么回复"})
        self.assertTrue(res["content"])   # 有内容即链路通
        # 用一个没有话术的 store 构造真正空命中
        tools2 = self.ins.build_tools(FakeEmbedder(), _NoScriptStore(), _Cfg(), store=None)
        res3 = tools2["search_sales_scripts"]["handler"]({"query": "计划一还是计划二"})
        self.assertIn("暂无", res3["content"])
        self.assertEqual(res3["reference"], [])

    def test_system_contains_script_rules(self):
        self.assertIn("search_sales_scripts", self.ins.SYSTEM)
        self.assertIn("表达参考", self.ins.SYSTEM)
        self.assertIn("不要编造", self.ins.SYSTEM)

    def test_retrieve_tool_names_includes_script(self):
        b = self.ins.bundle(FakeEmbedder(), self.store, _Cfg(), store=None)
        self.assertEqual(b["retrieve_tool_names"], {"search_knowledge", "search_sales_scripts"})


class _NoScriptStore:
    """只回条款块(无任何话术)→ 话术检索必然空命中。"""
    def search(self, vec, top_k):
        return [{"chunk_id": c["chunk_id"], "content": c["content"], "score": 0.9,
                 "meta": c["meta"]} for c in CORPUS if c["meta"]["doc_type"] == "policy_document"][:top_k]


if __name__ == "__main__":
    unittest.main()
