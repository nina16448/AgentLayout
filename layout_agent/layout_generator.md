# Layout Generator 設計規格

---

## 職責說明

Layout Generator 是整個 pipeline 最核心的 LLM Agent。它的工作是根據 Design Spec、Layout Tree 和背景分析結果，生成版面候選，每個候選包含所有元素的完整像素座標。

**Layout Generator 只負責決定「每個元素要放在哪裡、多大、文字視覺屬性」，不分析素材、不建立語意樹、不評審美感。**

---

## 實作架構

Layout Generator 遵循 MetaGPT 的 Role → Action → Tool 三層結構，是整個 pipeline 中檔案數最多、邏輯最複雜的 Agent：

```
LayoutGeneratorRole (roles/layout_generator.py, 231 行)
  ├── watches: PlanAssets (Asset Planner 完成時首次觸發)
  │            RetryGeneration (IterationState 路由 Judge 回饋時觸發)
  ├── action: GenerateLayout   (actions/generate_layout.py, 1126 行)
  │             ├── PROMPT_TEMPLATE (~400 行，pipeline 最大的 prompt)
  │             ├── LLM 呼叫（支援 vision channel：背景圖 + 自渲染圖）
  │             ├── Refusal Detection（視覺安全拒答偵測 + 降級）
  │             └── Pydantic 驗證 → CandidatesBatch
  └── tools (downstream，不在 Action 內呼叫，由 Role 或 Pipeline Driver 串接):
        ├── quality_checker.py (1706 行) — 程式化驗證，16 種 violation 規則
        ├── constraint_solver.py (889 行) — 確定性數學排版（Step 66，LLM-free）
        └── renderer.py (541 行) — PIL 渲染引擎（Candidate → PNG）
```

### 相關檔案

| 層級 | 檔案 | 行數 | 說明 |
|------|------|------|------|
| Role | `roles/layout_generator.py` | 231 | MetaGPT Role 殼：雙觸發、env history 回溯、top-up 迴圈、graceful degradation |
| Action | `actions/generate_layout.py` | 1126 | Prompt 組裝 + LLM 呼叫 + vision channel + refusal detection + JSON 解析 |
| Tool | `tools/quality_checker.py` | 1706 | 程式化規則驗證（16 種 ViolationType），公開 API：`check_candidate` / `filter_valid` |
| Tool | `tools/constraint_solver.py` | 889 | Step 66 確定性排版：把 LLM 從幾何決策中完全移除，由數學構造候選 |
| Tool | `tools/renderer.py` | 541 | Candidate → PIL Image 渲染：字型解析、文字換行、z-order 疊加、旋轉 |

---

## 呼叫時機

Layout Generator 有兩種被觸發的情況（由 Role 的 `_watch` 決定）：

### 情況一：首次執行（cold-start generation）

LayoutGeneratorRole 收到 `PlanAssets` Message（Asset Planner 完成），從 Message 的 `instruct_content` 取得 LayoutTree，從 env history 回溯取得 DesignSpec（透過 `_find_by_cause(AnalyzeBrief, DesignSpec)`）。`prev_best_layout` 為空，Generator 從零產出 5 個 distinctly different 候選。此時 `_retry_round` 重置為 0。

### 情況二：RetryGeneration（Judge 評分後 IterationState 路由回來）

LayoutGeneratorRole 收到 `RetryGeneration` Message，其 `instruct_content` 是 `RetryPayload`，包含：
- `feedback: AestheticFeedback` — Judge 的改善建議（含 structured_suggestions）
- `prev_best_layout: Dict[str, Tuple]` — 上一輪 best candidate 的 bbox 字典
- `prev_best_subscores: Dict[str, int]` — 上一輪 best candidate 的 COLE 5 軸子分數
- `iteration: int` — 當前迭代輪數

LayoutTree 從 env history 回溯取得（最近一次 PlanAssets 輸出仍然有效）。`_retry_round` 遞增，用於 candidate ID prefix 計算。

