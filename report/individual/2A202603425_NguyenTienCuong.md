# Báo cáo cá nhân — Day 6

**Học viên:** Nguyễn Tiến Cường · 2A202603425 · L3A
**Thời điểm chạy:** 2026-11-13 (ingest, dev, test) · 2026-11-14 (chấm tay, kiểm tra nạp lại)

> Báo cáo do Claude (trợ lý AI) soạn từ kết quả chạy thật trong repo. Mục "Sử dụng AI" ghi rõ phần việc của AI. Học viên cần đọc lại và xác nhận các mục ở cuối báo cáo trước khi nộp.

## Dữ liệu và pipeline

- **Chủ đề, nguồn/quyền, số trang và quy tắc tính trang:**
  - **Nguồn:** đồ án tốt nghiệp năm 2026 của chính học viên, *Xây dựng hệ thống phân tích và phát hiện lưu lượng truy cập ảo trong Digital Marketing dựa trên Machine Learning*. Một file PDF xuất từ LaTeX, 89 trang PDF, tiếng Việt có nhiều thuật ngữ tiếng Anh.
  - **Quyền:** học viên là tác giả và cho phép xử lý cục bộ cho lab. PDF gốc không được commit, vì trang bìa có thông tin cá nhân và chưa xác minh quy định công bố đồ án của trường. [data/manifest.json](../../data/manifest.json) ghi sha256 của file để đối chiếu.
  - **Quy tắc tính trang:** dùng số trang PDF vật lý, không dùng số in ở đầu trang (số in = trang PDF − 10).
    - Tính các trang 11–86 (từ Mở đầu đến hết Kết luận).
    - Loại bìa (1–2), lời cảm ơn và lời cam đoan (3–4), mục lục (5–7), danh sách bảng và hình (8–10), tài liệu tham khảo (87–88), trang nhận xét để trống (89).
    - Loại thêm 7 trang chỉ có hình hoặc chú thích, dưới 200 ký tự chữ (40–41, 80–84).
  - **Kết quả: 69 trang nội dung, 138.371 ký tự** sau chuẩn hóa. Log từng trang (tính hay loại, lý do, số ký tự) ở [results/ingestion_pages.csv](../../results/ingestion_pages.csv).
- **Chuẩn hóa, lỗi và giới hạn:**
  - **Trích chữ và chuẩn hóa:** pypdf 6.21.4 trích từng trang. Chuẩn hóa Unicode NFC, bỏ ký tự vô hình, gộp khoảng trắng trong dòng, tách số trang in ở dòng đầu.
  - **Sửa lỗi trích chữ** dựa trên từ vựng của chính tài liệu:
    - Nối 63 từ bị LaTeX ngắt dòng bằng gạch nối ("anal-ysis" → "analysis").
    - Giữ 3 gạch nối thật ("rule-based", "user-agent").
    - Gộp 29 chữ hoa bị kerning tách ra ("Y êu" → "Yêu", "T ypeScript" → "TypeScript").
    - Bản đầu gộp nhầm "A Definition" thành "ADefinition"; lỗi này đã được sửa trước khi chia chunk.
  - **Heading:** tìm thấy trong thân bài đủ 89/89 mục có trong mục lục. Mỗi trang và mỗi vị trí ký tự được gắn `chapter`/`section`.
  - **Không có trang lỗi trích chữ hay trang scan**, nên không cần OCR.
  - **Giới hạn:** bảng bị trích thành chuỗi ô liền nhau, mất cấu trúc hàng/cột. Heading dài xuống dòng chỉ được lấy dòng đầu, nên `section_title` có thể bị cắt (phát hiện khi audit metadata).
