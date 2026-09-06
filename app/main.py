"""App 入口:仅初始化 FastAPI、注册路由、托管前端 dist。不写任何接口。"""
import logging
import os
import re
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routers import approval, audit, citation, config, health, kb, login, memory, metrics, prompt, sessions
from app.api.services import container, auth_service
from app.api.ratelimit import RateLimiter
import app.db as dbmod
from app.util.logging import setup_logging
from app.util.http_log import decode_body, should_capture_request, should_capture_response

_rate = RateLimiter()

# 访问日志:每个 /api 请求打一行(方法/路径/状态/耗时 + 会话 id + 用户 + 打码截断的入参/错误出参),
# 供 tail .log 排查"接口报错是鉴权/参数/500"。body 只记请求入参 + 错误(>=400)响应;敏感键打码、流式/文件上传不记。
# 默认过滤掉高频/机器对机器的轮询(GET /api/sessions、/api/config、/api/metrics、/api/health、/api/prompt 这些前端每 2-3s 拉一次的),
# 只在错误(>=400)或显式开启 log_api_verbose 时打。
# 可由 .env 配 LOG_API_VERBOSE=true 强制全打(诊断用)。
_API_LOG_VERBOSE = os.environ.get("LOG_API_VERBOSE", "").lower() in ("1", "true", "yes")
# 不打 INFO 的路径前缀(每分钟可能上百次);错误请求仍会打(>=400)
_QUIET_PREFIXES = (
    "/api/sessions",   # 前端拉会话列表(每 2-3s 一次)
    "/api/config",     # 前端拉系统配置(每 2-3s 一次)
    "/api/metrics",    # 机器对机器的 metrics 探针
    "/api/health",     # 容器/负载均衡健康检查
    "/api/prompt",     # 前端拉提示词(轮询)
)


def _is_quiet(path: str) -> bool:
    return any(path.startswith(p) for p in _QUIET_PREFIXES)


def _sid_from_path(path: str) -> str:
    m = re.search(r"/([0-9a-f]{12})(?:/|$)", path)
    return m.group(1) if m else ""


def _user_from_token(request: Request) -> str | None:
    """从 Bearer 会话 token 解 user_id(尽力而为,失败或未带返回 None;单次 index 查询)。"""
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return None
    try:
        return auth_service.validate_token(container.get_store(), auth[len("Bearer "):])
    except Exception:
        return None


async def _access_log(request: Request, call_next):
    _start = time.time()
    _cfg = container.get_cfg()
    _bodies = bool(getattr(_cfg, "log_api_bodies", True))
    _char = int(getattr(_cfg, "log_api_body_chars", 300) or 300)
    _req_ct = request.headers.get("content-type", "")
    _req_body: str | None = None
    if should_capture_request(request.url.path, _req_ct, _bodies):
        try:
            _req_body = decode_body(await request.body(), _char)
        except Exception:
            _req_body = None

    def _emit(status, duration, resp_body="-", sid="", user=None, path="", method=""):
        try:
            logging.getLogger("insurance.agent").info(
                "http sid=%s %s %s -> %s %dms user=%s req=%s resp=%s",
                sid, method, path, status, duration, user or "-", _req_body or "-", resp_body,
                extra={"trace_id": sid or None, "session_id": sid or None,
                       "http_status": status, "user": user or None})
        except Exception:
            pass

    resp = None
    try:
        resp = await call_next(request)
    except Exception as e:
        # 未处理异常:访问行要带上"是什么异常"(别再只写 EXCEPTION),并另记完整堆栈供诊断
        _emit(500, int((time.time() - _start) * 1000), f"EXCEPTION {type(e).__name__}: {e}",
              _sid_from_path(request.url.path), _user_from_token(request), request.url.path, request.method)
        logging.getLogger("insurance.agent").exception("http %s %s 未处理异常", request.method, request.url.path)
        raise

    _status = getattr(resp, "status_code", "?")
    _rct = (resp.headers.get("content-type", "") if resp is not None else "")
    _sid = _sid_from_path(request.url.path)
    _user = getattr(request.state, "user_id", None) or _user_from_token(request)
    _dur = int((time.time() - _start) * 1000)
    _path = request.url.path
    _method = request.method

    # 静默路径:GET + 200 + 命中白名单前缀 + 未开 verbose → 不打 INFO(避免前端轮询淹没日志)
    # 失败请求(>=400) / 非 GET / verbose 模式 → 仍正常打
    if (_status == 200 and _method == "GET" and _is_quiet(_path)
            and not _API_LOG_VERBOSE):
        return resp

    # 错误(>=400)且非流式:tee 响应体,流完再记 resp body(成功/流式不记出参,避免噪音/体积)
    if should_capture_response(request.url.path, _rct, _status, _bodies) and hasattr(resp, "body_iterator"):
        _buf = bytearray()
        _iter = resp.body_iterator

        async def _tee(_iter=_iter, _buf=_buf, _status=_status, _dur=_dur, _sid=_sid,
                       _user=_user, _path=request.url.path, _method=request.method, _char=_char):
            try:
                async for part in _iter:
                    _buf.extend(part)
                    yield part
            finally:
                _emit(_status, _dur, decode_body(bytes(_buf), _char) or "-",
                      _sid, _user, _path, _method)

        resp.body_iterator = _tee()
        return resp

    _emit(_status, _dur, "-", _sid, _user, request.url.path, request.method)
    return resp


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


def _check_admin(request: Request, cfg) -> bool:
    """检查当前请求是否是管理员。
    规则:
    - 全局 api_token 直接视为 admin (开发/单用户场景)
    - 会话 token: 取出 username → 查询用户 → 检查 role == 'admin'
    """
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return False
    bearer = auth[len("Bearer "):]
    global_tok = (getattr(cfg, "api_token", "") or "").strip()
    if global_tok and bearer == global_tok:
        # 全局配置的api_token默认是admin
        return True
    # 会话token:验证token后查用户role
    username = auth_service.validate_token(container.get_store(), bearer)
    if not username:
        return False
    user = container.get_store().get_user(username)
    if not user:
        return False
    return user.get("role", "agent") == "admin"


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
    # /api/kb/* 额外检查 admin 权限
    if path.startswith("/api/kb"):
        if not _check_admin(request, cfg):
            return JSONResponse(status_code=403, content={"detail": "仅管理员可操作知识库"})
    return await call_next(request)


def create_app() -> FastAPI:
    _c = container.get_cfg()
    setup_logging(getattr(_c, "log_level", "INFO"), getattr(_c, "log_dir", "data/logs"),
                  file_format=getattr(_c, "log_file_format", "text"))
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
    app.include_router(kb.router)
    app.include_router(memory.router)
    app.middleware("http")(_auth_and_ratelimit)
    app.middleware("http")(_access_log)   # 最外层访问日志:能记录到鉴权/限流/参数/500 等拒绝
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

    # 启动就绪横幅:uvicorn 自身的 "running on..." 行已被 _quiet_third_party 压掉,
    # 且轮询访问日志默认静默(见 _QUIET_PREFIXES),这里补一行醒目的"服务已就绪",
    # 让控制台/日志有明确的"启动成功"标志。
    @app.on_event("startup")
    async def _startup_ready():
        logging.getLogger("insurance.agent").info(
            "===== 后端已就绪 http://127.0.0.1:8181 (docs: /docs) · db=%s · 访问日志静默路径=%s =====",
            dbmod.dial(_c), ",".join(_QUIET_PREFIXES) if not _API_LOG_VERBOSE else "verbose(全打)",
        )
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8181, reload=True, reload_dirs=["app"])