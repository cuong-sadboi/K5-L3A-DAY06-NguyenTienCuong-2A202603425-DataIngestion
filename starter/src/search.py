"""Truy xuất top-k cho một câu hỏi theo chiến lược A/B/C; bước đánh giá dùng lại hàm retrieve().

Chạy thử từ thư mục starter/:  python -m src.search "câu hỏi" [--strategy C] [--chapter 2]
"""
from __future__ import annotations

import argparse
from typing import Any

from . import config
from .embeddings import SentenceTransformerEmbedder, create_embedder
from .indexing import database_url
from .ingestion import use_utf8_output
from .store import PgVectorStore


def retrieve(store: PgVectorStore, embedder: SentenceTransformerEmbedder, strategy: str, question: str,
             filters: dict[str, Any] | None = None,
             top_k: int = config.TOP_K) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Trả về (top-k, filter thực sự áp dụng). Chỉ chiến lược có use_filter mới dùng filter của câu hỏi."""
    spec = config.STRATEGIES[strategy]
    applied = dict(filters) if spec["use_filter"] and filters else None
    return store.search(spec["index"], embedder.embed_query(question), top_k, applied), applied


def main(argv: list[str] | None = None) -> None:
    use_utf8_output()
    parser = argparse.ArgumentParser(description="Truy xuất thử top-k theo chiến lược A/B/C.")
    parser.add_argument("question")
    parser.add_argument("--strategy", choices=sorted(config.STRATEGIES), help="mặc định chạy cả ba")
    parser.add_argument("--chapter", type=int, help="filter theo chương (chỉ chiến lược C dùng)")
    parser.add_argument("--top-k", type=int, default=config.TOP_K)
    args = parser.parse_args(argv)

    filters = {"chapter": args.chapter} if args.chapter is not None else None
    embedder = create_embedder()
    with PgVectorStore(database_url(), embedder.dimensions, embedder.model_name, config.VECTOR_TABLE) as store:
        for strategy in [args.strategy] if args.strategy else list(config.STRATEGIES):
            hits, applied = retrieve(store, embedder, strategy, args.question, filters, args.top_k)
            print(f"== Chiến lược {strategy} (index {config.STRATEGIES[strategy]['index']}, filter: {applied})")
            for hit in hits:
                snippet = " ".join(hit["text"].split())[:150]
                print(f"  {hit['rank']}. score {hit['score']:.4f} | trang {hit['page']} | chương {hit['chapter']} "
                      f"| mục {hit['section']} | {hit['chunk_id']}\n     {snippet}…")


if __name__ == "__main__":
    main()