- **Embedding, vector store, metric, lệnh chạy và chi phí:**
  - **Embedding:** `intfloat/multilingual-e5-base` (revision `d128750597153bb5987e10b1c3493a34e5a4502a`), chạy local bằng sentence-transformers 6.3.0 (transformers 5.22.2, torch 2.15.0 CPU).
    - 768 chiều, tối đa 512 token.
    - Tiền tố `passage: ` cho chunk và `query: ` cho câu hỏi; vector được chuẩn hóa.
    - Không chunk nào vượt 512 token (token tối đa: A 155, B 232).
  - **Vector store:** PostgreSQL 16.16 + pgvector 0.8.7 (image `pgvector/pgvector:0.8.7-pg16`), bảng `chunks` với khóa chính `(strategy, chunk_id)`. Tìm kiếm chính xác, không tạo index ANN, theo khoảng cách cosine `<=>`; score = 1 − khoảng cách.
  - **Lệnh chạy:** xem [README](../../README.md). Các bước chính: `python -m src.indexing` để nạp, `python -m src.run dev|test|audit` để đánh giá.
  - **Chi phí:** 0, vì model chạy local và không gọi API trả phí. Môi trường: Windows 11, CPU, Python 3.14.10.
- **Metadata bắt buộc, trường lọc bổ sung, nguồn giá trị và lý do chọn:**
  - **Bắt buộc:** `document_id`, `source` (đường dẫn PDF), `page` (trang PDF).
  - **Bổ sung:**
    - `chapter` (số nguyên; null với Mở đầu và Kết luận) là **trường lọc**.
    - `section`, `section_title` lấy từ heading trong thân bài và đã đối chiếu với mục lục.
    - `char_start`/`char_end` là vị trí ký tự trên trang, dùng để truy vết và chọn mẫu coherence.
    - `content_hash`, `doc_sha256` dùng để phát hiện thay đổi khi nạp lại.
  - **Vì sao lọc theo chapter:** nhiều chủ đề lặp lại giữa các chương. Ví dụ: phát biểu bài toán ở mục 1.2 và 2.1.1; MySQL ở 1.7.2, 2.3.5 và 2.5; độ chính xác có mục tiêu "trên 85%" ở chương 1 nhưng kết quả 93,85% ở chương 3. Giá trị chương lấy từ dòng heading "CHƯƠNG n" trong văn bản, không suy đoán.

## Cấu hình và benchmark

- **A / B / C (tham số cụ thể, khác biệt duy nhất B–C):**

  | | Cách chia | Tham số | Index | Filter |
  |---|---|---|---|---|
  | A | Cửa sổ ký tự cố định, có thể cắt giữa từ | size 500, overlap 50, không vượt ranh giới trang | A: 337 chunk | Không |
  | B | Theo heading → đoạn → câu; câu quá dài cắt tại khoảng trắng | Tối đa 800 ký tự; mục ngắn dưới 120 ký tự gộp vào mục sau; không vượt ranh giới trang hay mục | B: 254 chunk | Không |
  | C | Giống hệt B (dùng chung chunk và vector) | Như B | B | `WHERE chapter = n`, chỉ khi câu hỏi có filter |

  - Cả ba dùng cùng corpus, model, store, metric và top-3. A–B chỉ khác cách chia chunk; **B–C chỉ khác filter lúc truy vấn**.
  - Mức tối đa 800 ký tự của B được chọn từ thống kê độ dài chunk, trước khi có kết quả truy xuất: độ dài trung bình 544 ký tự, gần với A (450). Mức 600 sinh ra 15 mảnh dưới 100 ký tự.
- **≥20 câu: phân chia ≥15 dev / ≥5 test, câu metadata filter và cách kiểm chứng evidence:**
  - **Bộ câu hỏi:** 24 câu trong [benchmark/questions.jsonl](../../benchmark/questions.jsonl), gồm 18 dev và 6 test. Theo loại: 11 câu hỏi trực tiếp, 9 câu diễn đạt lại, 4 câu về điều kiện.
  - **Câu có filter:** 8 câu lọc theo chương (6 dev, 2 test). Giá trị filter nêu ngay trong câu hỏi ("Trong chương 2, …").
  - **Chia dev/test theo mục của đồ án:** không mục nào có câu ở cả hai tập. Tập test lấy từ các mục 1.5.1, 2.2.2, 3.5, 3.19, 3.20 và Kết luận.
  - **Kiểm chứng evidence:** `python -m src.benchmark` xác nhận 24/24 evidence nằm đúng trang gold sau chuẩn hóa (bỏ qua hoa/thường, khoảng trắng, gạch nối). Có gold thay thế cho q15, q16, q21. Báo cáo kiểm tra kèm sha256 của file câu hỏi (`5f5613d48d0d…`): [results/benchmark_validation.json](../../results/benchmark_validation.json).
  - Claude soạn nháp câu hỏi từ văn bản đã chuẩn hóa, trước khi viết code chia chunk và trước mọi lần truy xuất.
