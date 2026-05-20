# Aesthetic Judge 設計規格

---

## 職責說明

Aesthetic Judge 是整個 pipeline 的最後一個 LLM Agent。它的工作是對通過 Quality Checker 的所有候選版面進行美感評審，給出分數和評語，並在分數不達標時提供具體的改進建議。

**Aesthetic Judge 只負責「評審和建議」，不知道也不需要知道系統架構，不指定建議要給哪個 Agent。**

---

## 輸入

| 輸入 | 說明 | 來源 |
|------|------|------|
| candidate_images | 5 個通過 Quality Checker 後渲染好的版面圖片 | Python 渲染 |
| candidate_ids | 每張圖片對應的 candidate_id | Layout Generator |
| Design Spec JSON | 含元素清單、hard/soft constraints、style_keywords | Analyst |
| Layout Tree | 元素之間的語意層級關係 | Asset Planner |
| dominant_palette | 背景主色清單 | Background Analyzer |

> 渲染在 Quality Checker 之後進行，只有通過驗證的候選才會被渲染，避免浪費。

---

## 評分維度

總分 100 分，分四個維度，各 25 分。

| 維度 | 說明 |
|------|------|
| 需求符合度 | 版面是否達成使用者的設計目標，是否遵守 hard_constraints |
| 資訊層級清晰度 | 視覺重點是否清楚，閱讀順序是否自然，是否符合 Layout Tree 的層級關係 |
| 版面平衡 | 視覺重量分佈是否均衡，有無過度擁擠或空洞感 |
| 整體一致性 | 風格、配色是否與 style_keywords 和 dominant_palette 和諧 |

**達標門檻：總分 ≥ 80 分**

---

## 輸出

### 情況一：最高分 ≥ 80（達標，但仍輸出 refinement feedback）

```json
{
  "decision": "accept",
  "best_candidate_id": "cand_02",
  "evaluations": [
    {
      "candidate_id": "cand_01",
      "total": 74,
      "scores": {
        "requirement_alignment": 20,
        "info_hierarchy": 18,
        "layout_balance": 19,
        "visual_coherence": 17
      },
      "strengths": "標題位置清楚，Logo 固定右上角符合需求。",
      "weaknesses": "產品圖與標題距離過遠，語意關聯不明顯。"
    },
    {
      "candidate_id": "cand_02",
      "total": 85,
      "scores": {
        "requirement_alignment": 23,
        "info_hierarchy": 21,
        "layout_balance": 20,
        "visual_coherence": 21
      },
      "strengths": "整體留白充足，視覺層級清楚，配色與風格一致。",
      "weaknesses": "價格標籤稍微偏小，辨識度略低。"
    }
  ],
  "feedback": {
    "common_issues": "整體達標，但 price_1 視覺辨識度仍略低，可再強化。",
    "suggestions": [
      "微幅放大 price_1（約 +15% 寬高），維持其他元素 ±5% 內不動"
    ]
  }
}
```

> 注意：採用 Refinement Loop 架構後，ACCEPT 時 feedback **不再是 null**，而是輸出「small-step polish 建議」供下一輪 refinement 使用。Iteration Router 看到 ACCEPT 也會強制再跑一次 Layout Generator（帶 prev_best_layout + 這份 feedback），refinement 後再評一次，若仍 accept 或達到 max_total_rounds 才終止。

### 情況二：最高分 < 80（不達標）

```json
{
  "decision": "reject",
  "best_candidate_id": "cand_03",
  "evaluations": [
    {
      "candidate_id": "cand_03",
      "total": 71,
      "scores": {
        "requirement_alignment": 20,
        "info_hierarchy": 16,
        "layout_balance": 18,
        "visual_coherence": 17
      },
      "strengths": "配色與風格關鍵字一致，背景利用得當。",
      "weaknesses": "headline_1 尺寸過小，視覺上無法主導版面，與其重要性不符。"
    }
  ],
  "feedback": {
    "common_issues": "所有候選的 headline_1 視覺重量不足，無法清楚主導版面。product_img_1 與 headline_1 之間距離過大，語意關聯不明顯。",
    "suggestions": [
      "增大 headline_1 的尺寸，使其在視覺上明顯大於其他文字元素",
      "縮短 product_img_1 與 headline_1 之間的距離，讓兩者形成視覺群組",
      "考慮更大膽的留白設計，避免版面過於擁擠"
    ]
  }
}
```

---

## 系統層面的回饋路由（Refinement Loop 架構）

Aesthetic Judge 輸出通用的改進建議，不指定給誰。系統根據 `decision` + 輪數決定：

