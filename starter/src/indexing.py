"""Nạp corpus vào pgvector cho từng index (A, B): chia chunk, tạo embedding, lưu và đo đạc.

Chạy từ thư mục starter/:  python -m src.indexing  [--rebuild]
Chạy lại an toàn: chunk không đổi (cùng id, nội dung, model) được bỏ qua; chunk của phiên bản tài liệu
cũ bị xóa. `--rebuild` xóa index rồi nạp lại toàn bộ, dùng khi cần đo lại thời gian ingest đầy đủ.
Kết quả: results/index_stats.json, results/index_runs.jsonl (lịch sử mỗi lần chạy), results/chunks/<index>.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime
from typing import Any

from dotenv import load_dotenv

from . import config
from .chunking import build_chunks
from .embeddings import SentenceTransformerEmbedder, create_embedder
from .ingestion import load_source, use_utf8_output
from .models import Chunk, DocumentPage
from .store import PgVectorStore


def database_url() -> str:
    load_dotenv(config.ENV_FILE)
    url = os.getenv("DATABASE_URL")
    if not url:
        sys.exit(f"Thiếu DATABASE_URL: sao chép starter/.env.example thành {config.ENV_FILE}.")
    return url


def load_content_pages() -> list[DocumentPage]:
    """Trang nội dung của mọi nguồn, kèm sha256 của file nguồn để ghi vào từng chunk."""
    pages = []
    for spec in config.SOURCES:
        source_pages, info = load_source(spec)
        for page in source_pages:
            if page.metadata["status"] == "content":
                page.metadata["doc_sha256"] = info["sha256"]
                pages.append(page)
    return pages


def chunk_corpus(pages: list[DocumentPage], index_name: str) -> list[Chunk]:
    sha_by_document = {page.document_id: page.metadata["doc_sha256"] for page in pages}
    chunks = build_chunks(pages, index_name)
    if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
        raise RuntimeError(f"Index {index_name}: chunk_id bị trùng")
    for chunk in chunks:
        chunk.metadata["doc_sha256"] = sha_by_document[chunk.document_id]
    return chunks


def sync_index(store: PgVectorStore, embedder: SentenceTransformerEmbedder, index_name: str,
               chunks: list[Chunk]) -> dict[str, Any]:
    """Đồng bộ một index với danh sách chunk hiện tại; chỉ embed chunk mới hoặc đã đổi nội dung/model."""
    started = time.perf_counter()
    counts = {"inserted": 0, "updated": 0, "unchanged": 0, "deleted": 0}
    embed_seconds = 0.0
    by_document: dict[str, list[Chunk]] = defaultdict(list)
    for chunk in chunks:
        by_document[chunk.document_id].append(chunk)
    for document_id, document_chunks in by_document.items():
        existing = store.existing(index_name, document_id)
        todo = [c for c in document_chunks
                if existing.get(c.chunk_id) != (c.metadata["content_hash"], embedder.model_name)]
        counts["unchanged"] += len(document_chunks) - len(todo)
        counts["updated"] += sum(c.chunk_id in existing for c in todo)
        counts["inserted"] += sum(c.chunk_id not in existing for c in todo)
        embed_started = time.perf_counter()
        vectors = embedder.embed_documents([c.text for c in todo])
        embed_seconds += time.perf_counter() - embed_started
        store.upsert(index_name, todo, vectors)
    counts["deleted"] = store.remove_obsolete(
        index_name, {document_id: {c.chunk_id for c in items} for document_id, items in by_document.items()})
    rows = store.count(index_name)
    return {**counts, "rows_in_db": rows, "rows_match_chunks": rows == len(chunks),
            "embed_seconds": round(embed_seconds, 2), "sync_seconds": round(time.perf_counter() - started, 2)}


def describe_chunks(chunks: list[Chunk], max_seq_length: int) -> dict[str, Any]:
    lengths = [len(chunk.text) for chunk in chunks]
    tokens = [chunk.metadata["tokens"] for chunk in chunks]
    deciles = statistics.quantiles(lengths, n=10)
    return {
        "chunks": len(chunks),
        "pages_covered": len({(chunk.document_id, chunk.page) for chunk in chunks}),
        "total_chars": sum(lengths),
        "chars": {"min": min(lengths), "p10": round(deciles[0]), "median": statistics.median(lengths),
                  "mean": round(statistics.mean(lengths), 1), "p90": round(deciles[-1]), "max": max(lengths)},
        "chunks_under_100_chars": sum(length < 100 for length in lengths),
        "tokens": {"mean": round(statistics.mean(tokens), 1), "max": max(tokens),
                   "over_max_seq_length": sum(count > max_seq_length for count in tokens)},
    }


def write_chunk_dump(index_name: str, chunks: list[Chunk]) -> None:
    """Toàn bộ chunk của index (để đọc khi chấm coherence và để người chấm kiểm tra)."""
    folder = config.RESULTS_DIR / "chunks"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f"{index_name}.jsonl").open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            meta = chunk.metadata
            handle.write(json.dumps({
                "chunk_id": chunk.chunk_id, "document_id": chunk.document_id, "page": chunk.page,
                "chapter": meta["chapter"], "section": meta["section"], "section_title": meta["section_title"],
                "char_start": meta["char_start"], "char_end": meta["char_end"], "chars": len(chunk.text),
                "tokens": meta["tokens"], "text": chunk.text,
            }, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> None:
    use_utf8_output()
    parser = argparse.ArgumentParser(description="Nạp corpus vào pgvector cho các index A, B.")
    parser.add_argument("--rebuild", action="store_true", help="xóa index cũ rồi nạp lại toàn bộ")
    args = parser.parse_args(argv)

    run_started = time.perf_counter()
    pages = load_content_pages()
    load_started = time.perf_counter()
    embedder = create_embedder()
    model_load_seconds = round(time.perf_counter() - load_started, 2)
    indexes: dict[str, Any] = {}
    with PgVectorStore(database_url(), embedder.dimensions, embedder.model_name, config.VECTOR_TABLE) as store:
        store.create_schema()
        server = store.server_info()
        for index_name, params in config.INDEXES.items():
            if args.rebuild:
                store.delete_strategy(index_name)
            chunk_started = time.perf_counter()
            chunks = chunk_corpus(pages, index_name)
            chunk_seconds = round(time.perf_counter() - chunk_started, 2)
            for chunk, count in zip(chunks, embedder.count_tokens([c.text for c in chunks])):
                chunk.metadata["tokens"] = count
            indexes[index_name] = {"params": params, **describe_chunks(chunks, embedder.max_seq_length),
                                   "chunk_seconds": chunk_seconds,
                                   "sync": sync_index(store, embedder, index_name, chunks)}
            write_chunk_dump(index_name, chunks)

    stats = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "generated_by": "python -m src.indexing" + (" --rebuild" if args.rebuild else ""),
        "corpus": {"pages": len(pages), "chars": sum(len(page.text) for page in pages)},
        "embedding": {**embedder.describe(), "model_load_seconds": model_load_seconds},
        "store": {"backend": "PostgreSQL + pgvector", **server, "table": config.VECTOR_TABLE,
                  "search": "exact (không dùng index ANN)", "metric": "cosine distance <=>, score = 1 - distance"},
        "strategies": config.STRATEGIES,
        "indexes": indexes,
        "total_seconds": round(time.perf_counter() - run_started, 2),
    }
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (config.RESULTS_DIR / "index_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n",
                                                         encoding="utf-8")
    with (config.RESULTS_DIR / "index_runs.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"run_at": stats["generated_at"], "command": stats["generated_by"],
                                 "model": embedder.model_name, "revision": stats["embedding"]["revision"],
                                 "indexes": {name: item["sync"] for name, item in indexes.items()},
                                 "total_seconds": stats["total_seconds"]}, ensure_ascii=False) + "\n")

    emb = stats["embedding"]
    print(f"Model: {emb['model']} ({emb['dimensions']} chiều, tối đa {emb['max_seq_length']} token, "
          f"revision {str(emb['revision'])[:10]}), chạy local, chi phí 0. Tải model {model_load_seconds} giây")
    print(f"Store: pgvector {server['pgvector']} trên PostgreSQL {server['postgresql']}, bảng {config.VECTOR_TABLE}, "
          "exact search theo cosine")
    for name, item in indexes.items():
        sync, chars, tokens = item["sync"], item["chars"], item["tokens"]
        print(f"Index {name} {item['params']}: {item['chunks']} chunk | ký tự trung bình {chars['mean']}, "
              f"trung vị {chars['median']}, tối đa {chars['max']} | token tối đa {tokens['max']} "
              f"({tokens['over_max_seq_length']} chunk vượt giới hạn)")
        print(f"  Đồng bộ: thêm {sync['inserted']}, cập nhật {sync['updated']}, giữ nguyên {sync['unchanged']}, "
              f"xóa {sync['deleted']} | embedding {sync['embed_seconds']} giây | số dòng trong DB "
              f"{sync['rows_in_db']} ({'khớp' if sync['rows_match_chunks'] else 'KHÔNG KHỚP'} số chunk)")
    print("Chiến lược: " + " | ".join(
        f"{name} -> index {spec['index']}{' + filter' if spec['use_filter'] else ''}"
        for name, spec in config.STRATEGIES.items()))
    print(f"Đã ghi results/index_stats.json, results/index_runs.jsonl, results/chunks/*.jsonl "
          f"({stats['total_seconds']} giây)")
    if not all(item["sync"]["rows_match_chunks"] for item in indexes.values()):
        sys.exit("Số dòng trong DB không khớp số chunk: kiểm tra lại dữ liệu cũ trong bảng.")


if __name__ == "__main__":
    main()
