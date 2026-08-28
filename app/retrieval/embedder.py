"""bge-large-zh-v1.5 嵌入器(sentence-transformers,CPU)。"""
from __future__ import annotations
from sentence_transformers import SentenceTransformer


class Embedder:
    def __init__(self, model_path: str, device: str = "cpu", batch_size: int = 32):
        self.model = SentenceTransformer(model_path, device=device)
        self.batch_size = batch_size

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self.model.encode(list(texts), batch_size=self.batch_size, normalize_embeddings=True).tolist()
