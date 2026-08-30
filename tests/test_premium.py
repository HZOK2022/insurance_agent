# -*- coding: utf-8 -*-
"""保费计算(app/businesses/premium.py)测试:PremiumStore 查表 + calculate_premium 计算(多包/每5万保额/家庭折扣/不适用/未知产品)。"""
import os
import tempfile
import unittest

from app.businesses.premium import (
    PremiumStore, calculate_premium, load_xx2025_xlsx, PRODUCT_XX,
)

XX_XLSX = "C:/Users/mi/Desktop/个人/files/尊享e生2025/尊享e生·中高端医疗保险PLUS（2025版）年缴费率表.xlsx"


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = PremiumStore(os.path.join(self.dir, "p.db"))
        self._seed()

    def tearDown(self):
        self.store.close()

    def _seed(self):
        st = self.store
        st.upsert_rate(PRODUCT_XX, "plan", "必选计划(0元年免赔额,计划一)",
                       {"deductible": "0元", "plan_variant": "计划一"}, 26, 30, 2312.0, "元/年", "x", "必选计划费率表")
        st.upsert_rate(PRODUCT_XX, "plan", "必选计划(3万年免赔额,计划二)",
                       {"deductible": "3万", "plan_variant": "计划二"}, 26, 30, 1244.0, "元/年", "x", "必选计划费率表")
        st.upsert_rate(PRODUCT_XX, "critical", "重疾加油包（每5万保额）",
                       {"gender": "男"}, 26, 30, 56.0, "元/每5万保额", "x", "加油包费率表")
        st.upsert_rate(PRODUCT_XX, "clinic_a", "门急诊加油包A-不含器质",
                       {}, 26, 30, 6055.0, "元/年", "x", "加油包费率表")
        st.upsert_rate(PRODUCT_XX, "clinic_a", "门急诊加油包A-不含器质",
                       {}, 81, 85, None, "元/年", "x", "加油包费率表")
        st.upsert_product(PRODUCT_XX, "尊享 e 生·中高端医疗保险 PLUS（2025版）（年缴版）", PRODUCT_XX,
                          "v2025", "coverage", "rules", {"dummy": 1}, "x")


class PremiumStoreTest(_Base):
    def test_get_rate_matches_age_dims(self):
        r = self.store.get_rate(PRODUCT_XX, "plan", {"deductible": "0元", "plan_variant": "计划一"}, 30)
        self.assertIsNotNone(r)
        self.assertEqual(r["premium"], 2312.0)

    def test_get_rate_dims_not_cross(self):
        r = self.store.get_rate(PRODUCT_XX, "plan", {"deductible": "3万", "plan_variant": "计划二"}, 30)
        self.assertEqual(r["premium"], 1244.0)

    def test_get_rate_not_found(self):
        self.assertIsNone(self.store.get_rate(PRODUCT_XX, "clinic_b", {}, 30))


class PremiumCalcTest(_Base):
    def test_plan_only(self):
        res = calculate_premium(self.store, {"product": PRODUCT_XX, "age": 30,
                                             "items": [{"item_key": "plan", "dims": {"deductible": "0元", "plan_variant": "计划一"}}]})
        self.assertIn("2,312.00", res["content"])

    def test_multi_and_per_unit(self):
        res = calculate_premium(self.store, {"product": PRODUCT_XX, "age": 30, "items": [
            {"item_key": "plan", "dims": {"deductible": "0元", "plan_variant": "计划一"}},
            {"item_key": "critical", "dims": {"gender": "男"}, "coverage": 100000}]})
        self.assertIn("112.00", res["content"])
        self.assertIn("2,424.00", res["content"])
        self.assertEqual(len(res["reference"]), 2)
        self.assertTrue(all("chunk_id" in r and "doc_id" in r for r in res["reference"]))

    def test_family_discount_3(self):
        res = calculate_premium(self.store, {"product": PRODUCT_XX, "age": 30,
                                             "items": [{"item_key": "plan", "dims": {"deductible": "0元", "plan_variant": "计划一"}}],
                                             "family_member_count": 3})
        self.assertIn("2,080.80", res["content"])
        self.assertIn("90折", res["content"])

    def test_family_discount_2(self):
        res = calculate_premium(self.store, {"product": PRODUCT_XX, "age": 30,
                                             "items": [{"item_key": "plan", "dims": {"deductible": "0元", "plan_variant": "计划一"}}],
                                             "family_member_count": 2})
        self.assertIn("2,196.40", res["content"])

    def test_not_applicable(self):
        res = calculate_premium(self.store, {"product": PRODUCT_XX, "age": 83, "items": [{"item_key": "clinic_a"}]})
        self.assertIn("不适用", res["content"])

    def test_unknown_product(self):
        res = calculate_premium(self.store, {"product": "未知产品", "age": 30, "items": [{"item_key": "plan"}]})
        self.assertIn("不存在", res["content"])

    def test_missing_items(self):
        res = calculate_premium(self.store, {"product": PRODUCT_XX, "age": 30, "items": []})
        self.assertIn("至少指定", res["content"])


class XlsxLoaderTest(_Base):
    def test_load_xlsx_inserts_276(self):
        if not os.path.exists(XX_XLSX):
            self.skipTest("xlsx 不在本机")
        st = PremiumStore(os.path.join(self.dir, "full.db"))
        n = load_xx2025_xlsx(st, XX_XLSX)
        self.assertEqual(n, 276)
        cnt = st.conn.execute("SELECT COUNT(*) FROM premium_rates").fetchone()[0]
        self.assertEqual(cnt, 276)
        r = st.get_rate(PRODUCT_XX, "plan", {"deductible": "0元", "plan_variant": "计划一"}, 30)
        self.assertEqual(r["premium"], 2312.0)
        st.close()
