"""Kiểm tra bộ câu hỏi benchmark trước khi chạy thử (GUIDE mục 2, BENCHMARK.md).

Chạy từ thư mục starter/:  python -m src.benchmark  [--questions đường/dẫn.jsonl]

Mỗi dòng của benchmark/questions.jsonl là một câu hỏi dạng JSON, ví dụ:
{"id": "q01", "split": "dev", "type": "truc_tiep", "question": "...", "expected_answer": "...",
 "gold_source_id": "doan-bot-traffic-2026", "gold_page": 44, "evidence_quote": "...", "filter": null}

- gold_page: số trang PDF (số trên thanh công cụ của trình đọc PDF), không phải số in ở đầu trang.
- filter: null hoặc {"chapter": 1 | 2 | 3}; phải suy ra được từ câu hỏi/ngữ cảnh, không từ đáp án.
- alt_gold (tùy chọn): danh sách {"source_id", "page", "evidence_quote"} khác cũng được chấp nhận.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config
from .ingestion import load_source, normalize_for_match, use_utf8_output
from .models import DocumentPage

REQUIRED_FIELDS = ("id", "split", "question", "expected_answer", "gold_source_id", "gold_page", "evidence_quote")
SPLITS = ("dev", "test")
FILTER_KEYS = ("chapter",)
LONG_EVIDENCE_CHARS = 300


@dataclass
class CorpusIndex:
    """Trang nội dung của corpus và khóa so khớp evidence của từng trang."""

    pages: dict[tuple[str, int], DocumentPage]
    match_text: dict[tuple[str, int], str]
    excluded: dict[tuple[str, int], str]
    chapters: set[int]

    @classmethod
    def build(cls) -> CorpusIndex:
        pages, match_text, excluded, chapters = {}, {}, {}, set()
        for spec in config.SOURCES:
            for page in load_source(spec)[0]:
                key = (page.document_id, page.page)
                if page.metadata["status"] == "content":
                    pages[key] = page
                    match_text[key] = normalize_for_match(page.text)
                    chapters.update(page.metadata["chapters_on_page"])
                else:
                    excluded[key] = page.metadata["exclude_reason"]
        return cls(pages, match_text, excluded, chapters)

    @property
    def sources(self) -> set[str]:
        return {source for source, _ in [*self.pages, *self.excluded]}

    def find(self, source_id: str, key: str) -> list[int]:
        return sorted(page for (source, page), text in self.match_text.items() if source == source_id and key in text)


def load_questions(path: Path) -> list[dict[str, Any]]:
    """Đọc JSONL (chấp nhận BOM do Notepad/PowerShell thêm vào); bỏ qua dòng trống."""
    questions = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"dòng {line_no}: JSON không hợp lệ ({exc.msg}, cột {exc.colno})") from exc
        if not isinstance(item, dict):
            raise ValueError(f"dòng {line_no}: mỗi dòng phải là một object JSON")
        item["_line"] = line_no
        questions.append(item)
    return questions


def gold_entries(question: dict[str, Any]) -> list[tuple[Any, Any, Any]]:
    """Gold chính và các gold thay thế (alt_gold) cùng được chấp nhận."""
    entries = [(question.get("gold_source_id"), question.get("gold_page"), question.get("evidence_quote"))]
    for alt in question.get("alt_gold") or []:
        alt = alt if isinstance(alt, dict) else {}
        entries.append((alt.get("source_id"), alt.get("page"), alt.get("evidence_quote")))
    return entries


def _chapter_name(chapter: int | None) -> str:
    return f"chương {chapter}" if chapter is not None else "phần Mở đầu/Kết luận (không thuộc chương nào)"


def _where_hint(index: CorpusIndex, source_id: str, key: str, page: int) -> str:
    found = index.find(source_id, key)
    if not found:
        return "; không tìm thấy ở trang nội dung nào, kiểm tra lại đã chép đúng nguyên văn chưa"
    hint = f"; evidence nằm ở trang {', '.join(map(str, found))}"
    if any(index.pages[(source_id, p)].metadata["page_label"] == str(page) for p in found):
        hint += " (bạn đang ghi số trang in ở đầu trang, hãy dùng số trang PDF)"
    return hint


def check_question(question: dict[str, Any], index: CorpusIndex) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    missing = [name for name in REQUIRED_FIELDS if question.get(name) in (None, "")]
    if missing:
        errors.append("thiếu trường " + ", ".join(missing))
    if question.get("split") not in SPLITS:
        errors.append('split phải là "dev" hoặc "test"')

    filters = question.get("filter") or {}
    if not isinstance(filters, dict):
        errors.append('filter phải là null hoặc object, vd {"chapter": 2}')
        filters = {}
    unknown = sorted(set(filters) - set(FILTER_KEYS))
    if unknown:
        errors.append(f"filter có trường không hỗ trợ {unknown}; chỉ dùng: {', '.join(FILTER_KEYS)}")
    if "chapter" in filters and filters["chapter"] not in index.chapters:
        errors.append(f"chapter={filters['chapter']!r} không tồn tại; giá trị hợp lệ: {sorted(index.chapters)}")

    for source_id, page, quote in gold_entries(question):
        if source_id not in index.sources:
            errors.append(f"source_id {source_id!r} không có trong config.SOURCES")
            continue
        if not isinstance(page, int) or isinstance(page, bool):
            errors.append(f"số trang {page!r} phải là số nguyên (số trang PDF)")
            continue
        key = normalize_for_match(quote) if isinstance(quote, str) else ""
        if not key:
            errors.append(f"trang {page}: evidence_quote rỗng")
            continue
        if len(quote) > LONG_EVIDENCE_CHARS:
            warnings.append(f"evidence dài {len(quote)} ký tự, dễ bị cắt ở chiến lược A; nên rút còn khoảng 1 câu")
        if (source_id, page) not in index.pages:
            reason = index.excluded.get((source_id, page), "không có trang này")
            errors.append(f"trang {page} không thuộc corpus được tính ({reason}){_where_hint(index, source_id, key, page)}")
            continue
        if key not in index.match_text[(source_id, page)]:
            errors.append(f"evidence không có ở trang {page}{_where_hint(index, source_id, key, page)}")
            continue
        chapter = index.pages[(source_id, page)].metadata["chapter"]
        if "chapter" in filters and filters["chapter"] != chapter:
            errors.append(f"trang {page} thuộc {_chapter_name(chapter)}, không thỏa filter chapter={filters['chapter']}")
    return errors, warnings


def validate(questions: list[dict[str, Any]], index: CorpusIndex) -> dict[str, Any]:
    id_counts = Counter(q.get("id") for q in questions)
    text_counts = Counter(normalize_for_match(str(q.get("question", ""))) for q in questions)
    results = []
    for question in questions:
        errors, warnings = check_question(question, index)
        if id_counts[question.get("id")] > 1:
            errors.append(f"id {question.get('id')!r} bị trùng")
        if text_counts[normalize_for_match(str(question.get("question", "")))] > 1:
            warnings.append("câu hỏi trùng nội dung với câu khác")
        results.append({"id": question.get("id"), "line": question["_line"], "split": question.get("split"),
                        "filter": question.get("filter") or None, "errors": errors, "warnings": warnings})

    splits = Counter(q.get("split") for q in questions)
    with_filter = [q for q in questions if isinstance(q.get("filter"), dict) and q["filter"]]
    filter_splits = Counter(q.get("split") for q in with_filter)
    requirements = [
        {"rule": "≥ 20 câu", "value": len(questions), "ok": len(questions) >= 20},
        {"rule": "≥ 15 câu dev", "value": splits["dev"], "ok": splits["dev"] >= 15},
        {"rule": "≥ 5 câu test", "value": splits["test"], "ok": splits["test"] >= 5},
        {"rule": "≥ 3 câu có filter", "value": len(with_filter), "ok": len(with_filter) >= 3},
        {"rule": "≥ 1 câu có filter ở test", "value": filter_splits["test"], "ok": filter_splits["test"] >= 1},
    ]
    recommendations = [
        {"rule": "nên có ≥ 5 câu filter", "value": len(with_filter), "ok": len(with_filter) >= 5},
        {"rule": "nên có ≥ 2 câu filter ở test", "value": filter_splits["test"], "ok": filter_splits["test"] >= 2},
    ]
    return {
        "summary": {
            "questions": len(questions),
            "valid_questions": sum(1 for r in results if not r["errors"]),
            "splits": dict(splits),
            "with_filter": len(with_filter),
            "with_filter_by_split": dict(filter_splits),
            "types": dict(Counter(q["type"] for q in questions if q.get("type"))),
        },
        "requirements": requirements,
        "recommendations": recommendations,
        "questions": results,
    }


def _display(path: Path) -> str:
    try:
        return path.resolve().relative_to(config.REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> None:
    use_utf8_output()
    parser = argparse.ArgumentParser(description="Kiểm tra benchmark trước khi chạy thử.")
    parser.add_argument("--questions", type=Path, default=config.BENCHMARK_PATH)
    parser.add_argument("--report", type=Path, default=config.RESULTS_DIR / "benchmark_validation.json")
    args = parser.parse_args(argv)

    if not args.questions.is_file():
        sys.exit(f"Chưa có {_display(args.questions)}. Tạo file này, mỗi dòng một câu hỏi JSON "
                 "(xem ví dụ ở đầu file src/benchmark.py).")
    try:
        questions = load_questions(args.questions)
    except ValueError as exc:
        sys.exit(f"{_display(args.questions)}: {exc}")

    report = validate(questions, CorpusIndex.build())
    digest = hashlib.sha256(args.questions.read_bytes()).hexdigest()
    report = {"validated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "questions_file": _display(args.questions), "questions_sha256": digest, **report}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Kiểm tra {_display(args.questions)} ({len(questions)} câu, sha256 {digest[:12]})")
    for kind, title in (("errors", "Lỗi"), ("warnings", "Cảnh báo")):
        rows = [(r, message) for r in report["questions"] for message in r[kind]]
        if rows:
            print(f"{title}:")
            for result, message in rows:
                print(f"  {result['id']} (dòng {result['line']}): {message}")
    for title, items in (("Yêu cầu bắt buộc", report["requirements"]), ("Khuyến nghị", report["recommendations"])):
        print(f"{title}:")
        for item in items:
            print(f"  [{'ĐẠT' if item['ok'] else 'CHƯA ĐẠT'}] {item['rule']}: {item['value']}")
    summary = report["summary"]
    if summary["types"]:
        print("Loại câu: " + ", ".join(f"{name}={count}" for name, count in summary["types"].items()))
    passed = summary["valid_questions"] == summary["questions"] and all(i["ok"] for i in report["requirements"])
    print(f"Kết quả: {summary['valid_questions']}/{summary['questions']} câu hợp lệ"
          f"{'' if passed else '; còn lỗi hoặc yêu cầu chưa đạt'}. Báo cáo: {_display(args.report)}")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
