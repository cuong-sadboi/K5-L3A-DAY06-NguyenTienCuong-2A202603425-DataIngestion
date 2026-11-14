"""Chia trang thành chunk.

- fixed_size (chiến lược A): cửa sổ ký tự cố định có overlap, không quan tâm cấu trúc.
- by_paragraph (chiến lược B): cắt theo heading (mục), gom đoạn văn/câu đến giới hạn kích thước.

Chunk không vượt qua ranh giới trang để luôn truy vết được về một trang PDF. Nội dung chunk là
nguyên văn một đoạn liên tục của trang (char_start..char_end), không chèn thêm chữ nào.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from . import config
from .ingestion import section_at
from .models import Chunk, DocumentPage

# Dòng mở đầu một mục liệt kê: "- ...", "• ...", "1. ...", "a) ...".
_LIST_ITEM = re.compile(r"^(?:[-•+*]|\d+[.)]\s|[a-z][.)]\s)")
_LINE_END_PUNCT = re.compile(r"[.!?:;]$")
_SENTENCE_GAP = re.compile(r"(?<=[.!?…])\s+")


def _make_chunk(page: DocumentPage, strategy: str, start: int, end: int) -> Chunk | None:
    """Tạo chunk từ đoạn [start, end) của trang, bỏ khoảng trắng ở hai đầu và giữ offset chính xác."""
    raw = page.text[start:end]
    start += len(raw) - len(raw.lstrip())
    end -= len(raw) - len(raw.rstrip())
    if end <= start:
        return None
    text = page.text[start:end]
    return Chunk(
        chunk_id=f"{strategy}:{page.document_id}:p{page.page}:{start}-{end}",
        document_id=page.document_id,
        source=page.source,
        page=page.page,
        text=text,
        metadata={
            "strategy": strategy,
            **section_at(page, start),
            "char_start": start,
            "char_end": end,
            "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        },
    )


def fixed_size(page: DocumentPage, size: int, overlap: int, strategy: str = "A") -> list[Chunk]:
    """Cửa sổ `size` ký tự, bước `size - overlap`; có thể cắt giữa từ/câu (đây là baseline)."""
    if not 0 <= overlap < size:
        raise ValueError("Cần 0 <= overlap < size")
    chunks: list[Chunk] = []
    start = 0
    while start < len(page.text):
        end = min(start + size, len(page.text))
        if chunk := _make_chunk(page, strategy, start, end):
            chunks.append(chunk)
        if end == len(page.text):
            break
        start += size - overlap
    return chunks


def _segments(page: DocumentPage, min_segment: int) -> list[tuple[int, int]]:
    """Cắt trang tại các heading; mục quá ngắn (thường chỉ có dòng heading) được gộp vào mục kế tiếp."""
    cuts = sorted({0, len(page.text), *(h["offset"] for h in page.metadata.get("headings", []))})
    spans = [(a, b) for a, b in zip(cuts, cuts[1:]) if page.text[a:b].strip()]
    merged: list[tuple[int, int]] = []
    carry: int | None = None
    for index, (start, end) in enumerate(spans):
        if carry is not None:
            start, carry = carry, None
        is_last = index == len(spans) - 1
        if len(page.text[start:end].strip()) < min_segment and not is_last:
            carry = start
            continue
        if len(page.text[start:end].strip()) < min_segment and merged:
            merged[-1] = (merged[-1][0], end)  # heading cuối trang không có nội dung: gộp vào mục trước
            continue
        merged.append((start, end))
    return merged


def _paragraphs(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Đoạn văn: kết thúc khi dòng kết bằng dấu câu và dòng sau viết hoa, hoặc dòng sau là mục liệt kê."""
    spans: list[tuple[int, int]] = []
    lines = text[start:end].split("\n")
    para_start = offset = start
    for index, line in enumerate(lines):
        line_end = offset + len(line)
        following = lines[index + 1] if index + 1 < len(lines) else ""
        boundary = (
            index + 1 == len(lines)
            or bool(_LIST_ITEM.match(following))
            or (bool(_LINE_END_PUNCT.search(line)) and following[:1].isupper())
        )
        if boundary:
            if text[para_start:line_end].strip():
                spans.append((para_start, line_end))
            para_start = line_end + 1
        offset = line_end + 1
    return spans


def _sentences(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Câu: tách sau . ! ? … khi chữ tiếp theo viết hoa, là số hoặc dấu mở ngoặc/ngoặc kép."""
    spans: list[tuple[int, int]] = []
    current = start
    for match in _SENTENCE_GAP.finditer(text, start, end):
        following = text[match.end():match.end() + 1]
        if following and (following.isupper() or following.isdigit() or following in "\"“("):
            spans.append((current, match.start()))
            current = match.end()
    spans.append((current, end))
    return [(a, b) for a, b in spans if b > a]


def _hard_split(text: str, start: int, end: int, max_size: int) -> list[tuple[int, int]]:
    """Câu dài hơn giới hạn: cắt tại khoảng trắng gần nhất trước giới hạn."""
    spans: list[tuple[int, int]] = []
    while end - start > max_size:
        cut = max(text.rfind(" ", start, start + max_size), text.rfind("\n", start, start + max_size))
        if cut <= start:
            cut = start + max_size
        spans.append((start, cut))
        start = cut + 1 if text[cut:cut + 1].isspace() else cut
    spans.append((start, end))
    return spans


def _units(text: str, start: int, end: int, max_size: int) -> list[tuple[int, int]]:
    """Đơn vị nhỏ nhất để gom: đoạn văn; đoạn quá dài thì tách câu; câu quá dài thì cắt cứng."""
    units: list[tuple[int, int]] = []
    for p_start, p_end in _paragraphs(text, start, end):
        if p_end - p_start <= max_size:
            units.append((p_start, p_end))
            continue
        for s_start, s_end in _sentences(text, p_start, p_end):
            units.extend(_hard_split(text, s_start, s_end, max_size))
    return units


def by_paragraph(page: DocumentPage, max_size: int, min_segment: int = 120, strategy: str = "B") -> list[Chunk]:
    """Chunk theo cấu trúc: không vượt ranh giới mục, gom các đoạn liền nhau đến tối đa `max_size` ký tự."""
    chunks: list[Chunk] = []
    for seg_start, seg_end in _segments(page, min_segment):
        group: tuple[int, int] | None = None
        for unit_start, unit_end in _units(page.text, seg_start, seg_end, max_size):
            if group is None:
                group = (unit_start, unit_end)
            elif unit_end - group[0] <= max_size:
                group = (group[0], unit_end)
            else:
                chunks.append(_make_chunk(page, strategy, *group))
                group = (unit_start, unit_end)
        if group is not None:
            chunks.append(_make_chunk(page, strategy, *group))
    return [chunk for chunk in chunks if chunk is not None]


def build_chunks(pages: list[DocumentPage], index_name: str) -> list[Chunk]:
    """Chia toàn bộ corpus theo tham số của một index khai báo trong config.INDEXES."""
    params: dict[str, Any] = config.INDEXES[index_name]
    if params["chunker"] == "fixed_size":
        make = lambda page: fixed_size(page, params["size"], params["overlap"], strategy=index_name)  # noqa: E731
    elif params["chunker"] == "by_paragraph":
        make = lambda page: by_paragraph(page, params["max_size"], params["min_segment"], strategy=index_name)  # noqa: E731
    else:
        raise ValueError(f"Chunker không hỗ trợ: {params['chunker']}")
    return [chunk for page in pages for chunk in make(page)]
