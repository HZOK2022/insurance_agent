# -*- coding: utf-8 -*-
"""统一时间工具:全项目用北京时间(UTC+8)。

存储格式:YYYY-MM-DD HH:mm:ss.SSS(北京时间,保留毫秒保证同秒内排序正确)
显示格式:YYYY-MM-DD HH:mm:ss(去掉毫秒,对人友好)
"""
from __future__ import annotations
import datetime

_BEIJING_TZ = datetime.timezone(datetime.timedelta(hours=8))
_FMT_STORE = "%Y-%m-%d %H:%M:%S.%f"   # 存储:带毫秒
_FMT_DISPLAY = "%Y-%m-%d %H:%M:%S"    # 显示:不带毫秒


def beijing_now() -> str:
    """当前北京时间(存储用),格式 YYYY-MM-DD HH:mm:ss.SSS。"""
    return datetime.datetime.now(_BEIJING_TZ).strftime(_FMT_STORE)[:-3]


def beijing_now_display() -> str:
    """当前北京时间(显示用),格式 YYYY-MM-DD HH:mm:ss。"""
    return datetime.datetime.now(_BEIJING_TZ).strftime(_FMT_DISPLAY)


def beijing_now_dt() -> datetime.datetime:
    """当前北京时间的 aware datetime(供需要时间运算的场景使用)。"""
    return datetime.datetime.now(_BEIJING_TZ)


def beijing_iso() -> str:
    """当前北京时间 ISO 格式(带 +08:00),仅少数需要时区信息的场景用。"""
    return datetime.datetime.now(_BEIJING_TZ).isoformat(timespec="seconds")


def parse_beijing(s: str) -> datetime.datetime:
    """解析北京时间字符串为 aware datetime。

    兼容带毫秒(YYYY-MM-DD HH:mm:ss.SSS)和不带毫秒(YYYY-MM-DD HH:mm:ss)两种格式。
    """
    s = s.strip()
    # 尝试带毫秒的格式
    try:
        dt = datetime.datetime.strptime(s, _FMT_STORE)
        return dt.replace(tzinfo=_BEIJING_TZ)
    except ValueError:
        pass
    # 尝试不带毫秒的格式
    dt = datetime.datetime.strptime(s, _FMT_DISPLAY)
    return dt.replace(tzinfo=_BEIJING_TZ)
