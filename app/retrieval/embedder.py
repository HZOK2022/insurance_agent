"""bge-large-zh-v1.5 嵌入器(sentence-transformers,CPU)。"""
from __future__ import annotations


class Embedder:
    def __init__(self, model_path: str, device: str = "cpu", batch_size: int = 32):
        # 惰性 import:避免 uvicorn 启动时被 sentence-transformers/torch 拖慢
        # (否则重启后有 ~12s 监听窗口,期间前端请求失败会误显示"暂无会话")
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(model_path, device=device)
        self.batch_size = batch_size

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self.model.encode(list(texts), batch_size=self.batch_size, normalize_embeddings=True).tolist()
