# Kết quả truy xuất: test

| Chiến lược | Index | Hit@1 | Evidence Hit@3 | Trang gold trong top-3 | Evidence bị tách trong index | Filter tuân thủ | Độ trễ TB |
|---|---|---|---|---|---|---|---|
| A | A | 2/6 | 3/6 | 5/6 | 1 | 0/2 | 66.5 ms |
| B | B | 4/6 | 4/6 | 5/6 | 0 | 1/2 | 68.8 ms |
| C | B | 4/6 | 4/6 | 5/6 | 0 | 2/2 | 72.7 ms |

## Từng câu (hạng của chunk hit đầu tiên; '-' là không có hit trong top-3)

| Câu | Loại | Filter | Trang gold | A | B | C |
|---|---|---|---|---|---|---|
| q19 | dien_dat_lai |  | 23 | 1 | - | - |
| q20 | dieu_kien |  | 64 | 3 | 1 | 1 |
| q21 | truc_tiep | {'chapter': 3} | 75, 77 | - | 1 | 1 |
| q22 | truc_tiep | {'chapter': 2} | 38 | 1 | 1 | 1 |
| q23 | dien_dat_lai |  | 77 | - | 1 | 1 |
| q24 | dien_dat_lai |  | 85 | - | - | - |
