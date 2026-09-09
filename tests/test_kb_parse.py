# -*- coding: utf-8 -*-
"""kb_service.parse_preview 测试(monkeypatch reader.try_backend,不碰真实解析/网络/嵌入)。"""
from __future__ import annotations
import unittest
from unittest import mock

from app.api.services import kb_service

SAMPLE = """第一部分 总则
第一条 合同构成
本保险合同由保险条款组成。
（一）一般医疗费用
1.住院医疗费用
指住院期间的费用。
第二条 合同成立
"""


class ParsePreviewTest(unittest.TestCase):
    def test_rows_per_backend(self):
        side = {"markitdown": (True, SAMPLE, ""), "pdfplumber": (True, SAMPLE, ""),
                "mineru": (False, None, "MINERU_API_KEY 未配置")}
        with mock.patch("app.retrieval.ingest.reader.try_backend",
                        side_effect=lambda path, b: side[b]):
            rows = kb_service.parse_preview("x.pdf", ["markitdown", "pdfplumber", "mineru"], chunk_size=500)
        self.assertEqual([r["backend"] for r in rows], ["markitdown", "pdfplumber", "mineru"])
        ok_rows = [r for r in rows if r["backend"] in ("markitdown", "pdfplumber")]
        self.assertTrue(all(r["ok"] and r["chars"] > 0 and r["part"] == 1 and r["article"] == 2
                            for r in ok_rows))
        self.assertGreater(ok_rows[0]["chunks"], 0)
        self.assertGreater(ok_rows[0]["section_fill_pct"], 0)
        self.assertIn("outline", ok_rows[0])
        self.assertIn("text", ok_rows[0])
        self.assertTrue(ok_rows[0]["outline"])  # SAMPLE 能抽出大纲
        self.assertIn("chunks_view", ok_rows[0])
        self.assertGreater(len(ok_rows[0]["chunks_view"]), 0)
        self.assertIn("section", ok_rows[0]["chunks_view"][0])
        self.assertIn("content", ok_rows[0]["chunks_view"][0])
        self.assertIn("elapsed_ms", ok_rows[0])
        self.assertGreaterEqual(ok_rows[0]["elapsed_ms"], 0)
        fail = rows[2]
        self.assertIn("elapsed_ms", fail)
        self.assertFalse(fail["ok"])
        self.assertIn("MINERU_API_KEY", fail["err"])

    def test_auto_and_unknown_filtered(self):
        with mock.patch("app.retrieval.ingest.reader.try_backend", return_value=(True, SAMPLE, "")):
            rows = kb_service.parse_preview("x.pdf", ["auto", "bogus", "markitdown"])
        self.assertEqual([r["backend"] for r in rows], ["markitdown"])

    def test_docx_native_applicable(self):
        with mock.patch("app.retrieval.ingest.reader.try_backend", return_value=(True, SAMPLE, "")):
            rows = kb_service.parse_preview("x.docx", ["native", "pdfplumber"])
        # pdfplumber 不适用 .docx → try_backend 走 mock 恒 True,但 service 不做适用性过滤;
        # 这里验证 .docx 也能出 native 行(路由过滤在 try_backend/前端)
        self.assertTrue(any(r["backend"] == "native" and r["ok"] for r in rows))


    def test_preview_upload_returns_outline_and_chunks(self):
        # 上传前"预览切块"(D70):不写库、不发嵌入;mock build_docs 避免真实文件/网络
        def fake_build_docs(path, category="", backend=None):
            return [{"text": SAMPLE, "meta": {"doc_id": "x", "doc_type": "policy_docx",
                     "title": "x", "section": "", "version": "v1",
                     "product_category": "", "source": path, "chunk_id": "x"}}]
        with mock.patch("app.retrieval.ingest.reader.build_docs", side_effect=fake_build_docs):
            ok, res, err = kb_service.preview_upload("x.docx", parser="native",
                                                     text_splitter="structured", chunk_size=500)
        self.assertTrue(ok)
        self.assertEqual(err, "")
        self.assertEqual(res["text_splitter"], "structured")
        self.assertEqual(res["parser"], "native")
        self.assertGreater(res["chunk_count"], 0)
        self.assertTrue(res["outline"], "结构模式应抽出大纲")
        self.assertEqual(len(res["chunks"]), res["chunk_count"])
        self.assertIn("section", res["chunks"][0])
        self.assertIn("content", res["chunks"][0])

    def test_preview_upload_character_no_section(self):
        def fake_build_docs(path, category="", backend=None):
            return [{"text": SAMPLE, "meta": {"doc_id": "x", "doc_type": "policy_docx",
                     "title": "x", "section": "", "version": "v1",
                     "product_category": "", "source": path, "chunk_id": "x"}}]
        with mock.patch("app.retrieval.ingest.reader.build_docs", side_effect=fake_build_docs):
            ok, res, err = kb_service.preview_upload("x.docx", parser="native",
                                                     text_splitter="character", chunk_size=200)
        self.assertTrue(ok)
        self.assertTrue(all(not c["section"] for c in res["chunks"]), "字符模式切块无 section")

    def test_commit_upload_writes_chunks(self):
        # commit(D70):用 stub ingester 验证 write_chunks 被调 + mark_bm25_dirty;不碰真实嵌入/Qdrant
        class FakeIngester:
            def write_chunks(self, meta, chunk_items, outline, on_progress=None):
                return {"chunks_written": len(chunk_items), "chunks_embedded": len(chunk_items),
                        "doc_id": meta.get("doc_id", "")}
        ok, res, msg = kb_service.commit_upload(FakeIngester(), {"doc_id": "d1"},
                                                [{"section": "", "title": "", "content": "abc"}], [])
        self.assertTrue(ok)
        self.assertEqual(res["chunks_written"], 1)
        self.assertEqual(res["doc_id"], "d1")


    def test_commit_upload_duplicate_blocked(self):
        # D75:内容与已有文档完全相同 → duplicate 硬拦,commit 不写库
        class FakeIngester:
            def write_chunks(self, meta, chunk_items, outline, on_progress=None):
                return {"chunks_written": 0, "chunks_embedded": 0, "doc_id": meta.get("doc_id", ""),
                        "duplicate": True, "message": "内容与已有文档完全相同,未重复入库"}
        ing = FakeIngester()
        items = [{"section": "", "title": "", "content": "abc"}]
        ok, res, msg = kb_service.commit_upload(ing, {"doc_id": "d1"}, items, [])
        self.assertFalse(ok, "重复内容应拒绝写入")
        self.assertTrue(res.get("duplicate"), "应返回 duplicate 标志")


if __name__ == "__main__":
    unittest.main()
