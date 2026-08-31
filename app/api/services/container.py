"""惰性构建并缓存服务依赖(避免每次请求重载模型)。"""
from functools import lru_cache

from app.config import load
from app.llm.client import LLMClient
from app.retrieval.embedder import Embedder
from app.retrieval.qdrant_store import QdrantStore
from app.session.store import SessionStore


def get_cfg():
    """每次调用都从 .env 重新读取配置(不缓存):.env 是配置事实源,改它应立即生效。

    重单例(store/embedder/qstore/llm)各自 @lru_cache,只在首次构建时读一次 cfg;
    而这里的 cfg 是"动态值"供每轮上下文窗口/压缩阈值用,必须反映最新 .env。
    """
    return load()


@lru_cache(maxsize=1)
def get_store() -> SessionStore:
    return SessionStore(get_cfg().sqlite_path)


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    return Embedder(get_cfg().embedding_model, get_cfg().embedding_device, get_cfg().embedding_batch_size)


@lru_cache(maxsize=1)
def get_qstore() -> QdrantStore:
    return QdrantStore(get_cfg().qdrant_url, get_cfg().qdrant_collection, get_cfg().embedding_dim)


@lru_cache(maxsize=1)
def get_llm() -> LLMClient:
    _c = get_cfg()
    return LLMClient(_c.deepseek_api_key, _c.deepseek_base_url, _c.deepseek_model,
                     _c.deepseek_temperature, _c.deepseek_max_tokens,
                     max_retries=_c.llm_retry_max_tries,
                     retry_base_delay=_c.llm_retry_base_delay_ms / 1000.0,
                     retry_max_delay=_c.llm_retry_max_delay_ms / 1000.0)


@lru_cache(maxsize=1)
def get_insurance_bundle() -> dict:
    from app.businesses.insurance import bundle
    return bundle(get_embedder(), get_qstore(), get_cfg())