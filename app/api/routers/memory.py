# -*- coding: utf-8 -*-
"""记忆管理面板(P2.3):GET/POST/DELETE /api/memory + 压实预览。

- 鉴权:会话 token 身份 → 只操作"自己"的记忆;无 token(开发模式)回退 'u1'(与前端会话缺省一致)。
- 事实源:memory_entries(SQLite),面板操作直接读写;powered by memory_enabled 开关(关=只读返回 enabled:false)。
- 与 agent 运行时的会话内记忆同库(container.get_memory_store()),故面板能看到 agent 在会话里存下的记忆。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.api.schemas.memory import MemorySaveIn, MemoryForgetIn
from app.api.services import container, auth_service
from app.memory.tools import _TARGETS

router = APIRouter(prefix="/api", tags=["memory"])


def _current_user(request: Request, user_id: str | None) -> str:
    """解析"当前用户":显式 user_id 优先;否则会话 token(登录身份);否则开发模式默认 'u1'。"""
    if user_id:
        return user_id
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        try:
            username = auth_service.validate_token(container.get_store(), auth[len("Bearer "):])
        except Exception:
            username = None
        if username:
            return username
    return "u1"


def _mstore():
    return container.get_memory_store()


def _limit():
    return int(getattr(container.get_cfg(), "memory_bucket_limit_chars", 2000) or 2000)


def _enabled():
    return bool(getattr(container.get_cfg(), "memory_enabled", False))


@router.get("/memory")
def memory_list(request: Request, user_id: str | None = None, session_id: str | None = None):
    """列当前用户的记忆:三桶条目 + 各桶注入帧 + 字符量。session 桶仅当传了 session_id。"""
    user = _current_user(request, user_id)
    limit = _limit()
    if not _enabled():
        return {"enabled": False, "user_id": user, "limit": limit, "buckets": {}, "frames": {}, "counts": {}}
    mstore = _mstore()
    buckets: dict = {}
    frames: dict = {}
    counts: dict = {}
    for b in ("user", "cross_session"):
        buckets[b] = mstore.list_active(user, bucket=b)
        frames[b] = mstore.bucket_frame(user, b, limit_chars=limit)
        counts[b] = mstore.count_chars(user, bucket=b)
    if session_id:
        buckets["session"] = mstore.list_active(user, bucket="session", session_id=session_id)
        frames["session"] = mstore.bucket_frame(user, "session", session_id, limit_chars=limit)
        counts["session"] = mstore.count_chars(user, bucket="session", session_id=session_id)
    # 各桶允许的类型(供前端表单下拉)
    target_meta = {t: {"label": s["label"], "cat": sorted(s["cat"])} for t, s in _TARGETS.items()}
    return {"enabled": True, "user_id": user, "limit": limit,
            "buckets": buckets, "frames": frames, "counts": counts, "targets": target_meta}


@router.post("/memory")
def memory_save(body: MemorySaveIn, request: Request):
    """新增/更新一条记忆(同 key 覆盖,留历史)。按 target 校验 category,桶超限自动压实。"""
    user = _current_user(request, body.user_id)
    spec = _TARGETS.get(body.target)
    if not spec:
        raise HTTPException(status_code=400, detail=f"未知 target: {body.target}")
    if body.category not in spec["cat"]:
        raise HTTPException(status_code=400,
                            detail=f"category={body.category} 不适用于 {body.target}(应为 {sorted(spec['cat'])})")
    if not _enabled():
        raise HTTPException(status_code=403, detail="未启用记忆系统(MEMORY_ENABLED)")
    if body.target == "session" and not body.session_id:
        raise HTTPException(status_code=400, detail="会话记忆需要当前会话(session_id)")
    mstore = _mstore()
    limit = _limit()
    res = mstore.save(user, body.target, body.category, body.key, body.content,
                      scope=None if body.target == "session" else "user",
                      confidence="explicit", source_session_id=body.session_id)
    archived = []
    if mstore.count_chars(user, bucket=body.target, session_id=body.session_id) > limit:
        archived = mstore.compact_bucket(user, body.target, int(limit * 0.3), body.session_id)
    verb = "更新" if not res["is_new"] else "新增"
    return {"ok": True, "user_id": user, "bucket": body.target, "entry": res,
            "archived": len(archived), "message": f"已{verb}【{spec['label']}】:key={body.key}"}


@router.delete("/memory")
def memory_forget(request: Request, target: str = "cross_session", key: str = "",
                  user_id: str | None = None, session_id: str | None = None):
    """遗忘一条记忆(标记 archived,历史保留)。"""
    if not key:
        raise HTTPException(status_code=400, detail="缺 key")
    user = _current_user(request, user_id)
    if not _enabled():
        raise HTTPException(status_code=403, detail="未启用记忆系统(MEMORY_ENABLED)")
    ok = _mstore().forget(user, key, bucket=target, session_id=session_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"未找到要遗忘的记忆:key={key}")
    label = _TARGETS.get(target, {}).get("label", target)
    return {"ok": True, "user_id": user, "bucket": target, "message": f"已遗忘【{label}】:key={key}"}


@router.post("/memory/compact")
def memory_compact(request: Request, target: str, user_id: str | None = None, session_id: str | None = None):
    """手动触发某桶压实(规则压缩到 30%):返回归档条数。"""
    user = _current_user(request, user_id)
    if not _enabled():
        raise HTTPException(status_code=403, detail="未启用记忆系统(MEMORY_ENABLED)")
    limit = _limit()
    archived = _mstore().compact_bucket(user, target, int(limit * 0.3), session_id)
    return {"ok": True, "user_id": user, "bucket": target, "archived": len(archived), "entries": archived}
