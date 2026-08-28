"""集中配置(app/config/config.py)。

AGENTS.md:所有上限/预算/审批阈值集中在 config,禁止散落硬编码。
从环境变量(.env)读取 + 默认值;启动时校验数值阈值,缺失/非法大声失败。
"""
from __future__ import annotations

import os
from dataclasses import dataclass

@dataclass(frozen=True)
class Config:
    # LLM
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    # 嵌入
    embedding_provider: str = "bge_m3"
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024
    # 上限(完整结果预算)
    max_steps_per_turn: int = 20
    max_tokens_per_turn: int = 16000
    tool_timeout_seconds: int = 30
    max_tool_result_chars: int = 8000
    daily_token_budget_per_user: int = 200000
    # 审批
    write_tools_approval: str = "auto"
    approval_exempt_tools: tuple[str, ...] = ()
    # 鉴权(起步)
    internal_token: str = ""
    # 存储
    sqlite_path: str = "data/agent.db"
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_collection: str = "insurance_knowledge"
    redis_url: str = "redis://127.0.0.1:6379/0"

_ENV = {
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "deepseek_base_url": "DEEPSEEK_BASE_URL",
    "deepseek_model": "DEEPSEEK_MODEL",
    "embedding_provider": "EMBEDDING_PROVIDER",
    "embedding_model": "EMBEDDING_MODEL",
    "embedding_dim": "EMBEDDING_DIM",
    "max_steps_per_turn": "MAX_STEPS_PER_TURN",
    "max_tokens_per_turn": "MAX_TOKENS_PER_TURN",
    "tool_timeout_seconds": "TOOL_TIMEOUT_SECONDS",
    "max_tool_result_chars": "MAX_TOOL_RESULT_CHARS",
    "daily_token_budget_per_user": "DAILY_TOKEN_BUDGET_PER_USER",
    "write_tools_approval": "WRITE_TOOLS_APPROVAL",
    "approval_exempt_tools": "APPROVAL_EXEMPT_TOOLS",
    "internal_token": "INTERNAL_TOKEN",
    "sqlite_path": "SQLITE_PATH",
    "qdrant_url": "QDRANT_URL",
    "qdrant_collection": "QDRANT_COLLECTION",
    "redis_url": "REDIS_URL",
}

_POSITIVE_INTS = ("embedding_dim", "max_steps_per_turn", "max_tokens_per_turn",
                  "tool_timeout_seconds", "max_tool_result_chars", "daily_token_budget_per_user")

_DEFAULT = Config()


def _coerce(name: str, raw: str):
    default = getattr(_DEFAULT, name)
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes")
    if isinstance(default, int):
        try:
            return int(raw)
        except ValueError:
            raise ValueError(f"配置 {name}={raw!r} 不是整数")
    if isinstance(default, tuple):
        return tuple(x.strip() for x in raw.split(",") if x.strip())
    return raw


def _validate(cfg: Config) -> None:
    for name in _POSITIVE_INTS:
        if getattr(cfg, name) <= 0:
            raise ValueError(f"配置 {name}={getattr(cfg, name)!r} 必须 > 0")


def load() -> Config:
    kw = {}
    for name, env_key in _ENV.items():
        raw = os.environ.get(env_key)
        if raw is None or raw == "":
            continue
        kw[name] = _coerce(name, raw)
    cfg = Config(**kw)
    _validate(cfg)
    return cfg
