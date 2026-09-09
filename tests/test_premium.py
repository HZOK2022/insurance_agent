# -*- coding: utf-8 -*-
"""保费计算(app/businesses/premium.py)测试:PremiumStore 查表 + calculate_premium 计算(多包/每5万保额/家庭折扣/不适用/未知产品)。"""
import os
import tempfile
import unittest

from app.businesses.premium import (
    PremiumStore, calculate_premium, load_xx2025_xlsx, PRODUCT_XX,
    query_hospital, build_hospital_tool, load_hospital_list_xlsx,
)
from app.businesses.premium_ax import PRODUCT_AX, load_ax2025_xlsx

AX_XLSX = "C:/Users/mi/Desktop/个人/files/安盛天平卓越馨选(A款)/卓越馨选费率（2025版）（互联网专属）费率表--新保.xlsx"

XX_XLSX = "C:/Users/mi/Desktop/个人/files/尊享e生2025/尊享e生·中高端医疗保险PLUS（2025版）年缴费率表.xlsx"

HOSPITAL_XLSX = "C:/Users/mi/Desktop/个人/files/直付网络医院清单-DTH-202506.xlsx"


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

    def test_numeric_deductible_normalized(self):
        # 模型把档位传成数字(0 而非 "0元") → get_rate 应归一化命中费率
        res = calculate_premium(self.store, {"product": PRODUCT_XX, "age": 30,
                                             "items": [{"item_key": "plan", "dims": {"deductible": 0, "plan_variant": "计划一"}}]})
        self.assertIn("2,312.00", res["content"])  # 命中 0元/计划一 档
        self.assertEqual(len(res["reference"]), 1)

    def test_wan_deductible_normalized(self):
        # 数字 30000 → "3万" 归一化命中第二档
        res = calculate_premium(self.store, {"product": PRODUCT_XX, "age": 30,
                                             "items": [{"item_key": "plan", "dims": {"deductible": 30000, "plan_variant": "计划二"}}]})
        self.assertIn("1,244.00", res["content"])
        self.assertEqual(len(res["reference"]), 1)

    def test_all_missing_marks_no_fabricate(self):
        # 全部方案未命中费率 → 明确提示禁止臆造,防止模型编造数字
        res = calculate_premium(self.store, {"product": PRODUCT_XX, "age": 30,
                                             "items": [{"item_key": "clinic_b", "dims": {}}]})
        self.assertIn("未命中费率表", res["content"])
        self.assertIn("请勿臆造", res["content"])
        self.assertEqual(res["reference"], [])


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

class AxCalcTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = PremiumStore(os.path.join(self.dir, "ax.db"))
        self.store.upsert_product(PRODUCT_AX, "安盛天平卓越馨选（A款）（互联网专属）医疗保险", PRODUCT_AX,
                                  "v2025", "cov", "rules", {"mandatory": ["hospital"]}, "x")
        self.store.upsert_rate(PRODUCT_AX, "hospital", "一般住院+重疾住院(免赔0元)",
                               {"deductible": "0元", "tier": "普A", "social": "有社保"}, 30, 30, 660.0, "元/年", "x", "住院医疗-免赔0元")
        self.store.upsert_rate(PRODUCT_AX, "outpatient", "门急诊(免赔0元,保额1万)",
                               {"deductible": "0元", "coverage": "1万", "social": "有社保"}, 30, 30, 1132.0, "元/年", "x", "门急诊医疗-免赔0元")

    def tearDown(self):
        self.store.close()

    def test_ax_calc_lookup(self):
        res = calculate_premium(self.store, {"product": PRODUCT_AX, "age": 30, "items": [
            {"item_key": "hospital", "dims": {"deductible": "0元", "tier": "普A", "social": "有社保"}},
            {"item_key": "outpatient", "dims": {"deductible": "0元", "coverage": "1万", "social": "有社保"}}]})
        self.assertIn("660.00", res["content"])
        self.assertIn("1,132.00", res["content"])
        self.assertIn("1,792.00", res["content"])

    def test_ax_calc_by_name(self):
        # product 参数传名称也能解析(安盛天平...)
        res = calculate_premium(self.store, {"product": "安盛天平卓越馨选（A款）（互联网专属）医疗保险", "age": 30,
                                             "items": [{"item_key": "hospital", "dims": {"deductible": "0元", "tier": "普A", "social": "有社保"}}]})
        self.assertIn("660.00", res["content"])