```
Aesthetic Judge 輸出 verdict（accept 或 reject）
    ↓
無論 decision 為何，都強制把 best_candidate 的 bbox 字典 + 子分數 + feedback
打包成 RetryPayload 送回 Layout Generator（Refinement Loop）
    ↓
第 1、2 輪 → Layout Generator 做 targeted refinement，再進 Judge
第 3 輪以上仍 reject → 改送 Analyst 重新規劃 Design Spec
    ↓
終止條件（任一觸發即停）：
  (a) Judge 連續兩輪 accept（accept → refine → 仍 accept）
  (b) iteration > max_total_rounds（預設 5）
```

> Aesthetic Judge 的建議對兩個 Agent 都有參考價值——Layout Generator 可以根據 prev_best_layout 做 targeted edit，Analyst 可以反思 importance、constraints 或 style_keywords 是否設定有誤。

> 為甚麼 ACCEPT 也要再 refine 一次：cold-start 第一輪 LayoutGenerator 沒看過任何 Judge critique，是「盲跑」結果，即使分數 ≥ 80 仍是 unrefined output；強制一輪 refinement 讓 best candidate 至少經歷一次 critique-aware 編輯，與 SEGA (ICCV 2025) coarse-to-fine 範式對齊。終止條件 (a) 確保 refinement 真有效（兩次都 accept 才停），避免 over-polishing 把分數越改越低（step 6 教訓：72→70→69 漂移）。

---

## PROMPT_TEMPLATE 結構

```
Role: You are a senior graphic designer and aesthetic evaluator.
Your goal is to evaluate each layout candidate and provide scores,
strengths, weaknesses, and actionable improvement suggestions.

# Context
Design Spec: {design_spec}
Layout Tree: {layout_tree}
Dominant palette: {dominant_palette}
Candidate images: {candidate_images}
(Each image is a rendered version of a layout candidate. Candidate IDs: {candidate_ids})

# Scoring rubric (each dimension 0–25, total 100)
A. requirement_alignment (0–25)
   Does the layout fulfill the user's design goals and hard_constraints?

B. info_hierarchy (0–25)
   Is the visual focus clear? Is the reading order natural?
   Do elements follow the importance hierarchy in the Layout Tree?

C. layout_balance (0–25)
   Is visual weight distributed evenly?
   No excessive crowding or empty space?

D. visual_coherence (0–25)
   Do the style, spacing, and colors align with style_keywords and dominant_palette?

# Format example
{format_example}

# Instruction
ATTENTION: Evaluate ALL candidates. Do not skip any.
ATTENTION: strengths and weaknesses must reference specific element IDs.
ATTENTION: feedback must always be present (NEVER null) — even when decision is "accept".
           If decision is "reject", suggestions list concrete fixes for the failing dimensions.
           If decision is "accept", suggestions list small-step polish ideas
           (e.g. "+15% on price_1", "tighten spacing between headline_1 and product_img_1").
ATTENTION: feedback.suggestions must be specific and actionable —
           reference element IDs, mention which dimension is being polished/fixed,
           and suggest concrete drift directions (size/position/spacing).
ATTENTION: best_candidate_id must be the candidate with the highest total score.
Output carefully referenced "format example" in JSON format, nothing else.
```

---

### PROMPT_TEMPLATE 各段說明

**`Role:`**
告訴 LLM 它是一位資深設計師和美感評審，工作是評分、寫評語、給建議。強調「可操作的改進建議」，讓 LLM 不要只說模糊的感覺。

**`# Context`**
把評審需要的所有資料餵進來：
- `{candidate_images}`：5 張渲染好的版面圖片，Aesthetic Judge 直接看圖評分
- `{candidate_ids}`：每張圖片對應的 candidate_id，用於輸出時對應
- `{design_spec}`：用來評估需求符合度和 hard_constraints 是否遵守
- `{layout_tree}`：用來評估資訊層級是否符合語意關係
- `{dominant_palette}`：用來評估配色是否和諧

**`# Scoring rubric`**
四個評分維度的詳細說明，讓 LLM 知道每個維度在評什麼，避免評分標準模糊。

**`# Format example`**
完整的輸出 JSON 範例，包含達標和不達標兩種情況，讓 LLM 照著輸出正確格式。

**`# Instruction` 的 ATTENTION 行**
- 所有候選都要評，不能跳過
- 優點缺點必須指出具體元素 id
- **feedback 在 accept / reject 兩種情況都必須有**（不可為 null）：
  - reject：suggestions 列具體修補方向（指元素 id + 失分維度 + 改善方向）
  - accept：suggestions 列 small-step polish 建議（如 `price_1 +15%`、`tighten spacing`）
- best_candidate_id 必須是分數最高的那個

**最後一行**
直接輸出 JSON，不加說明文字。

---

*最後更新：2026/04/23　討論範圍：Aesthetic Judge 設計規格 v1*