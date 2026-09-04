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
        (re.compile(r"^[（(][一二三四五六七八九十]+[）)]"), 6),
        (re.compile(r"^\d+[．.、]"), 7),
        (re.compile(r"^[（(]\d+[）)]"), 8),
    ],
    "md": [
        (re.compile(r"^(#{1,6})\s+"), lambda m: len(m.group(1))),
    ],
    # 通用编号(报告/论文/清单等):层级 = 数字段数(1.1→2,1.1.1→3);各标记为近似嵌套深度
    "generic": [
        (re.compile(r"^\d+(\.\d+)+[．.]?"), lambda m: m.group(0).rstrip("．.、 ").count(".") + 1),
        (re.compile(r"^\d+[．.、)]"), 1),          # 1. 2、 3)
        (re.compile(r"^[（(]\d+[）)]"), 2),        # (1)
        (re.compile(r"^[一二三四五六七八九十]+[、．.]"), 2),  # 一、
        (re.compile(r"^[（(][一二三四五六七八九十]+[）)]"), 3),  # (一)
        (re.compile(r"^[A-Za-z]+\.\s?"), 2),       # A. B. C.
    ],
}


def _detect_pattern_key(text: str) -> str:
    """自动探测该文本用哪种结构约定(通用化的关键):
    先剥 md # 前缀再判条款/通用编号(避免 MinerU 带 ## 的条款被误当 markdown)→ 'policy';
    其次原文有 md # 标题 → 'md';其次通用编号(1./1.1/(1)/一、…且通过标题判别)→ 'generic';
    都无 → 'none'(回退字符切)。"""
    raw_lines = [l.strip() for l in (text or "").splitlines()]
    stripped = [re.sub(r"^#{1,6}\s*", "", l).strip() for l in raw_lines]
    if any(re.match(r"^第[一二三四五六七八九十百千万零]+(部分|章|节|条)", l) for l in stripped):
        return "policy"
    if any(re.match(r"^#{1,6}\s", l) for l in raw_lines):
        return "md"
    if any((_match_struct(l, _STRUCT_PATTERNS["generic"]) and is_heading_like(l)) for l in stripped):
        return "generic"
    return "none"


# —— 目录/前言等非正文段剔除(借鉴 RegulationChunker._filter_non_content_sections)——
_TOC_KEYWORDS = ["目录", "目次", "前言", "引言", "序言", "编者按", "出版说明",
                 "Contents", "Table of Contents", "Preface", "Introduction"]


def _filter_toc_prefix(text: str) -> str:
    """去掉"目录/前言"等前缀:取最后一个目录类标记行,从其后的第一个真实结构标题起为正文;
    找不到结构标题则保守保留原文(避免误删正文)。"""
    lines = (text or "").split("\n")
    last = -1
    for i, line in enumerate(lines):
        norm = line.strip().replace(" ", "").replace("\t", "").replace("\u3000", "")
        for kw in _TOC_KEYWORDS:
            if norm == kw or norm == kw + "：" or norm == kw + ":":
                last = i
                break
    if last == -1:
        return text
    key = _detect_pattern_key(text)
    patterns = _STRUCT_PATTERNS.get(key, _STRUCT_PATTERNS["generic"])
    start = -1
    for i in range(last + 1, len(lines)):
        s = lines[i].strip()
        if key == "md":
            ok = bool(re.match(r"^#{1,6}\s", s))
        else:
            s2 = re.sub(r"^#{1,6}\s*", "", s).strip()
            ok = bool(s2 and _match_struct(s2, patterns) and is_heading_like(s2))
        if ok:
            start = i
            break
    if start == -1:
        return text
    return "\n".join(lines[start:])


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


