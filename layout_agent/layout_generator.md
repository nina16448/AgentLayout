# Layout Generator 設計規格

---

## 職責說明

Layout Generator 是整個 pipeline 最核心的 LLM Agent。它的工作是根據 Design Spec 和背景分析結果，生成版面候選，每個候選包含所有元素的完整像素座標。

**Layout Generator 只負責決定「每個元素要放在哪裡、多大」，不分析素材、不驗證規則、不評審美感。**

---

## 呼叫時機

Layout Generator 有三種被呼叫的情況：

### 情況一：第一次執行（cold-start generation）
Pipeline 正常啟動，第一次生成版面候選。此時 `prev_best_layout` 為空，Layout Generator 從零產出 5 個 distinctly different 候選。

### 情況二：Quality Checker 否決後補足
Quality Checker 驗證後合格候選數量不足 K_valid，動態補足至達到目標數量。同情況一的 prompt（不帶 prev_best_layout），不需把失敗的 candidate 傳回去。

### 情況三：Refinement Loop（mandatory，每次 Judge 評分後一定觸發）
Aesthetic Judge 評審完後（**無論 accept 或 reject 都會走這條路**），系統把上一輪的最佳 candidate 之 bbox + Judge 子分數 + feedback 一併傳回 Layout Generator，Layout Generator 對 `prev_best_layout` 做 **targeted refinement**（不是 from-scratch 重生），只動 feedback 點到的元素，其他元素保留在 ±10% 微調範圍內。

> Refinement Loop 是 SEGA (ICCV 2025) coarse-to-fine 範式的本系統實作：Round 0 是 coarse（無 critique 的盲跑），Round 1+ 是 fine（看 critique 的 anchored search）。Refinement Loop 比舊 reject-only loop 的優點：(a) 每樣本至少跑一次 refinement，避免 cold-start 第一輪 layout 直接被當最終答案；(b) Generator 看得到上一輪 bbox，可以對 Judge feedback 做 element-targeted 編輯，而不是憑 brief 重猜。

---

## 生成機制

### 目標
湊滿 **K_valid = 5 個**通過 Quality Checker 的合格候選。

### 動態補足流程

```
呼叫 Layout Generator，一次生成 5 個候選
    ↓
Quality Checker 逐一驗證
    ↓
通過 → 加入合格池
不通過 → 丟棄
    ↓
合格池未達 5 個 → 繼續呼叫 Layout Generator 補足
合格池達到 5 個 → 傳給 Aesthetic Judge
```

### 補足時的 prompt
Quality Checker 否決只是 LLM 的隨機失誤（hard_constraints 在 prompt 裡本來就已經說清楚了），補足時直接用同樣的 prompt 重新呼叫即可，不需要把失敗的版面傳回去。

### Refinement 時的 prompt
當 `prev_best_layout` 非空（情況三）時，PROMPT_TEMPLATE 會啟用 conditional 的 `# Previous Attempt` 區塊（見下方），讓 LLM 看到上一輪 bbox + 子分數 + feedback，並改以 **targeted edit** 的視角產出 5 個 refined candidates（仍滿足 5 candidate distinctness 要求，但都以 prev_best_layout 為錨點）。

---

## 輸入

| 輸入 | 說明 | 來源 |
|------|------|------|
| Design Spec JSON | 含所有元素的 semantic_type、importance、semantic_relevance、hard/soft constraints、style_keywords | Analyst  輸出 + Python 前處理填入 |
| Layout Tree | 元素之間的語意層級關係 | Asset Planner |
| safe_zones | 背景的可放置區域（bbox 清單） | Background Analyzer |
| dominant_palette | 背景主色清單 | Background Analyzer |
| recommended_text_color | 建議的文字顏色（依背景明暗度決定），Layout Generator 可覆蓋 | Background Analyzer |
| feedback | Aesthetic Judge 的改善建議（第一次執行時為空；ACCEPT/REJECT 兩情況皆有） | Aesthetic Judge |
| prev_best_layout | 上一輪 Judge 選中的 best candidate bbox 字典 `{element_id: [left, top, width, height]}`（第一次執行為空，第二輪起非空） | Aesthetic Judge → Iteration Router |
| prev_best_subscores | 上一輪 best candidate 的四維子分數 `{requirement_alignment, info_hierarchy, layout_balance, visual_coherence}`（0–25 各維度） | Aesthetic Judge |