class AxXlsxLoaderTest(unittest.TestCase):
    def test_load_ax_xlsx_6240(self):
        if not os.path.exists(AX_XLSX):
            self.skipTest("ax xlsx 不在本机")
        st = PremiumStore(os.path.join(tempfile.mkdtemp(), "a.db"))
        n = load_ax2025_xlsx(st, AX_XLSX)
        self.assertEqual(n, 6240)
        cnt = st.conn.execute("SELECT COUNT(*) FROM premium_rates").fetchone()[0]
        self.assertEqual(cnt, 6240)
        r = st.get_rate(PRODUCT_AX, "hospital", {"deductible": "0元", "tier": "普A", "social": "有社保"}, 30)
        self.assertEqual(r["premium"], 660.0)
        st.close()


class HospitalListTest(unittest.TestCase):
    """直付医院清单(hospital_list)存取与 query_hospital 工具测试。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = PremiumStore(os.path.join(self.dir, "p.db"))
        self.store.upsert_product(PRODUCT_XX, "尊享 e 生·中高端医疗保险 PLUS（2025版）（年缴版）", PRODUCT_XX,
                                  "v2025", "cov", "rules", {"dummy": 1}, "x")

    def tearDown(self):
        self.store.close()

    def _seed(self):
        self.store.upsert_hospital("DTH-T", "国内", "上海市", "上海市", "浦东新区", "公立", "预约直付",
                                   "测试三甲医院", "浦东路1号", "心内科", "周一至周五")
        self.store.upsert_hospital("DTH-T", "国内", "北京市", "北京市", "朝阳区", "公立", "见卡直付",
                                   "北京测试医院", "朝阳路2号", "骨科", "")
        self.store.upsert_hospital("DTH-T", "境外", "中国", "香港", "中环", "", "",
                                   "明德医疗中心", "", "", "")

    def test_upsert_and_list_by_city(self):
        self._seed()
        rows = self.store.list_hospitals("DTH-T", city="上海市")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["hospital"], "测试三甲医院")

    def test_list_by_hospital_fuzzy(self):
        self._seed()
        rows = self.store.list_hospitals("DTH-T", hospital="测试")
        self.assertEqual(len(rows), 2)

    def test_list_all_limit(self):
        self._seed()
        rows = self.store.list_hospitals("DTH-T")
        self.assertEqual(len(rows), 3)

    def test_clear_then_reload(self):
        self._seed()
        self.store.clear_hospital_list("DTH-T")
        self.assertEqual(self.store.list_hospitals("DTH-T"), [])

    def test_bind_and_unbound(self):
        self.store.bind_product_hospital(PRODUCT_XX, "DTH-T")
        prod = self.store.get_product(PRODUCT_XX)
        self.assertIsNotNone(prod)
        self.assertEqual(prod.get("hospital_list_key"), "DTH-T")

    def test_query_hospital_unbound(self):
        res = query_hospital(self.store, {"product": PRODUCT_XX, "city": "上海市"})
        self.assertIn("暂无绑定", res["content"])

    def test_query_hospital_by_city(self):
        self._seed()
        self.store.bind_product_hospital(PRODUCT_XX, "DTH-T")
        res = query_hospital(self.store, {"product": PRODUCT_XX, "city": "上海市"})
        self.assertIn("测试三甲医院", res["content"])

    def test_query_hospital_no_product(self):
        res = query_hospital(self.store, {})
        self.assertIn("请指定产品", res["content"])

    def test_tool_registered(self):
        tool = build_hospital_tool(self.store)
        self.assertEqual(tool["schema"]["function"]["name"], "query_hospital")
        res = tool["handler"]({"product": PRODUCT_XX})
        self.assertIn("content", res)

    def test_load_real_xlsx_counts(self):
        if not os.path.exists(HOSPITAL_XLSX):
            self.skipTest("xlsx 不在本机")
        st = PremiumStore(os.path.join(self.dir, "h.db"))
        r = load_hospital_list_xlsx(st, HOSPITAL_XLSX, "DTH-UT")
        self.assertEqual(r["total"], 787)
        self.assertEqual(r["domestic"], 602)
        self.assertEqual(r["overseas"], 185)
        st.close()