def chunk_by_paragraphs(text: str, chunk_size: int = 1000, overlap: int = 200) -> list[str]:
    """按段落切分(段落=空行分隔;单段超上限降级按句子/字符)。返回文本块(不含结构前缀)。
    用于"段落"切分模式;overlap 时相邻块尾交叠。"""
    body = (text or "").strip()
    if not body:
        return []
    paras = [p.strip() for p in re.split(r"\n{2,}", body) if p.strip()]
    if not paras:
        paras = [l.strip() for l in body.splitlines() if l.strip()]
    out, buf = [], ""
    for p in paras:
        if len(p) > chunk_size:
            if buf:
                out.append(buf)
                buf = ""
            out.extend(_cut_by_chars(p, "", chunk_size))
            continue
        if buf and len(buf) + 1 + len(p) > chunk_size:
            out.append(buf)
            buf = ""
        buf = (buf + "\n" + p) if buf else p
    if buf:
        out.append(buf)
    if overlap and overlap > 0 and len(out) > 1:
        out = [out[0]] + [out[i - 1][-overlap:] + out[i] for i in range(1, len(out))]
    return out


def _match_struct(line: str, patterns):
    for pat, level in patterns:
        m = pat.match(line)
        if m:
            return (level(m) if callable(level) else level), line.strip()
    return None


# ---- 标题 vs 内容判别(供 chunker 与 probe.build_outline 共用)----
_HEADING_MAX_CHARS = 64    # 标题是"短标签";超长→多为编号开头的条款正文
_SENT_END = "。！？；，、,.;?!"
_CLAUSE_PUNCT = "，,；;"   # 含逗号/分号 → 是"分句短语/条款",不是名词性标签标题
_PUNCT_FULLSTOP = "。！？!?"


def is_heading_like(line: str) -> bool:
    """命中编号/标记的行是否是"真标题"(名词性短标签):
    - 长度 ≤64;
    - 不以句末标点(。！？)或**冒号(：:)**结尾;
    - **不含逗号/分号(，,；;)**——真标题无逗号;含逗号的多为条款/分句(如 `（1）心脏淀粉样变性,被保险人…达到`)。
    否则(如 `(1)该特定药品须…必需的药品;`、`（2）自主生活能力完全丧失,无法独…`、`（2）至少存在下列一项：`)按正文处理
    ——冒号结尾="条件/清单引出项",应并回父级(借鉴 RegulationChunker 对 sub_item 的冒号判定),避免把疾病的 (1)(2) 标准拆成独立块。"""
    s = (line or "").strip()
    if not s:
        return False
    if len(s) > _HEADING_MAX_CHARS:
        return False
    if s[-1] in _PUNCT_FULLSTOP or s[-1] in "：:":
        return False
    if any(ch in s for ch in _CLAUSE_PUNCT):
        return False
    return True


def _split_into_units(text: str, pattern_key: str) -> list[dict]:
    """按结构行聚合成【语义单元】:新结构行开新单元,普通行累积;记录层级路径。
    md:按 # 层级;非 md(条款/通用):先剥行首 md#(MinerU 把层级拍平成 # 标记)再按编号 regex 匹配,
    使 chunk.section/content 与 doc_structure(probe.build_outline)同口径——**不含 #**(否则 # 混进
    section/content,且 # 行不匹配编号 regex 导致层级丢失,目录↔chunk 关联失败)。"""
    patterns = _STRUCT_PATTERNS.get(pattern_key, _STRUCT_PATTERNS["generic"])
    units, cur, stack = [], {"path": [], "lines": []}, []
    for raw in (text or "").splitlines():
        s = raw.strip()
        if not s:
            continue
        if pattern_key == "md":
            m = re.match(r"^(#{1,6})\s*(.*)$", s)
            if m and m.group(2).strip():
                title = m.group(2).strip()
                lvl = len(m.group(1))
                if cur["lines"]:
                    units.append(cur)
                stack = [x for x in stack if x[0] < lvl]   # 弹出同级/更深,保留祖先
                stack.append((lvl, title))
                cur = {"path": [t for _, t in stack], "lines": [title]}
                continue
            cur["lines"].append(s)
            continue
        # 非 md:剥行首 # 再匹配,避免 # 混入 section/content 或破坏编号层级
        clean = re.sub(r"^#{1,6}\s*", "", s).strip()
        if not clean:
            continue
        hit = _match_struct(clean, patterns)
        if hit and is_heading_like(clean):
            lvl, title = hit
            if cur["lines"]:
                units.append(cur)
            stack = [x for x in stack if x[0] < lvl]
            stack.append((lvl, title))
            cur = {"path": [t for _, t in stack], "lines": [clean]}
        else:
            # 命中标记但"是正文"(长句/以句末标点结尾)→ 不当作标题,归入当前单元内容
            cur["lines"].append(clean)
    if cur["lines"]:
        units.append(cur)
    return units