---

## 輸出

一次輸出 5 個版面候選，每個候選包含所有元素的完整幾何資訊（Crello 格式）。文字元素額外包含視覺屬性，供後續渲染使用。

```json
{
  "candidates": [
    {
      "candidate_id": "cand_01",
      "elements": [
        {
          "id": "headline_1",
          "left": 100, "top": 200, "width": 880, "height": 180,
          "angle": 0, "z_index": 3,
          "font_family": "sans-serif",
          "font_size": 72,
          "font_weight": "bold",
          "color": "#1B3A6B",
          "text_align": "center"
        },
        {
          "id": "logo_1",
          "left": 900, "top": 40, "width": 120, "height": 120,
          "angle": 0, "z_index": 4
        },
        {
          "id": "bg_1",
          "left": 0, "top": 0, "width": 1080, "height": 1920,
          "angle": 0, "z_index": 1
        }
      ]
    },
    {
      "candidate_id": "cand_02",
      "elements": ["..."]
    }
  ]
}
```

> 圖片元素（`visual_type: image`）只需要幾何座標，不需要文字視覺屬性。
> 文字元素（`visual_type: text`）需要額外輸出 `font_family`、`font_size`、`font_weight`、`color`、`text_align`。

---

## PROMPT_TEMPLATE 結構

```
Role: You are a professional graphic layout designer.
Your goal is to arrange the given design elements on a canvas
by assigning precise pixel coordinates to each element.

# Context
Design Spec: {design_spec}
Safe zones: {safe_zones}
Dominant palette: {dominant_palette}
Recommended text color (default, override if needed): {recommended_text_color}
Feedback from previous round (if any): {feedback}

# Previous Attempt (only present when refinement loop is active)
Previous best candidate bbox dict:
{prev_best_layout}
Previous best subscores (0–25 each, total /100):
  requirement_alignment={req}  info_hierarchy={hier}
  layout_balance={bal}         visual_coherence={coh}
You are refining the previous attempt, NOT generating from scratch.
- Edit ONLY the elements that the feedback explicitly criticizes.
- Keep every other element's (left, top, width, height) within ±10% of its previous value.
- The 5 candidates should explore distinct refinement directions
  (e.g. adjust sizing / spacing / alignment / hierarchy), but all must remain
  anchored to the previous layout — do not relocate elements to entirely new regions.

# Layout Tree
{layout_tree}

Elements in the same branch are semantically related.
Elements closer to the leaves have lower visual importance.

# size reference (relative to canvas)
full-canvas ≈ 100%  |  hero: 60–90%  |  large: 30–60%
medium: 15–30%      |  small: 8–15%  |  caption: 3–8%

# Format example
{format_example}

# Instruction
ATTENTION: Output exactly 5 candidates, each containing ALL element IDs from the spec.
ATTENTION: All coordinates must satisfy:
           left >= 0, top >= 0,
           left + width <= canvas_width,
           top + height <= canvas_height.
ATTENTION: Strictly obey all hard_constraints.
ATTENTION: For text elements, also output font_family, font_size, font_weight, color, text_align.
ATTENTION: For image elements, output geometry only — no visual style fields needed.
ATTENTION: Each candidate must take a distinctly different compositional approach.
           Do not repeat similar layouts across candidates.
ATTENTION: If feedback is provided, adjust your layouts according to the
           specific suggestions. Do not ignore the feedback.
ATTENTION: If a "# Previous Attempt" block is present, you are in refinement mode.
           All 5 candidates must be ANCHORED to the previous best layout
           (±10% per-element drift unless the feedback explicitly demands
           a larger change for that element id). Reuse element ids verbatim.
Output carefully referenced "format example" in JSON format, nothing else.
```

