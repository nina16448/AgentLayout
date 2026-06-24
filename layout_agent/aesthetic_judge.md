# Aesthetic Judge 設計規格

---

## 職責說明

Aesthetic Judge 是整個 pipeline 的最後一個 LLM Agent，也是唯一使用 **multimodal vision LLM** 的 Agent。它的工作是把每個候選版面渲染成 PNG 圖片，讓 LLM 直接看圖評分，給出 COLE 5 軸分數、優缺點評語，並提供 structured suggestions（機器可讀的改善建議）。

**Aesthetic Judge 只負責「評審和建議」，不知道也不需要知道系統架構，不指定建議要給哪個 Agent。**

回饋路由由下游的 IterationStateRole 負責（見本文件最後一章）。

---

## 實作架構

Aesthetic Judge 跨兩個 Role + 一個 Action：

```
AestheticJudgeRole (roles/aesthetic_judge.py, 97 行)
  ├── watches: GenerateLayout (接收 Generator 輸出的 CandidatesBatch)
  ├── _find_by_cause() → 從 env history 回溯取 DesignSpec 和 LayoutTree
  ├── resolve_background(spec.canvas) → CV 模組取背景分析
  └── action: JudgeAesthetic   (actions/judge_aesthetic.py, 538 行)
                ├── _render_images() — 每個候選渲染為 PNG + base64
                ├── _build_prompt() — 組裝 PROMPT_TEMPLATE（6 個變數替換）
                ├── LLM 呼叫（vision channel：候選渲染圖）
                ├── _parse_response() — CodeParser + model_validate_json
                ├── _validate_against_input() — 語意交叉驗證
                └── _attach_best_candidate_layout() — 提取 best candidate 的 bbox 字典

IterationStateRole (roles/iteration_state.py, 317 行)
  ├── watches: JudgeAesthetic
  ├── 純路由，不呼叫 LLM（set_actions([])）
  ├── 管理 IterationState 狀態機
  └── 路由：RetryGeneration / RetryAnalyst / IterationStop（sentinel Actions）
```

### 相關檔案

| 層級 | 檔案 | 行數 | 說明 |
|------|------|------|------|
| Role | `roles/aesthetic_judge.py` | 97 | MetaGPT Role 殼：env history 回溯、resolve_background、呼叫 JudgeAesthetic |
| Action | `actions/judge_aesthetic.py` | 538 | Prompt 模板 + vision LLM 呼叫 + 渲染 + 兩層驗證 + 重試邏輯 |
| Role | `roles/iteration_state.py` | 317 | 回饋路由器：IterationState 狀態機 + best-so-far guard + sentinel Actions |

---

## 呼叫時機

AestheticJudgeRole 的 `_watch([GenerateLayout])` 只有一個觸發點：收到 GenerateLayout Action 的 Message。

`_act()` 流程：
1. 從 `rc.news[-1].instruct_content` 取得 `CandidatesBatch`（驗證型別）
2. 從 env history 回溯取得 `DesignSpec`（透過 `_find_by_cause(AnalyzeBrief, DesignSpec)`）
3. 從 env history 回溯取得 `LayoutTree`（透過 `_find_by_cause(PlanAssets, LayoutTree)`）
4. 呼叫 `resolve_background(spec.canvas)` 取得 `BackgroundAnalysis`
5. 呼叫 `JudgeAesthetic.run(candidates, spec, tree, bg)` 取得 `AestheticJudgement`
6. 發出 Message（`instruct_content=judgement`, `cause_by=JudgeAesthetic`）

> `_find_by_cause` 走 `self.rc.env.history`（env-wide history），不是 `self.rc.history`（role-local history），因為 DesignSpec 和 LayoutTree 的 Message 是由其他 Role 發出的，Aesthetic Judge 的 `_watch` 清單沒有 subscribe 這些 cause。

---

## 輸入

| 輸入 | 類型 | 說明 | 來源 |
|------|------|------|------|
| `candidates` | `List[Candidate]` | 通過 QC 的候選版面（通常 K_VALID=5 個） | CandidatesBatch.candidates |
| `spec` | `DesignSpec` | 含元素清單、hard/soft constraints、style_keywords | env history 回溯（AnalyzeBrief） |
| `tree` | `LayoutTree` | 元素之間的語意層級關係 | env history 回溯（PlanAssets） |
| `bg` | `BackgroundAnalysis` | dominant_palette（用於 prompt 中的配色判斷） | resolve_background(spec.canvas) |

