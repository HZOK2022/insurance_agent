"""惰性构建并缓存服务依赖(避免每次请求重载模型)。"""
from functools import lru_cache

from app.config import load
from app.llm.client import LLMClient
from app.loop.loop import AgentLoop
from app.retrieval.embedder import Embedder
from app.retrieval.qdrant_store import QdrantStore
from app.session.store import SessionStore


@lru_cache(maxsize=1)
def get_cfg():
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
    return LLMClient(get_cfg().deepseek_api_key, get_cfg().deepseek_base_url, get_cfg().deepseek_model,
                     get_cfg().deepseek_temperature, get_cfg().deepseek_max_tokens)


@lru_cache(maxsize=1)
def get_loop() -> AgentLoop:
    return AgentLoop(get_store(), get_embedder(), get_qstore(), get_llm(), get_cfg())