# 可合并的"碎片级"编号:只并 （一）/1./(1) 这类子项;裸"一、"是独立术语(如释义的"一、保险人"),
# 各成一块、不合并 —— 否则目录里"一、二、三、四"会共同指向一个合并块导致没有各自关联(D68 拆分)。
_MERGEABLE_LISTY = re.compile(r"^[（(][一二三四五六七八九十]+[）)]|^\d+[．.、]|^[（(]\d+[）)]")


def _coalesce_units(units: list[dict], chunk_size: int, max_tokens: int | None = None) -> list[dict]:
    """防"切太碎":
    ① 纯容器标题单元(单行,且**确有子单元**——其标题文本已通过子块前缀保留)不单独成块;
       单行叶子条目(如 `(1)门诊肾透析费;`)无子单元,必须保留,否则正文丢失(D58 误伤回修)。
    ② 同级(同父)连续编号子项合并:上限 = max_tokens(token 预算,含父前缀)或 chunk_size(字符);
       合并块 section 取父路径,避免把多个子标题串进前缀。md 标题层级文档不做兄弟合并。
       只并 `（一）/1./(1)` 这种"碎片级"子项;裸 `一、`(如释义的"一、保险人")是独立术语,
       各成一块不合并(D68),使目录↔chunk 一一对应。
    """
    from app.utils.text import embedding_tokens

    def cost(lines: list[str], parent) -> int:
        text = "\n".join(lines)
        if parent:
            text = " > ".join(parent) + " " + text
        return embedding_tokens(text)

    # ① 只丢"有子单元"的单行容器标题;单行叶子项(无子单元)保留
    drop_idx: set[int] = set()
    for i, u in enumerate(units):
        if len(u.get("lines") or []) <= 1 and u.get("path"):
            has_child = any(len(v.get("path") or []) > len(u["path"])
                            and (v.get("path") or [])[:len(u["path"])] == u["path"]
                            for v in units[i + 1:])
            if has_child:
                drop_idx.add(i)
    body_units = [u for i, u in enumerate(units) if i not in drop_idx]
    out: list[dict] = []
    buf: dict | None = None
    for u in body_units:
        parent = tuple(u["path"][:-1])
        listy = bool(u["path"]) and bool(_MERGEABLE_LISTY.match(u["path"][-1]))
        if buf is not None and tuple(buf["_parent"]) == parent:
            cand = buf["lines"] + u["lines"]
            if max_tokens:
                over = cost(cand, parent) > max_tokens
            else:
                over = sum(len(s) for s in cand) > chunk_size
            if buf["_listy"] and listy and parent and not over:
                buf["lines"] = cand
                buf["_merged"] = True
                continue
        if buf is not None:
            out.append(_finalize_unit(buf))
        buf = {"lines": list(u["lines"]), "path": list(u["path"]),
               "_parent": parent, "_merged": False, "_listy": listy}
    if buf is not None:
        out.append(_finalize_unit(buf))
    return out


def _finalize_unit(b: dict) -> dict:
    if b["_merged"]:
        b["path"] = list(b["_parent"])     # 合并块:section=父路径(子标题留在 content 行内)
    for k in ("_parent", "_merged", "_listy"):
        b.pop(k, None)
    return b


