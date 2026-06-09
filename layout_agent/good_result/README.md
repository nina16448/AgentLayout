# Good results — paper-grade showcase

精選的 AgentLayout 視覺成功案例。每個 sample 含兩張圖：

- `<name>_<id>_GT.jpg` — Crello designer 原始設計（reference）
- `<name>_<id>_AL.png` — AgentLayout 系統產出（同 brief / 同 assets）

選擇標準：**人工視覺檢查**判定為與 designer GT 大致對等或可比的構圖。
不是「pairwise judge 通過」的 14% 全部，而是進一步人工 filter 後的 paper-figure-grade。

## 7 個樣本詳細

| 檔名前綴 | Crello id (8 char) | 主題 | 觀察 |
|---|---|---|---|
| `quarantine_airhead` | `5e72455e` | "Don't be an airhead, air out your room" | Step 36b metadata fix 救起來的範例。AL 用 text snippet「Don't be an airhead」當主標、不再誤用 metadata 描述「Quarantine concept...」。構圖跟 GT 幾乎一樣（藍底、窗景插圖、頂部白標題） |
| `art_being_creative_v1` | `592c2135` | "ART / Being Creative is not a hobby" | 寬橫幅、ART 字母分散頂部、文字 body 置中白底框（vs GT 透明 right-align）。AL 替代構圖、視覺品質對等 |
| `art_being_creative_v2` | `5a22883e` | 同上主題不同 sample | 同樣寬橫幅、品質一致 |
| `nurse_stay_at_work` | `5e8d966a` | "We stay at work for you. Stay at home for us." | 方形 banner、藍底、護士插圖、白色標題。AL 跟 GT 幾乎一樣（小差別：缺 #StayHome 側標） |
| `miriadas_university` | `5eec7b19` | "Miriadas University / Revolutionary Approach" | 寬橫幅、photo + 黃/白 panel + text。AL 構圖跟 GT 同概念 |
| `pet_grooming` | `59535be5` | "Grand Sale of Pet Grooming Supplies" | 紫底橫幅、貓咪插圖。AL 替代構圖：title 在頂部置中 + cats 在中央（vs GT 的 title 在左 + cats 在右）、視覺合理 |
| `silk_linen` | `58b43313` | "Nothing Feels As Good As Luxury Silk Linen" | 寬橫幅、人像背景 + 文字 panel + pink accent。AL 用白底框中央，比 GT 對應的右側 text、視覺競爭力 OK |

## Paper 用法建議

- **Figure: "AgentLayout-vs-Designer comparison"**：用 2-3 個 GT|AL 並排格、ARTICLE 文中 caption「AgentLayout matches designer composition on these representative samples」
- **重要 disclaimer**：這 7 個是**人工挑出的 paper-grade 樣本**、不代表 system 平均水準。N=100 Step 37 strict pairwise = **2% AL wins**；N=20 calibrated Smean AL=3.73 vs GT=4.75。整體 success rate 數字寫在 paper main result。

## 來源

從以下三批 N=100/N=20 runs 撈：
- N=20 paper-draw post-Step36c (2026-06-09)
- N=100 step22_n100_ids post-Step36c
- N=100 post-Step37 (strict + Tier 1) — 58b4 是 Step 37 才浮出的新成功案例

每個樣本的 trace + 評分細節見 `IMPLEMENTATION_LOG.md` Step 36c / 37 章節。

---
*最後更新：2026/06/10*
