"""Session title generation (deterministic fallback per dsh-session-title).

dsh reference: node_modules/@deepseek-ai/dsh-session-title/lib/index.js
- cleanTitleText: strip control/escape/directional chars, collapse whitespace, trim.
- truncateTitleUtf8: truncate within a UTF-8 byte budget without splitting a code point.
- fallbackSessionTitle: first N whitespace-delimited words + truncate (deterministic, no LLM call).
"""
from __future__ import annotations

import re
from typing import Iterable

BACKSLASH = chr(0x5C)


def _crange(lo: int, hi: int) -> set:
    return set(chr(c) for c in range(lo, hi + 1))


_CTRL_CHARS = _crange(0x00, 0x08) | _crange(0x0B, 0x0C) | _crange(0x0E, 0x1F) | {chr(0x7F)} | _crange(0x80, 0x9F)
_DIR_CHARS = _crange(0x200B, 0x200F) | _crange(0x202A, 0x202E) | _crange(0x2060, 0x2064) | _crange(0x2066, 0x206F) | {chr(0xFEFF)}
_ESC_SET = {chr(0x1B)}


def _strip_chars(text: str, bad: set) -> str:
    return "".join(ch for ch in text if ch not in bad)


def _strip_osc(text: str) -> str:
    out = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == chr(0x1B) and i + 1 < n and text[i + 1] == "]":
            j = i + 2
            while j < n and text[j] != chr(0x07) and text[j] != chr(0x1B) and text[j] != BACKSLASH:
                j += 1
            i = j if j < n else n
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _strip_csi(text: str) -> str:
    out = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == chr(0x1B) and i + 1 < n and text[i + 1] == "[":
            j = i + 2
            while j < n and "0" <= text[j] <= "?":
                j += 1
            while j < n and " " <= text[j] <= "/":
                j += 1
            if j < n and "@" <= text[j] <= "~":
                j += 1
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _strip_esc(text: str) -> str:
    out = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == chr(0x1B) and i + 1 < n and "@" <= text[i + 1] <= "_":
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


_MULTISPACE = re.compile(r"\s+")


def clean_title_text(input_text: str) -> str:
    if not input_text:
        return ""
    t = _strip_osc(input_text)
    t = _strip_csi(t)
    t = _strip_esc(t)
    t = _strip_chars(t, _CTRL_CHARS | _DIR_CHARS | _ESC_SET)
    return _MULTISPACE.sub(" ", t).strip()


def truncate_title_utf8(input_text: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    if len(input_text.encode("utf-8")) <= max_bytes:
        return input_text
    used = 0
    out = []
    for ch in input_text:
        b = len(ch.encode("utf-8"))
        if used + b > max_bytes:
            break
        out.append(ch)
        used += b
    return "".join(out)


def fallback_session_title(input_text: str, max_words: int = 12, max_bytes: int = 60) -> str:
    if max_words <= 0:
        return ""
    clean = clean_title_text(input_text)
    words = [w for w in clean.split() if w]
    limited = " ".join(words[:max_words])
    return truncate_title_utf8(limited, max_bytes).strip()


def first_user_text(events: Iterable[dict]) -> str | None:
    for e in events:
        if e.get("type") == "user_message":
            text = e.get("payload", {}).get("text", "")
            if clean_title_text(text):
                return text
    return None
