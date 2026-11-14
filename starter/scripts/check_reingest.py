"""Kiểm tra nạp lại trên một index tạm: nạp mới, nạp lại y nguyên, rồi mô phỏng tài liệu bị sửa.

Chạy từ thư mục starter/ khi database đang chạy:  python scripts/check_reingest.py
Không đụng tới index A, B thật (so dấu vân tay trước/sau); index tạm bị xóa khi kết thúc.
Kết quả: results/reingest_check.json
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config, indexing  # noqa: E402
from src.embeddings import create_embedder  # noqa: E402
from src.ingestion import use_utf8_output  # noqa: E402
from src.store import PgVectorStore  # noqa: E402

TEMP_INDEX = "ZZ_REINGEST_CHECK"


def main() -> None:
    use_utf8_output()
    real_indexes = list(config.INDEXES)
    config.INDEXES[TEMP_INDEX] = dict(config.INDEXES["B"])
    pages = indexing.load_content_pages()[:3]
    embedder = create_embedder()
    steps = {}
    with PgVectorStore(indexing.database_url(), embedder.dimensions, embedder.model_name,
                       config.VECTOR_TABLE) as store:
        store.create_schema()
        before = {name: store.fingerprint(name) for name in real_indexes}
        store.delete_strategy(TEMP_INDEX)
        try:
            chunks = indexing.chunk_corpus(pages, TEMP_INDEX)
            steps["1_nap_moi"] = {"chunks": len(chunks), **indexing.sync_index(store, embedder, TEMP_INDEX, chunks)}
            steps["2_nap_lai_y_nguyen"] = {"chunks": len(chunks),
                                           **indexing.sync_index(store, embedder, TEMP_INDEX, chunks)}
            pages[1].text += "\nCâu bổ sung để mô phỏng tài liệu đã được sửa."
            changed = indexing.chunk_corpus(pages[:2], TEMP_INDEX)
            steps["3_sua_trang_2_bo_trang_3"] = {"chunks": len(changed),
                                                 **indexing.sync_index(store, embedder, TEMP_INDEX, changed)}
        finally:
            removed = store.delete_strategy(TEMP_INDEX)
        after = {name: store.fingerprint(name) for name in real_indexes}

    rerun, edit = steps["2_nap_lai_y_nguyen"], steps["3_sua_trang_2_bo_trang_3"]
    checks = {
        "nap_lai_khong_them_sua_xoa": rerun["inserted"] + rerun["updated"] + rerun["deleted"] == 0,
        "nap_lai_so_dong_khop": rerun["rows_match_chunks"],
        "sua_tai_lieu_co_them_va_xoa": edit["inserted"] > 0 and edit["deleted"] > 0,
        "sua_tai_lieu_so_dong_khop": edit["rows_match_chunks"],
        "index_that_khong_doi": before == after,
    }
    result = {
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "command": "python scripts/check_reingest.py",
        "temp_index": TEMP_INDEX,
        "pages_used": [page.page for page in pages],
        "steps": steps,
        "temp_rows_removed": removed,
        "checks": checks,
        "passed": all(checks.values()),
    }
    out = config.RESULTS_DIR / "reingest_check.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name, step in steps.items():
        print(f"{name}: {step['chunks']} chunk | thêm {step['inserted']}, cập nhật {step['updated']}, "
              f"giữ nguyên {step['unchanged']}, xóa {step['deleted']} | số dòng {step['rows_in_db']}")
    print("Kiểm tra: " + ", ".join(f"{name}={'đạt' if ok else 'KHÔNG ĐẠT'}" for name, ok in checks.items()))
    print(f"Đã xóa index tạm ({removed} dòng). Kết quả: results/reingest_check.json")
    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
