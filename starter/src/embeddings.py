"""Embedding chạy local bằng Sentence Transformers; không bao giờ thay bằng vector giả khi lỗi."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

import numpy as np

from . import config


class Embedder(Protocol):
    model_name: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[np.ndarray]:
        """Một vector cho mỗi văn bản, đúng thứ tự đầu vào."""
        ...

    def embed_query(self, question: str) -> np.ndarray:
        """Vector câu hỏi, cùng không gian với vector tài liệu."""
        ...


def model_revision(model_name: str) -> str | None:
    """Mã commit của model trong cache Hugging Face, ghi lại để tái lập đúng phiên bản."""
    try:
        from huggingface_hub import try_to_load_from_cache

        path = try_to_load_from_cache(model_name, "config.json")
    except Exception:
        return None
    parts = Path(path).parts if isinstance(path, str) else ()
    return parts[parts.index("snapshots") + 1] if "snapshots" in parts else None


class SentenceTransformerEmbedder:
    """Model e5 đa ngữ: thêm tiền tố "passage: " cho tài liệu, "query: " cho câu hỏi.

    Vector được chuẩn hóa về độ dài 1, nên khoảng cách cosine trong pgvector cho cùng thứ hạng
    với tích vô hướng. Nếu không tải được model, chương trình dừng với lỗi rõ ràng.
    """

    def __init__(self, model_name: str, query_prefix: str = "", passage_prefix: str = "",
                 batch_size: int = 32, device: str = "cpu") -> None:
        from sentence_transformers import SentenceTransformer  # import nặng, chỉ nạp khi cần

        self.model_name = model_name
        self.query_prefix = query_prefix
        self.passage_prefix = passage_prefix
        self.batch_size = batch_size
        self.model = SentenceTransformer(model_name, device=device)
        get_dimension = getattr(self.model, "get_embedding_dimension", None) or \
            self.model.get_sentence_embedding_dimension
        self.dimensions = int(get_dimension())
        self.max_seq_length = int(self.model.max_seq_length)

    def _encode(self, texts: list[str]) -> np.ndarray:
        vectors = self.model.encode(texts, batch_size=self.batch_size, normalize_embeddings=True,
                                    convert_to_numpy=True, show_progress_bar=False)
        if vectors.shape != (len(texts), self.dimensions) or not np.isfinite(vectors).all():
            raise RuntimeError("Model trả về vector sai số chiều hoặc có giá trị không hợp lệ")
        return vectors.astype(np.float32)

    def embed_documents(self, texts: list[str]) -> list[np.ndarray]:
        if not texts:
            return []
        return list(self._encode([self.passage_prefix + text for text in texts]))

    def embed_query(self, question: str) -> np.ndarray:
        return self._encode([self.query_prefix + question])[0]

    def count_tokens(self, texts: list[str]) -> list[int]:
        """Số token đưa vào model (kể cả tiền tố); lớn hơn max_seq_length nghĩa là bị cắt bớt."""
        encoded = self.model.tokenizer([self.passage_prefix + text for text in texts], add_special_tokens=True)
        return [len(ids) for ids in encoded["input_ids"]]

    def describe(self) -> dict[str, Any]:
        import sentence_transformers
        import torch
        import transformers

        return {
            "provider": "Sentence Transformers, chạy local (CPU)",
            "model": self.model_name,
            "revision": model_revision(self.model_name),
            "dimensions": self.dimensions,
            "max_seq_length": self.max_seq_length,
            "query_prefix": self.query_prefix,
            "passage_prefix": self.passage_prefix,
            "normalized": True,
            "cost_usd": 0,
            "versions": {
                "sentence-transformers": sentence_transformers.__version__,
                "transformers": transformers.__version__,
                "torch": torch.__version__,
            },
        }


def create_embedder() -> SentenceTransformerEmbedder:
    """Embedder dùng chung cho mọi chiến lược, theo config.EMBEDDING."""
    return SentenceTransformerEmbedder(**config.EMBEDDING)