若 `prev_best_layout` 非空，進入 **refinement mode**（anchored edit）；若為空（例如 Analyst retry 導致的 cold-retry），走 cold-start 路徑。

---

## 生成機制：Top-up 迴圈

### 目標

湊滿 **K_VALID = 5 個**通過 Quality Checker 的合格候選。`K_VALID` 定義在 `schema.py`。

### 迴圈架構（`_generate_with_topup`）

```
for topup_idx in range(max_topup_rounds):   # max_topup_rounds = 3
    ↓
    呼叫 GenerateLayout.run() → 一批 5 個 raw candidates
    ↓
    給每個 candidate 加 prefix: f"r{prefix_offset + topup_idx}_{original_id}"
      （避免跨 retry round 的 ID 碰撞）
    ↓
    全部存入 pool（用於 degradation fallback）
    ↓
    filter_valid(batch, spec, bg=bg) → 通過的加入 kept、去重
    ↓
    len(kept) >= K_VALID → break
```

### prefix_offset 計算

`prefix_offset = _retry_round * max_topup_rounds`，確保每次 feedback retry 的 candidate ID 不與前一輪碰撞。例如：
- 首次執行：`r0_cand_01`, `r1_cand_01`, `r2_cand_01`
- 第一次 retry：`r3_cand_01`, `r4_cand_01`, `r5_cand_01`

### Graceful Degradation（Step 10b 修正）

當 3 輪 top-up 後 0 個候選通過 QC（例如 Analyst 給了未知的 position_preference hint），不 hard-crash，改為：
1. 用 `rank_candidates_by_violations(pool, all_reports)` 按 violation 數量排序
2. 取前 K_VALID 個 least-violating candidates 繼續
3. 這些候選會被 Judge 評分，Judge 的 feedback 可以路由回 Analyst 修正 spec

> 設計考量：Hard-crash 會無聲地縮小可評測的樣本數；degradation 讓 reject loop 繼續運作，feedback 仍可把 spec 送回 Analyst 修正。

---

## 輸入

| 輸入 | 類型 | 說明 | 來源 |
|------|------|------|------|
| `spec` | `DesignSpec` | 含所有元素的 semantic_type、importance、semantic_relevance、hard/soft constraints、style_keywords、composition | Analyst 輸出 + AssetAnalyzer 填入 + ComposeSketch 寫入 composition |
| `tree` | `LayoutTree` | 元素之間的語意層級關係 | Asset Planner |
| `bg` | `BackgroundAnalysis` | safe_zones、dominant_palette、recommended_text_color、saliency_histogram、low_saliency_regions、saliency_map | `resolve_background(spec.canvas)` — CV 模組（非 Role） |
| `feedback` | `AestheticFeedback` (optional) | Judge 的改善建議，含 suggestions + structured_suggestions | Aesthetic Judge → IterationState → RetryPayload |
| `prev_best_layout` | `Dict[str, Tuple[float,float,float,float]]` (optional) | 上一輪 best candidate 的 bbox 字典 `{element_id: [left, top, width, height]}` | RetryPayload |
| `prev_best_subscores` | `Dict[str, int]` (optional) | 上一輪 best candidate 的 COLE 5 軸子分數 `{design_layout, content_relevance, typography_color, graphics_images, innovation_originality}` (各 1-10) | RetryPayload |
| `prev_render_path` | `Path` (optional) | 上一輪 best candidate 的渲染 PNG 路徑（Step 65 self-render） | Pipeline driver |
| `exemplars` | `str` (optional) | Designer exemplar 佈局的正規化描述（Step 67） | Pipeline driver / retrieval |

### Background Analysis（CV 模組，非 LLM）

