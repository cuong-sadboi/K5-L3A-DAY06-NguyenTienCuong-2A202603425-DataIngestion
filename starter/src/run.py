"""Chạy benchmark theo đúng quy trình: dev trước, chốt chiến lược theo tiêu chí ghi sẵn, rồi mới chạy test.

Chạy từ thư mục starter/, sau khi đã chạy python -m src.indexing:
  python -m src.run dev                      # 3 chiến lược trên dev, tự chọn theo tiêu chí, tạo phiếu chấm coherence
  python -m src.run select B --reason "..."  # chỉ khi tiêu chí cần điểm coherence mà chưa có
  python -m src.run select --auto            # áp dụng lại tiêu chí sau khi đã chấm coherence
  python -m src.run test                     # chạy test; từ chối nếu chưa chốt hoặc benchmark/cấu hình/index đã đổi
  python -m src.run audit                    # tổng hợp phiếu chấm tay: coherence, metadata, grounding
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config
from .benchmark import CorpusIndex, load_questions, validate
from .chunking import build_chunks
from .embeddings import SentenceTransformerEmbedder, create_embedder
from .evaluation import evaluate_query, evidence_available, metrics_markdown, select_strategy, summarize
from .indexing import chunk_corpus, database_url, load_content_pages
from .ingestion import normalize_for_match, use_utf8_output
from .models import DocumentPage
from .search import retrieve
from .store import PgVectorStore

EXPERIMENT_LOG = config.REPO_ROOT / "artifacts" / "experiment_log.csv"
COHERENCE_FIELDS = ("position_id", "page", "char_offset", "source_context", "strategy", "chunk_id", "chunk_chars",
                    "chunk_text", "coherence_0_1_2", "reason")
METADATA_FIELDS = ("strategy", "question_id", "rank", "filter", "chunk_id", "source", "page", "chapter", "section",
                   "section_title", "text_preview", "auto_gold_page", "auto_contains_evidence", "auto_filter_ok",
                   "metadata_ok_1_0", "note")
GROUNDING_FIELDS = ("strategy", "question_id", "question", "expected_answer", "gold_pages", "evidence_quote",
                    "top1", "top2", "top3", "auto_hit_at_3", "grounding_0_1_2", "reason")


class Paths:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.selection = root / "selection.json"
        self.audit = root / "audit"
        self.audit_summary = root / "audit_summary.json"

    def traces(self, split: str) -> Path:
        return self.root / "traces" / f"{split}.jsonl"

    def metrics(self, split: str) -> Path:
        return self.root / f"metrics_{split}.json"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:  # utf-8-sig để Excel đọc đúng dấu
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _has_labels(path: Path, column: str) -> bool:
    return path.exists() and any((row.get(column) or "").strip() for row in _read_csv(path))


def _display(path: Path) -> str:
    try:
        return path.resolve().relative_to(config.REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def config_snapshot() -> dict[str, Any]:
    return json.loads(json.dumps({"INDEXES": config.INDEXES, "STRATEGIES": config.STRATEGIES,
                                  "EMBEDDING": config.EMBEDDING, "TOP_K": config.TOP_K,
                                  "SELECTION_RULE": config.SELECTION_RULE}))


def load_benchmark(path: Path, allow_incomplete: bool) -> tuple[list[dict[str, Any]], str]:
    """Đọc benchmark và dừng nếu còn lỗi (evidence sai trang, filter sai...) hoặc chưa đủ yêu cầu."""
    questions = load_questions(path)
    report = validate(questions, CorpusIndex.build())
    errors = [f"{item['id']}: {message}" for item in report["questions"] for message in item["errors"]]
    if errors:
        sys.exit("Benchmark còn lỗi (chạy python -m src.benchmark để xem):\n  " + "\n  ".join(errors))
    unmet = [f"{item['rule']} (hiện {item['value']})" for item in report["requirements"] if not item["ok"]]
    if unmet and not allow_incomplete:
        sys.exit("Benchmark chưa đạt yêu cầu: " + "; ".join(unmet))
    for question in questions:
        question.pop("_line", None)
    return questions, hashlib.sha256(path.read_bytes()).hexdigest()


def check_indexes(store: PgVectorStore, embedder: SentenceTransformerEmbedder,
                  pages: list[DocumentPage]) -> tuple[dict[str, str], dict[str, int], dict[str, dict]]:
    """Index trong DB phải khớp cách chia chunk hiện tại; trả về dấu vân tay, số chunk và text theo trang."""
    fingerprints, counts, page_texts = {}, {}, {}
    for index_name in config.INDEXES:
        expected = {(c.chunk_id, c.metadata["content_hash"]) for c in chunk_corpus(pages, index_name)}
        rows = store.chunk_rows(index_name)
        if {(r["chunk_id"], r["content_hash"]) for r in rows} != expected or \
                {r["model"] for r in rows} != {embedder.model_name}:
            sys.exit(f"Index {index_name} trong DB chưa khớp cấu hình hiện tại; chạy python -m src.indexing trước.")
        fingerprints[index_name] = store.fingerprint(index_name)
        counts[index_name] = len(rows)
        by_page: dict[tuple[str, int], list[str]] = defaultdict(list)
        for row in rows:
            by_page[(row["document_id"], row["page"])].append(normalize_for_match(row["content"]))
        page_texts[index_name] = by_page
    return fingerprints, counts, page_texts


def run_split(store: PgVectorStore, embedder: SentenceTransformerEmbedder, questions: list[dict[str, Any]],
              split: str, page_texts: dict[str, dict]) -> list[dict[str, Any]]:
    """Chạy cùng các câu của một split cho cả ba chiến lược và giữ trace đầy đủ top-k."""
    traces = []
    for strategy, spec in config.STRATEGIES.items():
        for question in (q for q in questions if q["split"] == split):
            started = time.perf_counter()
            hits, applied = retrieve(store, embedder, strategy, question["question"], question.get("filter") or None)
            traces.append(evaluate_query(
                question, hits, strategy=strategy, index=spec["index"], filter_applied=applied,
                evidence_in_index=evidence_available(question, page_texts[spec["index"]]),
                latency_ms=round((time.perf_counter() - started) * 1000, 1)))
    return traces


def save_split(paths: Paths, split: str, traces: list[dict[str, Any]], summary: dict[str, Any],
               benchmark_sha: str) -> None:
    trace_path = paths.traces(split)
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in traces), encoding="utf-8")
    _write_json(paths.metrics(split), {"split": split, "generated_at": _now(), "benchmark_sha256": benchmark_sha,
                                       "top_k": config.TOP_K, "metrics": summary})
    paths.metrics(split).with_suffix(".md").write_text(metrics_markdown(split, summary, traces), encoding="utf-8")


def update_experiment_log(split: str, summary: dict[str, Any]) -> None:
    """Ghi Hit@1/Hit@3 (n/N) vào artifacts/experiment_log.csv, giữ nguyên giả thuyết và ghi chú."""
    with EXPERIMENT_LOG.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields, rows = reader.fieldnames, list(reader)
    for row in rows:
        item = summary.get(row["strategy"], {}).get(split)
        if item:
            row[f"{split}_hit1"], row[f"{split}_hit3"] = item["hit_at_1"]["text"], item["hit_at_3"]["text"]
    with EXPERIMENT_LOG.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def print_summary(split: str, summary: dict[str, Any]) -> None:
    for strategy, by_split in summary.items():
        item = by_split[split]
        compliant = item["filter_compliant"] if isinstance(item["filter_compliant"], str) else \
            item["filter_compliant"]["text"]
        split_ids = ", ".join(item["evidence_split_in_index"]) or "không có"
        print(f"  {strategy} (index {item['index']}): Hit@1 {item['hit_at_1']['text']} | Evidence Hit@3 "
              f"{item['hit_at_3']['text']} | trang gold trong top-3 {item['gold_page_in_top_k']['text']} | "
              f"filter tuân thủ {compliant} | evidence bị tách trong index: {split_ids} | {item['mean_latency_ms']} ms")


def coherence_positions(pages: list[DocumentPage], count: int = 10) -> list[tuple[DocumentPage, int]]:
    """Vị trí cố định, chọn trước khi xem điểm: trang nội dung cách đều nhau, điểm giữa văn bản của trang."""
    ordered = sorted(pages, key=lambda page: (page.document_id, page.page))
    picks = [ordered[round(i * (len(ordered) - 1) / (count - 1))] for i in range(count)]
    return [(page, len(page.text) // 2) for page in picks]


def write_coherence_sheet(paths: Paths, pages: list[DocumentPage]) -> str:
    path = paths.audit / "coherence.csv"
    if _has_labels(path, "coherence_0_1_2"):
        return f"giữ nguyên {_display(path)} vì đã có điểm chấm"
    by_index = {name: build_chunks(pages, name) for name in config.INDEXES}
    rows = []
    for number, (page, offset) in enumerate(coherence_positions(pages), start=1):
        context = page.text[max(0, offset - 150):offset] + "[•]" + page.text[offset:offset + 150]
        for index_name, chunks in by_index.items():
            on_page = [c for c in chunks if c.document_id == page.document_id and c.page == page.page]
            covering = [c for c in on_page if c.metadata["char_start"] <= offset < c.metadata["char_end"]]
            chunk = covering[0] if covering else min(
                on_page, key=lambda c: min(abs(c.metadata["char_start"] - offset), abs(c.metadata["char_end"] - offset)))
            rows.append({"position_id": f"pos{number:02d}", "page": page.page, "char_offset": offset,
                         "source_context": " ".join(context.split()), "strategy": index_name,
                         "chunk_id": chunk.chunk_id, "chunk_chars": len(chunk.text), "chunk_text": chunk.text,
                         "coherence_0_1_2": "", "reason": ""})
    _write_csv(path, COHERENCE_FIELDS, rows)
    return f"đã tạo {_display(path)} ({len(rows)} dòng)"


def _hit_cell(hit: dict[str, Any]) -> str:
    return f"[trang {hit['page']} | chương {hit['chapter']} | mục {hit['section']}] {hit['text']}"


def write_test_sheets(paths: Paths, traces: list[dict[str, Any]]) -> list[str]:
    notes = []
    metadata_path, grounding_path = paths.audit / "metadata_test.csv", paths.audit / "grounding_test.csv"
    if _has_labels(metadata_path, "metadata_ok_1_0"):
        notes.append(f"giữ nguyên {_display(metadata_path)} vì đã có nhãn")
    else:
        rows = [{"strategy": t["strategy"], "question_id": t["question_id"], "rank": h["rank"],
                 "filter": json.dumps(t["filter"], ensure_ascii=False) if t["filter"] else "",
                 "chunk_id": h["chunk_id"], "source": h["source"], "page": h["page"], "chapter": h["chapter"],
                 "section": h["section"], "section_title": h["section_title"],
                 "text_preview": " ".join(h["text"].split())[:200], "auto_gold_page": int(h["gold_page"]),
                 "auto_contains_evidence": int(h["contains_evidence"]),
                 "auto_filter_ok": "" if h["filter_ok"] is None else int(h["filter_ok"]),
                 "metadata_ok_1_0": "", "note": ""} for t in traces for h in t["hits"]]
        _write_csv(metadata_path, METADATA_FIELDS, rows)
        notes.append(f"đã tạo {_display(metadata_path)} ({len(rows)} dòng)")
    if _has_labels(grounding_path, "grounding_0_1_2"):
        notes.append(f"giữ nguyên {_display(grounding_path)} vì đã có điểm chấm")
    else:
        rows = []
        for t in traces:
            row = {"strategy": t["strategy"], "question_id": t["question_id"], "question": t["question"],
                   "expected_answer": t["expected_answer"], "gold_pages": ", ".join(str(g["page"]) for g in t["gold"]),
                   "evidence_quote": t["gold"][0]["evidence_quote"], "auto_hit_at_3": int(t["hit_at_3"]),
                   "grounding_0_1_2": "", "reason": ""}
            row.update({f"top{h['rank']}": _hit_cell(h) for h in t["hits"]})
            rows.append(row)
        _write_csv(grounding_path, GROUNDING_FIELDS, rows)
        notes.append(f"đã tạo {_display(grounding_path)} ({len(rows)} dòng)")
    return notes


AUDIT_README = """# Phiếu chấm tay

