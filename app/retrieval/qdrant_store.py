"""Qdrant 存取(dense Cosine)。集合来自 config(insurance_knowledge,与其它项目隔离)。"""
from __future__ import annotations
import uuid
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct


def _cid(chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "insurance/" + chunk_id))


class QdrantStore:
    def __init__(self, url: str, collection: str, dim: int):
        self.client = QdrantClient(url=url)
        self.collection = collection
        self.dim = dim
        self._ensure()

    def _ensure(self) -> None:
        names = [c.name for c in self.client.get_collections().collections]
        if self.collection not in names:
            self.client.create_collection(self.collection,
                                          vectors_config=VectorParams(size=self.dim, distance=Distance.COSINE))

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

    def search(self, vector: list[float], top_k: int = 20) -> list[dict]:
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
