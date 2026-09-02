from fastapi import APIRouter

from app.api.schemas.event import EventModel
from app.api.schemas.session import SessionCreate, SessionRename, SessionSummary
from app.api.services import container, session_service

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.post("", response_model=SessionSummary, status_code=201)
def create_session(body: SessionCreate):
    return session_service.create_session(container.get_store(), body.user_id)


@router.get("", response_model=list[SessionSummary])
def list_sessions():
    return session_service.list_sessions(container.get_store())

@router.post("/prune-empty")
def prune_empty_sessions(keep: str = ""):
    """清理"从没发过消息"的会话(切走即弃)。keep=当前激活会话 id,豁免不删。

    有效性 = 发过消息:会话无任何 user_message 事件即视为占位,切走即硬删。
    """
    return {"pruned": session_service.prune_empty_sessions(container.get_store(), keep)}

@router.get("/{sid}/events", response_model=list[EventModel])
def session_events(sid: str):
    return session_service.list_events(container.get_store(), sid)

@router.delete("/{sid}", status_code=204)
def delete_session(sid: str):
    session_service.delete_session(container.get_store(), sid)
    return None

@router.patch("/{sid}", response_model=SessionSummary)
def rename_session(sid: str, body: SessionRename):
    return session_service.rename_session(container.get_store(), sid, body.title)