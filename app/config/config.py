"""集中配置(app/config/config.py)。

AGENTS.md:所有上限/预算/审批阈值集中在 config,禁止散落硬编码。
启动时读取项目根的 .env(如存在)并校验数值阈值,缺失/非法大声失败。
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# 项目根 = app/config/config.py 上三级
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_dotenv(path: str) -> None:
    """极简 .env 解析(字符串值,不覆盖已存在的环境变量)。"""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


@dataclass(frozen=True)
class Config:
    # LLM(默认 DeepSeek)
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_temperature: float = 0.7
    deepseek_max_tokens: int = 32768
    # 嵌入(本地 bge-large-zh-v1.5)
    embedding_model: str = "bge-large-zh-v1.5"
    embedding_device: str = "cpu"
    embedding_batch_size: int = 32
    embedding_dim: int = 1024
    # 切块
    text_splitter: str = "character"
    chunk_size: int = 1000
    chunk_overlap: int = 200
    # 检索
    top_k: int = 20
    top_k_reranker: int = 3
    relevance_threshold: float = 0.0
    hybrid_bm25_weight: float = 0.5
    # 重排(external = SiliconFlow)
    reranking_engine: str = "external"          # external | local | ''
    reranking_external_url: str = "https://api.siliconflow.cn/v1/rerank"
    reranking_external_api_key: str = ""
    reranking_external_model: str = "BAAI/bge-reranker-v2-m3"
    reranking_external_timeout: int = 30
    rerank_max_length: int = 512
    # 上限(完整结果预算)
    max_steps_per_turn: int = 20
    max_retrieve_per_turn: int = 5   # 单轮最多检索次数;超过后强制基于现有资料诚实回答,防反复无效检索
    max_tokens_per_turn: int = 16000
    tool_timeout_seconds: int = 30
    max_tool_result_chars: int = 8000
    daily_token_budget_per_user: int = 200000
    # 审批
    write_tools_approval: str = "auto"
    approval_exempt_tools: tuple[str, ...] = ()
    # 鉴权(起步)
    internal_token: str = ""
    # 存储(与其它项目不冲突:collection/db 各自独立)
    sqlite_path: str = "data/agent.db"
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "insurance_knowledge"
    redis_url: str = "redis://:123456@101.132.61.48:6379/2"


_ENV = {
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "deepseek_base_url": "DEEPSEEK_BASE_URL",
    "deepseek_model": "DEEPSEEK_MODEL",
    "deepseek_temperature": "DEEPSEEK_TEMPERATURE",
    "deepseek_max_tokens": "DEEPSEEK_MAX_TOKENS",
    "embedding_model": "EMBEDDING_MODEL",
    "embedding_device": "EMBEDDING_DEVICE",
    "embedding_batch_size": "EMBEDDING_BATCH_SIZE",
    "embedding_dim": "EMBEDDING_DIM",
    "text_splitter": "TEXT_SPLITTER",
    "chunk_size": "CHUNK_SIZE",
    "chunk_overlap": "CHUNK_OVERLAP",
    "top_k": "TOP_K",
    "top_k_reranker": "TOP_K_RERANKER",
    "relevance_threshold": "RELEVANCE_THRESHOLD",
    "hybrid_bm25_weight": "HYBRID_BM25_WEIGHT",
    "reranking_engine": "RERANKING_ENGINE",
    "reranking_external_url": "RERANKING_EXTERNAL_URL",
    "reranking_external_api_key": "RERANKING_EXTERNAL_API_KEY",
    "reranking_external_model": "RERANKING_EXTERNAL_MODEL",
    "reranking_external_timeout": "RERANKING_EXTERNAL_TIMEOUT",
    "rerank_max_length": "RERANKING_MAX_LENGTH",
    "max_steps_per_turn": "MAX_STEPS_PER_TURN",
    "max_retrieve_per_turn": "MAX_RETRIEVE_PER_TURN",
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

_POSITIVE_INTS = ("embedding_batch_size", "chunk_size", "top_k", "top_k_reranker",
                  "reranking_external_timeout", "rerank_max_length",
                  "max_steps_per_turn", "max_retrieve_per_turn", "max_tokens_per_turn", "tool_timeout_seconds",
                  "max_tool_result_chars", "daily_token_budget_per_user")
_NONNEG_INTS = ("chunk_overlap",)
_FLOATS_01 = ("hybrid_bm25_weight",)

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
    if isinstance(default, float):
        try:
            return float(raw)
        except ValueError:
            raise ValueError(f"配置 {name}={raw!r} 不是数字")
    if isinstance(default, tuple):
        return tuple(x.strip() for x in raw.split(",") if x.strip())
    return raw


def _validate(cfg: Config) -> None:
    for name in _POSITIVE_INTS:
        if getattr(cfg, name) <= 0:
            raise ValueError(f"配置 {name}={getattr(cfg, name)!r} 必须 > 0")
    for name in _NONNEG_INTS:
        if getattr(cfg, name) < 0:
            raise ValueError(f"配置 {name}={getattr(cfg, name)!r} 必须 >= 0")
    if not (0 <= cfg.hybrid_bm25_weight <= 1):
        raise ValueError(f"配置 hybrid_bm25_weight={cfg.hybrid_bm25_weight!r} 必须在 [0,1]")


def load() -> Config:
    load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))
    kw = {}
    for name, env_key in _ENV.items():
        raw = os.environ.get(env_key)
        if raw is None or raw == "":
            continue
        kw[name] = _coerce(name, raw)
    cfg = Config(**kw)
    _validate(cfg)
    return cfg