- **Tiêu chí chọn chiến lược đã chốt trước khi xem test:**
  1. Evidence Hit@3 trên dev cao nhất (giữ lại chiến lược kém chiến lược tốt nhất không quá 1 câu).
  2. Hit@1 trên dev.
  3. Coherence, nếu hai index khác nhau vẫn hòa.
  4. Ít chunk hơn.
  5. Cấu hình đơn giản hơn (không cần filter).
  - Tiêu chí được ghi trong `SELECTION_RULE` (starter/src/config.py) và [benchmark/README.md](../../benchmark/README.md) trước khi chạy dev.
  - `python -m src.run dev` áp dụng tiêu chí lúc 2026-11-13 19:29:54 và **chọn C ngay ở bước 1** (Hit@3 dev: A 12, B 15, C 17).
  - `python -m src.run test` chạy lúc 19:34:28. Lệnh này kiểm tra sha256 của benchmark, cấu hình và dấu vân tay của index không đổi so với lúc chốt ([results/selection.json](../../results/selection.json)).

## Kết quả

| Strategy | Split | Hit@1 (n/N) | Evidence Hit@3 (n/N) | Chunk count | Thời gian | Chi phí |
|---|---|---|---|---|---|---|
| A | dev | 8/18 | 12/18 | 337 | embed 35,0 s; 62 ms/câu | 0 |
| A | test | 2/6 | 3/6 | 337 | 67 ms/câu | 0 |
| B | dev | 9/18 | 15/18 | 254 | embed 31,05 s; 72 ms/câu | 0 |
| B | test | 4/6 | 4/6 | 254 | 69 ms/câu | 0 |
| C | dev | **11/18** | **17/18** | 254 (index B) | không nạp thêm; 62 ms/câu | 0 |
| C | test | 4/6 | 4/6 | 254 (index B) | 73 ms/câu | 0 |

Ghi chú về thời gian và các chỉ số phụ:
- "embed" là thời gian tạo embedding ở lần nạp đầu (2026-11-13 19:15, [results/index_runs.jsonl](../../results/index_runs.jsonl)). Cả lệnh nạp hai index mất 84,7 s, trong đó 16,6 s là tải model.
- "ms/câu" là thời gian truy xuất trung bình mỗi câu, kể cả embed câu hỏi trên CPU. Chỉ đo một lần, nên chênh lệch giữa các chiến lược nằm trong nhiễu.
- **Trang gold có trong top-3:** dev A 16/18, B 16/18, C 17/18; test 5/6 ở cả ba.
- **MRR@3 trên dev:** A 0,56, B 0,66, C 0,76.
- **Evidence bị cắt đôi trong index** (không chunk nào chứa trọn): với A là q05, q08, q18 (dev) và q23 (test); B không có câu nào.

| Strategy | Coherence trung bình /2 (n) | Metadata đủ và đúng (n/N) | Filter tuân thủ (n/N hoặc N/A) | Grounding 0/1/2 (số câu) và trung bình /2 |
|---|---|---|---|---|
| A | 0,80 (n=10; 0/1/2 = 2/8/0) | 16/18 | test 0/2 (dev 2/6) | 0/2/4 → 1,67 |
| B | 1,70 (n=10; 0/1/2 = 0/3/7) | 16/18 | test 1/2 (dev 2/6) | 1/0/5 → 1,67 |
| C | 1,70 (dùng chung chunk của B) | 16/18 | test 2/2 (dev 6/6) | 1/0/5 → 1,67 |

**Đường dẫn trace, nhãn thủ công và cách chọn mẫu:**
- **Trace và chỉ số:**
  - Trace top-3 đầy đủ (text, score, metadata, cờ hit): [results/traces/dev.jsonl](../../results/traces/dev.jsonl), [results/traces/test.jsonl](../../results/traces/test.jsonl).
  - Chỉ số: [metrics_dev.md](../../results/metrics_dev.md), [metrics_test.md](../../results/metrics_test.md) (kèm bản .json).