`resolve_background(spec.canvas)` 在 `roles/layout_generator.py` 的 `_act()` 內呼叫，不是一個 Role。行為：
- 如果 `canvas.background_asset_ref` 存在 → 跑 U2Net saliency 分析，產出真實的 safe_zones、energy_map、dominant_palette、saliency 資料
- 如果沒有背景圖 → fallback 到 solid-color stub（全畫布一個 safe zone）

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
          "left": 100, "top": 800, "width": 880, "height": 600,
          "angle": 0, "z_index": 3,
          "font_family": "sans-serif",
          "font_size": 96,
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
    }
  ]
}
```

> 圖片元素（`visual_type: image`）只需要幾何座標，不需要文字視覺屬性。
> 文字元素（`visual_type: text`）需要額外輸出 `font_family`、`font_size`、`font_weight`、`color`、`text_align`。

### Format Example（實際使用）

實作中的 `FORMAT_EXAMPLE_JSON` 包含 **2 個完整候選**，刻意使用不同的構圖方向（一個 headline 在中上、一個在下方），讓 LLM 理解 "5 distinct compositional approaches" 的期望。

---

## PROMPT_TEMPLATE 結構

實際的 PROMPT_TEMPLATE 約 400 行，遠大於早期規劃版本。以下是完整結構說明：

### 1. Role 宣告

```
Role: You are a professional graphic layout designer.
Your goal is to arrange the given design elements on a canvas
by assigning precise pixel coordinates to each element.
```

### 2. `# Context`

把這次生成需要的所有資料餵進來：
- `{design_spec}`：完整的 DesignSpec JSON
- `{safe_zones}`：BackgroundAnalyzer 產出的 CV 可放置區域
- `{saliency_landscape}`：F2 Step 72 的 3×3 grid + top-K 低顯著性矩形（feature flag 控制，預設 OFF）
- `{dominant_palette}`：背景主色
- `{recommended_text_color}`：背景明暗度推薦的文字顏色
- `{feedback}`：Judge 上一輪的改善建議（首次為 "None"）

### 3. `# Designer exemplars`（Step 67）

真人設計師佈局的正規化描述，來自結構相似的 brief 的檢索結果。正規化到 [0,1] 座標。LLM 用來學習構圖語言（照片相對文字的位置、照片大小、不對稱性），不抄座標。填入 `{exemplars}`，檢索關閉時為 "None"。

### 4. `# Aesthetic objective`（Step 33）

COLE 評審的 4 個軸向，每個 1-10 分，作為 **設計目標**（不是事後標準）讓 LLM 在排版時追求 8+ 分：

| 軸向 | 說明 |
|------|------|
| A. Design and Layout | 乾淨、平衡、一致的排版，有清晰的層級，反映 Layout Tree 的深度順序 |
| B. Content Relevance and Effectiveness | 排版必須服務 brief 和 spec，嚴守 hard_constraints |
| C. Typography and Color Scheme | 字型大小必須形成層級（title >> subtitle >> body），顏色協調 |
| D. Innovation and Originality | 5 個候選必須走不同的構圖方向，避免全部置中或全部上對齊 |

### 5. `# Previous Attempt`（Refinement Loop 專用）

填入 `{previous_attempt}`。cold-start 時為 "None"，refinement 時包含：

```
prev_best_layout (element_id -> [left, top, width, height], pixels):
{
  "headline_1": [100, 800, 880, 600],
  "logo_1": [900, 40, 120, 120]
}
prev_best_subscores (COLE 5-axis, 1-10 each):
  design_layout=6  content_relevance=7  typography_color=5  graphics_images=4  innovation_originality=6
```

接著是 refinement mode 的行為規則：
- **Anchored edit**：每個元素的 (left, top, width, height) 必須在上一輪的 ±10% 內
- **例外**：structured_suggestions 中 `kind="place_in_bbox"` 的元素不受 ±10% 限制
- **Stable IDs**：沿用 spec 的 element id，不改名
- **探索方向**：5 個候選可走不同 refinement 方向，但都必須以 prev_best_layout 為錨點
- **優先低分軸**：看 prev_best_subscores，最低分的子維度是編輯應改善的方向

