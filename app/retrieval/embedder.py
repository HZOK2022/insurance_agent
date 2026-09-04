"""嵌入器:本地(bge-large-zh-v1.5 sentence-transformers)或在线(SiliconFlow /v1/embeddings)。

配置(app.config):
- EMBEDDING_BACKEND = local | api(默认 local;api 需 key,留空自动复用 RERANKING_EXTERNAL_API_KEY)
- EMBEDDING_API_URL / EMBEDDING_API_MODEL(BAAI/bge-large-zh-v1.5)/ EMBEDDING_API_TIMEOUT_SECONDS

embed(texts, on_progress=None) 接口对两后端一致(分批 + 每批回调 done/total),摄取/重排/进度全复用。
"""
from __future__ import annotations

import time


class Embedder:
    def __init__(self, model_path: str = "", device: str = "cpu", batch_size: int = 32,
                 backend: str = "local", api_url: str = "", api_key: str = "",
                 api_model: str = "", timeout: int = 30):
        self.backend = backend or "local"
        self.batch_size = batch_size
        if self.backend == "api":
            # 惰性:不加载本地模型
            self.api_url = api_url or "https://api.siliconflow.cn/v1/embeddings"
            self.api_model = api_model or "BAAI/bge-large-zh-v1.5"
            self.api_key = api_key or ""
            self.timeout = timeout
            self.model = None
        else:
            # 惰性 import:避免 uvicorn 启动时被 sentence-transformers/torch 拖慢
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer(model_path, device=device)

    def embed(self, texts: list[str], on_progress=None) -> list[list[float]]:
        """分批嵌入;on_progress(done, total) 每批回调一次。"""
        if not texts:
            return []
        if getattr(self, "backend", "local") == "api":
            return self._embed_api(texts, on_progress)
        return self._embed_local(texts, on_progress)

    def _embed_local(self, texts: list[str], on_progress=None) -> list[list[float]]:
        out: list[list[float]] = []
        total = len(texts)
        for i in range(0, total, self.batch_size):
            batch = texts[i:i + self.batch_size]
            vecs = self.model.encode(batch, batch_size=self.batch_size,
                                     normalize_embeddings=True).tolist()
            out.extend(vecs)
            if on_progress:
                on_progress(min(i + len(batch), total), total)
        return out

    def _embed_api(self, texts: list[str], on_progress=None) -> list[list[float]]:
        if not self.api_key:
            raise RuntimeError(
                "在线 embedding 未配置 API key:设 EMBEDDING_API_KEY,或复用 RERANKING_EXTERNAL_API_KEY")
        out: list[list[float]] = []
        total = len(texts)
        for i in range(0, total, self.batch_size):
            batch = texts[i:i + self.batch_size]
            out.extend(self._api_encode(batch))
            if on_progress:
                on_progress(min(i + len(batch), total), total)
        return out

    def _api_encode(self, batch: list[str]) -> list[list[float]]:
        import requests
        payload = {"input": batch, "model": self.api_model}
        headers = {"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"}
        last_err = ""
        for attempt in range(3):
            try:
                r = requests.post(self.api_url, headers=headers, json=payload, timeout=self.timeout)
                if r.status_code in (429, 500, 502, 503, 504):
                    last_err = f"HTTP {r.status_code}"
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                if r.status_code != 200:
                    raise RuntimeError(f"embedding API {r.status_code}: {r.text[:200]}")
                data = sorted((r.json().get("data") or []), key=lambda d: d.get("index", 0))
                return [d["embedding"] for d in data]
            except requests.RequestException as e:
                last_err = str(e)
                time.sleep(0.5 * (2 ** attempt))
        raise RuntimeError(f"embedding API 失败(重试后): {last_err}")


def build_embedder(cfg=None):
    """按配置构建嵌入器(backend=api 时 key 复用 rerank 的 SiliconFlow key)。"""
    from app.config import load
    c = cfg if cfg is not None else load()
    key = (getattr(c, "embedding_api_key", "") or "").strip() or           (getattr(c, "reranking_external_api_key", "") or "").strip()
    return Embedder(
        getattr(c, "embedding_model", "") or "",
        getattr(c, "embedding_device", "cpu"),
        int(getattr(c, "embedding_batch_size", 32) or 32),
        backend=(getattr(c, "embedding_backend", "local") or "local").strip().lower(),
        api_url=getattr(c, "embedding_api_url", ""),
        api_key=key,
        api_model=getattr(c, "embedding_api_model", ""),
        timeout=int(getattr(c, "embedding_api_timeout_seconds", 30) or 30),
    )
