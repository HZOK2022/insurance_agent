# -*- coding: utf-8 -*-
"""摄取模块: 供 CLI 和 API 共用。"""
from .reader import is_supported, read_text, build_docs, supported_extensions
from .ingester import Ingester

__all__ = [
    "is_supported",
    "read_text",
    "build_docs",
    "supported_extensions",
    "Ingester",
]
