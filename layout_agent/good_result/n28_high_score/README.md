# N=28 high-score subset — best-case showcase

由 `select_high_score_subset.py` 從 1,902 個 Crello sample 中以 zero-LLM 4 層 cascade filter 出的 28 個 (1.5%) — 結構簡單 / canvas 幾何 / 短聚焦文字 / GT 保守構圖。**演算法選樣不是手工挑**，與 `../README.md` 的 2 組 paper-grade manual curation 屬不同性質。

## Step 69 aggregate（vs designer GT）

- **A 軸 5/6 勝**：alignment / overlay / underlay_loose / underlay_strict / readability 勝；occlusion 略輸 10%（見 `output/high_score_sega_n28.json`）
- **B 軸 COLE Smean 6.39**（27/28 ok；1 個 judge parse failure）vs N=100 fresh baseline 6.32（見 `output/high_score_n28_results.json`）
- **Floor 從 5.60 拉高到 6.00**：subset 沒有低分案例

## Per-sample 表（依 Smean 由高到低）

| 排序 | 主題 | id (8 char) | canvas | n_elem | Smean (COLE 5-axis) |
|---|---|---|---|---|---|
| 1 | Zombie power scary poster | `589dfb19` | 1190x1683 | 7 | 7.60 |
| 2 | Cute Parrot Bird Icon | `5dad9af2` | 500x500 | 4 | 7.40 |
| 3 | Education Program Students in Classroom | `5df39962` | 560x315 | 7 | 7.40 |
| 4 | Online Medical service | `5f4cfb34` | 1080x1920 | 5 | 7.40 |
| 5 | Spa Center Ad with Lotus Flower | `5da6dde0` | 500x500 | 4 | 6.40 |
| 6 | Research Center with Molecule Icon | `5da72151` | 500x500 | 4 | 6.40 |
| 7 | Park Locations Guide Bench Icon | `5dad77cb` | 500x500 | 4 | 6.40 |
| 8 | Medical Services with friendly Doctor | `5f4ce55d` | 1080x1920 | 4 | 6.40 |
| 9 | Clean up the planet annual event | `592d2055` | 1200x628 | 7 | 6.20 |
| 10 | Inspirational Quote Flying Bug in Blue | `592fd738` | 540x810 | 7 | 6.20 |
| 11 | Citation about how take a vacation | `59529b1d` | 1190x1683 | 5 | 6.20 |
| 12 | Happy people by Christmas Tree | `5aa915e0` | 560x315 | 6 | 6.20 |
| 13 | Handsome man wearing Suit and Watch | `5c178467` | 1080x1080 | 7 | 6.20 |
| 14 | Meal with greens and vegetables | `5c1e2af7` | 1080x1080 | 5 | 6.20 |
| 15 | Emergency Treatment Band Aid Cross | `5da719eb` | 500x500 | 7 | 6.20 |
| 16 | Gift Shop Ad with Branches with Flowers | `5da7332d` | 500x500 | 4 | 6.20 |
| 17 | Home Maintenance Services Ad with Geomet | `5da735de` | 500x500 | 4 | 6.20 |
| 18 | Parks And Recreations Icon with Leaves o | `5dad776a` | 500x500 | 5 | 6.20 |
| 19 | City Community with Torii Icon | `5dad78a8` | 500x500 | 5 | 6.20 |
| 20 | Investment Company Ad with Hand holding  | `5dc9369d` | 500x500 | 4 | 6.20 |
| 21 | Wellness concept with Woman keeping Bala | `5f02e4b0` | 1080x1920 | 5 | 6.20 |
| 22 | Customer Care services ad with High Five | `5f02e4b0` | 1080x1920 | 5 | 6.20 |
| 23 | Delicious glazed Donuts in box | `5f4f5e15` | 1080x1920 | 5 | 6.20 |
| 24 | None | `5fbf8a42` | 240x141 | 5 | 6.20 |
| 25 | Vacation Quote Man on Motorbike in Red | `59529ad2` | 540x810 | 5 | 6.00 |
| 26 | Old City Building Icon in Blue | `5dc93d43` | 500x500 | 6 | 6.00 |
| 27 | Clothes Sale with Stylish Girl in sungla | `5e7a3621` | 1080x1080 | 6 | 6.00 |
| 28 | Branding project overview | `5f1feb15` | 595x841 | 5 | N/A |

## 檔名規則

- `{topic_slug}_{sid8}_AL.png` — AgentLayout system 產出（cached step22 fresh）
- `{topic_slug}_{sid8}_GT.jpg` — Crello designer 原始設計（`crello_{sid}/ground_truth_preview.jpg`）

## Paper 用法建議

- **Figure: best-case grid**：挑 top-N（Smean 高的）GT|AL 並排，caption 「On the structurally-conditioned subset (4-layer a-priori filter, N=28/1902), AgentLayout matches or exceeds designer composition on 5/6 geometric axes」
- **誠實 disclaimer**：N=28 是 **conditioned subset**、不是 unbiased random。Main result 仍以 N=100 fresh 為準（A 軸 3 勝 3 輸、Smean 6.32）。

## 統計

- IDs 來源: `output/high_score_subset_ids.json` (28 個)
- 成功複製: 28 對（AL+GT 各 1 張）
- 缺檔跳過: 0 個

---
*最後更新：2026/06/15*
