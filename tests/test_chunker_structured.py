# -*- coding: utf-8 -*-
"""结构化分块(无 MinerU,自写编号 regex)。"""
from __future__ import annotations
import unittest
from app.retrieval.chunker import chunk_structured, chunk_documents, chunk_by_paragraphs

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

    def test_md_min_heading_level_default_all(self):
        # 默认(min_heading_level=6):各 # 标题节独立成块;**单行容器 H1 例外**(D93 治本:
        # # 投保规则/# 保障内容 无直接正文、只有 H2 子节 → 不立零信息块,经子块前缀保留)。
        items = chunk_structured(MD, "markdown", chunk_size=1000)
        sections = [it["section"] for it in items]
        self.assertNotIn("投保规则", sections)   # 单行容器 H1 不再独立成块
        self.assertNotIn("保障内容", sections)
        self.assertIn("投保规则 > 首次投保年龄", sections)
        self.assertIn("投保规则 > 续保", sections)
        self.assertIn("保障内容 > 保险责任", sections)

    def test_md_min_heading_level_2_merges_deep(self):
        # min_heading_level=2:只 H1/H2 成块,H3+ 并入父块(section 仍是父链,标题文本保留在 content)
        MD3 = "# A\n## A1\n### A1a\n正文甲。\n### A1b\n正文乙。\n## A2\n正文。"
        items = chunk_structured(MD3, "markdown", chunk_size=1000, min_heading_level=2)
        sections = [it["section"] for it in items]
        # A1 成块且包含其下 H3(A1a/A1b) 内容
        a1 = [it for it in items if it["section"] == "A > A1"][0]
        self.assertIn("A1a", a1["content"])
        self.assertIn("A1b", a1["content"])
        self.assertIn("正文甲。", a1["content"])
        # H3 不再单独成块
        self.assertFalse(any(s == "A > A1 > A1a" for s in sections))
        # A2 成块且含正文
        a2 = [it for it in items if it["section"] == "A > A2"][0]
        self.assertIn("正文。", a2["content"])

    def test_txt_not_affected_by_min_heading_level(self):
        # min_heading_level 仅对 md(pattern_key=md)生效;txt/policy 不受影响,仍按条款编号切
        items1 = chunk_structured(POLICY, "policy_pdf", chunk_size=1000, min_heading_level=6)
        items3 = chunk_structured(POLICY, "policy_pdf", chunk_size=1000, min_heading_level=2)
        self.assertEqual(
            [it["section"] for it in items1],
            [it["section"] for it in items3],
            "policy/policy 不应受 min_heading_level 影响")

    def test_md_front_matter_via_build_docs(self):
        import os, tempfile
        from app.retrieval.ingest.reader import build_docs
        md = "---\ncategory: 医疗险\nversion: v2025\n---\n# 产品\n正文。"
        p = os.path.join(tempfile.mkdtemp(), "P.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write(md)
        docs = build_docs(p, "")
        meta = docs[0]["meta"]
        self.assertEqual(meta["product_category"], "医疗险")
        self.assertEqual(meta["version"], "v2025")
        # front-matter 被剥离,不污染正文
        self.assertNotIn("category", docs[0]["text"])
        self.assertIn("产品", docs[0]["text"])

    def test_md_no_front_matter_defaults(self):
        import os, tempfile
        from app.retrieval.ingest.reader import build_docs
        p = os.path.join(tempfile.mkdtemp(), "Q.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("# 无fm\n内容")
        meta = build_docs(p, "")[0]["meta"]
        self.assertEqual(meta["version"], "v1")


    def test_number_item_keeps_parenthetical_ancestor(self):
        # 回归:(一)=6 必须浅于 1.=7,否则 1. 会把（一）弹出祖先栈,子块 section 丢父级,
        # 导致结构树深层节点无法精确匹配到自己的块。
        text = "第二部分 保障内容\n第六条 保险责任\n（一）一般医疗及外购药械费用\n1.住院医疗费用\n指住院期间的费用。\n"
        items = chunk_structured(text, "policy_pdf", chunk_size=1000)
        ones = [it for it in items if "1.住院医疗费用" in it["section"]]
        self.assertTrue(ones, "应切出含 1.住院医疗费用 的单元")
        self.assertIn("（一）一般医疗及外购药械费用", ones[0]["section"], "1. 子块 section 必须保留 （一） 父级")
    def test_empty_heading_not_standalone_chunk(self):
        # 空标题(部分/条无正文)不再单独成块——标题文本保留在子块前缀里,防碎片
        text = "第一部分 总则\n第一条 合同构成\n正文甲。\n第二条 合同的成立\n正文乙。\n"
        items = chunk_structured(text, "policy_pdf", chunk_size=1000)
        self.assertTrue(items)
        self.assertFalse(any(it["section"] == "第一部分 总则" for it in items),
                         "空标题'第一部分'不应单独成块")
        # 第一条的 section 仍含祖先(前缀继承)
        self.assertTrue(any("第一部分 总则 >" in it["section"] for it in items))

    def test_short_siblings_merged_same_parent(self):
        # 同级(同父)连续编号子项合并:三个短的（一）合成一块,section 取父路径
        text = ("第二部分 保障内容\n第六条 保险责任\n（一）住院医疗\n指住院费用。\n"
                "（二）门诊医疗\n指门诊费用。\n（三）外购药\n指外购药费用。\n")
        items = chunk_structured(text, "policy_pdf", chunk_size=1000)
        self.assertEqual(len(items), 1, "三个短同级子项应合并成一块")
        self.assertEqual(items[0]["section"], "第二部分 保障内容 > 第六条 保险责任")
        for t in ("住院医疗", "门诊医疗", "外购药"):
            self.assertIn(t, items[0]["content"])

    def test_bare_cn_numeral_terms_not_merged(self):
        # D68:裸"一、二、三、四"(如"第七部分 释义"的术语)是独立术语,各成一块,不做兄弟合并
        text = ("第七部分 释义\n一、保险人\n指众安在线财产保险股份有限公司。\n"
                "二、周岁\n以法定身份证明文件中记载的出生日期为基础计算的实足年龄。\n"
                "三、意外伤害\n指以外来的、突发的、非本意的和非疾病的客观事件直接致使身体受到的伤害。\n")
        items = chunk_structured(text, "policy_pdf", chunk_size=512, max_tokens=460)
        self.assertEqual(len(items), 3, "三个 一、 术语应各成一块(不合并成一块)")
        for t in ("一、保险人", "二、周岁", "三、意外伤害"):
            self.assertTrue(any(t in it["section"] for it in items), f"{t} 应有独立 chunk,section 含该术语(目录 1:1)")

    def test_character_mode_splits_by_chars_no_section(self):
        # 字符切分方式(D70):按 chunk_size 字符滑窗,不识别结构 → 无 section
        text = "甲" * 30 + "\n" + "乙" * 30 + "\n" + "丙" * 30   # 90 字
        ch = chunk_documents([{"text": text, "meta": {"doc_type": "text", "chunk_id": "d"}}],
                             chunk_size=50, overlap=0, text_splitter="character")
        self.assertGreater(len(ch), 1, "字符模式应按 chunk_size 滑窗切成多块")
        self.assertTrue(all(not c["meta"].get("section") for c in ch), "字符模式无 section(不识别结构)")
        self.assertTrue(all(len(c["content"]) <= 50 for c in ch))

    def test_paragraph_mode_packs_paragraphs(self):
        # 段落切分方式(D70):按空行分段落并打包到 ≤ chunk_size,无 section
        paras = "\n\n".join(["第%d段" % i + "内容内容内容" * 4 for i in range(4)])
        ch = chunk_by_paragraphs(paras, chunk_size=60, overlap=0)
        self.assertGreater(len(ch), 1, "段落模式应按 chunk_size 切出多块")
        item = chunk_documents([{"text": paras, "meta": {"doc_type": "text", "chunk_id": "d"}}],
                               chunk_size=60, overlap=0, text_splitter="paragraph")
        self.assertEqual(len(item), len(ch))
        self.assertTrue(all(not c["meta"].get("section") for c in item), "段落模式无 section")

    def test_oversize_sibling_not_merged_and_split(self):
        # 单个体积超上限:不与兄弟合并;仍按超长降级切成 ≤上限 且带前缀
        long1 = "甲甲乙丙。" * 160   # 640 字(含句号,可降级)
        long2 = "丁戊己庚。" * 90    # 450 字
        text = ("第六条 保险责任\n（一）住院医疗\n" + long1 + "\n（二）门诊\n" + long2 + "\n")
        items = chunk_structured(text, "policy_pdf", chunk_size=120)
        art1 = [it for it in items if "（一）住院医疗" in it["section"]]
        art2 = [it for it in items if "（二）门诊" in it["section"]]
        self.assertGreater(len(art1), 1, "（一）640字应降级成多块")
        self.assertGreater(len(art2), 1)
        self.assertTrue(all(len(it["content"]) <= 200 for it in art1 + art2),
                        "降级后每块长度应约 ≤上限+前缀")

    def test_token_budget_splits_overlong(self):
        # token 预算模式:超预算(即使字符上限很大)也按段落/句子切成 ≤预算 的块
        from app.utils.text import embedding_tokens
        body = "甲乙丙丁戊己庚辛壬癸。" * 40   # ~400 字,远超 120 token
        text = "第一部分 总则\n第一条 合同构成\n" + body + "\n"
        items = chunk_structured(text, "policy_pdf", chunk_size=100000, max_tokens=120)
        self.assertGreater(len(items), 1, "token 预算应把长单元切成多块")
        self.assertTrue(all(embedding_tokens(it["content"]) <= 120 for it in items),
                        "每块整串(含前缀)估算 token 应 ≤ 预算")

    def test_token_budget_limits_sibling_merge(self):
        # 同级合并尊重 token 预算:字符上限很大能全并,token 预算小则不会全并
        body = "住院医疗费用赔付。" * 8   # ~72 字/项
        text = ("第六条 保险责任\n（一）甲\n" + body + "\n（二）乙\n" + body + "\n（三）丙\n" + body + "\n")
        items_char = chunk_structured(text, "policy_pdf", chunk_size=100000)
        items_tok = chunk_structured(text, "policy_pdf", chunk_size=100000, max_tokens=150)
        self.assertEqual(len(items_char), 1, "字符上限足够大时应合并成一块")
        self.assertGreater(len(items_tok), 1, "token 预算应阻止把全组合并成一块(超预算)")

    def test_container_heading_with_children_merged_under_it(self):
        # 真容器标题(无正文、有子项)不作为独立空块;子项合并挂在容器下,内容仍保留
        text = "第六条 保险责任\n（一）住院医疗\n指住院费用。\n（二）门诊\n指门诊费用。\n"
        items = chunk_structured(text, "policy_pdf", chunk_size=1000)
        self.assertTrue(items)
        # 不存在"只有标题、无正文"的独立空块
        self.assertFalse(any(it["content"].strip() == "第六条 保险责任"
                             or it["content"].strip() == "（一）住院医疗" for it in items))
        self.assertTrue(any("住院费用" in it["content"] and "门诊费用" in it["content"] for it in items),
                        "子项内容应保留且合并到容器(第六条)之下")

    def test_md_single_line_h1_container_not_standalone_chunk(self):
        # D93 治本:md 单行 H1(下有 H2)是零信息容器块 → 不立块;标题经子块 section 前缀保留。
        # 此前 md 整段跳过 _coalesce_units(兄弟合并不适合 md),连带跳过了容器 drop 规则。
        md = ('# 尊享e生2025 客服话术库\n\n'
              '## 客户问"买计划一还是计划二"\n\n先问需求再给建议。\n\n'
              '## 客户问"结节3级能买吗"\n\n告知口径宽松。\n')
        items = chunk_structured(md, "markdown", chunk_size=1000)
        self.assertEqual(len(items), 2)
        # 无"纯标题"零信息块(内容只有标题本身)
        self.assertFalse(any(it["content"].strip().endswith("客服话术库")
                             and len(it["content"]) < 40 for it in items),
                        "单行 H1 容器不得成为独立 chunk")
        # 标题文本经子块前缀保留(检索锚点不丢)
        self.assertTrue(all("客服话术库" in it["content"] for it in items))
        self.assertIn('客户问"买计划一还是计划二"', items[0]["content"])
        # 不做兄弟合并:两个 H2 节各成一块(QA 一问一块)
        self.assertIn("先问需求再给建议", items[0]["content"])
        self.assertIn("告知口径宽松", items[1]["content"])

    def test_md_h1_with_body_and_orphan_h1_kept(self):
        # 边界①:H1 下直接有正文(非单行容器)→ 保留成块;
        # 边界②:孤立 H1(无正文)→ 不立空块(标题经前缀承载,无正文=零信息块,D94 过滤)。
        md1 = "# 标题A\n正文直接跟在H1下\n## 小节\n小节内容"
        items1 = chunk_structured(md1, "markdown", chunk_size=1000)
        self.assertTrue(any("正文直接跟在H1下" in it["content"] for it in items1),
                        "H1 带直接正文时整节保留")
        md2 = "# 孤立标题"
        items2 = chunk_structured(md2, "markdown", chunk_size=1000)
        self.assertEqual(len(items2), 0, "孤立 H1 无正文→不立空块(标题无正文=零信息)")

    def test_numbered_parent_container_keeps_numeric_leaf_items(self):
        # 用户场景:`2.重大疾病特殊门诊医疗费用`(层级7)下挂 `(1)(2)(3)`(层级8),子项为单行叶子
        text = ("2.重大疾病特殊门诊医疗费用\n（1）门诊肾透析费；\n"
                "（2）门诊恶性肿瘤——重度治疗费,包括化学疗法、放射疗法、肿瘤免疫疗法、肿瘤内分泌疗法、肿瘤靶向疗法的治疗费用；\n"
                "（3）器官移植后的门诊抗排异治疗费。\n")
        items = chunk_structured(text, "policy_pdf", chunk_size=1000)
        all_content = "\n".join(it["content"] for it in items)
        for t in ("门诊肾透析费", "器官移植后的门诊抗排异治疗费", "化学疗法", "肿瘤靶向疗法的治疗费用"):
            self.assertIn(t, all_content, "单行叶子条目正文不得丢失: " + t)

    def test_heading_not_duplicated_in_content(self):
        # D94:标题文本已由前缀(path)承载,content 正文首行不得再重复标题(否则标题出现两次)
        text = "# 产品\n## 保险责任\n7. 特定疾病保险（保额：3 万元）\n赔付规则：按 100% 赔付。\n"
        items = chunk_structured(text, "markdown", chunk_size=1000)
        self.assertTrue(items)
        for it in items:
            # 前缀里包含标题即可,正文首行不得再以该标题开头
            title = it["title"]
            if not title:
                continue
            body = it["content"]
            # 去掉 [prefix] 前缀,看正文首行
            pfx = f"[{it['section']}] "
            rest = body[len(pfx):] if body.startswith(pfx) else body
            first_line = rest.split("\n")[0].strip()
            self.assertNotEqual(first_line, title,
                                f"标题 '{title}' 不应在 content 正文首行重复出现(已在前缀里)")

    def test_horizontal_rule_not_creating_empty_chunk(self):
        # D94:---/*** / ___ 分隔线无检索意义,不得单独成空块,也不得残留在 chunk 末尾
        text = ("# 产品\n## 保险责任\n### 7. 特定疾病保险\n赔付规则：按 100% 赔付。\n---\n"
                "### 8. 其他责任\n其他内容。\n")
        items = chunk_structured(text, "markdown", chunk_size=1000, max_tokens=460)
        all_content = "\n".join(it["content"] for it in items)
        self.assertNotIn("---", all_content, "分隔线 --- 不得出现在任何 chunk content 里")
        # 无空块(content 去掉前缀后应有正文)
        for it in items:
            pfx = f"[{it['section']}]"
            body = it["content"][len(pfx):].strip() if it["content"].startswith(pfx) else it["content"].strip()
            self.assertTrue(body, f"chunk 不应为空(section={it['section']!r})")

    def test_long_clause_line_is_content_not_heading(self):
        # 编号开头的"长条款/句末标点" = 正文,不是标题:不劈开、不进结构树,内容保留
        from app.retrieval.ingest.probe import build_outline
        clause = ("2.被保险人所患既往症（释义五十五），及保险单中特别约定的除外疾病引起的相关费用；"
                  "等待期内被保险人确诊疾病所导致的医疗费用；未经科学或者医学认可的试验性或者研究性治疗"
                  "及其后果所产生的费用；未被治疗所在地权威部门批准的治疗，")  # >64 字,以 ，结尾
        text = ("第六条 保险责任\n（一）责任免除\n" + clause + "\n第三部分 释义\n")
        items = chunk_structured(text, "policy_pdf", chunk_size=1000)
        all_content = "\n".join(it["content"] for it in items)
        self.assertIn("既往症", all_content, "长条款正文不得丢失")
        # 该长句不作为一个独立"标题"块(form 一个 section)
        self.assertFalse(any("既往症" in it.get("section", "") for it in items))
        # 结构树里也不应出现该长句作为节点
        outline = build_outline(text)
        self.assertFalse(any("既往症" in n["title"] for n in outline), "长条款不应进结构树")

    def test_short_heading_still_recognized(self):
        # 短标题(无句末标点)仍识别为结构
        from app.retrieval.ingest.probe import build_outline
        text = "第六条 保险责任\n（一）住院医疗\n1.住院医疗费用\n指住院期间的费用。\n"
        items = chunk_structured(text, "policy_pdf", chunk_size=1000)
        self.assertTrue(any("1.住院医疗费用" in it["section"] for it in items))
        outline = build_outline(text)
        self.assertTrue(any(n["title"].startswith("1.住院医疗费用") for n in outline))

    def test_clause_with_comma_not_heading(self):
        # 含逗号/分号的"分句短语"(如诊断标准)是内容,不是标题(真标题是无逗号的名词标签)
        from app.retrieval.chunker import is_heading_like
        from app.retrieval.ingest.probe import build_outline
        self.assertTrue(is_heading_like("（三十三）严重全身性重症肌无力"))
        self.assertFalse(is_heading_like("（2）自主生活能力完全丧失，无法独"))
        self.assertFalse(is_heading_like("（1）心脏淀粉样变性，被保险人存在"))
        text = ("（三十三）严重全身性重症肌无力\n（2）自主生活能力完全丧失，无法独\n"
                "（三十四）严重类风湿性关节炎\n")
        outline = build_outline(text)
        self.assertEqual([n["title"] for n in outline],
                         ["（三十三）严重全身性重症肌无力", "（三十四）严重类风湿性关节炎"])

    def test_generic_document_detected_and_chunked(self):
        # 换一种文档风格(报告式 1./1.1/1.1.1):自动探测为 generic 并按结构切,不认死条款
        from app.retrieval.ingest.probe import build_outline
        from app.retrieval.chunker import _detect_pattern_key
        text = ("1. 背景\n概述正文。\n1.1 研究目标\n目标正文。\n1.1.1 具体指标\n指标正文。\n2. 方法\n方法正文。\n")
        self.assertEqual(_detect_pattern_key(text), "generic")
        items = chunk_structured(text, "text", chunk_size=1000)   # doc_type=text → 自动探测
        self.assertTrue(items, "应探测到 generic 结构并切出块")
        secs = [it["section"] for it in items]
        self.assertTrue(any(s == "1. 背景" or s.startswith("1. 背景 >") for s in secs))
        self.assertTrue(any(s.startswith("1. 背景 > 1.1 研究目标") for s in secs))
        self.assertTrue(any(s.startswith("2. 方法") for s in secs))
        outline = build_outline(text, doc_type="text")
        self.assertGreaterEqual(len(outline), 3)
        # 正文都在(不丢内容)
        allc = "\n".join(it["content"] for it in items)
        for k in ("概述正文", "目标正文", "指标正文", "方法正文"):
            self.assertIn(k, allc)

    def test_toc_prefix_filtered(self):
        # 目录/前言等非正文前缀被剔除;正文从第一个真结构标题开始
        from app.retrieval.chunker import _filter_toc_prefix
        text = ("目录\n第一部分 总则...............1\n第一条 合同构成...........1\n"
                "前言\n本条款根据...制定。\n第一部分 总则\n第一条 合同构成\n正文。\n")
        out = _filter_toc_prefix(text)
        self.assertIn("第一部分 总则\n第一条 合同构成\n正文。", out)
        self.assertNotIn("目录", out)
        self.assertNotIn("前言", out)

    def test_atomic_items_not_split(self):
        # _cut_by_tokens 按"编号项边界"打包;块首必须是某编号项标题,不出现句子碎片(不劈开单个项)
        from app.retrieval.chunker import _cut_by_tokens
        body = ("（一）一般医疗\n1.住院医疗费用\n指住院期间发生的费用。\n"
                "2.特殊门诊医疗费用\n指门诊肾透析等费用。\n3.门诊手术费用\n指门诊手术发生的费用。\n"
                "（二）一般门急诊\n指门急诊发生的费用。\n")
        pieces = _cut_by_tokens(body, "第六条 保险责任", 60, "policy")
        self.assertGreater(len(pieces), 0)
        starts = ("（一）一般医疗", "（二）一般门急诊", "1.住院医疗费用", "2.特殊门诊医疗费用", "3.门诊手术费用")
        for p in pieces:
            first = p.strip().split("\n")[0]
            i = first.find("] ")
            head = first[i + 2:] if i >= 0 else first
            self.assertTrue(any(head.startswith(s) for s in starts),
                            "块首应为编号项标题,而非句子碎片: " + head[:40])

    def test_colon_ending_item_merged_into_parent(self):
        # 冒号结尾的编号项(如 （2）至少存在下列一项：)是"条件/清单引出项",应并回父级,不拆成独立块
        from app.retrieval.chunker import is_heading_like
        from app.retrieval.ingest.probe import build_outline
        self.assertFalse(is_heading_like("（2）至少存在下列一项："))
        self.assertFalse(is_heading_like("（1）骨髓活组织检查符合多发性骨髓瘤的典型骨髓改变；"))
        self.assertTrue(is_heading_like("（九十八）多发性骨髓瘤"))       # 疾病名(短、无冒号/标点)仍是标题
        text = ("（九十八）多发性骨髓瘤\n多发性骨髓瘤是浆细胞异常增生的恶性肿瘤。必须满足下列所有条件：\n"
                "（1）骨髓活组织检查符合多发性骨髓瘤的典型骨髓改变；\n"
                "（2）至少存在下列一项：\na.异常球蛋白血症；\nb.溶骨性损害。\n孤立性骨髓瘤不在保障范围内。\n")
        items = chunk_structured(text, "policy_pdf", chunk_size=512, max_tokens=460)
        self.assertEqual(len(items), 1, "疾病的 (1)(2) 标准应并回父级,不拆成多块")
        allc = "\n".join(it["content"] for it in items)
        for k in ("多发性骨髓瘤是浆细胞", "骨髓活组织检查", "至少存在下列一项", "异常球蛋白血症", "孤立性骨髓瘤不在保障范围内"):
            self.assertIn(k, allc)

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
