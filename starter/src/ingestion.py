"""Đọc PDF, chuẩn hóa văn bản và gắn metadata truy vết (nguồn, trang, chương, mục).

Chạy từ thư mục starter/:  python -m src.ingestion
Kết quả: data/manifest.json và results/ingestion_pages.csv (log từng trang: tính hay loại, lý do).
"""
from __future__ import annotations

import csv
import hashlib
import json
import platform
import re
import sys
import time
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import pypdf
from pypdf import PdfReader

from . import config
from .models import DocumentPage

# Ký tự vô hình hay gặp khi trích PDF: soft hyphen, zero-width space/joiner, word joiner, BOM.
_INVISIBLE = dict.fromkeys(map(ord, "\u00ad\u200b\u200c\u200d\u2060\ufeff"))
_INLINE_SPACES = re.compile(r"[ \t\u00a0\u2000-\u200a\u202f\u205f\u3000]+")
_QUOTES = str.maketrans({"\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2018": "'", "\u2019": "'"})
_MATCH_DROP = re.compile(r"[\s\-\u2010-\u2015\u2212]+")

_WORD = r"[^\W\d_]+"
_PAGE_LABEL = re.compile(r"^(\d+|[ivxlcdm]+)$", re.IGNORECASE)
_HYPHEN_BREAK = re.compile(rf"({_WORD})-\n({_WORD})")
# Chữ hoa hay bị kerning tách khỏi phần sau của từ ("T ypeScript", "V À", "Y êu").
_KERNING_SPLIT = re.compile(rf"(?<![^\W\d_])([FPTVWY]) ({_WORD})")
_INLINE_COMPOUND = re.compile(rf"{_WORD}(?:-{_WORD})+")

_CHAPTER = re.compile(r"^CHƯƠNG\s+(\d+)\s*:\s*(.+)$")
_PART = re.compile(r"^(MỞ ĐẦU|KẾT LUẬN)$")
_SECTION = re.compile(r"^(\d+(?:\.\d+)+)\.\s+(\S.*)$")
_TOC_NUMBER = re.compile(r"^(\d+(?:\.\d+)+)\.\s")

STATE_KEYS = ("chapter", "chapter_title", "section", "section_title")
REPAIR_KEYS = ("hyphen_joined", "hyphen_kept", "kerning_merged")


