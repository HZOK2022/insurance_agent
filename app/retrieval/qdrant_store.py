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
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import (Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue, MatchExcept)

import pydantic

from app.retrieval.errors import RetrievalUnavailable, RetrievalClientError

logger = logging.getLogger(__name__)

_DOWN_COOLDOWN_S = 30.0   # 一次失败后进入冷却期:期内直接降级(不重复慢重试),期后可再试恢复


def _cid(chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "insurance/" + chunk_id))


def _is_client_error(e: Exception) -> bool:
    """确定性客户端错误(非上游服务故障):重试无效、冷却会误诊为"服务宕机"。
    含 pydantic 校验失败、ValueError、以及 Qdrant 4xx(请求非法/维度不符等)。
    5xx / 连接超时等瞬态故障仍走重试 + 冷却。"""
    if isinstance(e, (pydantic.ValidationError, ValueError)):
        return True
    if isinstance(e, UnexpectedResponse):
        code = getattr(e, "status_code", 0) or 0
        if 400 <= code < 500:
            return True
    return False


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
        """指数退避重试一次调用。
        - 上游瞬态故障(连不上/超时/5xx)→ 重试;重试耗尽/冷却期内抛 RetrievalUnavailable(服务宕机语义)。
        - 客户端参数/校验错误(ValidationError/4xx 等确定性 bug)→ **不重试、不进冷却期**,立即抛
          RetrievalClientError:重试必同样失败,冷却还会误诊为"服务宕机"并连累后续检索。"""
        if time.time() < self._down_until:
            raise RetrievalUnavailable(f"Qdrant {what} 不可用(冷却期内)")
        delay = self._retry_base_ms / 1000.0
        last: Exception | None = None
        for attempt in range(self._retry_tries + 1):
            try:
                return fn()
            except Exception as e:
                if _is_client_error(e):
                    # 确定性客户端错误:重试无效,冷却是误诊 → 直接抛,不重试不冷却
                    logger.error("Qdrant %s 客户端参数/校验错误(非重试型,不进冷却): %r",
                                 what, e, extra={"op": "qdrant.client_error", "what": what})
                    raise RetrievalClientError(f"Qdrant {what} 请求非法(客户端错误): {e}") from e
                last = e
                if attempt < self._retry_tries:
                    time.sleep(min(delay, self._retry_max_ms / 1000.0))
                    delay *= 2
        self._down_until = time.time() + _DOWN_COOLDOWN_S
        logger.error("Qdrant %s 失败(重试 %d 次后): %r → 进入冷却期", what, self._retry_tries, last)
        raise RetrievalUnavailable(f"Qdrant {what} 不可用(重试耗尽)") from last

    def delete_collection(self) -> None:
        self.client.delete_collection(self.collection)
        logger.info("【Qdrant】集合已删除:%s", self.collection,
                    extra={"op": "qdrant.delete_collection"})

    def upsert(self, items: list[dict]) -> None:
        pts = []
        for it in items:
            chunk_id = it["meta"]["chunk_id"]
            payload = dict(it["meta"])
            payload["content"] = it["content"]
            pts.append(PointStruct(id=_cid(chunk_id), vector=it["vector"], payload=payload))
        if pts:
            try:
                self.client.upsert(self.collection, points=pts)
            except Exception as e:
                logger.exception("【Qdrant】写入失败:集合 %s 写入 %d 个点出错(err=%r)", self.collection, len(pts), e,
                                 extra={"op": "qdrant.upsert", "points": len(pts)})
                raise
            logger.info("【Qdrant】写入成功:集合 %s 写入 %d 个向量点", self.collection, len(pts),
                        extra={"op": "qdrant.upsert", "points": len(pts)})

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

    def _valid_filter(self) -> Filter:
        """检索默认只取生效 chunk(D97):is_valid != 0。
        is_valid payload 存 int(1/0,见 set_is_valid_by_doc_id:141),故排除显式失效用 except=[0]。
        注意 qdrant-client>=1.x 的 MatchExcept 字段为 except_(alias=except)、类型为 List[int],
        且未开 populate_by_name → 只能用关键字别名 dict 写法 MatchExcept(**{'except': [0]})
        (except 是 Python 关键字,且必须是列表而非单 bool)。保留 is_valid=1,排除 is_valid=0。"""
        return Filter(must=[FieldCondition(key="is_valid", match=MatchExcept(**{'except': [0]}))])

    def search(self, vector: list[float], top_k: int = 20) -> list[dict]:
        def _q():
            filt = self._valid_filter()
            try:
                pts = self.client.query_points(self.collection, query=vector, limit=top_k,
                                               with_payload=True, query_filter=filt).points
            except AttributeError:
                pts = self.client.search(self.collection, query_vector=vector, limit=top_k,
                                         with_payload=True, query_filter=filt)
            out = []
            for h in pts:
                p = h.payload
                out.append({"chunk_id": p.get("chunk_id"), "score": float(h.score),
                            "content": p.get("content", ""),
                            "meta": {k: v for k, v in p.items() if k != "content"}})
            return out
        return self._retry_call("search", _q)

    def set_is_valid_by_doc_id(self, doc_id: str, is_valid: bool) -> None:
        """按 doc_id 批量更新该文档所有 chunk 的 payload.is_valid(第7条:MySQL+Qdrant 同生效同失效)。"""
        def _set():
            filter_ = Filter(must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))])
            self.client.set_payload(self.collection, {"is_valid": 1 if is_valid else 0}, points=filter_)
        r = self._retry_call("set_is_valid_by_doc_id", _set)
        logger.info("【Qdrant】文档生效状态已同步:doc_id=%s → is_valid=%s", doc_id, is_valid,
                    extra={"op": "qdrant.set_is_valid", "doc_id": doc_id, "is_valid": is_valid})
        return r

    def delete_by_doc_id(self, doc_id: str) -> int:
        """Delete all points where payload.doc_id == doc_id.
        Returns number of points deleted (estimated).
        """
        def _del():
            # Filter by doc_id in payload
            filter_ = Filter(
                must=[
                    FieldCondition(
                        key="doc_id",
                        match=MatchValue(value=doc_id)
                    )
                ]
            )
            result = self.client.delete(
                collection_name=self.collection,
                points_selector=filter_
            )
            # Qdrant doesn't return exact count in some versions, estimate from result
            if hasattr(result, 'status') and result.status == "completed":
                # We can't get exact count from delete response, but that's okay
                # The caller (KnowledgeStore) already has the exact count from SQLite
                return -1  # -1 means success, unknown count
            return 0
        r = self._retry_call("delete_by_doc_id", _del)
        logger.info("【Qdrant】按文档删除完成:doc_id=%s(结果=%s)", doc_id, r,
                    extra={"op": "qdrant.delete_by_doc_id", "doc_id": doc_id})
        return r
