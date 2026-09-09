# -*- coding: utf-8 -*-
"""文档内容指纹(D75 两级判重):
- text_fingerprint(原文归一化 sha256)= documents.content_hash 的现行口径,与切块参数无关,
  支撑跨文档查重(完全相同的文档不得重复入向量库)。
- content_hash(chunks 序列 sha256)= 旧口径,保留用于与存量行的兼容比对(命中即视为同内容,
  覆盖后自动迁移到新口径,自愈)。"""
from __future__ import annotations
import hashlib
import re
from typing import Iterable


def content_hash(chunks: Iterable[dict]) -> str:
    """对一组 chunk 的 content(按传入/文档顺序)做 sha256(旧口径,兼容比对用)。
    chunks: 每项含 content。顺序需确定(同一源文件经 chunk_documents 产生的顺序确定)。
    """
    h = hashlib.sha256()
    for c in chunks:
        ct = (c.get("content") or "").encode("utf-8")
        h.update(ct)
        h.update(b"\x00")   # 分隔,防拼接歧义
    return h.hexdigest()


def text_fingerprint(text: str) -> str:
    """原文归一化指纹:统一换行、去行尾空白、压缩连续空行后 sha256。
    只对文档本体敏感,对切块参数(chunk_size/分割器)不敏感 → 同一文件换切法仍是同一指纹。"""
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    t = "\n".join(line.rstrip() for line in t.split("\n"))
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return hashlib.sha256(t.encode("utf-8")).hexdigest()