def normalize_text(text: str) -> str:
    """Chuẩn hóa Unicode (NFC) và khoảng trắng trong dòng, giữ xuống dòng để nhận diện heading."""
    text = unicodedata.normalize("NFC", text).translate(_INVISIBLE)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    text = "\n".join(_INLINE_SPACES.sub(" ", line).strip() for line in lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def normalize_for_match(text: str) -> str:
    """Khóa so khớp evidence: NFKC, không phân biệt hoa/thường, bỏ khoảng trắng và gạch nối.

    Nhờ vậy lỗi trích chữ kiểu "Y êu cầu", "anal-ysis" hay chỗ xuống dòng khác nhau
    không làm trượt một evidence chép đúng từ PDF.
    """
    text = unicodedata.normalize("NFKC", text).translate(_INVISIBLE).translate(_QUOTES)
    return _MATCH_DROP.sub("", text.casefold())


def _split_page_label(text: str) -> tuple[str | None, str]:
    """Tách số trang in ở dòng đầu (vd "34", "iv") khỏi nội dung trang."""
    first, _, rest = text.partition("\n")
    if _PAGE_LABEL.match(first.strip()):
        return first.strip(), rest
    return None, text


def repair_extraction_artifacts(texts: list[str]) -> tuple[list[str], list[dict[str, int]]]:
    """Sửa lỗi trích chữ của pypdf dựa trên từ vựng của chính tài liệu.

    - Từ bị LaTeX ngắt dòng bằng gạch nối ("anal-\\nysis") được nối lại; giữ gạch nối nếu dạng
      có gạch ("rule-based") xuất hiện liền dòng ở chỗ khác còn dạng viết liền thì không.
    - Chữ hoa bị tách khỏi phần còn lại của từ do kerning ("Y êu", "T ypeScript", "V À") được gộp
      lại, chỉ khi phần sau viết thường hoặc toàn chữ hoa (không phải một từ viết hoa chữ đầu như
      "Definition") và không bao giờ đứng riêng thành từ trong tài liệu (để không gộp "Y tế").
    """
    whole = "\n".join(texts)
    words = Counter(word.casefold() for word in re.findall(_WORD, whole))
    compounds = Counter(m.group(0).casefold() for m in _INLINE_COMPOUND.finditer(whole))
    tokens = Counter(re.findall(_WORD, whole))
    split_fragments = Counter(m.group(2) for m in _KERNING_SPLIT.finditer(whole))

    repaired: list[str] = []
    stats: list[dict[str, int]] = []
    for text in texts:
        counts = dict.fromkeys(REPAIR_KEYS, 0)

        def fix_hyphen(match: re.Match[str]) -> str:
            head, tail = match.group(1), match.group(2)
            if compounds[f"{head}-{tail}".casefold()] and not words[(head + tail).casefold()]:
                counts["hyphen_kept"] += 1
                return f"{head}-{tail}"
            counts["hyphen_joined"] += 1
            return head + tail

        def fix_kerning(match: re.Match[str]) -> str:
            letter, fragment = match.group(1), match.group(2)
            capitalized_word = fragment[0].isupper() and not fragment.isupper()
            if capitalized_word or tokens[fragment] > split_fragments[fragment]:
                return match.group(0)
            counts["kerning_merged"] += 1
            return letter + fragment

        text = _HYPHEN_BREAK.sub(fix_hyphen, text)
        repaired.append(_KERNING_SPLIT.sub(fix_kerning, text))
        stats.append(counts)
    return repaired, stats


def load_pdf(path: Path, document_id: str, source: str) -> list[DocumentPage]:
    """Trích từng trang PDF vật lý (đánh số từ 1), chuẩn hóa, tách số trang in và ghi lỗi.

    Trang không trích được chữ vẫn được trả về (text rỗng) để ghi log; OCR, nếu cần, là bước riêng.
    """
    reader = PdfReader(str(path))
    raw_texts: list[str] = []
    errors: list[str | None] = []
    for page in reader.pages:
        try:
            raw_texts.append(page.extract_text() or "")
            errors.append(None)
        except Exception as exc:  # một trang hỏng không được làm dừng cả tài liệu
            raw_texts.append("")
            errors.append(f"{type(exc).__name__}: {exc}")
    if not raw_texts:
        return []

    labels, bodies = zip(*(_split_page_label(normalize_text(raw)) for raw in raw_texts))
    texts, repairs = repair_extraction_artifacts(list(bodies))
    return [
        DocumentPage(
            document_id=document_id,
            source=source,
            page=number,
            text=text,
            metadata={
                "page_label": label,
                "raw_chars": len(raw.strip()),
                "chars": len(text),
                "extract_error": error,
                **repair,
            },
        )
        for number, (raw, label, text, error, repair) in enumerate(
            zip(raw_texts, labels, texts, errors, repairs), start=1
        )
    ]


def annotate_structure(pages: list[DocumentPage]) -> None:
    """Gắn chương/mục cho các trang nội dung theo heading trong văn bản, truyền tiếp qua các trang.

    metadata["headings"] giữ offset ký tự của từng heading trên trang để bước chia chunk biết chunk
    thuộc mục nào; chapter/section của trang là giá trị tại đầu trang. Chỉ nhận mục đánh số thuộc
    đúng chương hiện tại và tăng dần, để dòng liệt kê kiểu "1. ..." không bị nhầm là heading.
    """
    state: dict[str, Any] = dict.fromkeys(STATE_KEYS)
    last_number: tuple[int, ...] = ()
    for page in pages:
        start_state = dict(state)
        headings: list[dict[str, Any]] = []
        offset = 0
        for index, line in enumerate(page.text.split("\n")):
            found = None
            if index < 2 and (match := _CHAPTER.match(line)):
                chapter, title = int(match.group(1)), match.group(2).strip()
                found = {"chapter": chapter, "chapter_title": title, "section": str(chapter),
                         "section_title": title, "level": 1}
                last_number = (chapter,)
            elif index < 2 and (match := _PART.match(line)):
                part = match.group(1)
                found = {"chapter": None, "chapter_title": part, "section": part,
                         "section_title": part, "level": 1}
                last_number = ()
            elif state["chapter"] is not None and (match := _SECTION.match(line)):
                number = tuple(int(part) for part in match.group(1).split("."))
                if number[0] == state["chapter"] and number > last_number:
                    found = {"chapter": state["chapter"], "chapter_title": state["chapter_title"],
                             "section": match.group(1), "section_title": match.group(2).strip(),
                             "level": len(number)}
                    last_number = number
            if found:
                state = {key: found[key] for key in STATE_KEYS}
                headings.append({"offset": offset, **found})
            offset += len(line) + 1
        if headings and headings[0]["offset"] == 0:
            start_state = {key: headings[0][key] for key in STATE_KEYS}
        chapters = {start_state["chapter"]} | {h["chapter"] for h in headings}
        page.metadata.update(start_state)
        page.metadata["headings"] = headings
        page.metadata["chapters_on_page"] = sorted(c for c in chapters if c is not None)


def section_at(page: DocumentPage, offset: int) -> dict[str, Any]:
    """Chương/mục đang hiệu lực tại một vị trí ký tự trên trang (dùng khi gắn metadata cho chunk)."""
    state = {key: page.metadata.get(key) for key in STATE_KEYS}
    for heading in page.metadata.get("headings", []):
        if heading["offset"] > offset:
            break
        state = {key: heading[key] for key in STATE_KEYS}
    return state


def _section_key(number: str) -> tuple[int, ...]:
    return tuple(int(part) for part in number.split("."))


def load_source(spec: dict[str, Any]) -> tuple[list[DocumentPage], dict[str, Any]]:
    """Đọc một nguồn khai báo trong config.SOURCES; trả về mọi trang (kèm trạng thái) và thông tin snapshot."""
    path = config.RAW_DATA_DIR / spec["file"]
    if not path.is_file():
        raise FileNotFoundError(f"Không thấy file nguồn {path}; kiểm tra config.SOURCES.")
    started = time.perf_counter()
    pages = load_pdf(path, spec["source_id"], path.relative_to(config.REPO_ROOT).as_posix())
    first, last = spec["content_pages"]
    non_content = spec["non_content_pages"]
    in_scope = [p for p in pages if first <= p.page <= last and p.page not in non_content]
    annotate_structure(in_scope)

    for page in pages:
        meta = page.metadata
        if meta["extract_error"]:
            status, reason = "error", f"lỗi trích chữ: {meta['extract_error']}"
        elif page.page in non_content:
            status, reason = "excluded", non_content[page.page]
        elif not first <= page.page <= last:
            status, reason = "excluded", "ngoài phạm vi nội dung"
        elif meta["chars"] == 0:
            status, reason = "excluded", "không trích được chữ (trang ảnh/scan, cần OCR)"
        elif meta["chars"] < config.MIN_CONTENT_CHARS:
            status, reason = "excluded", f"chỉ có hình/chú thích (dưới {config.MIN_CONTENT_CHARS} ký tự chữ)"
        else:
            status, reason = "content", None
        meta["status"], meta["exclude_reason"] = status, reason
        for key in STATE_KEYS:
            meta.setdefault(key, None)
        meta.setdefault("headings", [])
        meta.setdefault("chapters_on_page", [])

    toc_numbers = {
        match.group(1)
        for page in pages if page.page in spec.get("toc_pages", ())
        for line in page.text.split("\n") if (match := _TOC_NUMBER.match(line))
    }
    detected = {h["section"] for p in in_scope for h in p.metadata["headings"] if h["level"] > 1}
    info = {
        "path": path,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": path.stat().st_size,
        "pdf_producer": str((PdfReader(str(path)).metadata or {}).get("/Producer", "")),
        "ingest_seconds": round(time.perf_counter() - started, 2),
        "toc_sections": len(toc_numbers),
        "toc_sections_detected": len(toc_numbers & detected),
        "toc_sections_missing": sorted(toc_numbers - detected, key=_section_key),
        "headings_not_in_toc": sorted(detected - toc_numbers, key=_section_key),
    }
    return pages, info


def load_corpus() -> list[DocumentPage]:
    """Các trang nội dung (status == "content") của mọi nguồn: đầu vào cho bước chia chunk."""
    return [
        page
        for spec in config.SOURCES
        for page in load_source(spec)[0]
        if page.metadata["status"] == "content"
    ]


def page_ranges(numbers: list[int]) -> str:
    """[1, 2, 3, 5] -> "1-3, 5"."""
    ranges: list[str] = []
    for number in sorted(numbers):
        if ranges and number == int(ranges[-1].split("-")[-1]) + 1:
            ranges[-1] = f"{ranges[-1].split('-')[0]}-{number}"
        else:
            ranges.append(str(number))
    return ", ".join(ranges)


def _structure_summary(counted: list[DocumentPage]) -> list[dict[str, Any]]:
    """Gom các trang được tính theo chương/phần để ghi vào manifest."""
    groups: dict[tuple[Any, str], list[int]] = {}
    for page in counted:
        groups.setdefault((page.metadata["chapter"], page.metadata["chapter_title"]), []).append(page.page)
    return [
        {"chapter": chapter, "title": title, "pages": page_ranges(numbers), "page_count": len(numbers)}
        for (chapter, title), numbers in groups.items()
    ]


def build_manifest(loaded: list[tuple[dict[str, Any], list[DocumentPage], dict[str, Any]]]) -> dict[str, Any]:
    sources = []
    for spec, pages, info in loaded:
        counted = [p for p in pages if p.metadata["status"] == "content"]
        sources.append({
            "source_id": spec["source_id"],
            "title": spec["title"],
            "source_path_or_url": info["path"].relative_to(config.REPO_ROOT).as_posix(),
            "license_or_permission": spec["license_or_permission"],
            "accessed_at": spec["accessed_at"],
            "language": spec.get("language"),
            "snapshot": {"sha256": info["sha256"], "bytes": info["bytes"], "pdf_producer": info["pdf_producer"]},
            "physical_pages": len(pages),
            "content_page_range": list(spec["content_pages"]),
            "counted_page_count": len(counted),
            "counted_chars": sum(p.metadata["chars"] for p in counted),
            "counted_pages": [p.page for p in counted],
            "excluded_pages": [
                {"page": p.page, "status": p.metadata["status"], "reason": p.metadata["exclude_reason"],
                 "chars": p.metadata["chars"]}
                for p in pages if p.metadata["status"] != "content"
            ],
            "structure": _structure_summary(counted),
            "heading_check": {key: info[key] for key in
                              ("toc_sections", "toc_sections_detected", "toc_sections_missing", "headings_not_in_toc")},
            "repairs": {key: sum(p.metadata[key] for p in pages) for key in REPAIR_KEYS},
            "ingest_seconds": info["ingest_seconds"],
            "pages": [
                {"page": p.page, "page_label": p.metadata["page_label"], "chars": p.metadata["chars"],
                 "chapter": p.metadata["chapter"], "section": p.metadata["section"]}
                for p in counted
            ],
        })
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "generated_by": "python -m src.ingestion",
        "extractor": f"pypdf {pypdf.__version__}",
        "python": platform.python_version(),
        "page_numbering": "Số trang PDF vật lý, đánh số từ 1 (không phải số in ở đầu trang).",
        "counting_rule": (
            "Tính các trang trong phạm vi nội dung khai báo (Mở đầu đến Kết luận), trừ trang lỗi trích "
            f"chữ và trang chỉ có hình (dưới {config.MIN_CONTENT_CHARS} ký tự chữ sau chuẩn hóa). Loại bìa, "
            "lời cảm ơn, lời cam đoan, mục lục, danh sách bảng/hình, tài liệu tham khảo và trang nhận xét."
        ),
        "normalization": (
            "Unicode NFC; bỏ ký tự vô hình; gộp khoảng trắng trong dòng; tách số trang in ở đầu trang; "
            "nối từ bị ngắt dòng bằng gạch nối và gộp chữ hoa bị tách do kerning, dựa trên từ vựng của tài liệu."
        ),
        "sources": sources,
        "totals": {
            "sources": len(sources),
            "counted_pages": sum(s["counted_page_count"] for s in sources),
            "counted_chars": sum(s["counted_chars"] for s in sources),
        },
    }


