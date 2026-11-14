"""Chấm truy xuất tự động và chọn chiến lược trên dev.

- evaluate_query: đánh dấu từng chunk top-k (đúng trang gold? chứa đủ evidence?) và tính Hit@1/Hit@3.
- summarize: gộp theo chiến lược và split, báo dạng n/N.
- select_strategy: áp dụng tiêu chí chọn đã ghi trước (config.SELECTION_RULE) lên kết quả dev.

Nhãn coherence, metadata và grounding là chấm tay; không suy ra từ so khớp chuỗi.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any

from . import config
from .benchmark import gold_entries
from .ingestion import normalize_for_match


def gold_list(question: dict[str, Any]) -> list[dict[str, Any]]:
    """Gold chính và gold thay thế, kèm khóa so khớp của evidence."""
    return [{"source_id": source, "page": page, "evidence_quote": quote, "key": normalize_for_match(quote)}
            for source, page, quote in gold_entries(question)]


def evidence_available(question: dict[str, Any], page_texts: dict[tuple[str, int], list[str]]) -> bool:
    """Có chunk nào trong index chứa trọn evidence ở trang gold không. False nghĩa là evidence bị tách
    qua nhiều chunk, nên chiến lược này không thể hit câu đó dù truy xuất đúng chỗ."""
    return any(gold["key"] in text
               for gold in gold_list(question)
               for text in page_texts.get((gold["source_id"], gold["page"]), []))


def evaluate_query(question: dict[str, Any], hits: list[dict[str, Any]], **context: Any) -> dict[str, Any]:
    """Một hit là chunk thuộc đúng nguồn/trang gold và chứa đủ evidence đã ghi (chấp nhận mọi alt_gold).

    Hit@1: chunk hạng 1 là hit. Hit@3: có ít nhất một hit trong top-3. `context` (strategy, index,
    filter thực sự áp dụng, evidence_in_index, độ trễ) được ghi nguyên vào trace.
    """
    golds = gold_list(question)
    wanted = question.get("filter") or None
    marked = []
    for hit in hits:
        key = normalize_for_match(hit["text"])
        on_gold_page = [g for g in golds if g["source_id"] == hit["document_id"] and g["page"] == hit["page"]]
        contains = any(g["key"] in key for g in on_gold_page)
        filter_ok = None if not wanted else all(hit.get(field) == value for field, value in wanted.items())
        marked.append({**hit, "gold_page": bool(on_gold_page), "contains_evidence": contains,
                       "hit": contains, "filter_ok": filter_ok})
    first_hit = next((item["rank"] for item in marked if item["hit"]), None)
    return {
        **context,
        "question_id": question["id"],
        "split": question["split"],
        "type": question.get("type"),
        "question": question["question"],
        "expected_answer": question["expected_answer"],
        "filter": wanted,
        "gold": [{key: gold[key] for key in ("source_id", "page", "evidence_quote")} for gold in golds],
        "hit_at_1": bool(marked) and marked[0]["hit"],
        "hit_at_3": first_hit is not None,
        "first_hit_rank": first_hit,
        "gold_page_in_top_k": any(item["gold_page"] for item in marked),
        "filter_compliant": None if not wanted else all(item["filter_ok"] for item in marked),
        "hits": marked,
    }


def _ratio(count: int, total: int) -> dict[str, Any]:
    return {"n": count, "N": total, "text": f"{count}/{total}", "rate": round(count / total, 4) if total else None}


def summarize(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Hit@1 và Evidence Hit@3 theo chiến lược và split (n/N), kèm số liệu phụ để phân tích lỗi."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for trace in traces:
        groups[(trace["strategy"], trace["split"])].append(trace)
    summary: dict[str, Any] = {}
    for (strategy, split), items in sorted(groups.items()):
        total = len(items)
        filtered = [t for t in items if t["filter"]]
        by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for trace in items:
            by_type[trace["type"] or "khac"].append(trace)
        summary.setdefault(strategy, {})[split] = {
            "index": items[0]["index"],
            "questions": total,
            "hit_at_1": _ratio(sum(t["hit_at_1"] for t in items), total),
            "hit_at_3": _ratio(sum(t["hit_at_3"] for t in items), total),
            "gold_page_in_top_k": _ratio(sum(t["gold_page_in_top_k"] for t in items), total),
            "mrr_at_k": round(statistics.mean(1 / t["first_hit_rank"] if t["first_hit_rank"] else 0 for t in items), 4),
            "evidence_split_in_index": [t["question_id"] for t in items if t["evidence_in_index"] is False],
            "filter_compliant": (_ratio(sum(bool(t["filter_compliant"]) for t in filtered), len(filtered))
                                 if filtered else "N/A"),
            "hit_at_3_with_filter": _ratio(sum(t["hit_at_3"] for t in filtered), len(filtered)) if filtered else "N/A",
            "hit_at_3_without_filter": _ratio(sum(t["hit_at_3"] for t in items if not t["filter"]),
                                              total - len(filtered)),
            "hit_at_3_by_type": {name: _ratio(sum(t["hit_at_3"] for t in group), len(group))
                                 for name, group in sorted(by_type.items())},
            "mean_latency_ms": round(statistics.mean(t["latency_ms"] for t in items), 1),
        }
    return summary


def select_strategy(dev: dict[str, dict[str, Any]], chunk_counts: dict[str, int],
                    coherence: dict[str, float] | None = None) -> dict[str, Any]:
    """Áp dụng config.SELECTION_RULE lên metric dev. Trả về status "chosen" hoặc "pending"
    (khi cần điểm coherence chấm tay mà chưa có)."""
    index_of = {name: spec["index"] for name, spec in config.STRATEGIES.items()}
    uses_filter = {name: spec["use_filter"] for name, spec in config.STRATEGIES.items()}
    steps: list[str] = []
    candidates = sorted(dev)

    def keep(label: str, values: dict[str, Any], survivors: list[str]) -> list[str]:
        shown = ", ".join(f"{name}={values[name]}" for name in candidates)
        steps.append(f"{label}: {shown} -> còn {', '.join(survivors)}")
        return survivors

    hit3 = {name: dev[name]["hit_at_3"]["n"] for name in candidates}
    candidates = keep("Bước 1, Evidence Hit@3 dev (giữ các chiến lược kém tốt nhất không quá 1 câu)", hit3,
                      [n for n in candidates if hit3[n] >= max(hit3.values()) - 1])
    if len(candidates) > 1:
        hit1 = {name: dev[name]["hit_at_1"]["n"] for name in candidates}
        candidates = keep("Bước 2, Hit@1 dev", hit1, [n for n in candidates if hit1[n] == max(hit1.values())])
    if len(candidates) > 1 and len({index_of[n] for n in candidates}) > 1:
        if coherence is None:
            steps.append(f"Bước 3, coherence: cần điểm chấm tay để phân định {', '.join(candidates)}")
            return {"status": "pending", "strategy": None, "candidates": candidates, "steps": steps}
        scores = {name: coherence[index_of[name]] for name in candidates}
        candidates = keep("Bước 3, coherence trung bình (chấm tay)", scores,
                          [n for n in candidates if scores[n] == max(scores.values())])
    if len(candidates) > 1:
        counts = {name: chunk_counts[index_of[name]] for name in candidates}
        candidates = keep("Bước 4, số chunk (ít hơn tốt hơn)", counts,
                          [n for n in candidates if counts[n] == min(counts.values())])
    if len(candidates) > 1:
        simpler = [n for n in candidates if not uses_filter[n]] or candidates
        candidates = keep("Bước 5, ưu tiên cấu hình đơn giản hơn (không cần filter)",
                          {n: "không filter" if not uses_filter[n] else "có filter" for n in candidates}, simpler[:1])
    return {"status": "chosen", "strategy": candidates[0], "candidates": candidates, "steps": steps}


def metrics_markdown(split: str, summary: dict[str, Any], traces: list[dict[str, Any]]) -> str:
    """Bảng tóm tắt để dán vào báo cáo, kèm ma trận từng câu (hạng của hit đầu tiên, '-' nếu trượt)."""
    lines = [f"# Kết quả truy xuất: {split}", "",
             "| Chiến lược | Index | Hit@1 | Evidence Hit@3 | Trang gold trong top-3 | Evidence bị tách trong index "
             "| Filter tuân thủ | Độ trễ TB |",
             "|---|---|---|---|---|---|---|---|"]
    for strategy, by_split in summary.items():
        item = by_split.get(split)
        if not item:
            continue
        compliant = item["filter_compliant"]
        compliant = compliant if isinstance(compliant, str) else compliant["text"]
        lines.append(f"| {strategy} | {item['index']} | {item['hit_at_1']['text']} | {item['hit_at_3']['text']} | "
                     f"{item['gold_page_in_top_k']['text']} | {len(item['evidence_split_in_index'])} | {compliant} | "
                     f"{item['mean_latency_ms']} ms |")
    strategies = sorted({t["strategy"] for t in traces})
    lines += ["", "## Từng câu (hạng của chunk hit đầu tiên; '-' là không có hit trong top-3)", "",
              "| Câu | Loại | Filter | Trang gold | " + " | ".join(strategies) + " |",
              "|---|---|---|---|" + "---|" * len(strategies)]
    rows: dict[str, dict[str, Any]] = {}
    for trace in traces:
        row = rows.setdefault(trace["question_id"], {"trace": trace})
        row[trace["strategy"]] = trace["first_hit_rank"] or "-"
    for question_id, row in sorted(rows.items()):
        trace = row["trace"]
        gold_pages = ", ".join(str(g["page"]) for g in trace["gold"])
        cells = " | ".join(str(row.get(s, "")) for s in strategies)
        lines.append(f"| {question_id} | {trace['type']} | {trace['filter'] or ''} | {gold_pages} | {cells} |")
    return "\n".join(lines) + "\n"