---

### PROMPT_TEMPLATE 各段說明

**`Role:`**
告訴 LLM 它是一位版面設計師，工作是把元素排在畫布上並給出像素座標。這讓 LLM 從設計師的角度思考，而不是隨意產出數字。

**`# Context`**
把這次生成需要的所有資料餵進來：
- `{design_spec}`：完整的 Design Spec JSON，含元素清單、hard/soft constraints、style_keywords
- `{safe_zones}`：背景哪些區域可以放元素
- `{dominant_palette}`：背景主色，供 LLM 參考配色
- `{recommended_text_color}`：Background Analyzer 根據背景明暗度建議的文字顏色，Layout Generator 可在特殊情況下自行覆蓋
- `{feedback}`：Aesthetic Judge 上一輪的改善建議，第一次執行時為空（ACCEPT 也會帶 feedback 進來，讓 refinement 對「已通過」的版面再做小幅優化）

**`# Previous Attempt`（refinement loop 專用，conditional block）**
只在情況三（Refinement Loop）出現。把上一輪 Judge 選中的 best candidate 的 bbox 字典 + 四維子分數塞進來，並用三句 instruction 鎖定 Generator 的行為：
- **targeted edit**：只動 feedback 點到的元素，其他元素 ±10% 內微調，避免「整個 layout 重洗」造成 step 6 那種 72→70→69 漂移。
- **anchored search**：5 個 candidate 不能跳到完全不同的版面區域，必須以 prev_best_layout 為錨點探索不同 refinement 方向（調大小 / 間距 / 對齊 / 層級）。
- **stable ids**：強制沿用 spec.elements 既有 id，確保 Judge feedback 中提及的 element_id 在下一輪仍能找到對應 bbox（不能改 id 命名）。

> 設計考量：此 block 故意放在 `# Context` 之後、`# Layout Tree` 之前，讓 LLM 先看到「設計目標 + 既有 bbox」再看語意層級樹——避免 layout tree 規則被當作「砍掉重練」的指令。token 預算上整個 block ≤ 20 元素 × 約 30 tokens = 600 tokens，遠低於 step 8 失敗時加的 14 行 ATTENTION 的 attention budget 衝擊。

**`# Layout Tree`**
Asset Planner 產出的語意層級樹。兩句話說明樹的意義：
- 同一分支的元素語意相關
- 越靠近葉子的元素重要性越低

不給任何排版規定，LLM 自己決定怎麼運用這個資訊。

**`# size reference`**
給 LLM 一個尺寸對照表，讓他知道 importance 高的元素大概應該佔畫布多大比例，避免生成出來的座標比例怪異。

**`# Format example`**
完整的輸出 JSON 範例，是整個 Prompt 最重要的部分。LLM 照著範例輸出，格式才會穩定。

**`# Instruction` 的 ATTENTION 行**
- 一次輸出 5 個候選，每個候選包含所有元素
- 座標必須合法，不能超出畫布
- 嚴守 hard_constraints
- 5 個候選必須走不同的設計方向，不能重複相似的排版
- 如果有 feedback，必須根據建議調整，不能忽略
- **如果 `# Previous Attempt` block 存在（refinement 模式），所有 5 個 candidate 都必須錨定在 prev_best_layout（每元素 ±10% drift），feedback 明確要求大改的元素例外；element id 必須沿用，不可改名**

**最後一行**
直接輸出 JSON，不加任何說明文字，確保程式可以解析。

---

*最後更新：2026/04/23　討論範圍：Layout Generator 設計規格 v2（加入 Layout Tree 輸入）*