LOG_FIELDS = ("source_id", "page", "page_label", "status", "reason", "raw_chars", "chars", "chapter",
              "section", "section_title", "headings_on_page", *REPAIR_KEYS, "extract_error")


def _log_rows(loaded: list[tuple[dict[str, Any], list[DocumentPage], dict[str, Any]]]) -> list[dict[str, Any]]:
    return [
        {
            "source_id": spec["source_id"], "page": p.page, "page_label": p.metadata["page_label"],
            "status": p.metadata["status"], "reason": p.metadata["exclude_reason"],
            "raw_chars": p.metadata["raw_chars"], "chars": p.metadata["chars"],
            "chapter": p.metadata["chapter"], "section": p.metadata["section"],
            "section_title": p.metadata["section_title"],
            "headings_on_page": " | ".join(h["section"] for h in p.metadata["headings"]),
            **{key: p.metadata[key] for key in REPAIR_KEYS},
            "extract_error": p.metadata["extract_error"],
        }
        for spec, pages, _ in loaded for p in pages
    ]


def use_utf8_output() -> None:
    """In tiếng Việt an toàn kể cả khi output bị chuyển hướng trên Windows."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    use_utf8_output()
    started = time.perf_counter()
    loaded = [(spec, *load_source(spec)) for spec in config.SOURCES]
    manifest = build_manifest(loaded)

    config.MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = config.RESULTS_DIR / "ingestion_pages.csv"
    with log_path.open("w", encoding="utf-8-sig", newline="") as handle:  # utf-8-sig để Excel đọc đúng dấu
        writer = csv.DictWriter(handle, fieldnames=LOG_FIELDS)
        writer.writeheader()
        writer.writerows(_log_rows(loaded))

    for source in manifest["sources"]:
        print(f"{source['source_id']}: {source['physical_pages']} trang PDF -> tính "
              f"{source['counted_page_count']} trang nội dung, {source['counted_chars']} ký tự")
        by_reason: dict[str, list[int]] = {}
        for item in source["excluded_pages"]:
            by_reason.setdefault(item["reason"], []).append(item["page"])
        print(f"  Loại {len(source['excluded_pages'])} trang:")
        for reason, numbers in by_reason.items():
            print(f"    - {reason}: {page_ranges(numbers)}")
        parts = [f"{'Chương ' + str(s['chapter']) if s['chapter'] else s['title']} (trang {s['pages']})"
                 for s in source["structure"]]
        print("  Cấu trúc: " + " | ".join(parts))
        check = source["heading_check"]
        print(f"  Heading: khớp {check['toc_sections_detected']}/{check['toc_sections']} mục trong mục lục"
              + (f"; thiếu {check['toc_sections_missing']}" if check["toc_sections_missing"] else "")
              + (f"; ngoài mục lục {check['headings_not_in_toc']}" if check["headings_not_in_toc"] else ""))
        repairs = source["repairs"]
        print(f"  Sửa lỗi trích chữ: nối {repairs['hyphen_joined']} từ ngắt dòng, giữ {repairs['hyphen_kept']} "
              f"gạch nối, gộp {repairs['kerning_merged']} chữ bị tách")
    print(f"Đã ghi {config.MANIFEST_PATH.relative_to(config.REPO_ROOT).as_posix()} và "
          f"{log_path.relative_to(config.REPO_ROOT).as_posix()} ({time.perf_counter() - started:.1f} giây)")

    total = manifest["totals"]["counted_pages"]
    if total < config.MIN_CORPUS_PAGES:
        sys.exit(f"Corpus mới có {total} trang nội dung, cần ít nhất {config.MIN_CORPUS_PAGES}.")


if __name__ == "__main__":
    main()
