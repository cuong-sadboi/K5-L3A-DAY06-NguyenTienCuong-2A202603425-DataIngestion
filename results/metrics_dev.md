# Kết quả truy xuất: dev

| Chiến lược | Index | Hit@1 | Evidence Hit@3 | Trang gold trong top-3 | Evidence bị tách trong index | Filter tuân thủ | Độ trễ TB |
|---|---|---|---|---|---|---|---|
| A | A | 8/18 | 12/18 | 16/18 | 3 | 2/6 | 62.2 ms |
| B | B | 9/18 | 15/18 | 16/18 | 0 | 2/6 | 71.7 ms |
| C | B | 11/18 | 17/18 | 17/18 | 0 | 6/6 | 61.6 ms |

## Từng câu (hạng của chunk hit đầu tiên; '-' là không có hit trong top-3)

| Câu | Loại | Filter | Trang gold | A | B | C |
|---|---|---|---|---|---|---|
| q01 | truc_tiep |  | 15 | 1 | 1 | 1 |
| q02 | dien_dat_lai |  | 15 | 1 | 1 | 1 |
| q03 | dien_dat_lai |  | 18 | 1 | 1 | 1 |
| q04 | truc_tiep |  | 24 | 2 | 3 | 3 |
| q05 | truc_tiep |  | 27 | - | 1 | 1 |
| q06 | dien_dat_lai |  | 30 | 1 | 2 | 2 |
| q07 | truc_tiep | {'chapter': 1} | 30 | - | - | 2 |
| q08 | truc_tiep | {'chapter': 3} | 60 | - | - | 3 |
| q09 | dien_dat_lai | {'chapter': 1} | 31 | 2 | 2 | 1 |
| q10 | truc_tiep | {'chapter': 2} | 49 | 1 | 1 | 1 |
| q11 | truc_tiep | {'chapter': 2} | 35 | 2 | 2 | 1 |
| q12 | truc_tiep |  | 47 | - | 1 | 1 |
| q13 | dieu_kien |  | 53 | - | 1 | 1 |
| q14 | truc_tiep |  | 54 | 2 | 2 | 2 |
| q15 | dien_dat_lai |  | 55, 54 | 1 | 1 | 1 |
| q16 | dien_dat_lai |  | 57, 49 | 1 | - | - |
| q17 | dieu_kien | {'chapter': 1} | 32 | 1 | 2 | 2 |
| q18 | dieu_kien |  | 71 | - | 1 | 1 |
