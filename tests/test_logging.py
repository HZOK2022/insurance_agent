# -*- coding: utf-8 -*-
"""日志双格式(A):控制台人读文本 / 文件 JSON。改日志格式前先跑本文件,确保两种格式都成立。"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import unittest

from app.util.logging import ConsoleFormatter, JsonFormatter, setup_logging


def _record(name: str, level: int, msg: str, *args, **extra):
    r = logging.LogRecord(name, level, "f.py", 1, msg, args, None)
    for k, v in extra.items():
        setattr(r, k, v)
    return r


class LoggingFormatterTest(unittest.TestCase):
    def test_console_human_readable(self):
        f = ConsoleFormatter()
        out = f.format(_record("app.demo", logging.INFO, "turn end steps=%d", 3,
                                session_id="s1", trace_id="s1", tool="search_knowledge"))
        # 人读文本:时间 + 级别 + [logger] + msg + 上下文字段
        self.assertIn("INFO", out)
        self.assertIn("[app.demo]", out)
        self.assertIn("turn end steps=3", out)
        self.assertIn("session_id=s1", out)
        self.assertIn("trace_id=s1", out)
        self.assertIn("tool=search_knowledge", out)

    def test_console_exception_has_traceback(self):
        f = ConsoleFormatter()
        try:
            raise ValueError("boom")
        except ValueError as e:
            r = _record("app.demo", logging.ERROR, "tool failed: %s", e)
            r.exc_info = (type(e), e, e.__traceback__)
        out = f.format(r)
        self.assertIn("Traceback", out)
        self.assertIn("tool failed", out)

    def test_json_valid_with_fields(self):
        f = JsonFormatter()
        out = f.format(_record("app.demo", logging.ERROR, "boom", tool="search_knowledge",
                                session_id="s1", turn=2))
        d = json.loads(out)  # 必须是合法 JSON
        self.assertEqual(d["level"], "ERROR")
        self.assertEqual(d["logger"], "app.demo")
        self.assertEqual(d["msg"], "boom")
        self.assertEqual(d["tool"], "search_knowledge")
        self.assertEqual(d["turn"], 2)

    def test_json_unicode_not_mojibake(self):
        f = JsonFormatter()
        out = f.format(_record("app.demo", logging.INFO, "检索命中 %s", "重疾险100种"))
        self.assertIn("重疾险100种", out)   # ensure_ascii=False,中文原样

    def test_setup_dual_format_console_text_file_text(self):
        # 默认:控制台 + 文件都人读文本(tail .log 即可排查)
        lg = logging.getLogger()
        saved = [h for h in lg.handlers]
        for h in list(lg.handlers):
            lg.removeHandler(h)
        lg.__dict__.pop("_dsh_setup", None)
        d = tempfile.mkdtemp()
        try:
            setup_logging("INFO", log_dir=d, backup_count=1)
            cons = [h for h in lg.handlers if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)]
            files = [h for h in lg.handlers if isinstance(h, logging.FileHandler)]
            self.assertTrue(cons, "控制台 handler 缺失")
            self.assertIsInstance(cons[0].formatter, ConsoleFormatter, "控制台应为人读文本")
            self.assertTrue(files, "文件 handler 缺失")
            self.assertIsInstance(files[0].formatter, ConsoleFormatter, "文件应为人读文本(tail .log 可读)")
        finally:
            for h in list(lg.handlers):
                lg.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass
            for h in saved:
                lg.addHandler(h)
            for p in os.listdir(d):
                try:
                    os.remove(os.path.join(d, p))
                except OSError:
                    pass
            try:
                os.rmdir(d)
            except OSError:
                pass

    def test_setup_file_json_when_requested(self):
        # 传 file_format="json" 时文件仍为 JSON(供日志采集器)
        lg = logging.getLogger()
        saved = [h for h in lg.handlers]
        for h in list(lg.handlers):
            lg.removeHandler(h)
        lg.__dict__.pop("_dsh_setup", None)
        d = tempfile.mkdtemp()
        try:
            setup_logging("INFO", log_dir=d, backup_count=1, file_format="json")
            files = [h for h in lg.handlers if isinstance(h, logging.FileHandler)]
            self.assertTrue(files)
            self.assertIsInstance(files[0].formatter, JsonFormatter)
        finally:
            for h in list(lg.handlers):
                lg.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass
            for h in saved:
                lg.addHandler(h)
            for p in os.listdir(d):
                try:
                    os.remove(os.path.join(d, p))
                except OSError:
                    pass
            try:
                os.rmdir(d)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
