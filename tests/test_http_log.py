# -*- coding: utf-8 -*-
"""接口出入参日志工具(http_log)专测:可记性判定 + 敏感键打码 + PII 脱敏 + 截断。"""
from __future__ import annotations

import json
import unittest

from app.util.http_log import (
    decode_body, is_stream, is_upload,
    should_capture_request, should_capture_response,
)


class HttpLogTest(unittest.TestCase):
    def test_is_stream_and_upload(self):
        self.assertTrue(is_stream("text/event-stream; charset=utf-8"))
        self.assertFalse(is_stream("application/json"))
        self.assertTrue(is_upload("multipart/form-data; boundary=x"))
        self.assertTrue(is_upload(None) is False)

    def test_capture_request_eligibility(self):
        # 开启:非噪音、非上传 → 记
        self.assertTrue(should_capture_request("/api/sessions", "application/json", True))
        self.assertTrue(should_capture_request("/api/login", "application/json", True))
        # 噪音/上传/关闭 → 不记
        self.assertFalse(should_capture_request("/api/health", "application/json", True))
        self.assertFalse(should_capture_request("/api/metrics", "application/json", True))
        self.assertFalse(should_capture_request("/api/kb/ingest/file", "multipart/form-data; boundary=x", True))
        self.assertFalse(should_capture_request("/api/sessions", "application/json", False))

    def test_capture_response_eligibility(self):
        # 仅错误(>=400)且非流式
        self.assertTrue(should_capture_response("/api/x", "application/json", 401, True))
        self.assertTrue(should_capture_response("/api/x", "application/json", 500, True))
        self.assertFalse(should_capture_response("/api/x", "application/json", 200, True))
        self.assertFalse(should_capture_response("/api/x", "text/event-stream", 500, True))
        self.assertFalse(should_capture_response("/api/x", "application/json", 500, False))

    def test_decode_body_masks_sensitive_and_pii_and_truncates(self):
        raw = json.dumps({"username": "alice", "password": "sec123",
                          "phone": "13812345678", "note": "a" * 500},
                         ensure_ascii=False).encode()
        out = decode_body(raw, limit=100)
        self.assertIn('"password": "***"', out)
        self.assertNotIn("sec123", out)
        self.assertNotIn("13812345678", out)   # PII 手机号被掩码
        self.assertIn("手机号***", out)
        self.assertIn("…(+", out)               # 超长被截断
        self.assertLessEqual(len(out), 120)

    def test_decode_body_non_json_and_empty(self):
        self.assertEqual(decode_body(b"", 100), "")
        self.assertEqual(decode_body(b"plain text", 100), "plain text")
        # 非 UTF-8 字节不抛
        _ = decode_body(b"\xff\xfe\x00", 100)


if __name__ == "__main__":
    unittest.main()