**前置條件：** `JudgeAesthetic.run()` 進入後立即呼叫 `spec.assert_enriched()`，確認 AssetAnalyzer 已跑過。

### Vision Channel

JudgeAesthetic 是 pipeline 中唯一**看圖評分**的 Agent。每個候選在送入 LLM 前都會：
1. 呼叫 `render(candidate, spec)` 渲染為 PIL Image
2. 呼叫 `image_to_base64(img)` 轉成 base64 PNG 字串
3. 按照與 `candidates` 相同的順序作為 `images` 參數傳入 `llm.aask(prompt, images=images)`

如果 LLM 不支援圖片輸入（`self.llm.support_image_input()` 返回 False），會 log warning 並退化為 text-only 模式（images 參數被靜默忽略，評分只靠文字上下文）。

---

## 評分維度（COLE 5 軸）

### 制度說明

採用 **COLE 5-axis** 評分標準（Step 30 遷移，2026-06-09）。5 個維度各 1-10 分，總分 5-50。

與原規劃的差異：
- 原規劃：4 維 × 0-25 = 100 分制
- 實作：5 維 × 1-10 = 50 分制，與離線 Phase B COLE eval（`step21_phaseb_eval.py`）對齊

### 分數錨點（Grading Anchors）

| 分數 | 語意 |
|------|------|
| 10 | Flawless on this axis |
| 7 | Mediocre / acceptable |
| 4 | Clear shortcomings |
| 1-2 | Severely poor |

這些錨點適用於**所有 5 個軸**。

### 5 個評分維度

| 軸向 | 欄位名 | 說明 |
|------|--------|------|
| A | `design_layout` | 版面是否乾淨、平衡、一致？元素組織是否建立清晰的視線路徑？是否遵循 Layout Tree 的 importance 層級？ |
| B | `content_relevance` | 版面是否達成 brief 的設計目標和 hard_constraints？是否與目標受眾產生共鳴？（吸收了舊版 `requirement_alignment` 的語意） |
| C | `typography_color` | 字型選擇、大小、行距、顏色、放置位置和整體色彩方案是否協同增強可讀性？是否與 `style_keywords` 和 `dominant_palette` 和諧？ |
| D | `graphics_images` | 圖像是否增強設計而非干擾？品質、相關性和其他元素是否和諧？如果只有 placeholder 框，按放置/大小品質保守給分（5-7） |
| E | `innovation_originality` | 設計是否展現原創性？是否超越跟風的泛用設計？ |

### 達標門檻

**ACCEPT_THRESHOLD = 35**（定義在 `schema.py`）

- 35 = 5 × 7，即每軸平均 7/10（COLE 的 "mediocre design" 錨點）
- 校準歷史：80（4×25 制）→ 75（GT 校準下調）→ 35（Step 30 COLE 遷移，保持 0.75 acceptance ratio）
- best candidate 的 total ≥ 35 → `decision = "accept"`
- best candidate 的 total < 35 → `decision = "reject"`

---

## 輸出

### Schema 定義（schema.py）

```python
class JudgeScores(BaseModel):
    design_layout: int        # 1-10
    content_relevance: int    # 1-10
    typography_color: int     # 1-10
    graphics_images: int      # 1-10
    innovation_originality: int  # 1-10

class Evaluation(BaseModel):
    candidate_id: str
    total: int                # 5-50, model_validator 強制 = sum(scores)
    scores: JudgeScores
    strengths: str
    weaknesses: str

class AestheticFeedback(BaseModel):
    common_issues: str
    suggestions: List[str]                        # 自由文字（相容舊格式）
    structured_suggestions: List[Suggestion]      # 機器可讀建議（Step 14 新增）

class AestheticJudgement(BaseModel):
    decision: JudgeDecision                       # "accept" | "reject"
    best_candidate_id: str
    evaluations: List[Evaluation]
    feedback: AestheticFeedback                   # BOTH accept 和 reject 都必填
    best_candidate_layout: Optional[Dict]         # {element_id: (left, top, width, height)}
```

### Pydantic 驗證

