# -*- coding: utf-8 -*-
"""接口鉴权 + 限流(③):Bearer token + 进程内滑动窗口限流,防未授权访问/成本滥用。"""
from __future__ import annotations

import unittest
from dataclasses import replace

from fastapi.testclient import TestClient

from app import main as appmod
from app.api.services import container
from app.config import Config


def cfg_with(**kw):
    return replace(Config(), **kw)   # 完整默认配置 + 覆盖指定字段(frozen dataclass 用 replace)


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(appmod.app)
        appmod._rate._hits.clear()   # 重置全局限流器,避免跨测试污染

    def test_health_exempt(self):
        self.assertEqual(self.client.get("/api/health").status_code, 200)

    def test_auth_off_when_token_empty(self):
        orig = container.get_cfg
        container.get_cfg = lambda: cfg_with(api_token="")
        try:
            self.assertEqual(self.client.get("/api/sessions").status_code, 200)
        finally:
            container.get_cfg = orig

    def test_auth_required_when_token_set(self):
        orig = container.get_cfg
        container.get_cfg = lambda: cfg_with(api_token="secret")
        try:
            self.assertEqual(self.client.get("/api/sessions").status_code, 401)                       # 无 header
            self.assertEqual(self.client.get("/api/sessions", headers={"Authorization": "Bearer wrong"}).status_code, 401)  # 错误 token
            self.assertEqual(self.client.get("/api/sessions", headers={"Authorization": "Bearer secret"}).status_code, 200)  # 正确 token
        finally:
            container.get_cfg = orig

    def test_rate_limit_429(self):
        orig = container.get_cfg
        container.get_cfg = lambda: cfg_with(api_token="", api_rate_limit=2, api_rate_window_seconds=60)
        try:
            self.assertEqual(self.client.get("/api/sessions").status_code, 200)
            self.assertEqual(self.client.get("/api/sessions").status_code, 200)
            self.assertEqual(self.client.get("/api/sessions").status_code, 429)   # 第 3 次超限
        finally:
            container.get_cfg = orig


if __name__ == "__main__":
    unittest.main()