Mở các file CSV bằng Excel hoặc VS Code. Chỉ điền các cột chấm điểm, không sửa cột khác, rồi chạy
`python -m src.run audit` (trong thư mục `starter/`) để tổng hợp. Các cột `auto_*` chỉ để tham khảo;
hãy tự đọc và chấm. Người chấm là tác giả đồ án, cần ghi nguy cơ thiên lệch trong báo cáo.

## coherence.csv (tạo ở bước dev)

- 10 vị trí cố định, chọn trước khi xem điểm: {pages} trang nội dung xếp theo số trang, lấy 10 trang cách đều
  nhau; vị trí là điểm giữa văn bản của trang (ô `source_context` đánh dấu `[•]`).
- Mỗi vị trí chấm chunk chứa nó trong index A và B. Chiến lược C dùng chung chunk với B nên coherence của C bằng B.
- `coherence_0_1_2`: 0 = bị cắt giữa ý hoặc thiếu chủ thể nên không hiểu; 1 = hiểu một phần, thiếu ngữ cảnh;
  2 = tự hiểu được và giữ trọn ý. Ghi lý do ở cột `reason`.

## metadata_test.csv (tạo ở bước test)

- Mỗi câu test × top-3 × 3 chiến lược. Mở PDF đúng trang `page`, kiểm tra đoạn chữ có nằm ở trang đó và
  `source`, `chapter`, `section` có đúng không.
