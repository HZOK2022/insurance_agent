"""从会话日志的 retrieval 事件解析引用 chunk 的原文(可追溯)。"""
from app.session.store import SessionStore


def get_citation(store: SessionStore, session_id: str, chunk_id: str) -> dict | None:
    for e in store.read(session_id):
        if e["type"] == "retrieval":
            for c in e["payload"]["chunks"]:
                if c["chunk_id"] == chunk_id:
                    return {"content": c["content"], "source": c["source"], "doc_id": c["doc_id"],
                            "version": c["version"], "section": c.get("section", "")}
    return None