# -*- coding: utf-8 -*-
"""文本工具:工具结果内容的确定性剪枝(保头+尾、砍中间、插标记)。

照 dsh compaction-tool-result-pruner:按 Unicode 码点测长(不切代理对),
超 threshold 就保留 head+tail、中间折叠成 PRUNE_MARKER;返回必严格短于原内容。
"""
from __future__ import annotations

PRUNE_MARKER = "\n\n[... 工具结果中间部分已截断 ...]\n\n"


def code_point_len(text: str) -> int:
    # Python str 的 len 即码点数(非 UTF-8 字节、非 UTF-16 单元)。
    return len(text)


def estimate_tokens(text: str) -> int:
    """粗糙 token 估算(CJK≈1 token/字符,其它≈1/4)。仅用于窗口上限的启发式。"""
    cjk = sum(1 for ch in text
              if '\u4e00' <= ch <= '\u9fff' or '\u3000' <= ch <= '\u303f' or '\uff00' <= ch <= '\uffef')
    other = len(text) - cjk
    return max(1, cjk + (other + 3) // 4)


def prune_tool_content(content: str, threshold_chars: int, head_chars: int,
                       tail_chars: int) -> str | None:
    """超 threshold 就保 head+tail、砍中间、插标记;不超或无法有效剪则返回 None。"""
    total = code_point_len(content)
    if total <= threshold_chars:
        return None
    if head_chars + code_point_len(PRUNE_MARKER) + tail_chars > threshold_chars:
        return None
    removed_start = head_chars
    removed_end = total - tail_chars
    if removed_end <= removed_start:
        return None
    head = content[:removed_start]
    tail = content[removed_end:]
    pruned = head + PRUNE_MARKER + tail
    if code_point_len(pruned) >= total:
        return None
    return pruned
