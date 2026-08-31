# -*- coding: utf-8 -*-
"""⑨ 审计/追溯 + ⑥ 可观测 的 HTTP 视图。只读 events(SQLite),不写历史。"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

from app.api.services import container
from app.audit import queries as audit
from app.observability import metrics as obs

router = APIRouter(prefix="/api", tags=["audit"])


def _prices():
    cfg = container.get_cfg()
    return cfg.llm_price_input_per_1m, cfg.llm_price_output_per_1m


@router.get("/audit")
def audit_list(session_id: str | None = None, user_id: str | None = None,
               since: str | None = None, until: str | None = None, limit: int = 50):
    """查询视图:按会话/客服/时间检索历史问答(审计留证、合规)。"""
    lim = max(1, min(limit, 500))
    items = audit.history_qa(container.get_store(), session_id=session_id, user_id=user_id,
                             since=since, until=until, limit=lim)
    return {"count": len(items), "items": items}


@router.get("/audit/{sid}/export")
def audit_export(sid: str, fmt: str = "jsonl"):
    """导出某会话完整事件流(审计报表,给监管/内审)。fmt: jsonl|json|csv。"""
    _cfg = container.get_cfg()
    store = container.get_store()
    text = audit.export_session(store, sid, fmt)
    media = {"jsonl": "application/x-ndjson", "json": "application/json", "csv": "text/csv"}.get(fmt, "application/octet-stream")
    ext = fmt if fmt in ("jsonl", "json", "csv") else "jsonl"
    return Response(content=text, media_type=media,
                    headers={"Content-Disposition": 'attachment; filename="%s.%s"' % (sid, ext)})


@router.get("/observability")
def obs_overall():
    """全局可观测汇总:tokens/成本/错误/重试/审批/平均时延,及 per-session 明细。"""
    pin, pout = _prices()
    return obs.overall_metrics(container.get_store(), price_in_per_1m=pin, price_out_per_1m=pout)


@router.get("/observability/{sid}")
def obs_session(sid: str):
    """某会话的可观测汇总。"""
    pin, pout = _prices()
    return obs.session_metrics(container.get_store(), sid, price_in_per_1m=pin, price_out_per_1m=pout)