### 6. `# How to read feedback`（structured_suggestions 解讀表）

當 feedback 不是 "None" 時，指導 LLM 如何解讀 `structured_suggestions` 的 7 種 kind：

| kind | 操作 |
|------|------|
| `place_in_bbox` | 直接覆蓋元素的 (left,top,width,height) 為 target_bbox，**繞過 ±10% drift cap** |
| `resize` | 改元素的 width / height |
| `move` | 改元素的 left / top |
| `spacing` | 改兩元素之間的間距（metric 格式：`gap_to:OTHER_ID`） |
| `typography` | 改文字的 font_size / font_weight / font_family / text_align |
| `color` | 改元素的 color（用精確 hex 值） |
| `zorder` | 改元素的 z_index |
| `other` | 通用：把 operator/value 應用到 metric 欄位 |

6 種 operator：`>=`（下界）、`<=`（上界）、`==`（精確值）、`set_to`（同 ==）、`increase_by`、`decrease_by`

> 規則：feedback 的每個 structured_suggestion 必須在至少 4/5 個候選中被執行。suggestions 自由文字只當補充上下文。

### 7. `# Layout Tree`

Asset Planner 產出的語意層級樹，加兩句語意說明。

### 8. `# size reference`（尺寸對照表 + Prompt/QC 不對稱設計）

```
full-canvas: >=95%  |  hero: >=60%   |  large: >=30%
prominent:   >=20%  |  medium: >=15% |  small: >=8%   |  caption: >=3%
photo-prominent: >=20%  (GT-calibrated photo floor, Step 60)
```

**刻意的 Prompt/QC 不對稱（Step 67 審計確認）：** Prompt 中的百分比是 **STRETCH TARGET**（例如 prominent ≥20%），QC 的實際 acceptance floor 更低（例如 prominent = 0.10）。這是因為 LLM 有系統性的「尺寸膽怯」（Step 58/60），如果 prompt 數字對齊 QC floor，實際輸出會落在 floor 以下。Step 22–66 的所有校準都是在這個 gap 下做的，不可擅自對齊。

### 9. `# GT-calibrated photo size prior`（Step 60）

填入 `{photo_size_prior}`。只針對 `semantic_type == product_image` 的元素：
- 無 Composition Directive 時：告知 GT 中位數 0.213、上四分位 0.445，目標 0.20–0.45
- 有 Composition Directive 時：Directive 的 size bucket 覆蓋歷史 prior（避免兩個矛盾的面積訊號）

### 10. `# Composition directive`（Step 62）

填入 `{composition_directive}`。把 Composition Director 選擇的抽象模板轉換為具體像素約束：
- 照片中心必須落在哪個 grid cell
- 照片面積比必須在哪個區間
- **Worked Example**：滿足兩個約束的具體 (left, top, width, height) 數字（Step 64）
- 文字質心必須落在哪個 grid cell
- 照片-文字關係規則（text-on-photo / stacked / side-by-side / centered-mix）
- Underlay 合約（text-on-photo 時文字必須坐在 decorative_image underlay 上，underlay 覆蓋 ≥80% 文字 bbox）

### 11. `# Layout constraints`（7 條 QC 硬規則，Step 37）

在 prompt 中**明列** Quality Checker 下游會驗證的規則，讓 Generator 一次就生出合規候選：

1. **DECORATIVE 面積** — decorative_image 每個 < 40% 畫布面積
2. **TITLE 大小+位置** — area_ratio ≥ 0.025、水平中心 [0.10, 0.90]、垂直中心 [0.05, 0.85]
3. **TEXT_OBSCURED** — 文字上方不可有 z_index ≥ 文字的非文字元素覆蓋 ≥ 20%
4. **WCAG 對比** — 文字顏色 vs 背景色的 WCAG 2.1 AA 對比 ≥ 4.5
5. **SEQUENTIAL Y-ORDER** — asset_list 中的文字順序必須對應 top-to-bottom y 順序
6. **SAFE-ZONE OVERLAP** — 主要元素（title/subtitle/body_text/product_image/logo）與至少一個 safe_zone 的重疊 ≥ 50%
6b. **SALIENCY-AWARE TEXT**（F2, Step 72）— 文字 bbox 的平均背景顯著性 ≤ 0.50
7. **COVERAGE / DEAD SPACE**（Step 57）— 前景覆蓋 ≥ 10%、無超過 60% 畫布寬/高的連續空白帶

