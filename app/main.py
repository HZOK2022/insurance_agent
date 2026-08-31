"""App 入口:仅初始化 FastAPI、注册路由、托管前端 dist。不写任何接口。"""
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routers import citation, config, health, prompt, sessions


def create_app() -> FastAPI:
    app = FastAPI(title="insurance-agent", version="0.1")
    app.include_router(health.router)
    app.include_router(config.router)
    app.include_router(sessions.router)
    app.include_router(prompt.router)
    app.include_router(citation.router)
    dist = os.path.join(os.path.dirname(__file__), "..", "web", "dist")
    if os.path.isdir(dist):
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8181, reload=True, reload_dirs=["app"])