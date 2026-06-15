# CGL-Dataset V2 可行性評估（scoping memo，不含實作）

**日期**：2026-06-13　**性質**：回應「能不能跑 CGL-V2」。**本版基於實際查證 CGL-Dataset V2 的 HF loader schema 與 RADM 論文 task setup 重寫**（第一版誤把 CGL 當與 PKU 同構，已作廢）。
**前置決策**：2026-06-04「不跑 PKU」、2026-06-13「Generator-bounded 探索線結案」。本 memo **不擅自推翻**這些決策，但指出 CGL-V2 與 PKU 的可行性結構**不同**，值得單獨拍板。
**關聯**：[[project-no-pku-postero-alignment]]、[[feedback-underlay-is-placement]]、[[project-generator-bounded-line-closed]]；對照 `PKU_FEASIBILITY.md`、`METRIC_ALIGNMENT_AUDIT.md`。

---

## 0. TL;DR（與第一版相反的修正結論）

- **CGL-V2 不需要自生資料**，與 PKU **不同構**。資料集本身提供：每個元素的 **類別 + bbox + 實際文字內容**（`user_selected_value` / `adv_sellpoint` 等），外加（V2 招牌）**inpainted 乾淨底圖**。這讓它**比 PKU 更接近 Crello**——AgentLayout 的 content-aware **placement 範式可直接對接**，不是「固定模板假裝 generative」。
- **能跑、且是真實任務**：採 **category-conditioned placement**（給定 GT 元素類別＋文字內容，預測 bbox），AgentLayout 對 `text / logo / highlighted text` 三類可正常擺位，對 GT 算 Ali/Ove/Rea/Occ/mIoU。
- **中文不是障礙**：CGL-V2 是中文電商海報，但 `renderer.py:134` 已有 `CJK_FONT_CANDIDATES`（NotoSansCJK），本機 `/usr/share/fonts/opentype/noto/NotoSansCJK-*.ttc` 已安裝、`fc-list` 確認可用。
- **真實限制縮小為三點**（非「任務是假的」）：① 裝飾兩類 `underlay(衬底)` / `embellishment(符号元素)` schema 不生 → Und 吃虧；② inpainted clean bg 的取得來源須複驗；③ logo 無圖片 asset，content-aware Judge 渲染時為空 box。

---

## 1. CGL-V2 任務本質（查證後）

**資料集提供（HF `creative-graphic-design/CGL-Dataset-v2` loader 實際 features）**：
- `image`（海報圖）、`width/height`、`file_name`。
- per-element：`bbox`（Sequence int64）、`category_id`、`category.name`（ClassLabel）、`area`、`segmentation`。
- **類別 5 種**：`rename=True` → `logo / text / underlay / embellishment / highlighted text`；原始中文 → `Logo / 文字 / 衬底 / 符号元素 / 强调突出子部分文字`。
- **text content（V2 新增、關鍵）**：`data[].user_selected_value`（實際文字）、`adv_sellpoint`（廣告賣點）、`product_detail_highlighted_word`、`blc_text`；另含預抽 RoBERTa `feats`。
- **無 per-element 圖片 asset**（logo 等只有 bbox+類別）。

**RADM（2306.09086）的 task setup**：輸入 = **clean background image + text content（促銷標語）**；輸出 = 各元素 **category + (cx,cy,w,h)**；元素數量/類別由文字隱含決定（partially generative）。inpainting 用來擦掉元素得乾淨底圖，train+test 皆做。

**對 AgentLayout 的意義**：
- AgentLayout 是 **placement**（吃已知元素的內容＋類別，擺位置）。CGL-V2 **正好提供文字內容與類別** → 可採 **category-conditioned** 設定（給 GT 類別＋文字，預測 bbox），這是 layout 文獻標準條件之一，**不是自生、不是假裝**。
- 與 Crello 的差異只在：CGL 元素的「視覺 asset」沒給（logo 無圖、文字未預渲染），但 AgentLayout 本就自己渲染文字；logo 在純幾何評估下只需 bbox。
- 與 PKU 的關鍵差異：**PKU 無 text content**（純靠 saliency 自生），CGL-V2 **有** → 可行性結構完全不同，第一版 memo 的「同構」判斷作廢。

---

## 2. 現況盤點：基礎設施齊備度（查證後）

| 元件 | 檔案 | CGL 用途 | 狀態 |
| --- | --- | --- | --- |
| 指標套件 | `evaluation/sega_metrics.py` | Ali/Ove/Und/Read/Occ；`CLS 1/2/3` 對齊 text/logo/underlay | ✅ A1–A7 對齊稽核已完成 |
| saliency | `evaluation/saliency_basnet_isnet.py` | Occ 用 BASNet+ISNet | ✅ 共用 |
| **中文渲染** | `tools/renderer.py:134 CJK_FONT_CANDIDATES` | NotoSansCJK fallback | ✅ 系統字型已裝、`fc-list` 確認 |
| placement 生成 | `actions/generate_layout.py` | 吃元素內容＋類別、emit bbox | ✅ 範式相容 |
| QC | `tools/quality_checker.py` | 界內/非退化/coverage | ✅ canvas-only |
| Analyst | `actions/analyze_brief.py` | 分析元素語意 | ⚠️ 須吃中文 text content（prompt 可能須微調，非重構） |
| **CGL loader** | — | 載入 image/bbox/類別/文字 | ❌ 須寫 |
| **inpainted clean bg** | — | 當 canvas | ⚠️ 來源須複驗（見 §7） |

---

## 3. 改動清單（真實 conditional placement，非假裝）

