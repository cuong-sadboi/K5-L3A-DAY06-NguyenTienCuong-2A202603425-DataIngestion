# Phiếu chấm tay

Mở các file CSV bằng Excel hoặc VS Code. Chỉ điền các cột chấm điểm, không sửa cột khác, rồi chạy
`python -m src.run audit` (trong thư mục `starter/`) để tổng hợp. Các cột `auto_*` chỉ để tham khảo;
hãy tự đọc và chấm. Người chấm là tác giả đồ án, cần ghi nguy cơ thiên lệch trong báo cáo.

## coherence.csv (tạo ở bước dev)

- 10 vị trí cố định, chọn trước khi xem điểm: 69 trang nội dung xếp theo số trang, lấy 10 trang cách đều
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
