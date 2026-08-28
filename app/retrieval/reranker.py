"""外部重排(SiliconFlow bge-reranker-v2-m3)。失败返回 None(调用方回退原顺序)。"""
from __future__ import annotations
import requests


def rerank(query: str, documents: list[str], url: str, api_key: str, model: str,
           top_n: int = 3, timeout: int = 30):
    if not api_key:
        return None
    try:
        r = requests.post(url, headers={"Authorization": "Bearer " + api_key},
                          json={"model": model, "query": query, "documents": documents, "top_n": top_n},
                          timeout=timeout)
        r.raise_for_status()
        return r.json().get("results")
    except Exception:
        return None
