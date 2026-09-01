"""App 入口:仅初始化 FastAPI、注册路由、托管前端 dist。不写任何接口。"""
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routers import approval, audit, citation, config, health, prompt, sessions
from app.api.services import container
from app.api.ratelimit import RateLimiter

_rate = RateLimiter()


async def _auth_and_ratelimit(request: Request, call_next):
    """接口鉴权(Bearer token)+ 进程内限流:对 /api/*(health 除外)保护。

    - api_token 为空(开发模式)→ 只做限流,不做鉴权;api_rate_limit<=0 → 不限流。
    - 401 未授权、429 限流;其余放行(SSE 流式照常)。"""
    path = request.url.path
    if not (path.startswith("/api") and path != "/api/health"):
        return await call_next(request)
    cfg = container.get_cfg()
    tok = (getattr(cfg, "api_token", "") or "").strip()
    if tok:
        auth = request.headers.get("authorization", "")
        if auth != "Bearer " + tok:
            return JSONResponse(status_code=401, content={"detail": "未授权"})
    limit = int(getattr(cfg, "api_rate_limit", 60) or 60)
    window = int(getattr(cfg, "api_rate_window_seconds", 60) or 60)
    client = (request.client.host if request.client else "unknown")
    if not _rate.allow(client, limit, window):
        return JSONResponse(status_code=429, content={"detail": "请求过于频繁,请稍后再试"})
    return await call_next(request)


def create_app() -> FastAPI:
    app = FastAPI(title="insurance-agent", version="0.1")
    app.include_router(health.router)
    app.include_router(config.router)
    app.include_router(sessions.router)
    app.include_router(prompt.router)
    app.include_router(approval.router)
    app.include_router(audit.router)
    app.include_router(citation.router)
    app.middleware("http")(_auth_and_ratelimit)
    dist = os.path.join(os.path.dirname(__file__), "..", "web", "dist")
    if os.path.isdir(dist):
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8181, reload=True, reload_dirs=["app"])