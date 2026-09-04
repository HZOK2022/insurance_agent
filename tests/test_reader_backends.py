# -*- coding: utf-8 -*-
"""reader 三后端路由测试(纯逻辑,monkeypatch try_backend,不碰真实解析/网络)。"""
from __future__ import annotations
import os
import tempfile
import unittest
from unittest import mock

from app.retrieval.ingest import reader


def _tmp(ext: str, content: str = "x") -> str:
    fd, p = tempfile.mkstemp(suffix=ext)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return p


class ReadTextRoutingTest(unittest.TestCase):
    def test_md_txt_bypass_any_backend(self):
        for ext in (".txt", ".md"):
            p = _tmp(ext, "第一行\n第二行")
            try:
                self.assertEqual(reader.read_text(p, backend="mineru"), "第一行\n第二行")
                self.assertEqual(reader.read_text(p, backend="pdfplumber"), "第一行\n第二行")
            finally:
                os.unlink(p)

    def test_explicit_backend_calls_only_that_one(self):
        p = _tmp(".pdf")
        try:
            with mock.patch.object(reader, "try_backend", return_value=(True, "T", "")) as tb:
                got = reader.read_text(p, backend="pdfplumber")
            self.assertEqual(got, "T")
            calls = [c.args[1] for c in tb.call_args_list]
            self.assertEqual(calls, ["pdfplumber"], "显式后端只能被调它自己")
        finally:
            os.unlink(p)

    def test_auto_falls_through_chain_in_order(self):
        p = _tmp(".pdf")
        try:
            side = {"mineru": (False, None, "no key"), "markitdown": (False, None, "err"),
                    "pdfplumber": (True, "PP", "")}
            with mock.patch.object(reader, "try_backend", side_effect=lambda path, b: side[b]):
                got = reader.read_text(p, backend="auto")
            self.assertEqual(got, "PP")
        finally:
            os.unlink(p)

    def test_auto_stops_at_first_success(self):
        p = _tmp(".pdf")
        try:
            with mock.patch.object(reader, "try_backend", return_value=(True, "M", "")) as tb:
                got = reader.read_text(p, backend="auto")
            self.assertEqual(got, "M")
            self.assertEqual(len(tb.call_args_list), 1, "auto 首个成功即停,不继续探测")
        finally:
            os.unlink(p)

    def test_unknown_backend_falls_back_to_auto(self):
        p = _tmp(".pdf")
        try:
            with mock.patch.object(reader, "try_backend", return_value=(True, "A", "")) as tb:
                got = reader.read_text(p, backend="bogus")
            self.assertEqual(got, "A")
            self.assertEqual(tb.call_args_list[0].args[1], "mineru", "未知后端回退 auto 链")
        finally:
            os.unlink(p)


class TryBackendTest(unittest.TestCase):
    def test_wrong_ext_rejected_without_parsing(self):
        p = _tmp(".xlsx")
        try:
            ok, text, err = reader.try_backend(p, "pdfplumber")
            self.assertFalse(ok)
            self.assertIsNone(text)
            self.assertIn("不适用", err)
        finally:
            os.unlink(p)

    def test_unknown_backend(self):
        ok, text, err = reader.try_backend("a.pdf", "nope")
        self.assertFalse(ok)
        self.assertIn("未知后端", err)

    def test_backend_supported_for(self):
        self.assertTrue(reader.backend_supported_for("a.pdf", "pdfplumber"))
        self.assertFalse(reader.backend_supported_for("a.docx", "pdfplumber"))
        self.assertTrue(reader.backend_supported_for("a.docx", "native"))


class ConfigDefaultTest(unittest.TestCase):
    def test_none_backend_uses_config_parser_backend(self):
        p = _tmp(".pdf")
        try:
            fake_cfg = type("Cfg", (), {"parser_backend": "pdfplumber"})()
            with mock.patch("app.config.load", return_value=fake_cfg):
                with mock.patch.object(reader, "try_backend", return_value=(True, "X", "")) as tb:
                    got = reader.read_text(p)  # backend=None -> config
            self.assertEqual(got, "X")
            self.assertEqual(tb.call_args_list[0].args[1], "pdfplumber", "默认后端应取 config.parser_backend")
        finally:
            os.unlink(p)

    def test_config_backend_invalid_falls_back_auto(self):
        p = _tmp(".pdf")
        try:
            fake_cfg = type("Cfg", (), {"parser_backend": "nope"})()
            with mock.patch("app.config.load", return_value=fake_cfg):
                with mock.patch.object(reader, "try_backend", side_effect=lambda path, b: (True, b, "")) as tb:
                    reader.read_text(p)
            self.assertEqual(tb.call_args_list[0].args[1], "mineru", "非法 config 值回退 auto 链首路")
        finally:
            os.unlink(p)


if __name__ == "__main__":
    unittest.main()
if __name__ == "__main__":
    unittest.main()
