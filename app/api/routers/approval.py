from fastapi import APIRouter
from pydantic import BaseModel

from app.api.services import container

router = APIRouter(prefix="/api/sessions", tags=["approval"])


class ApprovalDecisionIn(BaseModel):
    request_id: str
    status: str                    # approve | reject | defer
    edited_args: dict | None = None
    reason: str = ""
    decided_by: str = "user"


@router.post("/{sid}/approval")
def submit_approval(sid: str, body: ApprovalDecisionIn):
    """前端审批卡片提交决定。写 approval_decision 事件(审计)+ 唤醒正在等审批的 turn。"""
    store = container.get_store()
    payload = {"request_id": body.request_id, "status": body.status,
               "edited_args": body.edited_args, "reason": body.reason, "decided_by": body.decided_by}
    store.append(sid, "approval_decision", payload)
    ok = container.get_approval().decide(body.request_id, body.status,
                                         body.edited_args, body.reason, body.decided_by)
    return {"ok": ok, "request_id": body.request_id, "status": body.status}
