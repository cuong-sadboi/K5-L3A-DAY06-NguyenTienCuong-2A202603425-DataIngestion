# Benchmark: đồ án phát hiện bot traffic

`questions.jsonl` có 24 câu (18 dev / 6 test), mỗi dòng một câu. Kiểm tra bằng lệnh `python -m src.benchmark` chạy trong thư mục `starter/`. Kết quả ghi ra `results/benchmark_validation.json`, kèm sha256 của file câu hỏi tại thời điểm chốt.

## Cách soạn và kiểm chứng

- Bộ câu hỏi do Claude (trợ lý AI) soạn nháp theo yêu cầu của học viên. Nguồn là chính văn bản corpus đã chuẩn hóa (`python -m src.ingestion`). Việc soạn diễn ra trước khi viết code chia chunk và trước mọi lần chạy truy xuất.
- Mỗi `evidence_quote` được chép nguyên văn từ trang gold. Script kiểm tra xác nhận evidence nằm đúng trang PDF; phép so khớp bỏ qua hoa/thường, khoảng trắng và gạch nối.
- Những trang khác nêu cùng thông tin được ghi thêm vào `alt_gold`: q15 (trang 54), q16 (trang 49), q21 (trang 77).
- Loại câu: `truc_tiep` (11 câu) dùng từ ngữ gần với nguồn; `dien_dat_lai` (9 câu) hỏi bằng từ ngữ khác nguồn; `dieu_kien` (4 câu) hỏi về điều kiện hoặc tình huống.

## Chia dev/test

- Chia theo mục của đồ án: không mục nào có cả câu dev lẫn câu test. Tập test lấy từ các mục 1.5.1, 2.2.2, 3.5, 3.19, 3.20 và Kết luận.
- Nguy cơ rò rỉ: vài chủ đề gần nhau giữa hai tập, gồm hạn chế của cách dựa trên luật (q04, q18 ở dev; q23 ở test) và dữ liệu thực nghiệm (q08 ở dev; q24 ở test).
- Tập test được viết cùng lúc với dev, trước khi chạy thử. Không sửa tập test sau khi xem kết quả.

## Câu có filter (8 câu)

Giá trị filter được nêu ngay trong câu hỏi ("Trong chương …"), tức là đầu vào có sẵn trước khi truy xuất, không lấy từ gold.

| Câu | Tập | Filter | Vì sao cần lọc |
|---|---|---|---|
| q07 | dev | `{"chapter": 1}` | Chương 3 cũng mô tả dữ liệu thực nghiệm |
| q08 | dev | `{"chapter": 3}` | Chương 1 cũng liệt kê các nguồn dữ liệu traffic |
| q09 | dev | `{"chapter": 1}` | Chương 2 mô tả MySQL theo cách khác (mục 2.3.5, 2.5) |
| q10 | dev | `{"chapter": 2}` | Chương 1 cũng nói MySQL lưu dữ liệu gì |
| q11 | dev | `{"chapter": 2}` | Trang 27 (chương 1) nêu cùng ba nhãn; chương 3 chỉ phân loại hai lớp |
| q17 | dev | `{"chapter": 1}` | Chương 3 bàn về rủi ro và độ tin cậy của AI Service |
| q21 | test | `{"chapter": 3}` | Chương 1 đặt mục tiêu "Accuracy trên 85%", dễ nhầm với kết quả đạt được |
| q22 | test | `{"chapter": 2}` | Chương 3 có số đo độ trễ thực tế (42 ms, 185 ms, …) |

## Tiêu chí chọn chiến lược (đề xuất, cần học viên xác nhận trước khi chạy)

1. Evidence Hit@3 trên dev cao nhất.
2. Nếu chênh nhau không quá 1 câu: so Hit@1 trên dev.
3. Nếu vẫn hòa giữa hai index khác nhau (A và B): so điểm coherence trung bình trên 10 vị trí.
4. Nếu vẫn hòa: chọn cấu hình ít chunk hơn.
5. Nếu vẫn hòa (B và C dùng chung index): chọn cấu hình đơn giản hơn, tức không cần filter.

Chỉ chạy tập test sau khi đã chốt cấu hình theo các bước trên. `python -m src.run dev` tự áp dụng các bước này và ghi lựa chọn vào `results/selection.json`; `python -m src.run test` từ chối chạy nếu chưa chốt, hoặc nếu bộ câu hỏi, cấu hình hay index đã đổi sau khi chốt.

## Checklist rà soát của học viên

- [ ] Đọc từng câu: đáp án đúng với nội dung đồ án, câu hỏi tự nhiên.
- [ ] Sửa hoặc thay câu chưa ổn, rồi chạy lại `python -m src.benchmark` đến khi đạt.
- [ ] Xác nhận tiêu chí chọn chiến lược ở trên.
- [ ] Ghi vào báo cáo rằng bộ câu hỏi do Claude soạn nháp, học viên đã rà soát và kiểm chứng bằng script.
