# -*- coding: utf-8 -*-
"""保费计算(PremiumStore 费率事实源 + calculate_premium 工具)。

设计要点:
- 费率"数据"一张表(premium_rates):产品 × 责任列(item_key) × 维度(dims JSON) × 年龄区间 × 保费 × 单位。
  不同产品、不同结构(区间年龄/逐岁、有无社保、性别、免赔额档、计划一/二等)都表达为同构行。
- "计算逻辑"按产品分:calculate_premium 是通用分发器,按 product_id 找到该产品的计算器(策略)。
  尊享e生2025 的计算器:必选计划(3免赔额档×计划一/二)+ 加油包(家庭共享/门急诊A/B/药费院/重疾每5万保额×男/女)。
  重疾按「每5万保额」计 = premium × (coverage/50000);家庭单优享 = 2人×0.95、≥3人×0.90。
- 金额走"查表+确定性计算",LLM 只整理答复 + 引用角标,不自己算(保险=金融,务必准确可追溯)。
- 引用 [idx] 绑定费率行 id(chunk_id = 费率行确定性 hash)+ product_id/version,可追溯(铁律 3)。

费率数据来源:Excel(openpyxl 解析入库,比手敲准确)。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from typing import Any

PRODUCT_XX = "尊享e生2025"
PRODUCT_XX_NAME = "尊享 e 生·中高端医疗保险 PLUS（2025版）（年缴版）"
VERSION_XX = "v2025"

_XX_PLAN_COLS = [
    ("0元", "计划一"), ("0元", "计划二"),
    ("1.5万", "计划一"), ("1.5万", "计划二"),
    ("3万", "计划一"), ("3万", "计划二"),
]
_XX_ADDON_COLS = [
    ("family_deductible", "家庭共享免赔额", {}, "元/年"),
    ("clinic_a", "门急诊加油包A-不含器质", {}, "元/年"),
    ("clinic_b", "门急诊加油包B-含器质", {}, "元/年"),
    ("drug", "药费院加油包", {}, "元/年"),
    ("critical", "重疾加油包（每5万保额）", {"gender": "男"}, "元/每5万保额"),
    ("critical", "重疾加油包（每5万保额）", {"gender": "女"}, "元/每5万保额"),
]


def _rate_id(product_id, version, item_key, dims, age_min, age_max):
    s = f"{product_id}|{version}|{item_key}|{json.dumps(dims, sort_keys=True, ensure_ascii=False)}|{age_min}-{age_max}"
    return "p_" + hashlib.sha1(s.encode("utf-8")).hexdigest()[:20]


def _age_range(label):
    m = re.match(r"\[\s*(\d+)\s*,\s*(\d+)\s*\]", label.strip())
    if not m:
        raise ValueError(f"bad age label: {label!r}")
    return int(m.group(1)), int(m.group(2))


def _num(val):
    if val is None:
        return None
    s = str(val).strip().replace(",", "")
    if s == "" or s in ("不适用", "不适用。", "N/A", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _fmt_dims(dims):
    if not dims:
        return ""
    return "(" + ",".join(f"{v}" for v in dims.values()) + ")"


class PremiumStore:
    def __init__(self, path):
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS premium_rates (
          id TEXT PRIMARY KEY,
          product_id TEXT NOT NULL,
          product_name TEXT,
          version TEXT,
          table_type TEXT,
          item_key TEXT NOT NULL,
          item_name TEXT,
          dims TEXT NOT NULL DEFAULT '{}',
          age_min INTEGER NOT NULL,
          age_max INTEGER NOT NULL,
          premium REAL,
          unit TEXT,
          source TEXT,
          section TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_rate_lookup ON premium_rates(product_id, item_key, age_min, age_max);
        CREATE TABLE IF NOT EXISTS products (
          product_id TEXT PRIMARY KEY,
          product_name TEXT,
          version TEXT,
          coverage TEXT,
          rules TEXT,
          calc_config TEXT,
          source TEXT,
          created_at TEXT
        );
        """)
        self.conn.commit()

    def upsert_rate(self, product_id, version, table_type, item_key, item_name, dims,
                    age_min, age_max, premium, unit, source, section):
        rid = _rate_id(product_id, version, item_key, dims, age_min, age_max)
        self.conn.execute(
            """INSERT INTO premium_rates(id, product_id, product_name, version, table_type, item_key, item_name,
                 dims, age_min, age_max, premium, unit, source, section)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET premium=excluded.premium, unit=excluded.unit""",
            (rid, product_id, PRODUCT_XX_NAME, version, table_type, item_key, item_name,
             json.dumps(dims, sort_keys=True, ensure_ascii=False), age_min, age_max,
             premium, unit, source, section))
        self.conn.commit()
        return {"chunk_id": rid, "product_id": product_id, "product_name": PRODUCT_XX_NAME,
                "version": version, "table_type": table_type, "item_key": item_key,
                "item_name": item_name, "dims": dims, "age_min": age_min, "age_max": age_max,
                "premium": premium, "unit": unit, "source": source, "section": section}

    def upsert_product(self, product_id, name, version, coverage, rules, calc_config, source):
        self.conn.execute(
            """INSERT INTO products(product_id, product_name, version, coverage, rules, calc_config, source, created_at)
               VALUES(?,?,?,?,?,?,?,datetime('now'))
               ON CONFLICT(product_id) DO UPDATE SET calc_config=excluded.calc_config, rules=excluded.rules""",
            (product_id, name, version, coverage, rules,
             json.dumps(calc_config, ensure_ascii=False).replace("\n", ""), source))
        self.conn.commit()

    def get_rate(self, product_id, item_key, dims, age):
        cur = self.conn.execute(
            "SELECT * FROM premium_rates WHERE product_id=? AND item_key=? AND ?>=age_min AND ?<=age_max",
            (product_id, item_key, age, age))
        for row in cur.fetchall():
            if json.loads(row["dims"]) == dims:
                d = dict(row)
                d["dims"] = json.loads(d["dims"])
                return d
        return None

    def get_product(self, product_id):
        row = self.conn.execute("SELECT * FROM products WHERE product_id=?", (product_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["calc_config"] = json.loads(d["calc_config"]) if d.get("calc_config") else {}
        return d

    def close(self):
        self.conn.close()


CALCULATORS = {}


def register(product_id):
    def deco(fn):
        CALCULATORS[product_id] = fn
        return fn
    return deco


@register(PRODUCT_XX)
def _calc_xx2025(store, product_id, age, items, family_member_count=1):
    lines = []
    rows = []
    total = 0.0
    for it in items:
        item_key = it.get("item_key")
        dims = it.get("dims") or {}
        coverage = it.get("coverage")
        row = store.get_rate(product_id, item_key, dims, age)
        if not row:
            lines.append(f"方案 {item_key}{_fmt_dims(dims)}:未找到费率")
            continue
        if row["premium"] is None:
            lines.append(f"{row['item_name']}{_fmt_dims(dims)}:该年龄段不适用")
            rows.append(row)
            continue
        amount = float(row["premium"])
        if row.get("unit") == "元/每5万保额" and coverage:
            amount = amount * (coverage / 50000)
        idx = len(rows) + 1
        lines.append(f"{row['item_name']}{_fmt_dims(row['dims'])}: {amount:,.2f}元/年 [^{idx}]")
        total += amount
        rows.append(row)
    disc = 1.0
    if family_member_count is not None and family_member_count >= 3:
        disc = 0.90
    elif family_member_count is not None and family_member_count == 2:
        disc = 0.95
    total_x = total * disc
    disc_note = "" if disc == 1.0 else f" (家庭单优享 {family_member_count}人 → {disc*100:.0f}折)"
    header = f"{PRODUCT_XX_NAME} {VERSION_XX} 保费测算(年龄 {age}, 家庭单 {family_member_count or 1} 人):"
    content = f"{header}\n" + "\n".join(lines) + f"\n合计: {total_x:,.2f}元/年{disc_note}"
    return content, rows


def calculate_premium(store, args):
    product_id = (args.get("product") or "").strip()
    if not product_id:
        return {"content": "请指定产品(product)", "reference": []}
    calc = CALCULATORS.get(product_id)
    if not calc:
        return {"content": f"产品 {product_id} 暂无保费计算配置", "reference": []}
    try:
        age = int(args.get("age"))
    except (TypeError, ValueError):
        return {"content": "年龄(age)无效", "reference": []}
    items = args.get("items") or []
    if not isinstance(items, list) or not items:
        return {"content": "请至少指定一个方案(items)", "reference": []}
    try:
        family = int(args.get("family_member_count")) if args.get("family_member_count") is not None else 1
    except (TypeError, ValueError):
        family = 1
    content, store_rows = calc(store, product_id, age, items, family)
    ref = []
    for r in store_rows:
        ref.append({
            "chunk_id": r.get("chunk_id") or r.get("id"), "score": None,
            "doc_id": r["product_id"], "version": r["version"],
            "section": r["section"] or r["table_type"],
            "source": r.get("source", ""),
            "content": f"{r['item_name']}{_fmt_dims(r['dims'])}: {r['premium']}元/年({r['unit']})",
        })
    return {"content": content, "reference": ref}


def build_premium_tool(store):
    schema = {"type": "function", "function": {
        "name": "calculate_premium",
        "description": "按产品/投保年龄/所选方案计算年缴保费(查表确定性计算,含免赔额/计划/性别/社保维度的选档、重疾按每5万保额按比例、家庭单优享折扣)。返回可读账单+可溯源引用",
        "parameters": {"type": "object", "properties": {
            "product": {"type": "string", "description": "产品ID,如 尊享e生2025"},
            "age": {"type": "integer", "description": "投保年龄(周岁)"},
            "items": {"type": "array", "description": "要计算的方案/包,可多选(必选计划+若干加油包)",
                      "items": {"type": "object", "properties": {
                          "item_key": {"type": "string", "description": "方案键:plan=必选计划;family_deductible/clinic_a/clinic_b/drug/critical=加油包"},
                          "dims": {"type": "object", "description": "plan 用 {\"deductible\":\"0元|1.5万|3万\",\"plan_variant\":\"计划一|计划二\"};critical 用 {\"gender\":\"男|女\"};其余留空"},
                          "coverage": {"type": "number", "description": "保额(元),仅 critical(每5万保额)需传,如 100000=10万保额"},
                      }, "required": ["item_key"]}},
            "family_member_count": {"type": "integer", "description": "家庭单成员数(可选);2人95折,≥3人9折"},
        }, "required": ["product", "age", "items"]}}}

    def handler(args):
        return calculate_premium(store, args or {})

    return {"schema": schema, "handler": handler}


def load_xx2025_xlsx(store, xlsx_path):
    import openpyxl
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    n = 0
    source = xlsx_path
    ws = wb["必选计划费率表"]
    for row in ws.iter_rows(min_row=6, values_only=True):
        label = row[0]
        if label is None or not str(label).strip().startswith("["):
            continue
        age_min, age_max = _age_range(str(label))
        for i, (deductible, plan) in enumerate(_XX_PLAN_COLS):
            premium = _num(row[i + 1])
            store.upsert_rate(product_id=PRODUCT_XX, version=VERSION_XX, table_type="必选计划",
                              item_key="plan", item_name=f"必选计划({deductible}年免赔额,{plan})",
                              dims={"deductible": deductible, "plan_variant": plan},
                              age_min=age_min, age_max=age_max, premium=premium,
                              unit="元/年", source=source, section="必选计划费率表")
            n += 1
    ws = wb["加油包费率表"]
    for row in ws.iter_rows(min_row=6, values_only=True):
        label = row[0]
        if label is None or not str(label).strip().startswith("["):
            continue
        age_min, age_max = _age_range(str(label))
        for i, (item_key, item_name, dims, unit) in enumerate(_XX_ADDON_COLS):
            premium = _num(row[i + 1])
            store.upsert_rate(product_id=PRODUCT_XX, version=VERSION_XX, table_type="加油包",
                              item_key=item_key, item_name=item_name, dims=dims,
                              age_min=age_min, age_max=age_max, premium=premium,
                              unit=unit, source=source, section="加油包费率表")
            n += 1
    store.upsert_product(
        product_id=PRODUCT_XX, name=PRODUCT_XX_NAME, version=VERSION_XX,
        coverage=("一般医疗(对应年免赔额)+特定疾病医疗+外购药品及外购医疗器械费用医疗+特定药品费用医疗"
                  "+恶性肿瘤先进疗法医疗+特定疾病异地转诊公共交通费用及住宿费用+特定疾病住院津贴"
                  "+意外紧急牙齿门急诊医疗费用+全球紧急救援服务。"),
        rules=("首次投保年龄:出生满30天-70周岁;71周岁及以上仅限保单期满指定期限内重新投保;"
               "投保保单数量=1,个人单标准保费。家庭单优享:同投保人 尊享/众民保 系列家庭成员 2人享95折,3人及以上享9折。"),
        calc_config={
            "mandatory": ["plan"],
            "optional": ["family_deductible", "clinic_a", "clinic_b", "drug", "critical"],
            "unit_map": {"critical": 50000},
            "discounts": {"family": [[2, 0.95], [3, 0.90]]},
            "item_dims": {"plan": ["deductible", "plan_variant"], "critical": ["gender"],
                          "family_deductible": [], "clinic_a": [], "clinic_b": [], "drug": []},
        },
        source=source)
    return n