| 驗證項目 | 機制 | 說明 |
|------|------|------|
| total = sum(scores) | `Evaluation._total_matches_scores` model_validator | LLM 輸出的 total 必須等於 5 軸加總 |
| feedback 非 null | `AestheticJudgement` Field(required) | accept 和 reject 都必須有 feedback |
| kind=color → hex 字串 | `Suggestion._value_matches_kind` | 驗證 `#RGB` / `#RRGGBB` / `#RRGGBBAA` 格式 |
| 數值 kind → numeric value | `Suggestion._value_matches_kind` | resize / move / spacing / zorder 的 value 必須是數字 |
| kind=typography → metric 白名單 | `Suggestion._value_matches_kind` | metric 必須是 font_size / font_weight / font_family / text_align |
| kind=place_in_bbox → target_bbox 4 ints | `Suggestion._value_matches_kind` | L≥0, T≥0, R>L, B>T |

### 輸出範例（Accept, total ≥ 35）

```json
{
  "decision": "accept",
  "best_candidate_id": "cand_02",
  "evaluations": [
    {
      "candidate_id": "cand_01",
      "total": 33,
      "scores": {
        "design_layout": 7, "content_relevance": 7,
        "typography_color": 6, "graphics_images": 6,
        "innovation_originality": 7
      },
      "strengths": "headline_1 position is clear, logo_1 matches brief.",
      "weaknesses": "product_img_1 and headline_1 too far apart."
    },
    {
      "candidate_id": "cand_02",
      "total": 38,
      "scores": {
        "design_layout": 8, "content_relevance": 8,
        "typography_color": 7, "graphics_images": 7,
        "innovation_originality": 8
      },
      "strengths": "Generous whitespace, clear reading order.",
      "weaknesses": "price_1 slightly small."
    }
  ],
  "feedback": {
    "common_issues": "Overall accepted, but price_1 readability weak.",
    "suggestions": ["Slightly increase price_1 size by about 15%."],
    "structured_suggestions": [
      {
        "kind": "resize", "target_id": "price_1",
        "metric": "width", "op": "increase_by", "value": 24,
        "rationale": "Small bump improves legibility."
      }
    ]
  }
}
```

### 輸出範例（Reject, total < 35）

```json
{
  "decision": "reject",
  "best_candidate_id": "cand_03",
  "evaluations": [
    {
      "candidate_id": "cand_03",
      "total": 31,
      "scores": {
        "design_layout": 6, "content_relevance": 7,
        "typography_color": 6, "graphics_images": 6,
        "innovation_originality": 6
      },
      "strengths": "Palette aligns with style_keywords.",
      "weaknesses": "headline_1 too small to dominate layout."
    }
  ],
  "feedback": {
    "common_issues": "All candidates fail to make headline_1 dominate.",
    "suggestions": ["Increase headline_1 size.", "Reduce distance to product_img_1."],
    "structured_suggestions": [
      {
        "kind": "place_in_bbox", "target_id": "headline_1",
        "metric": "bbox", "op": "set_to",
        "value": "[80, 200, 720, 480]",
        "target_bbox": [80, 200, 720, 480],
        "rationale": "Upper-left sky region is empty."
      },
      {
        "kind": "typography", "target_id": "headline_1",
        "metric": "font_size", "op": ">=", "value": 72,
        "rationale": "Currently ~32px, too small to anchor hierarchy."
      }
    ]
  }
}
```

---

## Structured Suggestions（機器可讀改善建議）

Structured suggestions 是 Aesthetic Judge 最重要的輸出之一。Generator 的 refinement 模式**只讀 structured_suggestions**，自由文字的 suggestions 被忽略。

### 8 種 kind

| kind | 語意 | 偏好程度 | value 類型 |
|------|------|----------|-----------|
| `place_in_bbox` | 把元素放到精確的畫布區域 [L,T,R,B] | **強烈推薦**（繞過 ±10% drift cap） | 字串（鏡像 target_bbox） |
| `resize` | 改元素的 width 或 height | 常用 | 數字（px） |
| `move` | 改元素的 left 或 top | 常用 | 數字（px） |
| `spacing` | 改兩元素之間的間距 | 偶爾 | 數字（px） |
| `typography` | 改 font_size / font_weight / font_family / text_align | 常用（Step 45） | 視 metric 而定 |
| `color` | 設定 hex 顏色 | 偶爾 | hex 字串 `"#RRGGBB"` |
| `zorder` | 設定 z_index | 偶爾 | 整數 |
| `other` | 通用 fallback | **儘量避免**（每次回應最多 1 個） | 任意 |

### place_in_bbox 機制（Step 44）

