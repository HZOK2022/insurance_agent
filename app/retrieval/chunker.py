"""character 切块器(chunk_size/overlap 来自 config)。"""
from __future__ import annotations


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if chunk_size <= 0:
        return [text]
    step = max(1, chunk_size - overlap) if overlap >= 0 else chunk_size
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        chunks.append(text[start:end])
        if end >= n:
            break
        start += step
    return chunks


def chunk_documents(docs, chunk_size: int = 1000, overlap: int = 200) -> list[dict]:
    out = []
    for d in docs:
        meta = dict(d.get("meta", {}))
        base = meta.get("chunk_id", "")
        for i, c in enumerate(chunk_text(d.get("text", ""), chunk_size, overlap)):
            m = dict(meta)
            if base:
                m["chunk_id"] = f"{base}:{i}"
            out.append({"content": c, "meta": m})
    return out
