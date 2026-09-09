# -*- coding: utf-8 -*-
"""保费计算(PremiumStore 费率事实源 + calculate_premium 工具)。

设计(定稿):
- products: id 自增主键;key 稳定业务键/代码(LLM 引用+外键);name 显示名;kb_doc_id 关联向量库条款文档。
- premium_rates: id 自增主键;product_key 外键→products.key;无 chunk_id(费率行不是文档片段);
  引用角标直接用该行 id(快照在会话 retrieval 事件里,事件解析可溯)。
- 不同产品计算方式/附加包不同 → calculate_premium 按 product_key 分发到各产品计算器(策略)。
- 金额查表+确定计算(重疾每5万保额、家庭单折扣),LLM 不手算。
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from typing import Any

import app.db as dbmod

logger = logging.getLogger(__name__)

PRODUCT_XX = "尊享e生2025"                        # key(稳定业务键,LLM 引用)
PRODUCT_XX_NAME = "尊享 e 生·中高端医疗保险 PLUS（2025版）（年缴版）"
VERSION_XX = "v2025"

# 在售产品目录(兜底,当 CALCULATORS 尚未载入时用)。产品不明确需追问题时据此给出可选清单。
_KNOWN_PRODUCTS: tuple[str, ...] = ("尊享e生2025", "安盛天平卓越馨选2025")


def available_product_keys() -> tuple[str, ...]:
    """返回当前已注册计算器的产品 key(即用户在售产品),供"产品不明确时追问"列出可选产品。

    以 CALCULATORS(费率计算器注册表)为权威;为空时退回静态兜底清单(防御:计算器未加载时也能给清单)。
    """
    try:
        keys = tuple(sorted(CALCULATORS.keys()))
    except Exception:   # 注册表未就绪/导入异常 → 兜底
        keys = ()
    return keys if keys else _KNOWN_PRODUCTS

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


def _dim_value_variants(v):
    """生成 dims 单个值的候选正则形式(供费率精确匹配未命中时的宽松匹配)。

    背景:模型常把档位枚举(字符串)传成数字(如 deductible=15000)或带单位/千分位的
    数字串,而费率表 dims 存的是中文档位字符串(如 "1.5万"/"5000元"/"计划一")。
    这里把数字解释成常见中文档位写法,供 get_rate 兜底重查;非数值标量原样返回。
    只做确定性转换,不臆造。
    """
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        s = str(v).strip() if isinstance(v, str) else v
        if not isinstance(s, str):
            return {v}
        cand = {v, s}
        if s.endswith("元") and len(s) > 1:
            cand.add(s[:-1])                 # "5000元" → "5000"
        try:
            num = float(s.replace(",", "").strip())
            if num.is_integer():
                cand |= _dim_value_variants(int(num))
        except ValueError:
            pass
        return cand
    n = float(v)
    cand = {v}
    if not n.is_integer():
        return cand
    i = int(n)
    cand.add(str(i))
    cand.add(f"{i}元")
    if i % 10000 == 0:
        cand.add(f"{i // 10000}万")          # 30000 → "3万"
    else:
        cand.add(f"{i / 10000:g}万")         # 15000 → "1.5万";5000 → "0.5万"
        cand.add(f"{i:,}")                    # 15000 → "15,000"
        cand.add(f"{i:,}元")                  # 15000 → "15,000元"
    return cand


def _dims_candidates(dims):
    """对 dims 各值取候选,笛卡尔积得到候选 dims 集合(最多 ~8 个,受控)。"""
    if not isinstance(dims, dict) or not dims:
        return []
    from itertools import product
    keys = list(dims.keys())
    value_sets = [_dim_value_variants(dims[k]) for k in keys]
    # 只保留与原始 dims 不同的组合作为候选
    out = []
    for combo in product(*value_sets):
        cand = dict(zip(keys, combo))
        if cand == dims:
            continue
        out.append(cand)
        if len(out) >= 8:
            break
    return out


def _fmt_dims(dims):
    if not dims:
        return ""
    return "(" + ",".join(f"{v}" for v in dims.values()) + ")"


class PremiumStore:
    """费率事实源(生产 MySQL / 开发 SQLite)。单写者;只 INSERT/UPSERT。"""

    def __init__(self, path=None, cfg=None):
        self._is_mysql = cfg is not None and dbmod.dial(cfg) == "mysql"
        # MySQL 里 key 是保留字,需反引号(SQLite 也接受反引号,但保持各自风格)
        self._q = (lambda n: f"`{n}`") if self._is_mysql else (lambda n: n)
        if self._is_mysql:
            self.path = None
            self.conn = dbmod.DB(dbmod.get_conn(cfg, "premium"), cfg)
            self._init_schema()
            return
        if path is None:
            path = dbmod._sqlite_path(cfg, "premium") if cfg else "data/premium.db"
        d = os.path.dirname(path) if path else None
        if d:
            os.makedirs(d, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _ddl(self) -> str:
        return """
        CREATE TABLE IF NOT EXISTS products (
          id         INTEGER PRIMARY KEY AUTOINCREMENT,
          key        TEXT NOT NULL UNIQUE,
          name       TEXT NOT NULL,
          kb_doc_id  TEXT,
          version    TEXT,
          coverage   TEXT,
          rules      TEXT,
          calc_config TEXT,
          source     TEXT
        );
        CREATE TABLE IF NOT EXISTS hospital_list (
          id          INTEGER PRIMARY KEY AUTOINCREMENT,
          list_key    TEXT NOT NULL,
          region      TEXT NOT NULL DEFAULT '',
          province    TEXT NOT NULL DEFAULT '',
          city        TEXT NOT NULL DEFAULT '',
          district    TEXT NOT NULL DEFAULT '',
          nature      TEXT NOT NULL DEFAULT '',
          direct_type TEXT NOT NULL DEFAULT '',
          hospital    TEXT NOT NULL,
          address     TEXT NOT NULL DEFAULT '',
          specialty   TEXT NOT NULL DEFAULT '',
          hours       TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_hospital_list_key ON hospital_list(list_key, city);
        CREATE INDEX IF NOT EXISTS idx_hospital_hospital ON hospital_list(list_key, hospital);
        CREATE TABLE IF NOT EXISTS premium_rates (
          id          INTEGER PRIMARY KEY AUTOINCREMENT,
          product_key TEXT NOT NULL,
          item_key    TEXT NOT NULL,
          item_name   TEXT,
          dims        TEXT NOT NULL DEFAULT '{}',
          age_min     INTEGER NOT NULL,
          age_max     INTEGER NOT NULL,
          premium     REAL,
          unit        TEXT,
          source      TEXT,
          section     TEXT,
          UNIQUE(product_key, item_key, dims, age_min, age_max)
        );
        CREATE INDEX IF NOT EXISTS idx_rate_lookup ON premium_rates(product_key, item_key, age_min, age_max);
        """

    def _mysql_ddl(self) -> None:
        """MySQL DDL:key 保留字反引号;唯一的 (product_key,item_key,dims,age_min,age_max) 要求各列够短
        (utf8mb4 索引前缀上限 3072 字节,故 product_key/item_key/dims 用短的 VARCHAR)。"""
        pk = self._q("key")
        stmts = [
            f"CREATE TABLE IF NOT EXISTS products ("
            f"  id INTEGER PRIMARY KEY AUTO_INCREMENT,"
            f"  {pk} VARCHAR(191) NOT NULL UNIQUE,"
            f"  name TEXT NOT NULL,"
            f"  kb_doc_id VARCHAR(191),"
            f"  version VARCHAR(64),"
            f"  coverage TEXT,"
            f"  rules TEXT,"
            f"  calc_config TEXT,"
            f"  source TEXT)",
            "CREATE TABLE IF NOT EXISTS hospital_list ("
            "  id INTEGER PRIMARY KEY AUTO_INCREMENT,"
            "  list_key VARCHAR(191) NOT NULL,"
            "  region VARCHAR(64) NOT NULL DEFAULT '',"
            "  province VARCHAR(191) NOT NULL DEFAULT '',"
            "  city VARCHAR(191) NOT NULL DEFAULT '',"
            "  district VARCHAR(191) NOT NULL DEFAULT '',"
            "  nature VARCHAR(64) NOT NULL DEFAULT '',"
            "  direct_type VARCHAR(64) NOT NULL DEFAULT '',"
            "  hospital VARCHAR(255) NOT NULL,"
            "  address TEXT NULL,"
            "  specialty TEXT NULL,"
            "  hours TEXT NULL)",
            "CREATE INDEX idx_hospital_list_key ON hospital_list(list_key, city)",
            "CREATE INDEX idx_hospital_hospital ON hospital_list(list_key, hospital)",
            f"CREATE TABLE IF NOT EXISTS premium_rates ("
            f"  id INTEGER PRIMARY KEY AUTO_INCREMENT,"
            f"  product_key VARCHAR(64) NOT NULL,"
            f"  item_key VARCHAR(64) NOT NULL,"
            f"  item_name TEXT,"
            f"  dims VARCHAR(300) NOT NULL,"
            f"  age_min INT NOT NULL,"
            f"  age_max INT NOT NULL,"
            f"  premium DOUBLE,"
            f"  unit VARCHAR(64),"
            f"  source TEXT,"
            f"  section TEXT,"
            f"  UNIQUE(product_key, item_key, dims, age_min, age_max))",
            "CREATE INDEX idx_rate_lookup ON premium_rates(product_key, item_key, age_min, age_max)",
        ]
        for stmt in stmts:
            try:
                self.conn.execute(stmt)
            except Exception as e:
                if getattr(e, "args", [None])[0] == 1061:
                    continue
                raise

    def _init_schema(self):
        if self._is_mysql:
            self._mysql_ddl()
        else:
            self.conn.executescript(self._ddl())
        self._migrate_product_hospital_col()
        self.conn.commit()

    def _migrate_product_hospital_col(self):
        """给已存在的 products 表补 hospital_list_key 列(fail-safe:列已存在则跳过 + 不抛异常)。
        背景:products 用 CREATE IF NOT EXISTS,历史库无该列;需显式 ALTER 迁移(MySQL8.0.16 不支持 ADD IF NOT EXISTS)。"""
        try:
            if self._is_mysql:
                cols = {r["Field"] for r in self.conn.execute("SHOW COLUMNS FROM products").fetchall()}
                if "hospital_list_key" not in cols:
                    self.conn.execute("ALTER TABLE products ADD COLUMN hospital_list_key VARCHAR(191) NULL")
            else:
                cols = {r[1] for r in self.conn.execute("PRAGMA table_info(products)").fetchall()}
                if "hospital_list_key" not in cols:
                    self.conn.execute("ALTER TABLE products ADD COLUMN hospital_list_key TEXT")
        except Exception as e:  # noqa: BLE001
            logger.warning("migrate products.hospital_list_key failed(忽略): %s", e)

    def upsert_product(self, key, name, kb_doc_id, version, coverage, rules, calc_config, source):
        pk = self._q("key")
        if self._is_mysql:
            self.conn.execute(
                f"""INSERT INTO products({pk}, name, kb_doc_id, version, coverage, rules, calc_config, source)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON DUPLICATE KEY UPDATE name=VALUES(name), kb_doc_id=VALUES(kb_doc_id),
                     coverage=VALUES(coverage), rules=VALUES(rules), calc_config=VALUES(calc_config)""",
                (key, name, kb_doc_id, version, coverage, rules,
                 json.dumps(calc_config, ensure_ascii=False).replace("\n", ""), source))
        else:
            self.conn.execute(
                """INSERT INTO products(key, name, kb_doc_id, version, coverage, rules, calc_config, source)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(key) DO UPDATE SET name=excluded.name, kb_doc_id=excluded.kb_doc_id,
                     coverage=excluded.coverage, rules=excluded.rules, calc_config=excluded.calc_config""",
                (key, name, kb_doc_id, version, coverage, rules,
                 json.dumps(calc_config, ensure_ascii=False).replace("\n", ""), source))
        self.conn.commit()
        logger.info("【费率】产品已保存:key=%s(版本=%s)", key, version,
                    extra={"op": "premium.upsert_product", "key": key})

    def list_products(self) -> list[dict]:
        """返回 products 表全部产品 [{key, name}]。供上传页/检索产品下拉 + 模糊检索。只读。"""
        pk = self._q("key")
        rows = self.conn.execute(f"SELECT {pk} AS k, name FROM products ORDER BY k").fetchall()
        return [{"key": r["k"] or "", "name": r["name"] or ""} for r in rows]

    def register_product(self, key: str, name: str = "") -> None:
        """注册/确保产品存在(供上传时自定义新产品名自动入表成为下拉项;名与 key 同值的最简登记)。"""
        key = (key or "").strip()
        if not key:
            return
        pk = self._q("key")
        if self._is_mysql:
            self.conn.execute(
                f"INSERT IGNORE INTO products({pk}, name, source) VALUES(?,?,?)",
                (key, name or key, "kb-upload-registered"))
        else:
            self.conn.execute(
                "INSERT OR IGNORE INTO products(`key`, name, source) VALUES(?,?,?)",
                (key, name or key, "kb-upload-registered"))
        self.conn.commit()

    def upsert_rate(self, product_key, item_key, item_name, dims, age_min, age_max,
                    premium, unit, source, section):
        d = json.dumps(dims, sort_keys=True, ensure_ascii=False)
        if self._is_mysql:
            self.conn.execute(
                """INSERT INTO premium_rates(product_key, item_key, item_name, dims, age_min, age_max,
                     premium, unit, source, section)
                   VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON DUPLICATE KEY UPDATE premium=VALUES(premium), item_name=VALUES(item_name), unit=VALUES(unit)""",
                (product_key, item_key, item_name, d, age_min, age_max, premium, unit, source, section))
        else:
            self.conn.execute(
                """INSERT INTO premium_rates(product_key, item_key, item_name, dims, age_min, age_max,
                     premium, unit, source, section)
                   VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(product_key, item_key, dims, age_min, age_max)
                   DO UPDATE SET premium=excluded.premium, item_name=excluded.item_name, unit=excluded.unit""",
                (product_key, item_key, item_name, d, age_min, age_max, premium, unit, source, section))
        self.conn.commit()
        logger.info("【费率】费率已保存:产品=%s,项目=%s,年龄=%s-%s", product_key, item_key, age_min, age_max,
                    extra={"op": "premium.upsert_rate", "key": product_key})
        row = self.conn.execute(
            """SELECT * FROM premium_rates WHERE product_key=? AND item_key=? AND dims=? AND age_min=? AND age_max=?""",
            (product_key, item_key, d, age_min, age_max)).fetchone()
        out = dict(row)
        out["dims"] = json.loads(out["dims"])
        return out

    def get_rate(self, product_key, item_key, dims, age):
        cur = self.conn.execute(
            "SELECT * FROM premium_rates WHERE product_key=? AND item_key=? AND ?>=age_min AND ?<=age_max",
            (product_key, item_key, age, age))
        for row in cur.fetchall():
            if json.loads(row["dims"]) == dims:
                d = dict(row)
                d["dims"] = json.loads(d["dims"])
                return d
        # 精确匹配未命中 → 对 dims 值做确定性归一化(数字→中文档位/去单位/千分位)兜底重查,
        # 命中率费表真实存在的那一档才返回;仍无则 None(防"数字档位 vs 字符串档位"误判为无费率)。
        # 安全性:候选仅由费率表已有维度值组合触发,不会臆造出表里不存在的方案。
        for cand in _dims_candidates(dims):
            cur = self.conn.execute(
                "SELECT * FROM premium_rates WHERE product_key=? AND item_key=? AND ?>=age_min AND ?<=age_max",
                (product_key, item_key, age, age))
            for row in cur.fetchall():
                if json.loads(row["dims"]) == cand:
                    d = dict(row)
                    d["dims"] = json.loads(d["dims"])
                    return d
        return None

    def get_rate_by_id(self, rid):
        """按费率行主键 id 取行(供引用层校验:calculate_premium 的引用 chunk_id=premium_rates.id)。"""
        cur = self.conn.execute("SELECT * FROM premium_rates WHERE id=?", (int(rid),))
        row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["dims"] = json.loads(d["dims"])
        return d

    def upsert_hospital(self, list_key, region, province, city, district,
                        nature, direct_type, hospital, address, specialty, hours):
        """插入一家直付医院(普通 INSERT;幂等由 loader 先删后插保证)。只 INSERT,不删历史。"""
        self.conn.execute(
            """INSERT INTO hospital_list(list_key, region, province, city, district, nature, direct_type, hospital, address, specialty, hours)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (list_key, region, province, city, district, nature, direct_type, hospital, address, specialty, hours))
        return None

    def clear_hospital_list(self, list_key: str) -> None:
        """清空某清单后再重灌(loader 幂等)。"""
        self.conn.execute("DELETE FROM hospital_list WHERE list_key=?", (list_key,))
        self.conn.commit()
        logger.info("【费率】医院清单已清空:%s", list_key,
                    extra={"op": "premium.clear_hospital_list", "key": list_key})

    def list_hospitals(self, list_key: str, city: str = "", hospital: str = "",
                       limit: int = 200) -> list[dict]:
        """按清单查询直付医院。city/hospital 任一非空做 LIKE 匹配(城市精确、机构模糊);
        都空则返回该清单前 limit 条(供概述覆盖范围)。只读事实源。
        返回 [{list_key, region, province, city, hospital, address, specialty, hours, ...}]。"""
        where = ["list_key=?"]
        params: list[str] = [list_key]
        if city.strip():
            where.append("city=?")
            params.append(city.strip())
        if hospital.strip():
            where.append("hospital LIKE ?")
            params.append("%" + hospital.strip() + "%")
        try:
            params.append(int(limit))
        except ValueError:
            limit = 200
            params[len(params) - 1] = 200
        sql = ("SELECT * FROM hospital_list WHERE " + " AND ".join(where)
               + " ORDER BY province, city, id LIMIT ?")
        cur = self.conn.execute(sql, tuple(params))
        return [dict(r) for r in cur.fetchall()]

    def hospital_cities(self, list_key: str, limit: int = 50) -> list[dict]:
        """概述:某清单覆盖的城市/地区分布(国内容前了 LIMIT 内按省聚合,境外按 region)。"""
        if self._is_mysql:
            rows = self.conn.execute(
                "SELECT region, province, city, COUNT(*) n FROM hospital_list "
                "WHERE list_key=? GROUP BY region, province, city ORDER BY n DESC LIMIT ?",
                (list_key, limit)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT region, province, city, COUNT(*) n FROM hospital_list "
                "WHERE list_key=? GROUP BY region, province, city ORDER BY n DESC LIMIT ?",
                (list_key, limit)).fetchall()
        return [dict(r) for r in rows]

    def bind_product_hospital(self, product_key: str, list_key: str) -> None:
        """给产品绑定其直付医院清单(每产品最多一份,upsert)。"""
        pk = self._q("key")
        if self._is_mysql:
            self.conn.execute(
                f"UPDATE products SET hospital_list_key=? WHERE {pk}=?",
                (list_key, product_key))
        else:
            self.conn.execute(
                "UPDATE products SET hospital_list_key=? WHERE `key`=?",
                (list_key, product_key))
        self.conn.commit()

    def get_product(self, ref):
        """按 key 或 name 查产品。"""
        ref = (ref or "").strip()
        if not ref:
            return None
        pk = self._q("key")
        row = self.conn.execute(f"SELECT * FROM products WHERE {pk}=? OR name=?", (ref, ref)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["calc_config"] = json.loads(d["calc_config"]) if d.get("calc_config") else {}
        return d

    def close(self):
        self.conn.close()


CALCULATORS = {}


def register(product_key):
    def deco(fn):
        CALCULATORS[product_key] = fn
        return fn
    return deco


@register(PRODUCT_XX)
def _calc_xx2025(store, product_key, age, items, family_member_count=1):
    lines, rows, total = [], [], 0.0
    for it in items:
        item_key = it.get("item_key")
        dims = it.get("dims") or {}
        coverage = it.get("coverage")
        row = store.get_rate(product_key, item_key, dims, age)
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
        lines.append(f"{row['item_name']}{_fmt_dims(dims)}: {amount:,.2f}元/年 [{len(rows)+1}]")
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
    ref_name = (args.get("product") or "").strip()
    if not ref_name:
        # 缺产品是常见错误(产品不明确就该先问)。给出可操作清单,让模型能照着实答/转述,而不是空泛的"请指定产品"。
        avail = "、".join(available_product_keys())
        return {"content": f"请指定产品(product,填产品 key 或名称)。当前在售:{avail}。" if avail
                else "请指定产品(product,填产品 key 或名称)", "reference": []}
    prod = store.get_product(ref_name)
    if not prod:
        return {"content": f"产品 {ref_name} 不存在/暂无费率", "reference": []}
    key = prod["key"]
    calc = CALCULATORS.get(key)
    if not calc:
        return {"content": f"产品 {ref_name} 暂无保费计算配置", "reference": []}
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
    content, store_rows = calc(store, key, age, items, family)
    if not store_rows and items:
        # 全部方案都未命中费率:明确标"未查到费率",禁止模型据此臆造保额/保费数字。
        content += ("\n\n【费用提示】所有方案均未命中费率表,请勿臆造任何保费/金额数字;"
                    "如实告知用户当前无法给出精确保费,并请其确认投保计划档位后重算,或转人工报价。")
    ref = []
    for r in store_rows:
        ref.append({
            "chunk_id": str(r["id"]), "score": None,
            "doc_id": key, "version": prod.get("version", ""),
            "section": r.get("section") or r.get("item_name", ""),
            "source": r.get("source", ""),
            "content": f"{r['item_name']}{_fmt_dims(r['dims'])}: {r['premium']}元/年({r['unit']})",
        })
    return {"content": content, "reference": ref}


def build_premium_tool(store):
    schema = {"type": "function", "function": {
        "name": "calculate_premium",
        "description": "按产品/投保年龄/方案计算年缴保费(查表确定性)。items=[{item_key,dims?,coverage?}]。item_key/dims:尊享e生2025→必选 plan:{deductible:'0元|1.5万|3万',plan_variant:'计划一|计划二'};加油包 family_deductible/clinic_a/clinic_b/drug:{},critical:{gender:'男|女'}且 coverage=保额(每5万保额);安盛天平卓越馨选2025→住院 hospital:{deductible:'0元|5000元|10000元|15000元|20000元',tier:'普A|普B|普C|特A|特B|特C',social:'有社保|无社保'},门急诊 outpatient:{deductible:'0元|200元|500元|1300元',coverage:'1万|1.5万|2万|3.5万',social:'有社保|无社保'},重疾津贴 majordaily:{version:'普通版|特需版',plan:'A|B|C'},重疾保险金 majorsum:{version:'普通版|特需版'},博鳌 boao:{type:'特药械|院外特定药品|特定医疗器械',social:'有社保|无社保|有/无社保'}。product 传 key 或名称(如 尊享e生2025 / 安盛天平卓越馨选2025);family_member_count≥2 享家庭单折扣。返回可读账单+可溯源引用。对比两方案/两口径(如 有/无社保)请调本工具分别算再比。",
        "parameters": {"type": "object", "properties": {
            "product": {"type": "string", "description": "产品 key 或名称"},
            "age": {"type": "integer", "description": "投保年龄(周岁)"},
            "items": {"type": "array", "description": "要计算的方案/包,可多选",
                      "items": {"type": "object", "properties": {
                          "item_key": {"type": "string"},
                          "dims": {"type": "object", "description": "按 description 中该 item_key 的 dims 构造,档位值必须是引号内的字符串字面量(如 \"0元\"/\"15000元\" 而非数字 15000),name 带\"元/万\"单价位的用中文档位串(如 \"1.5万\"),勿传数字或千分位字符串"},
                          "coverage": {"type": "number", "description": "保额(元),仅 critical 需传"},
                      }, "required": ["item_key"]}},
            "family_member_count": {"type": "integer", "description": "家庭单成员数;2人95折,≥3人9折"},
        }, "required": ["product", "age", "items"]}}}

    def handler(args, start_idx=0, session_id=None):
        res = calculate_premium(store, args or {})
        ref = res.get("reference") or []
        # D55:引用编号每轮 turn-local —— 结果行自带 [idx](start_idx 对齐轮内连续编号),
        # 与 loop 按当轮 references 平铺解析的编号一致(去掉了 D35 的全局重排覆盖)。
        if ref:
            numbered = "\n".join(f"[{i}] ({c['chunk_id']}) {c['content']}" for i, c in enumerate(ref, start_idx + 1))
            res["content"] = (res.get("content") or "") + "\n\n【费用逐项编号(回答需引用时可标 [idx])】\n" + numbered
        return res

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
            store.upsert_rate(product_key=PRODUCT_XX, item_key="plan",
                              item_name=f"必选计划({deductible}年免赔额,{plan})",
                              dims={"deductible": deductible, "plan_variant": plan},
                              age_min=age_min, age_max=age_max, premium=_num(row[i + 1]),
                              unit="元/年", source=source, section="必选计划费率表")
            n += 1
    ws = wb["加油包费率表"]
    for row in ws.iter_rows(min_row=6, values_only=True):
        label = row[0]
        if label is None or not str(label).strip().startswith("["):
            continue
        age_min, age_max = _age_range(str(label))
        for i, (item_key, item_name, dims, unit) in enumerate(_XX_ADDON_COLS):
            store.upsert_rate(product_key=PRODUCT_XX, item_key=item_key, item_name=item_name, dims=dims,
                              age_min=age_min, age_max=age_max, premium=_num(row[i + 1]),
                              unit=unit, source=source, section="加油包费率表")
            n += 1
    store.upsert_product(
        key=PRODUCT_XX, name=PRODUCT_XX_NAME, kb_doc_id=PRODUCT_XX, version=VERSION_XX,
        coverage=("一般医疗(对应年免赔额)+特定疾病医疗+外购药品及外购医疗器械费用医疗+特定药品费用医疗"
                  "+恶性肿瘤先进疗法医疗+特定疾病异地转诊公共交通费用及住宿费用+特定疾病住院津贴"
                  "+意外紧急牙齿门急诊医疗费用+全球紧急救援服务。"),
        rules=("首次投保年龄:出生满30天-70周岁;71周岁及以上仅限保单期满指定期限内重新投保;"
               "投保保单数量=1,个人单标准保费。家庭单优享:同投保人 尊享/众民保 系列家庭成员 2人享95折,3人及以上享9折。"),
        calc_config={"mandatory": ["plan"],
                     "optional": ["family_deductible", "clinic_a", "clinic_b", "drug", "critical"],
                     "unit_map": {"critical": 50000},
                     "discounts": {"family": [[2, 0.95], [3, 0.90]]},
                     "item_dims": {"plan": ["deductible", "plan_variant"], "critical": ["gender"]}},
        source=source)
    return n


HOSPITAL_DTH_202506 = "DTH-202506"   # 直付网络医院清单标识(尊享e生2025 绑定)


def load_hospital_list_xlsx(store, xlsx_path, list_key: str) -> dict:
    """从直付医院清单 xlsx 灌入 hospital_list(先删后插,幂等)。

    结构:['说明','国内网络医院','境外网络医院']。国内 9 列(省份/城市/区县/性质/直付类型/机构/地址/特色科室/开放时间),
    境外 4 列(地区/省份/城市/机构)。统一宽表存储:境外 region=境外、区县/性质/直付/地址/特色/开放均留空。
    Returns: {list_key, total, domestic, overseas}"""
    import openpyxl
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    store.clear_hospital_list(list_key)
    dom = oversea = 0
    # ---- 国内 ----
    ws = wb["国内网络医院"]
    for row in ws.iter_rows(min_row=3, values_only=True):
        cells = ["" if v is None else str(v).strip() for v in row]
        hospital = cells[5] if len(cells) > 5 else ""
        if not hospital or hospital in ("医疗机构", "None"):
            continue
        store.upsert_hospital(
            list_key=list_key, region="国内",
            province=cells[0] if len(cells) > 0 else "",
            city=cells[1] if len(cells) > 1 else "",
            district=cells[2] if len(cells) > 2 else "",
            nature=cells[3] if len(cells) > 3 else "",
            direct_type=cells[4] if len(cells) > 4 else "",
            hospital=hospital,
            address=cells[6] if len(cells) > 6 else "",
            specialty=cells[7] if len(cells) > 7 else "",
            hours=cells[8] if len(cells) > 8 else "")
        dom += 1
    # ---- 境外 ----
    ws = wb["境外网络医院"]
    for row in ws.iter_rows(min_row=3, values_only=True):
        cells = ["" if v is None else str(v).strip() for v in row]
        hospital = cells[3] if len(cells) > 3 else ""
        if not hospital or hospital in ("医疗机构", "None"):
            continue
        store.upsert_hospital(
            list_key=list_key, region="境外",
            province=cells[1] if len(cells) > 1 else "",
            city=cells[2] if len(cells) > 2 else "",
            district="", nature="", direct_type="",
            hospital=hospital,
            address="", specialty="", hours="")
        oversea += 1
    store.conn.commit()
    return {"list_key": list_key, "total": dom + oversea, "domestic": dom, "overseas": oversea}


def query_hospital(store, args):
    """按产品+城市/机构 查直付医院清单(结构化精确查询,事实源只读)。"""
    product = (args.get("product") or "").strip()
    if not product:
        avail = "、".join(available_product_keys())
        return {"content": f"请指定产品(product,填产品 key 或名称)。当前在售:{avail}。", "reference": []}
    prod = store.get_product(product)
    if not prod:
        return {"content": f"产品 {product} 不存在/暂无直付医院清单", "reference": []}
    list_key = (prod.get("hospital_list_key") or "").strip()
    if not list_key:
        return {"content": f"产品 {product} 暂无绑定直付医院清单", "reference": []}
    city = (args.get("city") or "").strip()
    hospital = (args.get("hospital") or "").strip()
    limit = int(args.get("limit") or 100)
    if limit < 1:
        limit = 100
    if city or hospital:
        rows = store.list_hospitals(list_key, city=city, hospital=hospital, limit=limit)
    else:
        rows = store.list_hospitals(list_key, limit=limit)
    if not rows:
        return {"content": f"未查到 {product} 符合条件的直付医院(城市={city or '不限'})。请核对城市/机构名,或提供城市后重查。",
                "reference": []}
    lines = [f"{product} 直付医院清单({list_key}):"]
    for r in rows:
        loc = f"{r.get('region','')} {r.get('province','')} {r.get('city','')} {r.get('district','')}".replace("  ", " ").strip()
        dt = f"({r.get('direct_type','')})" if r.get("direct_type") else ""
        lines.append(f"- {r.get('hospital')}{dt} · {loc}")
        if r.get("address"):
            lines.append(f"  地址:{r.get('address')}")
    if len(rows) >= limit:
        lines.append(f"(已显示前 {limit} 条,可再用 city/hospital 收窄)")
    return {"content": "\n".join(lines), "reference": []}


def build_hospital_tool(store):
    schema = {"type": "function", "function": {
        "name": "query_hospital",
        "description": ("按产品查其直付医院清单(结构化精确查询)。product 传产品 key 或名称;city 传城市(如 北京/上海);"
                        "hospital 传机构名(模糊)。当客户问\"某产品在某城市有没有/有哪些直付医院\"时调用。"
                        "不传 city/hospital 返回该清单覆盖范围/前若干家(供概述)。只读。"),
        "parameters": {"type": "object", "properties": {
            "product": {"type": "string", "description": "产品 key 或名称(如 尊享e生2025)"},
            "city": {"type": "string", "description": "城市(如 北京/上海);可空"},
            "hospital": {"type": "string", "description": "机构名,模糊;可空"},
            "limit": {"type": "integer", "description": "返回条数上限(默认100)"},
        }, "required": ["product"]}}}

    def handler(args, start_idx=0, session_id=None):
        res = query_hospital(store, args or {})
        return res

    return {"schema": schema, "handler": handler}
