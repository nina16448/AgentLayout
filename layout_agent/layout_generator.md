# Layout Generator 設計規格

---

## 職責說明

Layout Generator 是整個 pipeline 最核心的 LLM Agent。它的工作是根據 Design Spec 和背景分析結果，生成版面候選，每個候選包含所有元素的完整像素座標。

**Layout Generator 只負責決定「每個元素要放在哪裡、多大」，不分析素材、不驗證規則、不評審美感。**

---

## 呼叫時機

Layout Generator 有三種被呼叫的情況：

### 情況一：第一次執行
Pipeline 正常啟動，第一次生成版面候選。

### 情況二：Quality Checker 否決後補足
Quality Checker 驗證後合格候選數量不足 K_valid，動態補足至達到目標數量。

### 情況三：Aesthetic Judge 回饋後重新執行
Aesthetic Judge 評審後覺得所有候選都不夠好，把改善建議傳回來，Layout Generator 根據建議重新生成。

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

---

## 輸入

| 輸入 | 說明 | 來源 |
|------|------|------|
| Design Spec JSON | 含所有元素的 semantic_type、importance、semantic_relevance、hard/soft constraints、style_keywords | Analyst  輸出 + Python 前處理填入 |
| Layout Tree | 元素之間的語意層級關係 | Asset Planner |
| safe_zones | 背景的可放置區域（bbox 清單） | Background Analyzer |
| dominant_palette | 背景主色清單 | Background Analyzer |
| recommended_text_color | 建議的文字顏色（依背景明暗度決定），Layout Generator 可覆蓋 | Background Analyzer |
| feedback | Aesthetic Judge 的改善建議（第一次執行時為空） | Aesthetic Judge |

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
- `{feedback}`：Aesthetic Judge 上一輪的改善建議，第一次執行時為空

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

**最後一行**
直接輸出 JSON，不加任何說明文字，確保程式可以解析。

---

*最後更新：2026/04/23　討論範圍：Layout Generator 設計規格 v2（加入 Layout Tree 輸入）*