- **Nhãn chấm tay:**
  - Phiếu chấm: [coherence.csv](../../results/audit/coherence.csv), [metadata_test.csv](../../results/audit/metadata_test.csv), [grounding_test.csv](../../results/audit/grounding_test.csv).
  - Tổng hợp: [results/audit_summary.json](../../results/audit_summary.json).
  - Phương pháp và người chấm: [GRADING_NOTES.md](../../results/audit/GRADING_NOTES.md).
- **Cách chọn mẫu:**
  - **Coherence:** vị trí cố định, chọn trước khi chấm. Xếp 69 trang nội dung theo số trang, lấy 10 trang cách đều (11, 19, 26, 34, 43, 51, 58, 66, 73, 86); vị trí là điểm giữa văn bản của trang. Mỗi vị trí chấm chunk chứa nó ở index A và B.
  - **Metadata:** toàn bộ 6 câu test × top-3 × 3 chiến lược = 54 bản ghi.
  - **Grounding:** 6 câu test × 3 chiến lược.
- **Người chấm:** Claude chấm nháp ngày 2026-11-14, sau khi đã chạy test và đã biết C được chọn (xem phần Hạn chế).
- **Nạp lại:**
  - Chạy lại `python -m src.indexing` hai lần: không thêm và không xóa chunk nào; số dòng trong DB khớp 337/254.
  - `python scripts/check_reingest.py` chạy trên một index tạm: nạp lại y nguyên thì không thay đổi gì; sửa trang 2 và bỏ trang 3 thì thêm 1, giữ 8, xóa 4 chunk; index thật không đổi ([results/reingest_check.json](../../results/reingest_check.json)).

## Phân tích và kết luận