| # | 工作 | 做法 | 估行數 |
| --- | --- | --- | --- |
| ① | CGL loader | HF `creative-graphic-design/CGL-Dataset-v2`；取 image/bbox/category/text content；test=1,035 | ~60 |
| ② | clean background | 取 inpainted 底圖當 canvas（**來源須複驗**，可能須從 RADM repo 或自 inpaint） | ~20–60 |
| ③ | DesignSpec 組裝 | 用 GT 類別＋`user_selected_value` 文字建 typed 元素（`asset_ref=None`、content=真實中文）；canvas 指 clean bg | ~40 |
| ④ | 裝飾類 protocol | `underlay/embellishment` 兩類：category-conditioned 下選擇 **drop（缺席、明標）** 或併入 logo；`highlighted text` 併 text | ~15 |
| ⑤ | 輸出→指標 glue | `LayoutElement → (cls, xyxy)`、scale 到原生像素、餵 `sega_metrics`；CGL bbox 格式（xywh vs xyxy）**載入時須複驗** | ~30 |
| ⑥ | driver | 串 ①–⑤、聚合 mean；Judge 可開（有真文字可渲染）或先 BypassJudge | ~50 |

**合計約 ~220–280 行**。比 PKU 多了 inpainted bg 取得與裝飾類 protocol，但**不需 element-proposer**（GT 給文字與類別）。中文渲染零額外工作。

---

## 4. 結構性限制（查證後縮小）

1. **裝飾兩類缺席**：`underlay(衬底)` / `embellishment(符号元素)` AgentLayout schema 不生（[[feedback-underlay-is-placement]] / no-decoration 邊界）。category-conditioned 下若 GT 含這些類，候選缺席 → **Und_l/Und_s 吃虧**。但這只影響 5 類中的 2 類，`text/logo/highlighted text` 正常 → 比 PKU「三類全靠自生」輕。
2. **logo 無圖片 asset**：純幾何評估（Ali/Ove/Rea/Occ/mIoU）不受影響；若要過 **content-aware Judge** 渲染成品，logo 為空 box，美學評分會失真 → Judge 可選擇關（純幾何）或接受 logo placeholder。
3. **protocol 對齊**：RADM 是 partially generative（預測類別＋數量）；AgentLayout category-conditioned（給類別預測位置）**protocol 不同** → 與 RADM 數字不可直接同表，須標明條件差異（同 SEGA 跨-paper caveat）。
4. **Judge 訊號**：有真文字可渲染 → content-aware Judge **可上場**（不像 PKU 完全空 box），這是 CGL-V2 相對 PKU 的實質優勢；但 logo/裝飾缺視覺會稀釋訊號。

---

## 5. 數字怎麼定位（誠實 framing）

- category-conditioned placement 的 Ali/Ove/Rea/Occ/mIoU 可寫成 **AgentLayout 在 CGL-V2 文字/logo 子任務的 content-aware placement 表現**；Und 兩軸的吃虧須明標為「schema 不生裝飾」的 scope 邊界、非 bug。
- 與 RADM/DS-GAN 排行榜**不可直接同表**（protocol 不同 + 裝飾類缺席 + underlay labeling/judge drift），須加 caveat。
- 可寫的正向 framing：CGL-V2 證明 AgentLayout 的 placement 範式**可跨資料集遷移到中文電商海報的文字/logo 構圖**，而非只在 Crello。

---

## 6. 路徑成本一覽（供拍板）

| 路徑 | 工程 | 產出 | 可宣稱 |
| --- | --- | --- | --- |
| **A. 文字/logo placement（推薦的最小忠實版）** | ~220–280 行、零核心重構 | text/logo/highlighted 三類的 Ali/Ove/Rea/Occ/mIoU（Und 吃虧、明標） | content-aware placement 跨資料集遷移；非與 RADM 同表 |
| **B. 含裝飾的忠實 generative** | A ＋ underlay/embellishment 合成（**超出 schema scope、大改**） | 接近 CGL 完整任務 | 裝飾合成屬另一研究問題；回到 2026-06-04 架構抉擇 |
| **C. 不跑** | 0 | 維持現決策 | depth×narrow 定位 |

**與 PKU 的關鍵差別**：PKU 的「最小可跑版」必須靠固定模板假裝 generative，數字只能算 indicative；**CGL-V2 的路徑 A 是真實的 conditional placement**（文字與類別來自 GT），數字有實質意義。這把「值不值得跑」的天平往 A 推了一截——但仍與 [[project-generator-bounded-line-closed]] 的收斂決策有張力（Judge 構圖差距不會因換資料集消失），是否啟動須使用者拍板。

---

## 7. 已查證 / 待複驗清單

**已查證（本 session web + repo 親驗）**：
- HF loader features：`image` / per-element `bbox`+`category`+`area`+`segmentation` / text `data[].user_selected_value`+`adv_sellpoint`+RoBERTa `feats`。
- 類別 5 種：`logo/text/underlay/embellishment/highlighted text`（中文 `Logo/文字/衬底/符号元素/强调突出子部分文字`）。
- split：train 60,548 / **test 1,035**。
- RADM 座標：`(cx,cy,w,h)`（center 格式）；輸入 clean bg + 促銷標語。
- 中文字型：renderer CJK fallback + 系統 NotoSansCJK 已裝。

**待複驗（動工前務必確認）**：
- `image` 欄是**原圖還是 inpainted clean bg**？若為原圖，clean bg 須從 RADM repo (`github.com/liuan0803/RADM`) 取或自行 inpaint。
- loader `bbox` 的精確格式（xywh / xyxy / 是否 normalised）與原生解析度。
- 取資料是否需額外授權 / 阿里來源條款。
- Analyst/Generator prompt 對中文電商賣點的適配（預期 prompt 微調，非重構）。
