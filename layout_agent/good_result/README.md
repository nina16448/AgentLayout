# Good results — paper-grade showcase

精選的 AgentLayout 視覺成功案例。每個 sample 含兩張圖：

- `<name>_<id>_GT.jpg` — Crello designer 原始設計（reference）
- `<name>_<id>_AL.png` — AgentLayout 系統產出（同 brief / 同 assets）

選擇標準：**人工視覺檢查**判定為與 designer GT 大致對等或可比的構圖。
不是「pairwise judge 通過」的 14% 全部，而是進一步人工 filter 後的 paper-figure-grade。

## 樣本詳細（2026-06-11 人工複篩後保留 2 組）

| 檔名前綴 | Crello id (8 char) | 主題 | 觀察 |
|---|---|---|---|
| `pet_grooming` | `59535be5` | "Grand Sale of Pet Grooming Supplies" | 紫底橫幅、貓咪插圖。AL 替代構圖：title 在頂部置中 + cats 在中央（vs GT 的 title 在左 + cats 在右）、視覺合理 |
| `join_volunteering` | `5888cded` | "Join Volunteering Now! Let's Fight Against Cruelty To Animals Together!" | Step 43 LTRB-fix 之後新出的 paper-grade 樣本。藍底 + 紅 panel + 白色 T-shape 構圖跟 GT 同概念；AL 紅 panel 置中（vs GT 靠左）、文字置中對齊（vs GT 左對齊）、屬替代構圖 |

> 歷史紀錄：本資料夾曾收 8 組（quarantine_airhead、art_being_creative v1/v2、
> nurse_stay_at_work、miriadas_university、silk_linen 等），2026-06-11 二次人工
> 視覺複篩後剔除 6 組、僅保留上表 2 組最高標準樣本。被剔除的圖檔可從 git 歷史
> （commit `1d454e70` / `a2fa3c43`）取回。

## Paper 用法建議

- **Figure: "AgentLayout-vs-Designer comparison"**：用 2-3 個 GT|AL 並排格、ARTICLE 文中 caption「AgentLayout matches designer composition on these representative samples」
- **重要 disclaimer**：這 2 個是**人工挑出的 paper-grade 樣本**、不代表 system 平均水準。N=100 Step 37 strict pairwise = **2% AL wins**；N=20 calibrated Smean AL=3.73 vs GT=4.75。整體 success rate 數字寫在 paper main result。

## 來源

從以下三批 N=100/N=20 runs 撈：
- N=20 paper-draw post-Step36c (2026-06-09)
- N=100 step22_n100_ids post-Step36c
- N=100 post-Step37 (strict + Tier 1) — 58b4 是 Step 37 才浮出的新成功案例

每個樣本的 trace + 評分細節見 `IMPLEMENTATION_LOG.md` Step 36c / 37 章節。

---
*最後更新：2026/06/11*
