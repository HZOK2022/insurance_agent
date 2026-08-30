# -*- coding: utf-8 -*-
"""安盛天平卓越馨选(A款) 保费:loader(Excel→premium.db) + 计算器。

结构(13 张表,投保年龄 0-59 逐岁,均为绝对年费率):
- 住院医疗 ×5 免赔额(0/5000/10000/15000/20000元): 普A-特C × 有/无社保
- 门急诊医疗 ×4 免赔额(0/200/500/1300元): 保额(1万/1.5万/2万/3.5万) × 有/无社保
- 重大疾病住院津贴: 普通版/特需版 × A/B/C
- 重大疾病保险金: 普通版(保额1万)/特需版(保额2万)
- 海南博鳌特定医疗: 特药械(有/无社保)/院外特定药品/特定医疗器械
计算 = 纯查表(无按比例/折扣)。
"""
from __future__ import annotations
import json

from app.businesses.premium import PremiumStore, register, _num, _fmt_dims

PRODUCT_AX = "安盛天平卓越馨选2025"
PRODUCT_AX_NAME = "安盛天平卓越馨选（A款）（互联网专属）医疗保险"
VERSION_AX = "v2025"
KB_DOC_AX = "安盛天平卓越馨选综合住院医疗保险(2025版A款)(互联网专属)条款_全文"

_HOSPITAL_SHEETS = [("0元", "住院医疗-免赔0元"), ("5000元", "住院医疗-免赔5000元"),
                    ("10000元", "住院医疗-免赔10000元"), ("15000元", "住院医疗-免赔15000元"),
                    ("20000元", "住院医疗-免赔20000元")]
_HOSPITAL_TIERS = ["普A", "普B", "普C", "特A", "特B", "特C"]
_OUTPATIENT_SHEETS = [("0元", "门急诊医疗-免赔0元"), ("200元", "门急诊医疗-免赔200元"),
                      ("500元", "门急诊医疗-免赔500元"), ("1300元", "门急诊医疗-免赔1300元")]
_OUTPATIENT_COV = ["1万", "1.5万", "2万", "3.5万"]
_SOCIALS = ["有社保", "无社保"]


@register(PRODUCT_AX)
def _calc_ax2025(store: PremiumStore, product_key, age, items, family_member_count=1):
    """纯查表:按 (item_key, dims, age) 直接取年费率,求和。"""
    lines, rows, total = [], [], 0.0
    for it in items:
        item_key = it.get("item_key")
        dims = it.get("dims") or {}
        row = store.get_rate(product_key, item_key, dims, age)
        if not row:
            lines.append(f"方案 {item_key}{_fmt_dims(dims)}:未找到费率")
            continue
        if row["premium"] is None:
            lines.append(f"{row['item_name']}:该年龄段不适用")
            rows.append(row)
            continue
        lines.append(f"{row['item_name']}{_fmt_dims(row['dims'])}: {row['premium']:,.2f}元/年 [{len(rows)+1}]")
        total += float(row["premium"])
        rows.append(row)
    header = f"{PRODUCT_AX_NAME} {VERSION_AX} 保费测算(年龄 {age}):"
    content = f"{header}\n" + "\n".join(lines) + f"\n合计: {total:,.2f}元/年"
    return content, rows