Judge 可以**看到**候選渲染圖，因此能精確識別畫布上的空白區域。`place_in_bbox` 是最高頻寬的建議方式：

- `target_bbox: [L, T, R, B]`（4 個整數，像素座標）
- Generator 收到後直接設定 `(left=L, top=T, width=R-L, height=B-T)`
- **繞過 ±10% drift cap**——place_in_bbox 是唯一允許大幅移動元素的建議類型

### metric 白名單

每種 kind 有嚴格的 metric 限制：

| kind | 允許的 metric |
|------|--------------|
| place_in_bbox | `"bbox"` |
| resize | `"width"` / `"height"` |
| move | `"left"` / `"top"`（**不是** `"right"` / `"bottom"`） |
| spacing | `"gap_to:OTHER_ID"` |
| typography | `"font_size"` / `"font_weight"` / `"font_family"` / `"text_align"` |
| color | `"color"` |
| zorder | `"z_index"` |

> schema 沒有 `right` 或 `bottom` 欄位。如果 Judge 想把元素推到右下角，必須自己算出 target `left` 和 `top`，分別發出兩個 `move` suggestion。

### 6 種 operator（op）

`">="`、`"<="`、`"=="`、`"set_to"`、`"increase_by"`、`"decrease_by"`

### Accept vs Reject 的 suggestions 語意差異

| 情況 | 要求 | 行為 |
|------|------|------|
| reject | ≥1 個 structured suggestion（推薦 2-5 個） | 修正失敗維度的具體建議 |
| accept | ≥1 個 structured suggestion（典型 ≤2 個） | **保守的** small-step polish 建議，不可提出構圖級別的變更 |

---

## PROMPT_TEMPLATE 結構

實際的 PROMPT_TEMPLATE 約 200 行（不含兩個 Format Example），遠大於早期規劃版本。以下是完整結構說明：

### 1. Role 宣告

```
Role: You are a senior graphic designer and aesthetic evaluator.
Your goal is to evaluate each layout candidate and provide scores,
strengths, weaknesses, and actionable improvement suggestions.
```

### 2. `# Context`

| 變數 | 說明 |
|------|------|
| `{design_spec}` | 完整的 DesignSpec JSON（含 hard_constraints、style_keywords） |
| `{layout_tree}` | 語意層級樹 JSON |
| `{dominant_palette}` | 背景主色 JSON array |
| `{candidate_ids}` | 候選 id 清單（與附圖順序一致） |

> 原規劃有 `{candidate_images}` 佔位符——實作中圖片透過 `llm.aask(prompt, images=images)` 的 `images` 參數傳入，不嵌入 prompt 文字。

### 3. `# Scoring rubric`（COLE 5 軸 + Grading Anchors）

先列出 4 個分數錨點（10 / 7 / 4 / 1-2），再逐一定義 5 個軸的評分標準。每個軸都有一段完整的說明，告訴 LLM 10 分代表什麼、1 分代表什麼。

### 4. `# Structured suggestions`（Step 14/44/45 逐步擴充）

約 80 行的指引，涵蓋：

- **always emit feedback**（accept 和 reject 都必填）
- **reject vs accept 的語意差異**
- **8 種 kind** 的完整定義和使用條件
- **metric 白名單**（per-kind 的合法 metric 名稱）
- **typography kind 的 per-metric value 類型**（font_size → int, font_weight → int/str, font_family → str, text_align → enum）
- **op 列表**：6 種 operator
- **place_in_bbox 範例**（強烈推薦 + 具體 JSON）
- **numeric / color / typography 範例**

### 5. `# Format examples`

刻意放**兩個** Format Example（accept + reject），讓 LLM 不會因為 mimicry 而只產出其中一種格式：

- **Case A**：best score ≥ 35, accept（feedback 含 polish-step suggestions）
- **Case B**：best score < 35, reject（feedback 含 corrective suggestions）

兩個範例都包含完整的 structured_suggestions，讓 LLM 學習正確的 JSON 結構。

### 6. `# Instruction` 的 ATTENTION 行（~15 條）

包含以下重點指令：

