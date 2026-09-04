# -*- coding: utf-8 -*-
"""文档结构树(doc_structure):用正则 outline 抽取**全层级**结构树(含（一）/1./(1)),
供前端目录树 + 与切块 chunk 关联。

来源:probe.build_outline(编号 + 标题判别重建层级,包含书签没有的深层子项)。
说明:不采用 PDF 内嵌书签——其只有 1~2 级(部分/条/释义),层级不全;结构树要完整多层。

存 knowledge.db 的 doc_structure 表(按 doc_id 整树替换,随摄取重建);不落 Qdrant(派生索引不需要)。
"""
from __future__ import annotations


def _norm(s: str) -> str:
    """归一化标题(去所有空白,统一全/半角空格),用于结构标题与 chunk.section 的匹配。"""
    if s is None:
        return ""
    s = s.replace("\u3000", "").replace(" ", "")
    return s


def build_from_outline(text: str, doc_type: str = "policy_pdf") -> list[dict]:
    """正则 outline(probe.build_outline)回退:无页码,parent 由层级推导。"""
    from app.retrieval.ingest.probe import build_outline
    raw = build_outline(text, doc_type=doc_type)
    nodes = []
    stack: list[str] = []
    for n in raw:
        lvl = n.get("level", 1)
        title = (n.get("title") or "").strip()
        if not title:
            continue
        while stack and len(stack) >= lvl:
            stack.pop()
        stack.append(_norm(title))
        parent = stack[-2] if len(stack) >= 2 else ""
        nodes.append({"level": int(lvl), "title": title, "page": None,
                      "parent": parent, "norm": _norm(title)})
    return nodes


def infer_children(nodes: list[dict]) -> list[dict]:
    """给每个节点带上 children(按层级栈),供前端直接渲染嵌套;返回新列表(不改入参)。"""
    out = []
    stack: list[dict] = []
    for n in nodes:
        while stack and stack[-1]["level"] >= n["level"]:
            stack.pop()
        if stack:
            stack[-1].setdefault("children", []).append(n)
        n.setdefault("children", [])
        out.append(n)
        stack.append(n)
    return out
