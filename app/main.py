"""App 入口:仅初始化 FastAPI、注册路由、托管前端 dist。不写任何接口。"""
import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routers import approval, audit, citation, config, health, login, metrics, prompt, sessions
from app.api.services import container, auth_service
from app.api.ratelimit import RateLimiter
from app.util.logging import setup_logging

_rate = RateLimiter()


def _token_valid(cfg, request: Request) -> bool:
    """凭证有效:全局 api_token 或 auth_tokens 表内(未过期)会话 token。"""
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return False
    bearer = auth[len("Bearer "):]
    global_tok = (getattr(cfg, "api_token", "") or "").strip()
    if global_tok and bearer == global_tok:
        return True
    try:
        if auth_service.validate_token(container.get_store(), bearer):
            return True
    except Exception:
        return False
    return False


async def _auth_and_ratelimit(request: Request, call_next):
    """接口鉴权(Bearer token)+ 进程内限流:对 /api/*(health/login 除外)保护。

    - api_token 为空(开发模式)→ 不做全局校验,但仍认会话 token;api_rate_limit<=0 → 不限流。
    - /api/login 公开(否则无法登录),但同样受限流保护防爆破。
    - 401 未授权、429 限流;其余放行(SSE 流式照常)。"""
    path = request.url.path
    is_api = path.startswith("/api") and path != "/api/health"
    if not is_api:
        return await call_next(request)
    # 限流:所有 /api(除 health)均生效,含 /api/login
    cfg = container.get_cfg()
    limit = int(getattr(cfg, "api_rate_limit", 60) or 60)
    window = int(getattr(cfg, "api_rate_window_seconds", 60) or 60)
    client = (request.client.host if request.client else "unknown")
    if limit > 0 and not _rate.allow(client, limit, window):
        return JSONResponse(status_code=429, content={"detail": "请求过于频繁,请稍后再试"})
    # 鉴权:/api/login 公开;api_token 为空=开发模式,不做全局校验(缺失 token 也不 401);
    # api_token 非空则必须有效凭证(全局 token 或 会话 token)。
    if path == "/api/login":
        return await call_next(request)
    global_tok = (getattr(cfg, "api_token", "") or "").strip()
    if global_tok and not _token_valid(cfg, request):
        return JSONResponse(status_code=401, content={"detail": "未授权"})
    return await call_next(request)


def create_app() -> FastAPI:
    setup_logging(getattr(container.get_cfg(), "log_level", "INFO"), getattr(container.get_cfg(), "log_dir", "data/logs"))
    app = FastAPI(title="insurance-agent", version="0.1")
    app.include_router(health.router)
    app.include_router(config.router)
    app.include_router(sessions.router)
    app.include_router(prompt.router)
    app.include_router(approval.router)
    app.include_router(audit.router)
    app.include_router(citation.router)
    app.include_router(login.router)
    app.include_router(metrics.router)
    app.middleware("http")(_auth_and_ratelimit)
    # 首次启动播种管理员账号(users 为空才播种,不覆盖既有)
    auth_service.seed_admin_if_empty(container.get_store(), container.get_cfg().login_user, container.get_cfg().login_password)
    # 启动对账:补齐进程崩溃/断电遗留的悬挂 turn(补 turn_end reason=interrupted;失败不阻断启动)
    try:
        _fixed = container.get_store().reconcile_dangling_turns()
        if _fixed:
            logging.getLogger(__name__).warning("reconcile_dangling_turns: 补写 %d 个悬挂 turn 的终结事件", _fixed)
    except Exception:
        logging.getLogger(__name__).exception("reconcile_dangling_turns 失败(不阻断启动)")
    # 启动依赖体检:启动即构造 Qdrant(容错,失败已内部 logger.error;首次提问不再为连不上等重试),
    # 并对本地 SQLite 逐项探测打日志;绝不阻断启动。
    try:
        _cfg = container.get_cfg()
        _qstore = container.get_qstore()
        from app.startup_deps import report_startup_dependencies
        report_startup_dependencies(_cfg, _qstore)
    except Exception:
        logging.getLogger(__name__).exception("startup deps 体检失败(不阻断启动)")
    dist = os.path.join(os.path.dirname(__file__), "..", "web", "dist")
    if os.path.isdir(dist):
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8181, reload=True, reload_dirs=["app"])