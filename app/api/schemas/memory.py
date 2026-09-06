# -*- coding: utf-8 -*-
"""记忆管理面板(P2.3)的请求体。读取复用 store.list_active / bucket_frame(纯 dict,无需响应模型)。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class MemorySaveIn(BaseModel):
    target: str = Field(..., description="user | cross_session | session(存到哪个桶)")
    category: str = Field(..., description="按 target 选:user→profile/preference/habit;cross_session→fact/policy/lesson/pending;session→instruction/context")
    key: str = Field(..., min_length=1, max_length=160, description="语义标识,如 称呼、style、product:尊享e生:免赔额")
    content: str = Field(..., min_length=1, max_length=4000, description="自包含的一句话要点,带关键数字/产品/版本")
    user_id: str | None = Field(default=None, description="空=按登录用户(会话 token);开发模式回退 u1")
    session_id: str | None = Field(default=None, description="session 桶需提供当前会话 id")


class MemoryForgetIn(BaseModel):
    target: str = Field(default="cross_session", description="去哪个桶遗忘(user/cross_session/session)")
    key: str = Field(..., min_length=1, max_length=160)
    reason: str | None = Field(default=None, description="遗忘原因(可审计)")
    user_id: str | None = None
    session_id: str | None = None
