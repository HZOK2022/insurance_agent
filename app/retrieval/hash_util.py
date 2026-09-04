# -*- coding: utf-8 -*-
"""文档内容哈希:同一产品名判重——内容不同不盲目覆盖(防"写错产品名把别人覆盖")。
哈希只按 content(按文档顺序)计算,不含 doc_id/chunk_id,因此同一文件内容一致 → 哈希一致。"""
from __future__ import annotations
import hashlib
from typing import Iterable


def content_hash(chunks: Iterable[dict]) -> str:
    """对一组 chunk 的 content(按传入/文档顺序)做 sha256。
    chunks: 每项含 content。顺序需确定(同一源文件经 chunk_documents 产生的顺序确定)。
    """
    h = hashlib.sha256()
    for c in chunks:
        ct = (c.get("content") or "").encode("utf-8")
        h.update(ct)
        h.update(b"\x00")   # 分隔,防拼接歧义
    return h.hexdigest()