### 12. `# Reasoning checklist`（Step 42）

6 步心智清單，LLM 在產出 JSON 前必須「腦內走過」：
1. **SAFE-ZONE PLAN** — 讀 safe_zones，決定哪個 zone 放 title、哪個放 body
2. **ASSET INVENTORY** — 列出每個元素 id + semantic_type + asset，決定焦點元素
3. **HIERARCHY** — 設定大小使 importance 順序視覺明顯
4. **COLOR** — 預設 recommended_text_color，心算 WCAG 對比
5. **FEEDBACK APPLICATION** — 為每個 structured_suggestion 記下精確 target_id 和新值
6. **DIVERSITY CHECK** — 規劃 5 個候選各把 title 錨定在不同 safe_zone

### 13. `# Instruction` ATTENTION 行（~20 條）

包含以下重點指令（完整版見原始碼）：

- 輸出 5 個候選，每個包含所有 spec element ID（**ID 完全沿用、不改名不翻譯**）
- 座標合法、嚴守 hard_constraints
- **Typography direction（Step 49a）**：刻意選擇 font_family（sans-serif / serif / cursive / display），根據 style_keywords 映射設計風格；5 個候選至少用 2 種不同的 (title font_family, title color) 組合
- **Photo sizing（Step 60）**：photo-prominent 的元素面積 ≥ 20% 畫布，先算數學再輸出
- **Composition directive（Step 62）**：Directive 是 art director 決定、outranks Generator 自己的構圖品味，5 個候選全部滿足數學合約
- **Canvas 垂直覆蓋**：max(top+height) ≥ 0.85 × canvas_height、min(top) ≤ 0.10 × canvas_height
- **水平平衡（Step 49b）**：不可全部元素擠在畫布一半
- **Decorative-image underlay 堆疊**：z_index 嚴格低於所有文字/圖片/logo；每個 underlay 必須完全包含至少一個文字元素（配對強制）
- **Vision channel（Step 46）**：第一張附圖是畫布背景 PNG，當眼睛和數字不一致時相信圖片
- **Self-render（Step 65）**：最後一張附圖是上一次嘗試的渲染結果（conditional）
- **Refinement mode ±10% drift**：place_in_bbox 例外

---

## Vision Channel（Step 46/65）

### 背景圖附加（Step 46）

`GenerateLayout.run()` 在呼叫 LLM 前，如果模型支援圖片輸入（`self.llm.support_image_input()`）：
1. 呼叫 `_render_bg_image(spec)` 載入 `canvas.background_asset_ref`
2. 縮放最長邊至 `_BG_MAX_EDGE_PX = 768` px（控制 token 成本）
3. 轉 base64 PNG 作為第一張附圖

讓 LLM **看到**背景的焦點主體和空白區域，而不只是靠 safe_zones 的數字摘要。

### 自渲染圖附加（Step 65）

如果 `prev_render_path` 指向上一次嘗試的渲染 PNG：
1. 載入並縮放為 base64 PNG
2. 作為**最後一張附圖**
3. Prompt 啟用 `_SELF_RENDER_NOTE` ATTENTION 區塊，要求 LLM：
   - 看渲染結果並指出 2-3 個最差的視覺缺陷
   - 每個 feedback 項目都對照渲染圖確認
   - 新候選必須**視覺上可見地**修復缺陷

> 實驗結論（Step 65）：自渲染通道是 negative result — 演化 9/20 vs 基線 13/20 無加成，self-render 拒答率翻倍 42.5%。

