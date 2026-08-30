"""会话业务层(与 HTTP 解耦)。"""
from app.session.store import SessionStore


def create_session(store: SessionStore, user_id: str) -> dict:
    return store.create_session(user_id=user_id)


def list_sessions(store: SessionStore) -> list[dict]:
    return store.list_sessions()

def list_events(store: SessionStore, session_id: str) -> list[dict]:
    """读取某会话的完整事件日志(按 seq 升序)。"""
    return store.read(session_id)

def get_session(store: SessionStore, session_id: str) -> dict | None:
    return store.get_session(session_id)

def delete_session(store: SessionStore, session_id: str) -> bool:
    """软删除会话(从列表/视图移除;events 保留,遵守 append-only)。"""
    return store.delete_session(session_id)

def rename_session(store: SessionStore, session_id: str, title: str) -> dict | None:
    return store.rename_session(session_id, title)