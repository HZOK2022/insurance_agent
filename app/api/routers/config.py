from fastapi import APIRouter

from app.api.services import container

router = APIRouter(prefix="/api", tags=["config"])


@router.get("/config")
def get_config():
    """返回后端当前运行配置(前端据此显示上下文窗口等,不依赖可能过期的历史 request_context)。"""
    cfg = container.get_cfg()
    return {
        "context_window": cfg.context_window,
        "model": cfg.deepseek_model,
        "compaction_threshold_ratio": cfg.compaction_threshold_ratio,
        "compaction_retain_ratio": cfg.compaction_retain_ratio,
        "compaction_max_tokens": cfg.compaction_max_tokens,
        "max_tool_result_chars": cfg.max_tool_result_chars,
    }