---

## Refusal Detection（Step 64/65）

當 LLM 的 vision channel 觸發安全拒答（"I'm sorry, I can't assist with that."），JSON parse 會失敗。偵測邏輯：

1. **時機**：只在 JSON parse 失敗**之後**才掃描（合法 batch 永不被誤判）
2. **範圍**：只掃前 `_REFUSAL_MAX_LEN = 200` 字元（拒答句在開頭，候選文字內容在深處）
3. **Marker**：`"i'm sorry"`, `"i can't assist"`, `"unable to assist"` 等 8 種

偵測到拒答時的 **informed degradation**：
1. 丟棄所有 images（回到 pre-Step46 的 text-only 模式）
2. 如果之前有 self-render，重建 prompt 移除 self-render ATTENTION 區塊
3. 額外給 1 次重試機會（`budget += 1`）
4. 此降級**至多觸發一次**（`images and` guard）

---

## 錯誤處理與重試

`GenerateLayout.run()` 內建 **MAX_RETRIES = 3** 的重試機制：

1. 組裝 prompt + images → 呼叫 `self.llm.aask(prompt, images=images)`
2. 嘗試 `_parse_response(rsp)` — `CodeParser.parse_code(lang="json")` 剝離 markdown fence → `CandidatesBatch.model_validate_json()`
3. 如果 parse 失敗且偵測到 refusal → 降級為 text-only + 1 次額外機會
4. 其他 `ValueError` / `ValidationError` → log warning → 重試
5. 所有嘗試都失敗 → raise `ValueError` 終止

重試次數上限在有 refusal 時實際可達 4 次（3 + 1 bonus）。

---

## Quality Checker（`tools/quality_checker.py`）

Quality Checker 是 pipeline 中最大的單一工具（1706 行），對 Generator 候選做程式化驗證。公開 API：

### `check_candidate(candidate, spec, bg=None) -> CheckResult`

逐一執行所有驗證規則，收集完整的 violation 清單（不 fail-fast，為了分析需要全量記錄）。

### `filter_valid(candidates, spec, bg=None) -> (kept, reports)`

批次包裝器。返回 (通過的候選, 所有報告)。`bg` 參數傳遞到 `check_candidate` 啟用 safe-zone 規則。

### `rank_candidates_by_violations(candidates, reports) -> sorted_list`

按 violation 數量排序（穩定排序），用於 graceful degradation。

### 16 種 ViolationType

分為多個驗證階段：

**Phase 1 — 元素完整性：**

| 類型 | 說明 |
|------|------|
| `MISSING_ELEMENT` | Spec 中的元素 ID 未出現在候選中 |
| `EXTRA_ELEMENT` | 候選中出現 Spec 沒有的元素 ID |

**Phase 2 — 邊界檢查：**

| 類型 | 說明 |
|------|------|
| `OUT_OF_BOUNDS` | 元素的 left/top < 0 或 left+width > canvas_width 或 top+height > canvas_height |

**Phase 3 — Hard Constraints：**

| 類型 | 說明 | 容差 |
|------|------|------|
| `POSITION_PREFERENCE` | 元素中心不在指定的 3×3 grid band 內 | band 邊界 ±10% canvas dim（floor 16px） |
| `NO_OVERLAP` | 兩個 no_overlap target 的 bbox 重疊 | > 5% 較小元素面積才算違規 |
| `Z_ORDER` | z_index 不滿足 z_order constraint |  |
| `SIZE_PREFERENCE` | 元素面積比不滿足 size hint 的下界 | 見 SIZE_HINT_LOWER_BOUND 表 |
| `UNKNOWN_HINT` | position_preference hint 不在已知列表中 |  |
| `UNKNOWN_TARGET` | constraint 的 target ID 不在候選中 |  |

**Visual Quality Rules（Step 35/36/37）：**

