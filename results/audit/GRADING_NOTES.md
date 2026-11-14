# Ghi chú người chấm

- **Ngày chấm:** 2026-11-14, sau khi đã chạy test (chiến lược C đã được chốt từ dev hôm 2026-11-13).
- **Người chấm:** Claude (trợ lý AI) chấm nháp theo yêu cầu của học viên Nguyễn Tiến Cường. Học viên, cũng là tác giả đồ án, cần rà soát và sửa nhãn trước khi nộp. Nếu sửa, chạy lại `python -m src.run audit` trong thư mục `starter/`.
- **coherence.csv, grounding_test.csv:** đọc nguyên văn từng chunk và chấm theo thang trong GUIDE.md; lý do ghi ở cột `reason`.
- **metadata_test.csv:** dùng script đối chiếu từng bản ghi với ba nguồn: văn bản trích lại từ đúng trang PDF (pypdf), số trang in (trang PDF − 10) và mục lục (trang 5–7). Bản ghi chỉ đạt khi `source`, `page`, `chapter`, `section` đều khớp. Bị tính 0 nếu chunk vắt sang một mục không phải mục con của mục đã ghi, hoặc `section_title` bị cắt. Mọi dòng script báo lỗi đều được đọc lại; có 1 dòng script báo nhầm (do đọc mục lục) nên được giữ là 1 và ghi chú.

## Quy tắc chấm coherence áp dụng thống nhất

- **2:** chunk bắt đầu và kết thúc ở ranh giới câu hoặc đoạn; ý tại vị trí tự hiểu được.
- **1:** ý tại vị trí hiểu được, nhưng chunk bị cắt giữa câu hoặc từ ở đầu/cuối, hoặc thiếu câu dẫn hay tiêu đề cần thiết (kể cả mảnh câu nối từ trang trước).
- **0:** chính câu hoặc ý tại vị trí bị cắt làm mất nghĩa.

## Grounding

- Chấm trên cả top-3, có thể ghép nhiều chunk.
- Nếu top-3 chứa bằng chứng tương đương với gold nhưng ở trang khác, người chấm ghi rõ trong `reason`.
- Hit@1 và Evidence Hit@3 tự động vẫn giữ định nghĩa chặt. Benchmark không được sửa sau khi xem test.

## Nguy cơ thiên lệch

- Người chấm cũng là công cụ AI đã viết pipeline và benchmark.
- Người chấm biết chiến lược nào được chọn trước khi chấm, và không chấm ẩn danh chiến lược.
- Coherence chỉ có 10 vị trí, grounding chỉ có 6 câu: chênh 1 nhãn đã làm điểm trung bình đổi 0,1–0,17.
