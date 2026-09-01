from dataclasses import asdict
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.services import container, auth_service

router = APIRouter(prefix="/api", tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(..., min_length=1, max_length=64, description="账号")
    password: str = Field(..., min_length=1, max_length=128, description="密码")
    remember: bool = Field(default=False, description="记住我:true→token 30 天,false→8 小时")


class LoginOut(BaseModel):
    token: str
    expires_at: str
    username: str
    display_name: str


class MeOut(BaseModel):
    username: str
    display_name: str


@router.post("/login", response_model=LoginOut, status_code=200)
def login(body: LoginIn):
    store = container.get_store()
    user = auth_service.authenticate(store, body.username, body.password)
    if user is None:
        # 不区分"账号不存在/密码错",统一 401,防账号枚举
        raise HTTPException(status_code=401, detail="账号或密码错误")
    res = auth_service.issue_token(store, user["username"], body.remember)
    return LoginOut(**asdict(res))


@router.get("/me", response_model=MeOut)
def me(request: Request):
    """当前登录用户(由会话 token 反查)。返回用户名/展示名,供前端用户菜单展示。
    仅接受会话 token(落在 auth_tokens 表),全局 api_token 不绑定用户故不适用。"""
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    username = auth_service.validate_token(container.get_store(), auth[len("Bearer "):])
    if not username:
        raise HTTPException(status_code=401, detail="登录已过期")
    user = container.get_store().get_user(username)
    return MeOut(username=username, display_name=(user or {}).get("display_name", ""))


@router.post("/logout", status_code=204)
def logout(request: Request):
    """撤销当前会话 token(若持的是 auth_tokens 内的会话 token)。全局 api_token 不在表中,撤销无副作用。"""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        auth_service.revoke_token(container.get_store(), auth[len("Bearer "):])
    return None