| 類型 | 說明 | 閾值 |
|------|------|------|
| `TEXT_OBSCURED_BY_OVERLAY` | 文字上方（z_index ≥ 文字）的非文字元素覆蓋 ≥ 20% 文字面積 | 0.20 |
| `LOW_TEXT_CONTRAST` | 文字顏色 vs 畫布 background_color 的 WCAG 2.1 AA 對比 < 4.5 | 4.5 |
| `DECORATIVE_IMAGE_OVERSIZED` | decorative_image 佔畫布面積 > 40% | 0.40 |
| `TITLE_UNDERSIZED` | title 面積 < 2.5% 畫布面積 | 0.025 |
| `TITLE_PERIPHERAL` | title 中心 cx 不在 [0.10, 0.90] 或 cy > 0.85 或 cy < 0.05 |  |

**Content-Aware Rules（Step 43/57/59）：**

| 類型 | 說明 |
|------|------|
| `PRIMARY_OUTSIDE_SAFE_ZONE` | 主要元素（title/subtitle/body_text/product_image/logo）與所有 safe_zones 的重疊都 < 50% |
| `CANVAS_COVERAGE_LOW` | 前景元素 union 面積 < 10% 畫布面積 |
| `DEAD_BAND_EXCESSIVE` | 連續空白帶（水平或垂直）> 60% 畫布寬/高 |
| `TEXT_ON_BUSY_TEXTURE` | 文字所在區域的背景 Sobel 梯度過高（Rea 指標） |

**Composition Director Rules（Step 62）：**

| 類型 | 說明 |
|------|------|
| `COMPOSITION_MISMATCH` | 不滿足 Composition Directive 的數學合約（photo cell、photo size、text cell、relation） |
| `TEXT_ON_PHOTO_NO_UNDERLAY` | text-on-photo directive 下文字沒有 decorative_image underlay 覆蓋 ≥ 80% |

**Saliency Rule（F2, Step 72）：**

| 類型 | 說明 | 閾值 |
|------|------|------|
| `TEXT_ON_HIGH_SALIENCY` | 文字 bbox 的平均背景顯著性 > 0.50（feature flag 控制，預設 OFF） | tau = 0.50 |

### SIZE_HINT_LOWER_BOUND（QC Acceptance Floor）

| hint | QC floor | Prompt stretch target | 說明 |
|------|----------|----------------------|------|
| full-canvas | 0.95 | ≥95% | |
| hero | 0.60 | ≥60% | |
| large | 0.30 | ≥30% | |
| prominent | 0.10 | ≥20% | Prompt/QC 刻意不對稱（Step 67 審計） |
| photo-prominent | 0.20 | ≥20% | GT-calibrated（Step 60），由 inject_photo_size_prior 注入 |
| medium | 0.08 | ≥15% | 同上不對稱 |
| small | 0.08 | ≥8% | |
| caption | 0.03 | ≥3% | |

---

## Constraint Solver（`tools/constraint_solver.py`，Step 66）

### 背景

Step 30–65 確認 pipeline 是 **Generator-bounded**：LLM 系統性地無法在像素級別執行 Composition Director 的 GT 校準草圖，文字 QC 回饋（Step 59）和視覺自我修正（Step 65）都無法移動它。

### 設計

把 LLM 從幾何決策中**完全移除**：給定相同的輸入（DesignSpec + CompositionDirective + BackgroundAnalysis），用 Quality Checker 相同的數學（`cell_bounds` / `SIZE_BUCKET_RANGES`）**構造**候選。

**LLM 保留的工作**：brief 分析（Analyst）、模板選擇（Composition Director）、評審（Judge）。
**變為確定性的工作**：每個 bbox、z-index、typography 預設值。

### 機制