- **So sánh A–B và B–C; 2 ví dụ sai có ID câu và nguồn:**
  - **A → B (đổi cách chia chunk):**
    - B tốt hơn trên cả hai tập: Evidence Hit@3 dev từ 12 lên 15/18, test từ 3 lên 4/6. Hit@1 dev từ 8 lên 9/18, test từ 2 lên 4/6. Coherence từ 0,80 lên 1,70.
    - **Lợi thế chính của B là không cắt đôi evidence.** A tìm đúng trang gold gần ngang B (dev 16/18 cả hai), nhưng ở q05, q08, q18 (dev) và q23 (test) không chunk A nào chứa trọn câu evidence. Nhóm câu điều kiện bị ảnh hưởng nặng nhất: trên dev, A đạt 1/3 còn B đạt 3/3.
    - **A thắng ở 2 câu hỏi về một chi tiết hẹp:** dev q16 (access token) và test q19 (bad bot). Chunk ngắn của A tập trung đúng vào chi tiết; riêng q16 trúng nhờ một mảnh cuối trang chỉ 62 ký tự. Chunk dài của B trộn nhiều ý nên độ tương đồng bị pha loãng.
    - **Tài nguyên:** B ít hơn 25% số chunk (254 so với 337), embed nhanh hơn (31,05 s so với 35,0 s) và không lưu trùng phần overlap (138.186 so với 151.658 ký tự).
    - **Grounding hòa nhau (1,67):** A mất điểm vì bằng chứng bị cắt dở (q23, q24 chỉ được 1); B mất điểm vì trượt hẳn q19 (0).
  - **B → C (chỉ thêm filter chương):**
    - Với 12 câu dev và 4 câu test không có filter, B và C cho kết quả giống hệt nhau, vì cùng index và cùng truy vấn.
    - Với 6 câu dev có filter: Hit@3 tăng từ 4/6 lên 6/6 (q07, q08); Hit@1 tăng ở q09, q11 (từ hạng 2 lên hạng 1); tuân thủ filter tăng từ 2/6 lên 6/6.
      - Ví dụ q07 (chương 1, các bộ dữ liệu công khai): không lọc thì top-3 của B gồm hai chunk chương 3 về dữ liệu thực nghiệm (trang 66, 60) và một chunk khác của chương 1 (trang 27), còn chunk đúng (trang 30) rơi xuống hạng 4. Lọc theo chương 1 thì chunk đúng lên hạng 2.
    - Với 2 câu test có filter (q21, q22), B đã trúng ở hạng 1 nên Hit không đổi. C chỉ làm top-3 đúng phạm vi hơn (tuân thủ từ 1/2 lên 2/2).
      - Ví dụ q22: hạng 2 của B là bảng số đo độ trễ ở chương 3 (42 ms/request…), dễ bị nhầm với yêu cầu "≤ 2 giây" ở chương 2.
  - **Ví dụ sai 1: q19** (test; B và C trượt; gold `doan-bot-traffic-2026` trang 23, mục 1.5.1). Câu hỏi: "ưu tiên phát hiện bot hợp lệ hay bot độc hại?"
    - Câu trả lời nằm trong chunk B `p23:1304-2006` (702 ký tự, gồm định nghĩa hệ thống phát hiện bot và cả mục "Theo mục đích hoạt động"). Chunk này chỉ xếp hạng 12 (score 0,851, thấp hơn 0,860 của hạng 3).
    - Top-3 của B và C là bảng so sánh 3.23 (trang 79), phần Kết luận (trang 85) và nửa sau mục 1.5.1 (trang 23); không chunk nào nêu ý "tập trung vào bad bot".
    - A trúng ở hạng 1, vì cửa sổ 500 ký tự rơi gọn vào đoạn Good Bot/Bad Bot.
    - **Nguyên nhân:** chunk theo đoạn dài trộn nhiều ý, nên embedding không đại diện tốt cho một ý nhỏ.
  - **Ví dụ sai 2: q24** (test; cả ba trượt; gold trang 85, phần Kết luận).
    - Cả ba chiến lược đều xếp một chunk trang 66 (mục 3.8 sang 3.9) lên hạng 1. Chunk chứa câu gold chỉ xếp hạng 36 ở B và 40 ở A.
    - Với B và C, chunk hạng 1 nêu "Mặt hạn chế chính là dữ liệu thực nghiệm chưa đủ lớn và chưa phản ánh đầy đủ các kiểu bot tinh vi". Đây là cùng ý với câu gold, nên người chấm grounding cho B và C 2 điểm.
    - **Đây là lỗi của benchmark:** gold thay thế chỉ được dò bằng cách so khớp đúng chuỗi evidence, nên không bắt được cách diễn đạt khác của cùng một ý. Gold không được sửa sau khi xem test, nên Hit@3 vẫn tính là trượt.
  - Một ví dụ khác trên dev, q18 (A, trang 71): A lấy đúng trang gold ở hạng 1 và 3, nhưng câu evidence bị chia vào hai cửa sổ khác nhau.
- **Chiến lược chọn, đánh đổi, điều kiện áp dụng:**
  - **Chọn C** theo tiêu chí ghi trước (Hit@3 dev 17/18). Trên test, C bằng B về Hit@1 và Hit@3 (4/6), hơn A (Hit@1 2/6, Hit@3 3/6), và top-3 luôn đúng chương được lọc (2/2).
  - **Đánh đổi:**
    - C cần biết chương trước khi truy xuất, do người dùng hoặc ngữ cảnh cung cấp. Câu không nêu chương thì C chỉ bằng B.
    - Lọc theo chương loại luôn Mở đầu và Kết luận (chapter null).
    - Chunk dài của B và C giữ trọn ý (coherence 1,70) nhưng có thể bỏ lỡ chi tiết nhỏ nằm trong đoạn dài (q19).
  - **Điều kiện áp dụng:** tài liệu có cấu trúc chương/mục rõ, nhiều chủ đề lặp giữa các chương, và câu hỏi có nêu phạm vi. Kết luận chỉ đúng cho corpus này, không suy rộng.