- `metadata_ok_1_0`: 1 = đủ và đúng; 0 = có trường sai hoặc thiếu (ghi trường nào ở cột `note`).

## grounding_test.csv (tạo ở bước test)

- Mỗi dòng là một câu test của một chiến lược, kèm nguyên văn top-3.
- `grounding_0_1_2`: 0 = không có bằng chứng; 1 = có nhưng thiếu điều kiện/ngữ cảnh cần thiết; 2 = đủ để trả lời
  và truy nguồn. Đây là đánh giá bằng chứng truy xuất, không phải độ chính xác câu trả lời của LLM.
"""


def write_audit_readme(paths: Paths, pages: list[DocumentPage]) -> None:
    paths.audit.mkdir(parents=True, exist_ok=True)
    (paths.audit / "README.md").write_text(AUDIT_README.format(pages=len(pages)), encoding="utf-8")


def cmd_dev(args: argparse.Namespace, paths: Paths) -> None:
    questions, benchmark_sha = load_benchmark(args.questions, args.allow_incomplete)
    pages = load_content_pages()
    embedder = create_embedder()
    with PgVectorStore(database_url(), embedder.dimensions, embedder.model_name, config.VECTOR_TABLE) as store:
        fingerprints, counts, page_texts = check_indexes(store, embedder, pages)
        traces = run_split(store, embedder, questions, "dev", page_texts)
    summary = summarize(traces)
    save_split(paths, "dev", traces, summary, benchmark_sha)
    write_audit_readme(paths, pages)
    coherence_note = write_coherence_sheet(paths, pages)
    dev_metrics = {strategy: by_split["dev"] for strategy, by_split in summary.items()}
    decision = select_strategy(dev_metrics, counts)

    print(f"Kết quả dev ({sum(q['split'] == 'dev' for q in questions)} câu, top-{config.TOP_K}):")
    print_summary("dev", summary)
    print("Chọn chiến lược theo tiêu chí ghi trước:")
    for step in decision["steps"]:
        print(f"  {step}")
    if paths.metrics("test").exists():
        print("Test đã được chạy trước đó nên KHÔNG ghi đè lựa chọn trong selection.json.")
    else:
        _write_json(paths.selection, {
            "decided_at": _now(), "status": decision["status"], "strategy": decision["strategy"],
            "method": "auto", "rule": config.SELECTION_RULE, "steps": decision["steps"],
            "candidates": decision["candidates"], "dev_metrics": dev_metrics, "chunk_counts": counts,
            "snapshot": {"benchmark_sha256": benchmark_sha, "config": config_snapshot(),
                         "index_fingerprints": fingerprints},
        })
        verdict = (f"Chọn {decision['strategy']}." if decision["status"] == "chosen" else
                   "Chưa phân định được: chấm coherence.csv, chạy `python -m src.run audit` rồi `python -m src.run select --auto`.")
        print(f"  => {verdict} Đã ghi {_display(paths.selection)}")
    if args.update_log:
        update_experiment_log("dev", summary)
    print(f"Đã ghi {_display(paths.traces('dev'))}, {_display(paths.metrics('dev'))} (+ .md); phiếu coherence: "
          f"{coherence_note}")


def cmd_select(args: argparse.Namespace, paths: Paths) -> None:
    if paths.metrics("test").exists():
        sys.exit("Đã chạy test; không đổi lựa chọn sau khi xem kết quả test.")
    if not paths.selection.exists():
        sys.exit("Chưa có selection.json: chạy python -m src.run dev trước.")
    selection = json.loads(paths.selection.read_text(encoding="utf-8"))
    if args.auto:
        coherence = summarize_audit(paths)["coherence"]
        means = {name: item["mean"] for name, item in coherence.items() if item.get("mean") is not None}
        if set(means) != set(config.INDEXES):
            sys.exit("Chưa đủ điểm coherence cho cả hai index A và B trong coherence.csv.")
        decision = select_strategy(selection["dev_metrics"], selection["chunk_counts"], means)
        if decision["status"] != "chosen":
            sys.exit("Vẫn chưa phân định được: " + "; ".join(decision["steps"]))
        selection.update(status="chosen", strategy=decision["strategy"], method="auto (có coherence chấm tay)",
                         steps=decision["steps"], decided_at=_now())
    else:
        if not args.strategy or not args.reason:
            sys.exit("Chọn tay cần tên chiến lược và --reason, vd: python -m src.run select B --reason \"...\"")
        selection.update(status="chosen", strategy=args.strategy, method="manual", decided_at=_now(),
                         steps=selection["steps"] + [f"Chọn tay {args.strategy}: {args.reason}"])
    _write_json(paths.selection, selection)
    print(f"Đã chốt chiến lược {selection['strategy']} ({selection['method']}). Có thể chạy python -m src.run test.")


def cmd_test(args: argparse.Namespace, paths: Paths) -> None:
    if not paths.selection.exists():
        sys.exit("Chưa chạy dev nên chưa có lựa chọn: chạy python -m src.run dev trước.")
    selection = json.loads(paths.selection.read_text(encoding="utf-8"))
    if selection["status"] != "chosen":
        sys.exit("Chưa chốt chiến lược trên dev; xem results/selection.json.")
    questions, benchmark_sha = load_benchmark(args.questions, args.allow_incomplete)
    snapshot = selection["snapshot"]
    if benchmark_sha != snapshot["benchmark_sha256"]:
        sys.exit("Bộ câu hỏi đã đổi sau khi chốt trên dev; chạy lại python -m src.run dev.")
    if config_snapshot() != snapshot["config"]:
        sys.exit("Cấu hình chiến lược/model đã đổi sau khi chốt trên dev; chạy lại python -m src.run dev.")
    pages = load_content_pages()
    embedder = create_embedder()
    with PgVectorStore(database_url(), embedder.dimensions, embedder.model_name, config.VECTOR_TABLE) as store:
        fingerprints, _, page_texts = check_indexes(store, embedder, pages)
        if fingerprints != snapshot["index_fingerprints"]:
            sys.exit("Index đã đổi sau khi chốt trên dev; chạy lại python -m src.run dev.")
        traces = run_split(store, embedder, questions, "test", page_texts)
    summary = summarize(traces)
    save_split(paths, "test", traces, summary, benchmark_sha)
    write_audit_readme(paths, pages)
    notes = write_test_sheets(paths, traces)
    if args.update_log:
        update_experiment_log("test", summary)
    print(f"Kết quả test ({sum(q['split'] == 'test' for q in questions)} câu, top-{config.TOP_K}); chiến lược đã chốt "
          f"từ dev: {selection['strategy']}")
    print_summary("test", summary)
    print(f"Đã ghi {_display(paths.traces('test'))}, {_display(paths.metrics('test'))} (+ .md); " + "; ".join(notes))


def _scores(rows: list[dict[str, str]], column: str, allowed: set[str]) -> tuple[list[str], list[str]]:
    values, problems = [], []
    for row in rows:
        value = (row.get(column) or "").strip()
        if value and value not in allowed:
            problems.append(f"{row.get('strategy')}/{row.get('question_id') or row.get('position_id')}: "
                            f"giá trị {value!r} không hợp lệ")
        elif value:
            values.append(value)
    return values, problems


def summarize_audit(paths: Paths) -> dict[str, Any]:
    """Gộp nhãn chấm tay theo chiến lược; ô trống được đếm là chưa chấm, không tự điền."""
    result: dict[str, Any] = {"generated_at": _now(), "coherence": {}, "metadata": {}, "grounding": {},
                              "problems": []}
    sheets = {"coherence": ("coherence.csv", "coherence_0_1_2", {"0", "1", "2"}),
              "metadata": ("metadata_test.csv", "metadata_ok_1_0", {"0", "1"}),
              "grounding": ("grounding_test.csv", "grounding_0_1_2", {"0", "1", "2"})}
    for name, (filename, column, allowed) in sheets.items():
        path = paths.audit / filename
        if not path.exists():
            continue
        groups: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in _read_csv(path):
            groups[row["strategy"]].append(row)
        for strategy, rows in sorted(groups.items()):
            values, problems = _scores(rows, column, allowed)
            result["problems"] += problems
            counts = Counter(values)
            item: dict[str, Any] = {"rows": len(rows), "labeled": len(values), "unlabeled": len(rows) - len(values),
                                    "distribution": {key: counts.get(key, 0) for key in sorted(allowed)}}
            if name == "metadata":
                item["ok"] = f"{counts.get('1', 0)}/{len(values)}"
            else:
                item["mean"] = round(statistics.mean(int(v) for v in values), 3) if values else None
                item["mean_text"] = f"{item['mean']:.2f}/2" if values else "chưa chấm"
            result[name][strategy] = item
    if "B" in result["coherence"]:
        result["coherence_note"] = "C dùng chung chunk với B nên coherence của C bằng B."
    return result


def cmd_audit(args: argparse.Namespace, paths: Paths) -> None:
    summary = summarize_audit(paths)
    if paths.metrics("test").exists():
        metrics = json.loads(paths.metrics("test").read_text(encoding="utf-8"))["metrics"]
        summary["filter_compliance_test"] = {
            strategy: (item["test"]["filter_compliant"] if isinstance(item["test"]["filter_compliant"], str)
                       else item["test"]["filter_compliant"]["text"]) for strategy, item in metrics.items()}
    _write_json(paths.audit_summary, summary)
    for name in ("coherence", "metadata", "grounding"):
        for strategy, item in summary[name].items():
            score = item.get("ok") or item.get("mean_text")
            print(f"{name:<10} {strategy}: {score} | đã chấm {item['labeled']}/{item['rows']} | "
                  f"phân bố {item['distribution']}")
    for problem in summary["problems"]:
        print(f"Lỗi nhãn: {problem}")
    print(f"Đã ghi {_display(paths.audit_summary)}")


def main(argv: list[str] | None = None) -> None:
    use_utf8_output()
    parser = argparse.ArgumentParser(description="Benchmark Day 06: dev -> chốt chiến lược -> test -> chấm tay.")
    parser.add_argument("--questions", type=Path, default=config.BENCHMARK_PATH)
    parser.add_argument("--results-dir", type=Path, default=config.RESULTS_DIR)
    parser.add_argument("--allow-incomplete", action="store_true",
                        help="chỉ dùng khi thử code với bộ câu hỏi nhỏ, không dùng cho kết quả nộp bài")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("dev", help="chạy dev và chọn chiến lược")
    select = commands.add_parser("select", help="chốt chiến lược khi tiêu chí cần điểm chấm tay")
    select.add_argument("strategy", nargs="?", choices=sorted(config.STRATEGIES))
    select.add_argument("--reason")
    select.add_argument("--auto", action="store_true", help="áp dụng lại tiêu chí với điểm coherence đã chấm")
    commands.add_parser("test", help="chạy test sau khi đã chốt")
    commands.add_parser("audit", help="tổng hợp phiếu chấm tay")
    args = parser.parse_args(argv)
    paths = Paths(args.results_dir.resolve())
    args.update_log = paths.root == config.RESULTS_DIR.resolve() and not args.allow_incomplete
    {"dev": cmd_dev, "select": cmd_select, "test": cmd_test, "audit": cmd_audit}[args.command](args, paths)


if __name__ == "__main__":
    main()