- **ALL candidates**：不可跳過任何候選
- **Element IDs**：strengths/weaknesses 必須指出具體 element id
- **feedback MUST always be present**：accept 和 reject 都不可為 null
- **Numeric value**：resize / move / spacing / zorder 的 value 必須是數字，不能是 "bigger" 之類的文字
- **metric 白名單**：不可 emit `metric: "right"` 或 `metric: "bottom"`（schema 沒有這些欄位）
- **size_preference area gate**：提醒 Judge 在建議 enlarge 元素時要同時 emit width 和 height 的 resize，否則 QC 的面積門檻會攔住
- **Typography parity（Step 45）**：當候選文字元素在 typography_color 軸輸給 GT 時，要逐一檢查 font_size / font_weight / font_family / text_align 四個 metric，每個失敗的 metric 各發一個 suggestion
- **Prefer place_in_bbox**：任何涉及移動或縮放的建議都應優先使用 place_in_bbox（Judge 可以看圖，能比 Generator 更精確地識別空白區域）
- **Prefer kind ≠ "other"**：每次回應最多 1 個 "other" kind
- **best_candidate_id = highest total**
- **total = sum of five scores**
- **Single JSON output, nothing else**

---

## 驗證機制（雙層）

### 第一層：Pydantic Schema 驗證

`_parse_response(rsp)` 流程：
1. `strip()` 原始回應
2. 如果包含 markdown code fence → `CodeParser.parse_code(lang="json")` 剝離
3. `AestheticJudgement.model_validate_json(text)` → Pydantic 驗證所有 schema 約束

此層捕獲的錯誤：total ≠ sum(scores)、scores 超出 1-10 範圍、suggestion kind 不合法、value 類型不匹配 kind 等。

### 第二層：語意交叉驗證（`_validate_against_input`）

| 驗證項目 | 說明 |
|------|------|
| best_candidate_id 存在 | best_candidate_id 必須出現在輸入的 candidates 中 |
| evaluations 完整 | evaluations 的 candidate_id 集合必須 == 輸入 candidates 的 id 集合（不多不少） |

任一項失敗 → 拋出 `_JudgementValidationError`（自訂的 `ValueError` 子類），被重試迴圈捕獲。

### Post-validation：`_attach_best_candidate_layout`

驗證通過後，自動從輸入的 `candidates` 中查找 best candidate，提取其所有元素的 bbox 字典 `{element_id: (left, top, width, height)}`，寫入 `judgement.best_candidate_layout`。

這個 bbox 字典是 Refinement Loop 的關鍵資料——downstream 的 IterationStateRole 會把它打包進 `RetryPayload.prev_best_layout`，讓 Generator 的 refinement 模式以此為錨點做 ±10% 的 anchored edit。

---

## 錯誤處理與重試

`JudgeAesthetic.run()` 內建 **MAX_RETRIES = 3** 的重試機制：

1. 組裝 prompt → 渲染所有候選為 base64 PNG → `self.llm.aask(prompt, images=images)`
2. `_parse_response(rsp)` → Pydantic 驗證
3. `_validate_against_input(judgement, candidates)` → 語意交叉驗證
4. `_attach_best_candidate_layout(judgement, candidates)` → 提取 bbox 字典
5. 成功 → return judgement
6. `ValueError` / `ValidationError` → log warning → 重試
7. 三次都失敗 → raise `ValueError` 終止

> 與 GenerateLayout 不同，JudgeAesthetic 沒有 refusal detection 機制（Judge 不接收外部背景圖，只看系統自己渲染的候選圖，不太會觸發 vision safety refusal）。

---

## 回饋路由：IterationStateRole

### 背景

Aesthetic Judge 輸出通用的改進建議，不指定給誰。回饋路由由 **IterationStateRole** 負責——一個純路由 Role，不呼叫 LLM（`set_actions([])`），只管理 `IterationState` 狀態機和路由決策。

### Sentinel Actions

IterationStateRole 用 3 個**空的 Action 子類**作為 `cause_by` 標記，利用 MetaGPT 的 cause_by 通道路由 Message：

| Sentinel Action | 語意 | 誰在 watch |
|------|------|------|
| `RetryGeneration` | 回饋送給 LayoutGenerator，做 refinement | LayoutGeneratorRole |
| `RetryAnalyst` | 回饋送給 Analyst，重新規劃 DesignSpec | AnalystRole |
| `IterationStop` | 終止信號，沒有 Role watch 它 | （無） |

### RetryPayload（路由 Message 的 instruct_content）

```python
class RetryPayload(BaseModel):
    feedback: AestheticFeedback
    iteration: int
    target: FeedbackTarget           # "layout_generator" | "analyst"
    prev_best_layout: Optional[Dict] # bbox 字典（只在 target=generator 時填入）
    prev_best_subscores: Optional[Dict] # COLE 5 軸子分數（同上）
```

