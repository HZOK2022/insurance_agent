# -*- coding: utf-8 -*-
"""接口出入参日志工具:判可记性 + 打码 + 截断(联调排查用)。

原则(与 LLM prompt 处理一致):详细全文进 facts 源(events),日志只给"打码+截断"的可读摘要。
- 请求 body:非敏感、非流式、非文件上传时记(打码+截断);
- 响应 body:**仅错误(>=400)**、非流式时记(打码+截断)——联调最常追的是"后端报错返回什么";
- 敏感键(password/token/api_key/secret/authorization)置 ***;PII(手机/证件/银行卡/邮箱)复用 redact_pii。
"""
from __future__ import annotations

import json

from app.guardrails.redact import redact_pii

_STREAM_CT_PREFIX = "text/event-stream"     # SSE 流式接口:响应不记 body
_UPLOAD_CT_PREFIX = "multipart/"            # 文件上传:不读请求 body(避免缓冲/二进制)
_NO_BODY_PATHS = {"/api/health", "/api/config", "/api/metrics"}   # 无排查价值/噪音
_SENSITIVE_KEYS = {"password", "api_key", "apikey", "token", "secret", "authorization", "tok"}


def is_stream(content_type: str | None) -> bool:
    return bool(content_type) and content_type.startswith(_STREAM_CT_PREFIX)


def is_upload(content_type: str | None) -> bool:
    return bool(content_type) and content_type.startswith(_UPLOAD_CT_PREFIX)


def should_capture_request(path: str, content_type: str | None, enabled: bool) -> bool:
    """请求 body:开启 + 非噪音路径 + 非文件上传。login 可记(密码被打码)。"""
    if not enabled or path in _NO_BODY_PATHS or is_upload(content_type):
        return False
    return True


def should_capture_response(path: str, content_type: str | None, status: int, enabled: bool) -> bool:
    """响应 body:仅错误(>=400)且非流式才记。成功不记(避免噪音/体积)。"""
    if not enabled or status < 400 or is_stream(content_type):
        return False
    return True


def _mask(obj):
    """递归脱敏:敏感键->***,字符串->PII 掩码。"""
    if isinstance(obj, dict):
        return {k: ("***" if str(k).lower() in _SENSITIVE_KEYS else _mask(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask(x) for x in obj]
    if isinstance(obj, str):
        return redact_pii(obj)
    return obj


def _truncate(s: str, limit: int) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + f"…(+{len(s) - limit})"


def decode_body(raw: bytes | None, limit: int) -> str:
    """把原始 body 解析->脱敏->截断成可读摘要字符串。解析失败按文本处理。"""
    if not raw:
        return ""
    try:
        obj = json.loads(raw)
    except Exception:
        text = raw.decode("utf-8", "replace")
    else:
        text = json.dumps(_mask(obj), ensure_ascii=False)
    return _truncate(text, limit)
