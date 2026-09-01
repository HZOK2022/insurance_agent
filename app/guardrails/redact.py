# -*- coding: utf-8 -*-
"""输出侧 PII 脱敏(⑨/合规):在审计查询/导出等"对外/留证"边界掩码敏感信息。

手机上号/身份证/银行卡/邮箱等正则掩码。只对**展示/导出副本**脱敏;
事实源 events 保持原样(铁律:model 可见⟺已记录),但导出/查询视图不泄露原样 PII。
"""
from __future__ import annotations

import re

_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_IDCARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_BANKCARD = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")

_PHONE_REP = "手机号***"
_IDCARD_REP = "证件号***"
_BANKCARD_REP = "卡号***"
_EMAIL_REP = "邮箱***"


def redact_pii(text: str) -> str:
    if not text:
        return text
    t = _PHONE.sub(_PHONE_REP, text)
    t = _IDCARD.sub(_IDCARD_REP, t)
    t = _BANKCARD.sub(_BANKCARD_REP, t)
    t = _EMAIL.sub(_EMAIL_REP, t)
    return t


def _redact_value(v):
    if isinstance(v, str):
        return redact_pii(v)
    if isinstance(v, dict):
        return {k: _redact_value(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_redact_value(x) for x in v]
    return v


def redact_obj(obj):
    """递归地把对象里所有字符串字段做 PII 掩码。"""
    return _redact_value(obj)
