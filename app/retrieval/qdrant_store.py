"""Qdrant 存取(dense Cosine)。集合来自 config(insurance_knowledge,与其它项目隔离)。

黄金法则对齐:Qdrant 是**派生索引**(可从 SQLite KnowledgeStore 重建),不是事实源。
- 初始化连不上 → 不炸(进冷却期),上层降级 SQLite 关键词检索(Stage 1);
- search/all_chunks 对瞬时错误做指数退避重试(Stage 0),重试耗尽/冷却期内 → 抛 RetrievalUnavailable。
"""
from __future__ import annotations
import logging
import time
import uuid
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

from app.retrieval.errors import RetrievalUnavailable

logger = logging.getLogger(__name__)

_DOWN_COOLDOWN_S = 30.0   # 一次失败后进入冷却期:期内直接降级(不重复慢重试),期后可再试恢复


def _cid(chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "insurance/" + chunk_id))


class QdrantStore:
    def __init__(self, url: str, collection: str, dim: int,
                 retry_max_tries: int = 2, retry_base_delay_ms: int = 500,
                 retry_max_delay_ms: int = 3000):
        self.client = QdrantClient(url=url)
        self.collection = collection
        self.dim = dim
        self._retry_tries = int(retry_max_tries or 0)
        self._retry_base_ms = max(0, int(retry_base_delay_ms or 0))
        self._retry_max_ms = max(0, int(retry_max_delay_ms or 0))
        self._down_until = 0.0   # 冷却截止(epoch 秒);0=不冷却
        try:
            self._ensure()
        except Exception as e:
            logger.error("Qdrant 初始化失败(url=%s,collection=%s): %r → 知识库问答将降级为诚实拒答,直至 Qdrant 恢复(保费计算等本地功能不受影响)",
                         url, collection, e)
            self._down_until = time.time() + _DOWN_COOLDOWN_S

    def _ensure(self) -> None:
        names = [c.name for c in self.client.get_collections().collections]
        if self.collection not in names:
            self.client.create_collection(self.collection,
                                          vectors_config=VectorParams(size=self.dim, distance=Distance.COSINE))

    def is_down(self) -> bool:
        """是否处于不可用状态(冷却期内)。供启动体检/告警判断。"""
        return time.time() < self._down_until

    def _retry_call(self, what: str, fn):
        """指数退避重试一次调用;重试耗尽/冷却期内抛 RetrievalUnavailable。"""
        if time.time() < self._down_until:
            raise RetrievalUnavailable(f"Qdrant {what} 不可用(冷却期内)")
        delay = self._retry_base_ms / 1000.0
        last: Exception | None = None
        for attempt in range(self._retry_tries + 1):
            try:
                return fn()
            except Exception as e:
                last = e
                if attempt < self._retry_tries:
                    time.sleep(min(delay, self._retry_max_ms / 1000.0))
                    delay *= 2
        self._down_until = time.time() + _DOWN_COOLDOWN_S
        logger.error("Qdrant %s 失败(重试 %d 次后): %r → 进入冷却期", what, self._retry_tries, last)
        raise RetrievalUnavailable(f"Qdrant {what} 不可用(重试耗尽)") from last

    def delete_collection(self) -> None:
        self.client.delete_collection(self.collection)

    def upsert(self, items: list[dict]) -> None:
        pts = []
        for it in items:
            chunk_id = it["meta"]["chunk_id"]
            payload = dict(it["meta"])
            payload["content"] = it["content"]
            pts.append(PointStruct(id=_cid(chunk_id), vector=it["vector"], payload=payload))
        if pts:
            self.client.upsert(self.collection, points=pts)

    def all_chunks(self) -> list[dict]:
        """全量导出语料({chunk_id, content, meta})。不可用时抛 RetrievalUnavailable。"""

        def _scroll():
            out: list[dict] = []
            offset = None
            while True:
                pts, nxt = self.client.scroll(self.collection, limit=1000, with_payload=True, offset=offset)
                for h in pts:
                    p = h.payload
                    cid = p.get("chunk_id")
                    if not cid:
                        continue
                    out.append({"chunk_id": cid, "content": p.get("content", ""),
                                "meta": {k: v for k, v in p.items() if k != "content"}})
                if not nxt:
                    break
                offset = nxt
            return out
        return self._retry_call("all_chunks", _scroll)

    def search(self, vector: list[float], top_k: int = 20) -> list[dict]:
        def _q():
            try:
                pts = self.client.query_points(self.collection, query=vector, limit=top_k, with_payload=True).points
            except AttributeError:
                pts = self.client.search(self.collection, query_vector=vector, limit=top_k, with_payload=True)
            out = []
            for h in pts:
                p = h.payload
                out.append({"chunk_id": p.get("chunk_id"), "score": float(h.score),
                            "content": p.get("content", ""),
                            "meta": {k: v for k, v in p.items() if k != "content"}})
            return out
        return self._retry_call("search", _q)
