# -*- coding: utf-8 -*-
"""切块器:按 doc_type 路由(结构化 / character 兜底)。

结构化:识别文档结构(条款纲目编号 / md 标题 / 通用编号)→ 按【语义单元】切,
每个单元带"层级路径"前缀(如 第一部分 总则 > 第六条 保险责任);单元超长 →
递归按段落/句子降级切(保留前缀)。无结构可识别 → 回退 character 切(兜底)。
不引入 langchain;pattern 可扩展(_STRUCT_PATTERNS)。
"""
from __future__ import annotations

import re

_CN = "一二三四五六七八九十百千万零"

# 可配置结构 pattern:名称 -> [(regex, 层级)]   层级=数字 或 callable(m)->int
_STRUCT_PATTERNS = {
    "policy": [
        (re.compile(rf"^第[{_CN}]+部分"), 1),
        (re.compile(rf"^第[{_CN}]+章"), 2),
        (re.compile(rf"^第[{_CN}]+节"), 3),
        (re.compile(rf"^第[{_CN}]+条"), 4),
        (re.compile(r"^[一二三四五六七八九十]+[、．.]"), 5),
        (re.compile(r"^\d+[．.、]"), 6),
        (re.compile(r"^[（(][一二三四五六七八九十]+[）)]"), 7),
    ],
    "md": [
        (re.compile(r"^(#{1,6})\s+"), lambda m: len(m.group(1))),
    ],
    "generic": [
        (re.compile(r"^\d+(\.\d+)*[．.]\s?"), 1),
        (re.compile(r"^[A-Za-z]+\d*[．.]\s?"), 2),
    ],
}


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> list[str]:
    """character 兜底切(非结构化)。"""
    text = (text or "").strip()
    if not text:
        return []
    if chunk_size <= 0:
        return [text]
    step = max(1, chunk_size - overlap) if overlap >= 0 else chunk_size
    out, start, n = [], 0, len(text)
    while start < n:
        end = min(start + chunk_size, n)
        out.append(text[start:end])
        if end >= n:
            break
        start += step
    return out


def _match_struct(line: str, patterns):
    for pat, level in patterns:
        m = pat.match(line)
        if m:
            return (level(m) if callable(level) else level), line.strip()
    return None


def _split_into_units(text: str, pattern_key: str) -> list[dict]:
    """按结构行聚合成【语义单元】:新结构行开新单元,普通行累积;记录层级路径。"""
    patterns = _STRUCT_PATTERNS.get(pattern_key, _STRUCT_PATTERNS["generic"])
    units, cur, stack = [], {"path": [], "lines": []}, []
    for raw in (text or "").splitlines():
        s = raw.strip()
        if not s:
            continue
        hit = _match_struct(s, patterns)
        if hit:
            lvl, title = hit
            if cur["lines"]:
                units.append(cur)
            stack = [x for x in stack if x[0] < lvl]   # 弹出同级/更深,保留祖先
            stack.append((lvl, title))
            cur = {"path": [t for _, t in stack], "lines": [s]}
        else:
            cur["lines"].append(s)
    if cur["lines"]:
        units.append(cur)
    return units


def _recursive_cut(body: str, prefix: str, chunk_size: int) -> list[str]:
    """超长降级:按段落 → 句子 → 字数,每段保留前缀。"""
    if len(body) <= chunk_size:
        return [f"[{prefix}] {body}".strip()] if prefix else [body]
    segs = [p for p in re.split(r"\n{2,}", body) if p.strip()]
    if len(segs) <= 1:
        segs = [p for p in re.split(r"(?<=[。！？；])", body) if p.strip()]
    out, buf = [], ""
    for seg in segs:
        if buf and len(buf) + len(seg) > chunk_size:
            out.append((f"[{prefix}] " if prefix else "") + buf)
            buf = ""
        buf += seg
        if len(buf) >= chunk_size:
            out.append((f"[{prefix}] " if prefix else "") + buf)
            buf = ""
    if buf:
        out.append((f"[{prefix}] " if prefix else "") + buf)
    return out


def chunk_structured(text: str, doc_type: str, chunk_size: int = 1000, overlap: int = 200) -> list[dict]:
    """按结构切一篇:返回 [{content, section, title}]。识别不到结构 → []。"""
    key = "md" if doc_type == "markdown" else ("policy" if doc_type in ("policy_pdf", "policy_docx") else "generic")
    units = _split_into_units(text, key)
    return [{"content": c,
             "section": " > ".join(u["path"]),
             "title": u["path"][-1] if u["path"] else ""}
            for u in units
            for c in _recursive_cut("\n".join(u["lines"]), " > ".join(u["path"]), chunk_size)]


def chunk_documents(docs, chunk_size: int = 1000, overlap: int = 200,
                    text_splitter: str = "structured") -> list[dict]:
    """按 doc_type 路由;返回 [{content, meta}]。meta 含 section/title/chunk_id 等。"""
    out = []
    for d in docs:
        meta = dict(d.get("meta", {}))
        doc_type = meta.get("doc_type", "text")
        base = meta.get("chunk_id", "")
        items = []
        if text_splitter == "structured" and doc_type in ("markdown", "policy_pdf", "policy_docx"):
            items = chunk_structured(d.get("text", ""), doc_type, chunk_size, overlap)
        elif doc_type == "rate_table":
            # 表格:reader 已输出 "表名" + "列|值" 行;每数据行一个单元
            items = [{"content": l.strip(), "section": meta.get("title", ""), "title": ""}
                     for l in d.get("text", "").splitlines() if l.strip().startswith("|")]
        if not items:   # 兜底 character
            items = [{"content": c, "section": meta.get("section", ""), "title": ""}
                     for c in chunk_text(d.get("text", ""), chunk_size, overlap)]
        for i, it in enumerate(items):
            m = dict(meta)
            m["section"] = it.get("section") or m.get("section", "")
            m["title"] = it.get("title") or m.get("title", "")
            if base:
                m["chunk_id"] = f"{base}:{i}"
            out.append({"content": it["content"], "meta": m})
    return out