def load_ax2025_xlsx(store: PremiumStore, xlsx_path: str) -> int:
    """解析安盛天平 13 张表 → premium_rates;返回插入行数。"""
    import openpyxl
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    n = 0
    source = xlsx_path
    PROD = PRODUCT_AX

    def _iter_age_rows(ws):
        for row in ws.iter_rows(values_only=True):
            c0 = row[0]
            if c0 is None:
                continue
            s = str(c0).strip()
            if s.isdigit():
                yield int(s), row

    # 1) 住院医疗 ×5 档
    for deductible, sheet in _HOSPITAL_SHEETS:
        for age_v, row in _iter_age_rows(wb[sheet]):
            for ti, tier in enumerate(_HOSPITAL_TIERS):
                for si, social in enumerate(_SOCIALS):
                    idx = 1 + ti * 2 + si
                    val = row[idx] if idx < len(row) else None
                    store.upsert_rate(PROD, "hospital", f"一般住院+重疾住院(免赔{deductible})",
                                      {"deductible": deductible, "tier": tier, "social": social},
                                      age_v, age_v, _num(val), "元/年", source, f"住院医疗-免赔{deductible}")
                    n += 1
    # 2) 门急诊医疗 ×4 档
    for deductible, sheet in _OUTPATIENT_SHEETS:
        for age_v, row in _iter_age_rows(wb[sheet]):
            for ci, cov in enumerate(_OUTPATIENT_COV):
                for si, social in enumerate(_SOCIALS):
                    idx = 1 + ci * 2 + si
                    val = row[idx] if idx < len(row) else None
                    store.upsert_rate(PROD, "outpatient", f"门急诊(免赔{deductible},保额{cov})",
                                      {"deductible": deductible, "coverage": cov, "social": social},
                                      age_v, age_v, _num(val), "元/年", source, f"门急诊医疗-免赔{deductible}")
                    n += 1
    # 3) 重大疾病住院津贴
    for age_v, row in _iter_age_rows(wb["重大疾病住院津贴"]):
        for i, (version, plan) in enumerate([("普通版", "A"), ("普通版", "B"), ("普通版", "C"),
                                            ("特需版", "A"), ("特需版", "B"), ("特需版", "C")]):
            val = row[i + 1] if i + 1 < len(row) else None
            store.upsert_rate(PROD, "majordaily", f"重疾住院津贴({version}{plan})",
                              {"version": version, "plan": plan}, age_v, age_v, _num(val),
                              "元/年", source, "重大疾病住院津贴")
            n += 1
    # 4) 重大疾病保险金
    for age_v, row in _iter_age_rows(wb["重大疾病保险金"]):
        for i, version in enumerate(["普通版", "特需版"]):
            val = row[i + 1] if i + 1 < len(row) else None
            store.upsert_rate(PROD, "majorsum", f"重疾保险金({version})",
                              {"version": version}, age_v, age_v, _num(val),
                              "元/年", source, "重大疾病保险金")
            n += 1
    # 5) 海南博鳌特定医疗
    for age_v, row in _iter_age_rows(wb["海南博鳌特定医疗"]):
        specs = [("特药械", "有社保"), ("特药械", "无社保"),
                 ("院外特定药品", "有/无社保"), ("特定医疗器械", "有/无社保")]
        for i, (typ, social) in enumerate(specs):
            val = row[i + 1] if i + 1 < len(row) else None
            store.upsert_rate(PROD, "boao", f"海南博鳌({typ})",
                              {"type": typ, "social": social}, age_v, age_v, _num(val),
                              "元/年", source, "海南博鳌特定医疗")
            n += 1
    # products 元信息
    store.upsert_product(
        key=PROD, name=PRODUCT_AX_NAME, kb_doc_id=KB_DOC_AX, version=VERSION_AX,
        coverage=("一般住院医疗保险金 + 重大疾病住院医疗保险金(免赔额分0/5000/10000/15000/20000元)"
                  " + 门急诊医疗保险金(免赔额分0/200/500/1300元) + 重大疾病住院津贴 + 重大疾病保险金"
                  " + 海南博鳌·乐城特定医疗(特药械/院外特定药品/特定医疗器械)。"),
        rules=("投保年龄:0-59周岁;有社保/无社保两种口径费率不同;"
               "门急诊保额1万/1.5万/2万元适用于普通版及特需版6个计划,保额3.5万元仅适用于特需版3个计划。"),
        calc_config={"mandatory": ["hospital"],
                     "optional": ["outpatient", "majordaily", "majorsum", "boao"],
                     "unit_map": {},
                     "discounts": {},
                     "item_dims": {"hospital": ["deductible", "tier", "social"],
                                   "outpatient": ["deductible", "coverage", "social"],
                                   "majordaily": ["version", "plan"],
                                   "majorsum": ["version"],
                                   "boao": ["type", "social"]}},
        source=source)
    return n
