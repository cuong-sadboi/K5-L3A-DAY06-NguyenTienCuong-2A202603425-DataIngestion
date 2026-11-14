"""Đường dẫn và khai báo nguồn dữ liệu dùng chung cho pipeline Day 06."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DATA_DIR = REPO_ROOT / "starter" / "data"  # PDF gốc, không commit (xem .gitignore)
MANIFEST_PATH = REPO_ROOT / "data" / "manifest.json"
BENCHMARK_PATH = REPO_ROOT / "benchmark" / "questions.jsonl"
RESULTS_DIR = REPO_ROOT / "results"

ENV_FILE = REPO_ROOT / "starter" / ".env"  # DATABASE_URL

MIN_CORPUS_PAGES = 30
# Trang nằm trong phạm vi nội dung nhưng có ít chữ hơn ngưỡng này (chỉ còn chú thích hình)
# thì không được tính là trang nội dung và không đưa vào index.
MIN_CONTENT_CHARS = 200

# Hai index thật trong pgvector. Chiến lược C dùng lại đúng chunk và vector của index B,
# chỉ thêm metadata filter lúc truy vấn (khi câu hỏi có filter), để B và C chỉ khác một yếu tố.
# B giới hạn 800 ký tự: trung bình ~544 ký tự, gần A (~450) nên so sánh A-B chủ yếu do cách cắt;
# mức 600 cho kích thước sát A hơn nhưng sinh 15 mảnh dưới 100 ký tự (800 chỉ còn 1).
INDEXES = {
    "A": {"chunker": "fixed_size", "size": 500, "overlap": 50},
    "B": {"chunker": "by_paragraph", "max_size": 800, "min_segment": 120},
}
STRATEGIES = {
    "A": {"index": "A", "use_filter": False},
    "B": {"index": "B", "use_filter": False},
    "C": {"index": "B", "use_filter": True},
}
TOP_K = 3

# Tiêu chí chọn chiến lược, ghi trước khi chạy (xem benchmark/README.md); chỉ áp dụng trên dev.
SELECTION_RULE = (
    "1) Evidence Hit@3 trên dev cao nhất (giữ các chiến lược kém tốt nhất không quá 1 câu); "
    "2) so Hit@1 trên dev; 3) nếu vẫn hòa giữa hai index khác nhau: coherence trung bình chấm tay; "
    "4) ít chunk hơn; 5) cấu hình đơn giản hơn (không cần filter)."
)

# Embedding chạy local, giống nhau cho mọi chiến lược. Model e5 yêu cầu tiền tố "query: "/"passage: ".
EMBEDDING = {
    "model_name": "intfloat/multilingual-e5-base",
    "query_prefix": "query: ",
    "passage_prefix": "passage: ",
    "batch_size": 32,
}
VECTOR_TABLE = "chunks"

SOURCES = [
    {
        "source_id": "doan-bot-traffic-2026",
        "file": "K21.KTPM_Nguyễn Tiến Cường.pdf",
        "title": (
            "Xây dựng hệ thống phân tích và phát hiện lưu lượng truy cập ảo trong "
            "Digital Marketing dựa trên Machine Learning (đồ án tốt nghiệp, 2026)"
        ),
        "license_or_permission": (
            "Đồ án tốt nghiệp do chính học viên là tác giả; học viên cho phép xử lý cục bộ "
            "cho mục đích học tập trong lab này. Chưa xác minh quy định công bố đồ án của "
            "trường và trang bìa có thông tin cá nhân, nên bản PDF gốc không commit vào repo."
        ),
        "accessed_at": "2026-11-13",
        "language": "vi",
        # Trang PDF vật lý (đánh số từ 1) chứa nội dung: từ Mở đầu đến hết Kết luận.
        "content_pages": (11, 86),
        # Trang mục lục: dùng để đối chiếu heading phát hiện được trong thân bài.
        "toc_pages": (5, 6, 7),
        # Trang ngoài nội dung, xác định bằng cách đọc mục lục và tiêu đề từng trang.
        "non_content_pages": {
            1: "bìa",
            2: "bìa phụ",
            3: "lời cảm ơn",
            4: "lời cam đoan",
            5: "mục lục",
            6: "mục lục",
            7: "mục lục",
            8: "danh sách bảng",
            9: "danh sách bảng",
            10: "danh sách hình vẽ",
            87: "tài liệu tham khảo",
            88: "tài liệu tham khảo",
            89: "trang nhận xét của giáo viên hướng dẫn (để trống)",
        },
    },
]