- **Hạn chế của dữ liệu, nhãn, test nhỏ, đo thời gian/chi phí:**
  - **Dữ liệu:** chỉ có một tài liệu (69 trang) của một tác giả nên độ đa dạng thấp. Chỉ một trường lọc thực sự hữu ích (`chapter`, 3 giá trị).
  - **Nhãn:**
    - Benchmark, tiêu chí chọn và toàn bộ nhãn chấm tay đều do Claude soạn hoặc chấm nháp. Người chấm không ẩn danh chiến lược và đã biết C được chọn. Học viên chưa rà soát độc lập (xem mục cuối).
    - Hit@3 so khớp chuỗi một cách chặt chẽ, nên bằng chứng tương đương không được tính (A ở q21, cả ba ở q24).
  - **Test nhỏ:** chỉ 6 câu, mỗi câu tương đương 16,7 điểm phần trăm. Chênh lệch giữa dev (C 17/18) và test (4/6) phần lớn do 3/6 câu test là câu diễn đạt lại, nhóm mà cả ba chiến lược chỉ đạt 1/3.
  - **Đo đạc:** thời gian chỉ đo một lần trên laptop CPU và gồm cả thời gian embed câu hỏi. Chưa tính thời gian tải model lần đầu, vì model đã có sẵn trong cache.
  - **Lỗi phát hiện sau khi chạy test, giữ nguyên để test vẫn độc lập:**
    1. Chunker B gộp đuôi ngắn của mục trước (ở đầu trang) vào mục sau, nên ghi sai `section`. Ví dụ ở trang 38: chunk ghi 2.2.1 nhưng nội dung là 2.2.2.
    2. Heading xuống dòng làm `section_title` bị cắt (mục 1.1.2).
    3. A gán `section` theo điểm bắt đầu chunk, nên chunk vắt qua hai mục chỉ ghi mục đầu.
- **Nếu làm lại hoặc mở rộng, cải tiến gì?**
  - Sửa ba lỗi metadata ở trên. Cho đoạn văn nối qua trang (lưu `page_start`/`page_end`), để B không còn mảnh câu mồ côi ở đầu trang như ở vị trí coherence pos04 và pos10.
  - Thử truy xuất "small-to-big": embed các chunk nhỏ (câu hoặc đoạn ngắn) để bắt chi tiết như A làm được ở q19 và q16, nhưng trả về đoạn hoặc mục cha như B để giữ ngữ cảnh.
  - Thêm hybrid BM25 cho câu hỏi chứa số liệu hoặc tên endpoint (93,85%, `POST /v1/collect`), và thêm filter theo `section`.
  - Cải thiện benchmark:
    - Dùng embedding dò các đoạn diễn đạt khác của cùng một ý để ghi `alt_gold` trước khi chốt.
    - Tăng tập test lên ít nhất 15 câu.
    - Nhờ người khác chấm tay độc lập.

## Sử dụng AI

- **Công cụ:** Claude (Anthropic, Claude Opus 5.5) trong Claude Code desktop.
- **Phần việc của Claude:**
  - Viết code pipeline (`starter/src`, `starter/scripts`).
  - Soạn nháp 24 câu hỏi, tiêu chí chọn chiến lược và giả thuyết trong [experiment_log.csv](../../artifacts/experiment_log.csv).
  - Chấm nháp toàn bộ nhãn coherence, metadata, grounding.
  - Soạn báo cáo này từ kết quả chạy.
- **Cách kiểm tra:**
  - Mọi số liệu trong báo cáo lấy từ file kết quả do script sinh ra.
  - Evidence được kiểm tra bằng `src.benchmark` (24/24).
  - Metadata được đối chiếu bằng script với văn bản PDF và mục lục.
  - Việc nạp lại được kiểm tra bằng `scripts/check_reingest.py`.

## Việc học viên cần xác nhận trước khi nộp

- [ ] Đọc lại 24 câu hỏi, đáp án và tiêu chí chọn chiến lược (`benchmark/`).
- [ ] Rà soát nhãn trong `results/audit/*.csv`. Nếu sửa, chạy `python -m src.run audit` và cập nhật bảng kết quả thứ hai.
- [ ] Xác nhận mô tả quyền sử dụng corpus (`data/manifest.json`), và quyết định có công khai trace/chunk hay không, vì các file này chứa nguyên văn nội dung đồ án.
- [ ] Điền mã commit chốt bài vào `STUDENT.md`.
