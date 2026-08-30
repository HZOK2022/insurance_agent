"""混合检索:dense(Qdrant 余弦) + sparse(BM25) 按 hybrid_bm25_weight 融合,再接外排。

设计:BM25 作为"派生索引",从 Qdrant 语料(chunk/content/meta)懒加载构建一次,
只读;检索时与 dense 分数 min-max 归一后按 weight 加权融合,取 top_k 再交 rerank。
不触事实源;检索层可复用。
"""
from __future__ import annotations
import math
import re
from typing import Iterable


_CJK = re.compile(r"[\u4e00-\u9fff]")
_WORD = re.compile(r"[A-Za-z0-9]+")


def tokenize(text: str) -> list[str]:
    """极简 CJK 分词:英文/数字按词,中文按单字(unigram)。依赖无关,可测。"""
    out: list[str] = []
    for m in _WORD.finditer(text):
        out.append(m.group().lower())
    for ch in text:
        if _CJK.match(ch):
            out.append(ch)
    return out


class BM25Index:
    """Okapi BM25 索引(对一个 chunk 语料构建,只读,构建后可反复 search)。"""

    def __init__(self, chunks: Iterable[dict]):
        # chunks: [{chunk_id, content, meta}, ...] 来自 qstore.all_chunks()
        self.doc_ids: list[str] = []
        self.doc_lens: list[int] = []
        self.tf: list[dict[str, int]] = []
        self.df: dict[str, int] = {}
        self.chunks_by_id: dict[str, dict] = {}
        avg = 0
        for ch in chunks:
            cid = ch.get("chunk_id")
            if not cid:
                continue
            content = ch.get("content", "")
            toks = tokenize(content)
            self.doc_ids.append(cid)
            self.doc_lens.append(len(toks))
            tfd: dict[str, int] = {}
            for t in toks:
                tfd[t] = tfd.get(t, 0) + 1
            self.tf.append(tfd)
            for t in set(toks):
                self.df[t] = self.df.get(t, 0) + 1
            self.chunks_by_id[cid] = {"chunk_id": cid, "content": content, "meta": ch.get("meta", {})}
            avg += len(toks)
        self.N = len(self.doc_ids)
        self.avgdl = (avg / self.N) if self.N else 1.0

    def _idf(self, t: str) -> float:
        n = self.df.get(t, 0)
        if n == 0:
            return 0.0
        return math.log((self.N - n + 0.5) / (n + 0.5) + 1.0)

    def search(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        q = [t for t in tokenize(query) if self.df.get(t)]
        if not q or not self.N:
            return []
        scored: list[tuple[int, float]] = []
        for i in range(self.N):
            s = 0.0
            dl = self.doc_lens[i]
            tfd = self.tf[i]
            for t in q:
                tf = tfd.get(t, 0)
                if not tf:
                    continue
                idf = self._idf(t)
                # Okapi BM25 (k1=1.2, b=0.75),分母/参数写死常量,便于对照
                s += idf * (tf * 2.2) / (tf + 1.2 * (1 - 0.75 + 0.75 * dl / self.avgdl))
            if s:
                scored.append((i, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [(self.doc_ids[i], s) for i, s in scored[:top_k]]


def _normalize(x: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0 if x <= lo else 1.0
    return (x - lo) / (hi - lo)


def fuse_and_pick(dense: dict[str, float], bm25: dict[str, float], weight: float,
                  top_k: int) -> list[tuple[str, float]]:
    """把两路分数 min-max 归一后按 weight 加权融合,返回 top_k (chunk_id, combined_score)。"""
    ids = set(dense) | set(bm25)
    if not ids:
        return []
    w = max(0.0, min(1.0, weight))
    dl, dh = (min(dense.values()), max(dense.values())) if dense else (0.0, 1.0)
    bl, bh = (min(bm25.values()), max(bm25.values())) if bm25 else (0.0, 1.0)
    pairs: list[tuple[str, float]] = []
    for cid in ids:
        dn = _normalize(dense.get(cid, dl), dl, dh)
        bn = _normalize(bm25.get(cid, bl), bl, bh)
        pairs.append((cid, (1.0 - w) * dn + w * bn))
    pairs.sort(key=lambda x: x[1], reverse=True)
    return pairs[:top_k]