def _cut_by_chars(body: str, prefix: str, chunk_size: int) -> list[str]:
    """超长降级(字符):按段落 → 句子 → 字数,每段保留前缀。"""
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


def _cut_by_sentences(body: str, prefix: str, max_tokens: int) -> list[str]:
    """超长降级(token 预算)·叶子级:按段落→句子切,每片含前缀 ≤预算(单条无子结构时用)。"""
    from app.utils.text import embedding_tokens

    pfx = f"[{prefix}] " if prefix else ""
    full = pfx + body
    if embedding_tokens(full) <= max_tokens:
        return [full.strip()]
    limit = max(80, max_tokens - embedding_tokens(pfx))   # 每片正文可用预算
    segs = [p for p in re.split(r"\n{2,}", body) if p.strip()]
    if len(segs) <= 1:
        segs = [p for p in re.split(r"(?<=[。！？；])", body) if p.strip()]
    out, buf = [], ""
    for seg in segs:
        if embedding_tokens(seg) > limit:
            # 单段(无标点长串)超预算:按近似字符硬切
            if buf:
                out.append((pfx + buf).strip())
                buf = ""
            chunk_chars = max(1, int(limit / 1.15))
            for i in range(0, len(seg), chunk_chars):
                out.append((pfx + seg[i:i + chunk_chars]).strip())
            continue
        if buf and embedding_tokens(buf) + embedding_tokens(seg) > limit:
            out.append((pfx + buf).strip())
            buf = ""
        buf += seg
    if buf:
        out.append((pfx + buf).strip())
    return out or [full.strip()]


def _cut_by_tokens(body: str, prefix: str, max_tokens: int, key: str) -> list[str]:
    """超长降级(token 预算)·结构边界链优先(原子项不拆碎):
    先按"可见层→隐藏层(（一）/1./(1))"的编号边界打包【整项】;仅当单个原子项本身超预算,
    才降到下一层(其内部子编号),否则降到句子/段落。避免把一个 (1) 从中间劈开、让 (1)(2)(3) 尽量同块。"""
    from app.utils.text import embedding_tokens

    pfx = f"[{prefix}] " if prefix else ""
    full = pfx + body
    if embedding_tokens(full) <= max_tokens:
        return [full.strip()]
    patterns = _STRUCT_PATTERNS.get(key, _STRUCT_PATTERNS["generic"])
    lines = (body or "").split("\n")
    # 子项层级 = 首行(单元自身标题)之后的最小编号层级
    sub_levels: list[int] = []
    for l in lines[1:]:
        s = l.strip()
        if not s:
            continue
        m = _match_struct(s, patterns)
        if m:
            sub_levels.append(m[0])
    if not sub_levels:
        return _cut_by_sentences(body, prefix, max_tokens)
    region_level = min(sub_levels)
    # 按该层级切分"原子项"
    items: list[list[str]] = []
    cur: list[str] = []
    for l in lines:
        s = l.strip()
        m = _match_struct(s, patterns) if s else None
        if s and m and m[0] == region_level and cur:
            items.append(cur)
            cur = [l]
        else:
            cur.append(l)
    if cur:
        items.append(cur)
    if len(items) <= 1:
        # 只有一层/一个原子项 → 降到句子
        return _cut_by_sentences(body, prefix, max_tokens)
    groups: list[str] = []
    curg: list[str] = []
    cur_tok = 0

    def flush():
        nonlocal curg, cur_tok
        if curg:
            groups.append((pfx + "\n".join(curg)).strip())
            curg = []
            cur_tok = 0

    for it in items:
        it_text = "\n".join(it)
        it_tok = embedding_tokens(pfx + it_text)
        if it_tok > max_tokens:
            flush()
            groups.extend(_cut_by_tokens(it_text, prefix, max_tokens, key))  # 向更深一层降级
            continue
        if curg and cur_tok + it_tok > max_tokens:
            flush()
        curg.append(it_text)
        cur_tok += it_tok
    flush()
    return groups


