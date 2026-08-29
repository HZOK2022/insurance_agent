"""会话业务层(与 HTTP 解耦)。"""
from app.session.store import SessionStore


def create_session(store: SessionStore, user_id: str) -> dict:
    return store.create_session(user_id=user_id)


def list_sessions(store: SessionStore) -> list[dict]:
    return store.list_sessions()