1. **元素分組**：`_group_elements(spec)` 把元素分為 background / photos / underlays / texts / others
2. **照片排版**：`_place_photo()` 保持原始長寬比，面積 = `SIZE_BUCKET_TARGET[bucket]`，中心在 directive cell
3. **文字測量與堆疊**：使用 renderer 的 `_resolve_font` + `_wrap_to_width` 做精確文字測量，按 `TEXT_PRIORITY` 上下堆疊
4. **錨點搜索**：在 directive cell 內 5×5 grid 搜索，按背景梯度（Sobel）+ safe-zone 包含度排序，選最佳錨點
5. **Underlay 構造**：text-on-photo 時在文字 stack 外擴 `UNDERLAY_PAD_SCALE` 生成 underlay bbox
6. **顏色決策**：`_pick_text_color` 根據文字所在區域的背景亮度選顏色（亮度感知）

### variant 參數

`variant` (0/1/2) 讓 oracle 的 retry 迴圈產出不同的確定性候選：
- 0 = calibrated defaults
- 1 = bolder type scale（font_mult = 1.18）
- 2 = conservative type scale + photo-size nudge

相同 (spec, bg, variant) 永遠產出相同候選。

### 校準常數

| 常數 | 值 | 說明 |
|------|---|------|
| `FONT_SCALE[TITLE]` | 0.095 × min(cw,ch) | 標題字型大小，GT gallery 校準 |
| `STACK_GAP_SCALE` | 0.022 × min(cw,ch) | 文字堆疊間距 |
| `UNDERLAY_PAD_SCALE` | 0.06 | Underlay 外擴比例（Step 64 80% 覆蓋合約） |
| `EDGE_MARGIN_SCALE` | 0.02 | 元素到畫布邊緣的最小間距 |

> 實驗結論（Step 66）：constraint solver 是最強反證 — 幾何全移出 LLM，Judge 仍 55/55 全敗（acceptance 0/20）。QC 合規 ≠ Judge 偏好。Generator-bounded 探索線已結案為 limitation。

---

## Renderer（`tools/renderer.py`）

### 職責

把一個 `Candidate` + `DesignSpec` 轉成 PIL Image（和可選的 PNG 檔案）。

### 公開 API

| 函式 | 說明 |
|------|------|
| `render(candidate, spec) -> PIL.Image.Image` | 記憶體內渲染 |
| `render_to_file(candidate, spec, path) -> Path` | 渲染 + 存 PNG |
| `image_to_base64(img, format='PNG') -> str` | 轉 base64（供 vision LLM 輸入） |

### 四種工作

1. **背景畫布**：載入 `canvas.background_asset_ref` 或 fallback 白底
2. **圖片元素**：載入 `asset_ref`，resize（上限 `MAX_UPSCALE = 2.0`），paste with alpha
3. **文字元素**：解析系統字型、draw with alignment + color
4. **z-order 疊加**：按 `z_index` 升序繪製（低的先畫）

### 字型解析（Step 47/55）

`FONT_CANDIDATES` 字典映射 `(family, weight)` 到有序的字型路徑清單：

| family | 字型 |
|------|------|
| sans-serif | Montserrat (variable), DejaVuSans, LiberationSans |
| serif | DejaVuSerif, LiberationSerif, P052 |
| script/cursive | GreatVibes, Pacifico, DancingScript (variable) |
| display | Lobster, BebasNeue, Oswald (variable) |

優先從 `PROJECT_FONT_DIR`（`tools/fonts/`）載入 OFL 授權字型，再 fallback 到系統字型。Variable fonts 的 Bold named instance 在 load time 透過 `_apply_weight_variation` 應用。

### 文字渲染（Step 55 升級）

- **自動換行**：`_wrap_to_width` 把文字依 bbox 寬度換行
- **Shrink-to-fit**：文字溢出 bbox 時逐步縮小（floor 8px）
- **手動換行**：content 中的 `\n` 原樣保留
- **旋轉**：文字元素支援 `angle`（與圖片相同慣例）

> 設計考量（Step 54 render-parity 分析）：渲染通道佔 blind gap 的 61–68%。Step 55 升級後天花板從 22.5% 提升至 55%。

---
