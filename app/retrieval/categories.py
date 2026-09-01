# -*- coding: utf-8 -*-
"""产品类别(保险类型)判定:chunk 携带 product_category,用于区分医疗险/重疾险/意外险等。

- 这是**保险类别**(如 医疗险/重疾险/意外险),与 doc_type(文档类型:policy_document/structured/faq/sales_script)无关。
- 规则式按 doc_id(产品名/文件名)关键词判定,供摄取(ingest_kb)与历史数据回填(KnowledgeStore 迁移)复用。
- 归类逻辑集中在此(铁律4:类别映射集中在配置,不散落业务)。
"""
from __future__ import annotations

# 关键词 → 保险类别(按先后命中;先匹配到的优先)
CATEGORY_RULES: tuple[tuple[str, str], ...] = (
    ("重疾", "重疾险"),
    ("重大疾病", "重疾险"),
    ("意外", "意外险"),
    ("尊享e生", "医疗险"),  # 尊享e生(百万医疗)
    ("尊享", "医疗险"),
    ("医疗", "医疗险"),
    ("住院", "医疗险"),
    ("百万医疗", "医疗险"),
    ("e享", "医疗险"),
    ("寿险", "寿险"),
    ("定期寿", "寿险"),
)

DEFAULT_CATEGORY = "其他"


def classify_product_category(doc_id: str, title: str = "") -> str:
    """按 doc_id(及可选 title)里的关键词判定保险类别;无命中返回 DEFAULT_CATEGORY。"""
    hay = (doc_id or "") + " " + (title or "")
    for kw, cat in CATEGORY_RULES:
        if kw in hay:
            return cat
    return DEFAULT_CATEGORY
