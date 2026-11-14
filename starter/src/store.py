"""Lưu và tìm vector bằng PostgreSQL + pgvector.

Tìm kiếm là exact search (không tạo index ANN như HNSW) theo khoảng cách cosine `<=>`;
score = 1 - khoảng cách. Corpus vài trăm chunk nên quét toàn bảng vẫn nhanh, và filter
không làm hụt kết quả như khi lọc sau một index xấp xỉ.
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg import sql
from psycopg.types.json import Jsonb

from .models import Chunk

# Chỉ các khóa này được dùng làm filter; tên cột không bao giờ lấy trực tiếp từ dữ liệu đầu vào.
FILTER_COLUMNS = {"chapter": "chapter", "section": "section", "document_id": "document_id"}

# Khóa metadata của chunk đã có cột riêng; phần còn lại lưu vào cột jsonb `metadata`.
_COLUMN_KEYS = {"chapter", "section", "section_title", "char_start", "char_end", "content_hash", "doc_sha256"}
_RESULT_COLUMNS = ("chunk_id", "document_id", "source", "page", "chapter", "section", "section_title",
                   "char_start", "char_end", "content", "metadata")


class PgVectorStore:
    def __init__(self, database_url: str, dimensions: int, model_name: str, table: str = "chunks") -> None:
        """Mở kết nối (lỗi kết nối dừng ngay, không có phương án giả lập) và đăng ký kiểu vector."""
        if dimensions <= 0:
            raise ValueError("Số chiều vector phải dương")
        self.dimensions = dimensions
        self.model_name = model_name
        self.table_name = table
        self.table = sql.Identifier(table)
        self.conn = psycopg.connect(database_url, connect_timeout=5)
        with self.conn.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
        self.conn.commit()
        register_vector(self.conn)

    def __enter__(self) -> PgVectorStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.conn.close()

    def server_info(self) -> dict[str, str]:
        with self.conn.cursor() as cursor:
            cursor.execute("SELECT current_setting('server_version'), extversion FROM pg_extension "
                           "WHERE extname = 'vector'")
            postgres, pgvector = cursor.fetchone()
        return {"postgresql": postgres, "pgvector": pgvector}

    def create_schema(self) -> None:
        """Tạo bảng chunk (khóa chính strategy + chunk_id) và kiểm tra số chiều khớp với model."""
        create_table = sql.SQL("""
            CREATE TABLE IF NOT EXISTS {table} (
                strategy      text        NOT NULL,
                chunk_id      text        NOT NULL,
                document_id   text        NOT NULL,
                source        text        NOT NULL,
                page          integer     NOT NULL,
                chapter       integer,
                section       text,
                section_title text,
                char_start    integer     NOT NULL,
                char_end      integer     NOT NULL,
                content       text        NOT NULL,
                content_hash  text        NOT NULL,
                doc_sha256    text        NOT NULL,
                metadata      jsonb       NOT NULL DEFAULT '{{}}'::jsonb,
                model         text        NOT NULL,
                embedding     vector({dims}) NOT NULL,
                updated_at    timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (strategy, chunk_id)
            )""").format(table=self.table, dims=sql.Literal(self.dimensions))
        create_index = sql.SQL("CREATE INDEX IF NOT EXISTS {index} ON {table} (strategy, chapter)").format(
            index=sql.Identifier(f"{self.table_name}_strategy_chapter_idx"), table=self.table)
        with self.conn.cursor() as cursor:
            cursor.execute(create_table)
            cursor.execute(create_index)
            cursor.execute("SELECT atttypmod FROM pg_attribute WHERE attrelid = %s::regclass AND attname = 'embedding'",
                           (self.table_name,))
            stored_dims = cursor.fetchone()[0]
        self.conn.commit()
        if stored_dims != self.dimensions:
            raise RuntimeError(f"Cột embedding của bảng {self.table_name} có {stored_dims} chiều nhưng model có "
                               f"{self.dimensions} chiều; dùng bảng khác hoặc xóa dữ liệu cũ của model trước.")

    def existing(self, strategy: str, document_id: str) -> dict[str, tuple[str, str]]:
        """chunk_id -> (content_hash, model) đang có trong DB, để chỉ embed lại chunk mới hoặc đã đổi."""
        query = sql.SQL("SELECT chunk_id, content_hash, model FROM {table} WHERE strategy = %s AND document_id = %s")
        with self.conn.cursor() as cursor:
            cursor.execute(query.format(table=self.table), (strategy, document_id))
            return {chunk_id: (content_hash, model) for chunk_id, content_hash, model in cursor.fetchall()}

    def upsert(self, strategy: str, chunks: list[Chunk], vectors: Sequence[Sequence[float]]) -> int:
        """Ghi chunk theo ID ổn định: chưa có thì thêm, đã có thì cập nhật, nên nạp lại không nhân đôi.

        Chunk cũ của tài liệu đã đổi được xóa riêng bằng remove_obsolete().
        """
        if len(chunks) != len(vectors):
            raise ValueError(f"Có {len(chunks)} chunk nhưng {len(vectors)} vector")
        rows = []
        for chunk, vector in zip(chunks, vectors):
            embedding = np.asarray(vector, dtype=np.float32)
            if embedding.shape != (self.dimensions,) or not np.isfinite(embedding).all():
                raise ValueError(f"Vector của {chunk.chunk_id} sai số chiều hoặc có giá trị không hợp lệ")
            meta = chunk.metadata
            extra = {key: value for key, value in meta.items() if key not in _COLUMN_KEYS}
            rows.append((strategy, chunk.chunk_id, chunk.document_id, chunk.source, chunk.page, meta.get("chapter"),
                         meta.get("section"), meta.get("section_title"), meta["char_start"], meta["char_end"],
                         chunk.text, meta["content_hash"], meta["doc_sha256"], Jsonb(extra), self.model_name,
                         embedding))
        statement = sql.SQL("""
            INSERT INTO {table} (strategy, chunk_id, document_id, source, page, chapter, section, section_title,
                                 char_start, char_end, content, content_hash, doc_sha256, metadata, model, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (strategy, chunk_id) DO UPDATE SET
                document_id = EXCLUDED.document_id, source = EXCLUDED.source, page = EXCLUDED.page,
                chapter = EXCLUDED.chapter, section = EXCLUDED.section, section_title = EXCLUDED.section_title,
                char_start = EXCLUDED.char_start, char_end = EXCLUDED.char_end, content = EXCLUDED.content,
                content_hash = EXCLUDED.content_hash, doc_sha256 = EXCLUDED.doc_sha256,
                metadata = EXCLUDED.metadata, model = EXCLUDED.model, embedding = EXCLUDED.embedding,
                updated_at = now()""").format(table=self.table)
        with self.conn.cursor() as cursor:
            cursor.executemany(statement, rows)
        self.conn.commit()
        return len(rows)

    def remove_obsolete(self, strategy: str, keep: dict[str, set[str]]) -> int:
        """Xóa chunk không còn tồn tại: của tài liệu đã bỏ khỏi corpus, hoặc của phiên bản tài liệu cũ."""
        deleted = 0
        with self.conn.cursor() as cursor:
            cursor.execute(sql.SQL("DELETE FROM {table} WHERE strategy = %s AND NOT (document_id = ANY(%s))")
                           .format(table=self.table), (strategy, list(keep)))
            deleted += cursor.rowcount
            for document_id, chunk_ids in keep.items():
                cursor.execute(sql.SQL("DELETE FROM {table} WHERE strategy = %s AND document_id = %s "
                                       "AND NOT (chunk_id = ANY(%s))").format(table=self.table),
                               (strategy, document_id, list(chunk_ids)))
                deleted += cursor.rowcount
        self.conn.commit()
        return deleted

    def count(self, strategy: str) -> int:
        with self.conn.cursor() as cursor:
            cursor.execute(sql.SQL("SELECT count(*) FROM {table} WHERE strategy = %s").format(table=self.table),
                           (strategy,))
            return cursor.fetchone()[0]

    def chunk_rows(self, strategy: str) -> list[dict[str, Any]]:
        """Mọi chunk của một index (không kèm vector), sắp theo chunk_id."""
        query = sql.SQL("SELECT chunk_id, document_id, page, content, content_hash, model FROM {table} "
                        "WHERE strategy = %s ORDER BY chunk_id").format(table=self.table)
        with self.conn.cursor() as cursor:
            cursor.execute(query, (strategy,))
            names = [column.name for column in cursor.description]
            return [dict(zip(names, row)) for row in cursor.fetchall()]

    def fingerprint(self, strategy: str) -> str:
        """Dấu vân tay của index: đổi khi chunk, nội dung hoặc model thay đổi."""
        digest = hashlib.sha256()
        for row in self.chunk_rows(strategy):
            digest.update(f"{row['chunk_id']}\t{row['content_hash']}\t{row['model']}\n".encode("utf-8"))
        return digest.hexdigest()

    def delete_strategy(self, strategy: str) -> int:
        with self.conn.cursor() as cursor:
            cursor.execute(sql.SQL("DELETE FROM {table} WHERE strategy = %s").format(table=self.table), (strategy,))
            deleted = cursor.rowcount
        self.conn.commit()
        return deleted

    def search(self, strategy: str, query_vector: Sequence[float], top_k: int = 3,
               filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Top-k chunk gần nhất theo cosine; filter chỉ nhận khóa trong FILTER_COLUMNS, giá trị truyền tham số."""
        embedding = np.asarray(query_vector, dtype=np.float32)
        if embedding.shape != (self.dimensions,):
            raise ValueError(f"Vector câu hỏi phải có {self.dimensions} chiều")
        conditions = [sql.SQL("strategy = %s")]
        params: list[Any] = [strategy]
        for key, value in (filters or {}).items():
            if key not in FILTER_COLUMNS:
                raise ValueError(f"Filter {key!r} không được hỗ trợ; chỉ dùng: {', '.join(sorted(FILTER_COLUMNS))}")
            conditions.append(sql.SQL("{} = %s").format(sql.Identifier(FILTER_COLUMNS[key])))
            params.append(value)
        query = sql.SQL("""
            SELECT {columns}, 1 - (embedding <=> %s) AS score
            FROM {table}
            WHERE {where}
            ORDER BY embedding <=> %s, chunk_id
            LIMIT %s""").format(columns=sql.SQL(", ").join(map(sql.Identifier, _RESULT_COLUMNS)),
                                table=self.table, where=sql.SQL(" AND ").join(conditions))
        with self.conn.cursor() as cursor:
            cursor.execute(query, [embedding, *params, embedding, top_k])
            rows = cursor.fetchall()
        hits = []
        for rank, row in enumerate(rows, start=1):
            hit = dict(zip(_RESULT_COLUMNS, row[:-1]))
            hit["text"] = hit.pop("content")
            hits.append({"rank": rank, "score": round(float(row[-1]), 6), **hit})
        return hits