### 路由邏輯（`_act()`）

IterationStateRole 的 `_watch([JudgeAesthetic])` 每次收到 Judge 的 verdict 都會觸發：

```
收到 AestheticJudgement
    ↓
狀態更新：iteration += 1, 更新 consecutive_accepts / reject_count
    ↓
Best-so-far guard (Step 31)：
  如果 this_round_best > best_so_far_total →
    更新 best_so_far_total / best_so_far_layout / best_so_far_subscores
  否則 → 保留舊的 best_so_far（防止 noisy re-judge 導致分數回歸）
    ↓
終止檢查 #1：ACCEPT 且 consecutive_accepts ≥ 2
  → emit IterationStop（refinement 收斂）
    ↓
終止檢查 #2：iteration > max_total_rounds (5)
  → emit IterationStop（硬上限耗盡）
    ↓
路由決策：
  ACCEPT → 強制送 LayoutGenerator（mandatory refinement pass）
  REJECT →
    reject_count ≤ GENERATOR_FEEDBACK_ROUNDS (2) → LayoutGenerator
    reject_count > GENERATOR_FEEDBACK_ROUNDS     → Analyst
    ↓
組裝 RetryPayload：
  target = LayoutGenerator →
    prev_best_layout = best_so_far_layout（not this_round）
    prev_best_subscores = best_so_far_subscores
  target = Analyst →
    prev_best_layout = None（Analyst 從零重來，bbox 錨點無意義）
    ↓
emit Message(cause_by=RetryGeneration 或 RetryAnalyst)
```

### 關鍵設計細節

**Best-so-far guard（Step 31）：** Generator 的 refinement 總是以歷史最佳候選為錨點，而非最近一輪的結果。防止 noisy re-judge 讓分數倒退（Step 20b / Step 30 N=5 negative result 的 root cause #1）。`best_so_far_total` 只在新分數**嚴格高於**舊值時才更新。

**ACCEPT 也路由到 Generator：** cold-start 第一輪 Generator 沒看過 Judge critique，是「盲跑」結果。即使 total ≥ 35，仍強制一輪 refinement，讓 best candidate 至少經歷一次 critique-aware 編輯。

**reject_count vs iteration：** `reject_count` 只計 REJECT verdict，ACCEPT 觸發的 mandatory refinement 不消耗 GENERATOR_FEEDBACK_ROUNDS 預算。這確保 Generator 的 refinement 機會不被 accept → refine 的流程佔用。

### 終止條件

| 條件 | 觸發方式 |
|------|----------|
| Refinement 收斂 | `consecutive_accepts ≥ ACCEPT_CONSECUTIVE_STOP (2)` — accept → refine → 仍 accept |
| 硬上限耗盡 | `iteration > max_total_rounds (5)` — 無論最後 decision 是什麼 |

### IterationState Schema

```python
class IterationState(BaseModel):
    iteration: int = 0                     # 所有 verdict 的計數（accept + reject）
    consecutive_accepts: int = 0           # 連續 ACCEPT 計數，REJECT 時重置
    reject_count: int = 0                  # 累計 REJECT 計數（不含 accept-driven refinement）
    feedback_target: Optional[FeedbackTarget] = None
    last_feedback: Optional[AestheticFeedback] = None
    best_so_far_total: Optional[int] = None        # Step 31: 歷史最高 total
    best_so_far_layout: Optional[Dict] = None      # Step 31: 對應的 bbox 字典
    best_so_far_subscores: Optional[Dict] = None   # Step 31: 對應的 5 軸子分數
```

### 常數

| 常數 | 值 | 定義位置 | 說明 |
|------|---|---------|------|
| `ACCEPT_THRESHOLD` | 35 | schema.py | total ≥ 35 → accept |
| `K_VALID` | 5 | schema.py | 每輪目標合格候選數 |
| `GENERATOR_FEEDBACK_ROUNDS` | 2 | schema.py | 前 N 次 reject 送 Generator，之後送 Analyst |
| `ACCEPT_CONSECUTIVE_STOP` | 2 | iteration_state.py | 連續 2 次 accept 才停止 |
| `max_total_rounds` | 5 | IterationStateRole 屬性 | 硬上限（包含所有 verdict） |

---
