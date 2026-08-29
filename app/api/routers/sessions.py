from fastapi import APIRouter

from app.api.schemas.session import SessionCreate, SessionSummary
from app.api.services import container, session_service

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.post("", response_model=SessionSummary, status_code=201)
def create_session(body: SessionCreate):
    return session_service.create_session(container.get_store(), body.user_id)


@router.get("", response_model=list[SessionSummary])
def list_sessions():
    return session_service.list_sessions(container.get_store())