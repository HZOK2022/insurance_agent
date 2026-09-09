# -*- coding: utf-8 -*-
"""KnowledgeStore(SQLite chunks 事实源)测试:黄金法则 SQLite=事实源,Qdrant=派生(可重建)。"""
import os
import tempfile
import unittest

from app.retrieval.knowledge_store import KnowledgeStore


class KnowledgeStoreTest(unittest.TestCase):
    def setUp(self):
        self.store = KnowledgeStore(os.path.join(tempfile.mkdtemp(), "k.db"))
        self.chunks = [
            {"chunk_id": "尊享e生2025:0", "content": "条款 0 正文",
             "meta": {"doc_id": "尊享e生2025", "version": "v1", "section": "s0", "doc_type": "policy", "source": "x.pdf", "title": "尊享"}},
            {"chunk_id": "尊享e生2025:1", "content": "条款 1 正文",
             "meta": {"doc_id": "尊享e生2025", "version": "v1", "section": "s1", "doc_type": "policy", "source": "x.pdf", "title": "尊享"}},
        ]

    def tearDown(self):
        self.store.close()

    def test_upsert_and_count(self):
        n = self.store.upsert_chunks(self.chunks)
        self.assertEqual(n, 2)
        self.assertEqual(self.store.count(), 2)

    def test_upsert_is_idempotent(self):
        self.store.upsert_chunks(self.chunks)
        self.store.upsert_chunks(self.chunks)
        self.assertEqual(self.store.count(), 2)   # upsert,不重复

    def test_upsert_updates_content(self):
        self.store.upsert_chunks([{"chunk_id": "尊享e生2025:0", "content": "新版正文",
                                   "meta": {"doc_id": "尊享e生2025", "version": "v2", "section": "s0"}}])
        c = self.store.get_chunk("尊享e生2025:0")
        self.assertEqual(c["content"], "新版正文")
        self.assertEqual(c["meta"]["version"], "v2")

    def test_all_chunks_and_get(self):
        self.store.upsert_chunks(self.chunks)
        allc = self.store.all_chunks()
        self.assertEqual(len(allc), 2)
        self.assertIn("chunk_id", allc[0]) and self.assertIn("content", allc[0]) and self.assertIn("meta", allc[0])
        c = self.store.get_chunk("尊享e生2025:1")
        self.assertEqual(c["content"], "条款 1 正文")

    def test_get_missing(self):
        self.assertIsNone(self.store.get_chunk("不存在"))

    def test_documents_roundtrip_and_dedup(self):
        # D72:documents 表存 doc 级元数据(product_name/content_hash),供同名判重+唯一性
        self.store.upsert_chunks(self.chunks)
        self.store.upsert_document({"doc_id": "尊享e生2025", "product_name": "尊享e生2025",
                                    "product_category": "医疗险", "version": "v1", "title": "尊享e生2025",
                                    "source": "x.pdf", "content_hash": "hash1"})
        d = self.store.get_document("尊享e生2025")
        self.assertEqual(d["product_name"], "尊享e生2025")
        self.assertEqual(d["content_hash"], "hash1")
        # 同名覆盖(update)替换 content_hash
        self.store.upsert_document({"doc_id": "尊享e生2025", "product_name": "尊享e生2025",
                                    "product_category": "医疗险", "version": "v2", "title": "尊享e生2025",
                                    "source": "x.pdf", "content_hash": "hash2"})
        self.assertEqual(self.store.get_document("尊享e生2025")["content_hash"], "hash2")
        # delete_document 连带清理 documents 行
        self.store.delete_document("尊享e生2025")
        self.assertIsNone(self.store.get_document("尊享e生2025"))
        self.assertEqual(self.store.count(), 0)

    def test_backfill_documents_from_chunks(self):
        # 旧库迁移:documents 缺行时按 chunks 回填(product_name=doc_id,content_hash=按内容算)
        self.store.upsert_chunks(self.chunks)
        self.store._backfill_documents()
        d = self.store.get_document("尊享e生2025")
        self.assertIsNotNone(d, "旧库应回填 documents 行")
        self.assertEqual(d["product_name"], "尊享e生2025")
        self.assertTrue(d["content_hash"], "content_hash 非空")
        # 幂等:再跑不重复/不报错
        self.store._backfill_documents()
        self.assertEqual(self.store.get_document("尊享e生2025")["content_hash"], d["content_hash"])

    def test_list_products_dedup_and_skip_empty(self):
        # D95:按已上传文档聚合产品名(去重、非空),供上传页"产品"下拉框
        self.store.upsert_document({"doc_id": "尊享e生2025", "product_name": "尊享e生2025",
                                    "product_category": "医疗险", "version": "v1", "title": "尊享e生2025",
                                    "source": "a", "content_hash": "h1"})
        # 同一产品名另一文档(不同 doc_id 复用同名产品)→ 去重只出现一次
        self.store.upsert_document({"doc_id": "尊享e生2025-客服话术", "product_name": "尊享e生2025",
                                    "product_category": "医疗险", "version": "v1", "title": "话术",
                                    "source": "b", "content_hash": "h2"})
        # 无产品文档(doc_id=通用话术库,product_name 空)→ 不进产品下拉
        self.store.upsert_document({"doc_id": "通用客服话术库", "product_name": "",
                                    "product_category": "其他", "version": "v1", "title": "通用客服话术库",
                                    "source": "c", "content_hash": "h3"})
        self.store.upsert_document({"doc_id": "安盛卓越馨选2025", "product_name": "安盛卓越馨选2025",
                                    "product_category": "医疗险", "version": "v1", "title": "安盛",
                                    "source": "d", "content_hash": "h4"})
        prods = self.store.list_products()
        self.assertEqual(prods, ["安盛卓越馨选2025", "尊享e生2025"],
                         "应去重、排序、且跳过无产品(空)文档")
        self.assertNotIn("通用客服话术库", prods, "无产品文档的标题不应混入产品下拉")

    def test_resolve_product_identity_decoupled(self):
        # D75:product_name 只是归属列,与 doc_id(内容指纹)解耦 → 一产品可绑多份资料。
        #      显式空(=不关联产品)/无 key(旧 CLI)一律返回 "",不再回退成 doc_id。
        from app.retrieval.ingest.ingester import _resolve_product
        self.assertEqual(_resolve_product({"doc_id": "通用客服话术库", "product_name": ""}), "")
        self.assertEqual(_resolve_product({"product_name": "尊享e生2025"}), "尊享e生2025")
        self.assertEqual(_resolve_product({"doc_id": "通用客服话术库"}), "")
        self.assertEqual(_resolve_product({"doc_id": "x", "product_name": "安盛Excel"}), "安盛Excel")

    def test_find_doc_by_hash_global_dedup(self):
        # D75:documents 用"正文归一化指纹"作 doc_id/content_hash;find_doc_by_hash 跨键查重(防完全相同的文档重复)。
        from app.retrieval.hash_util import text_fingerprint
        fp = text_fingerprint("条款一\n条款二")
        self.store.upsert_document({"doc_id": fp, "product_name": "尊享e生2025",
                                    "product_category": "", "version": "v1", "title": "要点",
                                    "source": "s", "content_hash": fp})
        self.assertIsNotNone(self.store.find_doc_by_hash([fp]), "跨键内容指纹应命中")
        self.assertIsNone(self.store.find_doc_by_hash([fp], exclude_doc_id=fp), "排除自身 doc_id 后不应命中")
        # 旧口径 chunks 候选也参与命中(兼容存量)
        self.assertIsNotNone(self.store.find_doc_by_hash([fp, "old-chunks-fp"]))

    def test_text_fingerprint_normalizes_whitespace(self):
        # 正文归一化指纹:只对正文敏感、对换行/行尾空白/空行不敏感 → 同一文件换切法仍是同一指纹。
        from app.retrieval.hash_util import text_fingerprint
        a = text_fingerprint("第1条 保障\n第2条 责任\n\n第3条 免责")
        b = text_fingerprint("第1条 保障 \r\n第2条 责任\n\n\n\n第3条 免责  ")
        self.assertEqual(a, b, "换行/行尾空白/连续空行差异不改变指纹")
        c = text_fingerprint("第1条 保障\n第2条 责任\n第3条 免责")
        self.assertNotEqual(a, c, "正文不同则指纹不同")

    def test_next_version_auto_in_group(self):
        # D97:version 由 (产品名,文档名) 逻辑组自动递增 v1→v2;不同文档名/无产品各自 v1,杜绝手动。
        self.assertEqual(self.store.next_version("尊享e生2025", "产品要点"), "v1")
        self.store.upsert_document({"doc_id": "fp1", "product_name": "尊享e生2025",
                                    "product_category": "", "version": "v1", "title": "产品要点",
                                    "source": "s", "content_hash": "fp1"})
        self.assertEqual(self.store.next_version("尊享e生2025", "产品要点"), "v2")
        self.assertEqual(self.store.next_version("尊享e生2025", "费率表"), "v1")   # 不同文档名=新组
        self.assertEqual(self.store.next_version("安盛天平卓越馨选2025", "产品要点"), "v1")  # 不同产品
        self.assertEqual(self.store.next_version("", "通用话术"), "v1")           # 不绑定产品恒 v1

    def test_set_document_valid_sync_doc_and_chunks(self):
        # D97:切换生效/失效 → documents 与 chunks 的 is_valid 同步;get_document 可见。
        fp = "fp-doc1"
        self.store.upsert_chunks([
            {"chunk_id": "fp-doc1:0", "content": "c0",
             "meta": {"doc_id": fp, "version": "v1", "section": "", "doc_type": "policy",
                      "source": "s", "title": "t", "is_valid": True}},
            {"chunk_id": "fp-doc1:1", "content": "c1",
             "meta": {"doc_id": fp, "version": "v1", "section": "", "doc_type": "policy",
                      "source": "s", "title": "t", "is_valid": True}},
        ])
        self.store.upsert_document({"doc_id": fp, "product_name": "尊享e生2025",
                                    "product_category": "", "version": "v1", "title": "t",
                                    "source": "s", "content_hash": fp, "is_valid": True})
        self.assertTrue(self.store.get_document(fp)["is_valid"])
        # 切失效
        ok, emsg = self.store.set_document_valid(fp, False)
        self.assertTrue(ok); self.assertEqual(emsg, "")
        self.assertFalse(self.store.get_document(fp)["is_valid"])
        rows = self.store.conn.execute("SELECT is_valid FROM chunks WHERE doc_id=?", (fp,)).fetchall()
        self.assertEqual([r["is_valid"] for r in rows], [0, 0], "chunks 应同步失效")
        # 缺失文档
        ok2, _ = self.store.set_document_valid("不存在", True)
        self.assertFalse(ok2)
        # 再切生效
        self.store.set_document_valid(fp, True)
        self.assertTrue(self.store.get_document(fp)["is_valid"])

    def test_list_documents_exposes_is_valid(self):
        # D97:文档列表带 is_valid(join documents),供前端「生效中/已失效」按钮切换状态。
        self.store.upsert_chunks([
            {"chunk_id": "fp:0", "content": "c",
             "meta": {"doc_id": "fp", "version": "v1", "section": "", "doc_type": "policy",
                      "source": "s", "title": "t", "is_valid": True}}])
        self.store.upsert_document({"doc_id": "fp", "product_name": "尊享e生2025",
                                    "product_category": "", "version": "v1", "title": "t",
                                    "source": "s", "content_hash": "fp", "is_valid": False})
        r = self.store.list_documents(page=1, page_size=10)
        self.assertEqual(r["items"][0]["is_valid"], False)
