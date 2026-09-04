# -*- coding: utf-8 -*-
"""解析结果对比探测(纯函数,无外部解析依赖)。

给 scripts/compare_parsers.py 与后续"文档结构树抽取(doc_structure)"复用:
统计文本里的结构线索行(部分/章/节/条/子项/编号项/md 标题/页码噪音),
并用结构切块器(chunker)切后看 section 填充 —— 衡量"该解析器输出的层级可用性"。
"""
from __future__ import annotations
import re

_PART = re.compile(r"^第[一二三四五六七八九十百千万零]+部分")
_CHAPTER = re.compile(r"^第[一二三四五六七八九十百千万零]+章")
_SECTION = re.compile(r"^第[一二三四五六七八九十百千万零]+节")
_ARTICLE = re.compile(r"^第[一二三四五六七八九十百千万零]+条")
_SUBITEM = re.compile(r"^[（(][一二三四五六七八九十]+[）)]")
_NUMBERED = re.compile(r"^\d+[．.、]")
_CN_NUMBERED = re.compile(r"^[一二三四五六七八九十]+[、．.]")
_MD_HEAD = re.compile(r"^(#{1,6})\s+")
_PAGE_NOISE = re.compile(r"^=+.*页.*=+$|^\d{1,3}$")


def probe_text(text: str) -> dict:
    """逐行分类统计结构线索。返回 dict,字段:
    chars/lines(非空行)、md_heads{1..6}、part/chapter/section/article/subitem/numbered/cn_numbered、
    struct_lines(命中任一编号结构的行数)、page_noise、structural_ratio(struct_lines/lines)。"""
    text = text or ""
    lines = [s.strip() for s in text.splitlines() if s.strip()]
    out = {
        "chars": len(text), "lines": len(lines),
        "md_heads": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "6": 0},
        "part": 0, "chapter": 0, "section": 0, "article": 0,
        "subitem": 0, "numbered": 0, "cn_numbered": 0,
        "struct_lines": 0, "page_noise": 0,
    }
    for s in lines:
        m = _MD_HEAD.match(s)
        if m:
            out["md_heads"][str(len(m.group(1)))] += 1
        for key, pat in (("part", _PART), ("chapter", _CHAPTER), ("section", _SECTION),
                         ("article", _ARTICLE), ("subitem", _SUBITEM),
                         ("numbered", _NUMBERED), ("cn_numbered", _CN_NUMBERED)):
            if pat.match(s):
                out[key] += 1
        if _PART.match(s) or _ARTICLE.match(s) or _SUBITEM.match(s) or _NUMBERED.match(s) or _CN_NUMBERED.match(s):
            out["struct_lines"] += 1
        if _PAGE_NOISE.match(s):
            out["page_noise"] += 1
    out["structural_ratio"] = round(out["struct_lines"] / out["lines"], 4) if out["lines"] else 0.0
    return out


def strip_md_prefix(text: str) -> str:
    """去掉每行行首的 md 标题标记(^#+\s*),把 MinerU full.md 还原成"裸编号行"文本,
    供编号 regex / 结构切块器识别(对应"脱 # 前缀"方向)。"""
    return "\n".join(re.sub(r"^#{1,6}\s*", "", s) for s in (text or "").splitlines())




# policy 结构 pattern 顺序(层级由低到高编号):部分1 < 章2 < 节3 < 条4 < (一)5 < 1.6
_POLICY_RE = [
    (re.compile(r"^第[一二三四五六七八九十百千万零]+部分"), 1),
    (re.compile(r"^第[一二三四五六七八九十百千万零]+章"), 2),
    (re.compile(r"^第[一二三四五六七八九十百千万零]+节"), 3),
    (re.compile(r"^第[一二三四五六七八九十百千万零]+条"), 4),
    (re.compile(r"^[（(][一二三四五六七八九十]+[）)]"), 5),
    (re.compile(r"^\d+[．.、]"), 6),
    (re.compile(r"^[（(]\d+[）)]"), 7),
]