def chunk_structured(text: str, doc_type: str, chunk_size: int = 1000, overlap: int = 200,
                     max_tokens: int | None = None, pattern_key: str | None = None) -> list[dict]:
    """按结构切一篇(通用):返回 [{content, section, title}]。识别不到结构 → []。
    pattern_key: None=自动探测(md/policy/generic/none);指定则用该约定(md|policy|generic)。
    max_tokens: 给定时,合并/降级按"整串(含前缀)估算 token ≤ max_tokens"(embedding 预算);
                为 None 时保持旧行为(按 chunk_size 字符上限)。"""
    if pattern_key is None:
        key = "md" if doc_type == "markdown" else _detect_pattern_key(text)
    else:
        key = pattern_key
    if key == "none":
        return []
    if key != "md":
        text = _filter_toc_prefix(text)   # 去掉目录/前言等非正文前缀
    units = _split_into_units(text, key)
    if key != "md":
        units = _coalesce_units(units, chunk_size, max_tokens)   # 空标题不立块 + 同级短项合并(防切太碎)
    out = []
    for u in units:
        prefix = " > ".join(u["path"])
        body = "\n".join(u["lines"])
        if max_tokens:
            pieces = _cut_by_tokens(body, prefix, max_tokens, key)
        else:
            pieces = _cut_by_chars(body, prefix, chunk_size)
        for c in pieces:
            out.append({"content": c, "section": prefix, "title": u["path"][-1] if u["path"] else ""})
    return out


def chunk_documents(docs, chunk_size: int = 1000, overlap: int = 200,
                    text_splitter: str = "structured", max_tokens: int | None = None) -> list[dict]:
    """按 doc_type 路由;返回 [{content, meta}]。meta 含 section/title/chunk_id 等。
    max_tokens: 结构化路径的 embedding token 预算(见 chunk_structured);None=字符上限。
    除 rate_table 外一律先尝试"结构切"(自动探测 md/条款/通用编号),识别不出才回退字符切。"""
    out = []
    for d in docs:
        meta = dict(d.get("meta", {}))
        doc_type = meta.get("doc_type", "text")
        base = meta.get("chunk_id", "")
        text = d.get("text", "")
        kind = text_splitter if text_splitter in ("structured", "character", "paragraph") else "structured"
        if kind == "structured":
            items = []
            if doc_type != "rate_table":
                # 结构层级切(自动探测 md/条款/通用编号)
                items = chunk_structured(text, doc_type, chunk_size, overlap, max_tokens)
            else:
                # 表格:reader 已输出 "表名" + "列|值" 行;每数据行一个单元
                items = [{"content": l.strip(), "section": meta.get("title", ""), "title": ""}
                         for l in text.splitlines() if l.strip().startswith("|")]
            if not items:   # 结构识别不出 → 字符兜底
                items = [{"content": c, "section": meta.get("section", ""), "title": ""}
                         for c in chunk_text(text, chunk_size, overlap)]
        elif kind == "paragraph":
            # 段落切(无结构前缀)
            items = [{"content": c, "section": meta.get("section", ""), "title": meta.get("title", "")}
                     for c in chunk_by_paragraphs(text, chunk_size, overlap)]
            if not items:
                items = [{"content": c, "section": meta.get("section", ""), "title": ""}
                         for c in chunk_text(text, chunk_size, overlap)]
        else:   # character:字符滑窗切(无结构前缀)
            items = [{"content": c, "section": meta.get("section", ""), "title": meta.get("title", "")}
                     for c in chunk_text(text, chunk_size, overlap)]
        for i, it in enumerate(items):
            m = dict(meta)
            m["section"] = it.get("section") or m.get("section", "")
            m["title"] = it.get("title") or m.get("title", "")
            if base:
                m["chunk_id"] = f"{base}:{i}"
            out.append({"content": it["content"], "meta": m})
    return out