def build_outline(text: str, doc_type: str = "policy_pdf", strip_md: bool = True,
                  pattern_key: str | None = None) -> list[dict]:
    """抽取文档大纲(层级树):返回 [{level, title}],level=大纲内深度(1 起)。
    pattern_key=None 时自动探测(md/policy/generic/none);markdown 按 # 层级;其余用同一套
    chunker patterns + 标题判别(通用化,与 chunker._detect_pattern_key 一致)。"""
    from app.retrieval.chunker import is_heading_like, _detect_pattern_key, _STRUCT_PATTERNS
    text = text or ""
    if pattern_key is None:
        key = "md" if doc_type == "markdown" else _detect_pattern_key(text)
    else:
        key = pattern_key
    if key == "none":
        return []
    if key != "md":
        from app.retrieval.chunker import _filter_toc_prefix
        text = _filter_toc_prefix(text)
    if key == "md":
        nodes = []
        for s in text.splitlines():
            s = s.rstrip()
            m = re.match(r"^(#{1,6})\s*(.*)$", s)
            if m and m.group(2).strip():
                nodes.append({"level": len(m.group(1)), "title": m.group(2).strip()})
        return nodes
    if strip_md:
        lines = [re.sub(r"^#{1,6}\s*", "", s).strip() for s in text.splitlines()]
    else:
        lines = [s.strip() for s in text.splitlines()]
    patterns = _STRUCT_PATTERNS.get(key, _STRUCT_PATTERNS["generic"])
    stack: list[int] = []
    nodes = []
    for s in lines:
        if not s:
            continue
        hit = None
        for pat, lvl in patterns:
            m = pat.match(s)
            if m:
                hit = (lvl(m) if callable(lvl) else lvl, s)
                break
        if not hit:
            continue
        if not is_heading_like(s):   # 编号开头的"长条款/句末标点/含逗号"是正文,不是标题
            continue
        lvl, title = hit
        while stack and stack[-1] >= lvl:
            stack.pop()
        stack.append(lvl)
        nodes.append({"level": len(stack), "title": title})
    return nodes



def chunk_preview_list(text, doc_type="policy_pdf", chunk_size=1000,
                      strip_md=True, max_chunks=600, content_cap=None, max_tokens=None):
    # 切块预览:全文按结构切块,每块给 {i, section, title, content},供前端右侧切块预览。
    # content_cap=None 时给全文(前端折叠展示、点开看全文);cap 仅防极端超大块。
    from app.retrieval.chunker import chunk_structured
    body = strip_md_prefix(text) if strip_md else text
    items = chunk_structured(body, doc_type, chunk_size=chunk_size, max_tokens=max_tokens)
    out = []
    for i, it in enumerate(items[:max_chunks], 1):
        content = it.get("content", "")
        if content_cap and len(content) > content_cap:
            content = content[:content_cap] + " …"
        out.append({"i": i, "section": it.get("section", ""),
                    "title": it.get("title", ""), "content": content})
    return out


def chunk_probe(text: str, doc_type: str = "policy_pdf", chunk_size: int = 1000,
                strip_md: bool = False, max_tokens: int | None = None) -> dict:
    """用结构切块器切一段文本,统计 section 填充情况(层级可用性的检索口径)。
    strip_md=True 时先脱 md 前缀(用于 MinerU 输出预览)。max_tokens 见 chunker。"""
    from app.retrieval.chunker import chunk_structured
    body = strip_md_prefix(text) if strip_md else text
    items = chunk_structured(body, doc_type, chunk_size=chunk_size, max_tokens=max_tokens)
    if not items:
        return {"chunks": 0, "with_section": 0, "section_fill_pct": 0.0, "avg_path_len": 0.0,
                "titles_seen": 0, "overlong": 0}
    with_sec = [it for it in items if it.get("section")]
    overlong = [it for it in items if len(it.get("content", "")) > chunk_size]
    # 平均层级深度(按 section 里的 " > " 段数)
    depths = [it["section"].count(" > ") + 1 for it in with_sec if it.get("section")]
    return {
        "chunks": len(items),
        "with_section": len(with_sec),
        "section_fill_pct": round(len(with_sec) / len(items) * 100, 1) if items else 0.0,
        "avg_path_len": round(sum(depths) / len(depths), 2) if depths else 0.0,
        "titles_seen": len({it.get("title") for it in items if it.get("title")}),
        "overlong": len(overlong),
    }
