# Agent 1：Analyst 設計規格

---

## 職責說明

Agent 1 是整個 pipeline 的入口。它的工作是把使用者的自然語言描述和素材，整理成一份後續 Agent 都能解析的結構化 Design Spec JSON，並在 LLM 產出後由內建的 AssetAnalyzer 工具自動填入每個元素的 `importance`（重要性分數）和 `semantic_relevance`（語意相關度）。

**Agent 1 負責「整理使用者說了什麼」+ 「用查表法補上視覺層級權重」。** LLM 只做結構解析，重要性分數由 Python 查表決定，不經 LLM 推理。

---

## 實作架構

Analyst 遵循 MetaGPT 的三層結構，拆為 Role → Action → Tool：

```
AnalystRole (roles/analyst.py)
  ├── watches: UserRequirement (首次啟動)
  │           RetryAnalyst    (Agent 4 回饋後重跑)
  ├── action: AnalyzeBrief    (actions/analyze_brief.py)
  │             └── LLM 呼叫 → 解析 JSON → DesignSpec
  │             └── inject_photo_size_prior()  ← LLM 後處理
  └── tool:   AssetAnalyzer   (tools/asset_analyzer.py)
                └── 填 importance (查表) + semantic_relevance (CLIP stub)
                └── spec.assert_enriched() 驗證
```

### 檔案對照

| 層級 | 檔案 | 說明 |
|------|------|------|
| Role | `roles/analyst.py` | MetaGPT Role 殼：處理 Message 路由、區分首次/重跑、呼叫 Action + Tool |
| Action | `actions/analyze_brief.py` | Prompt 模板 + LLM 呼叫 + JSON 解析 + 重試邏輯 |
| Tool | `tools/asset_analyzer.py` | 零 LLM 的 Python 後處理：查表填 importance / semantic_relevance |

---

## 輸入

### 冷啟動（第一次執行）

AnalystRole 監聽 `UserRequirement` Message，其 `instruct_content` 是一個 `LayoutBrief`（定義在 pipeline.py），包含：

| 輸入 | 類型 | 說明 |
|------|------|------|
| `user_brief` | `str` | 使用者的自然語言描述，例：「設計一張夏日促銷海報，整體橘色調，Logo 放右上角」 |
| `asset_list` | `List[AssetInput]` | 每個素材只包含 `asset_ref`（圖片檔路徑）**或** `content`（文字字串），二擇一。Pydantic validator 強制恰好一個有值。 |

> **與最初規劃不同：** 素材不帶 `embedding_key`。CLIP 編碼由獨立的前處理步驟填入 `EmbeddingStore`，Analyst 全程不碰向量、輸出的 `embedding_key` 一律為 `null`。

### 被 Agent 4 回饋後重新執行

AnalystRole 也監聽 `RetryAnalyst` Message（由 `IterationStateRole` 發布），其 `instruct_content` 是 `RetryPayload`：

| 輸入 | 類型 | 說明 |
|------|------|------|
| `feedback` | `AestheticFeedback` | Agent 4 的結構化回饋（哪個維度不好、建議如何調整） |
| `iteration` | `int` | 當前迭代輪次 |

重跑時，AnalystRole 會用 `_find_layout_brief()` 從 env history 回溯找到原始的 `LayoutBrief`（`user_brief` + `asset_list`），再加上 `feedback` 一起送入 LLM。

---

## 輸出：DesignSpec JSON

DesignSpec 定義在 `schema.py`，是 Analyst 到下游所有 Agent 的共用合約。以下逐區塊說明。

### Canvas（畫布）

| 欄位 | 類型 | 說明 | 來源 |
|------|------|------|------|
| `width` | `int` | 畫布寬度（pixel） | LLM 從描述推理（例：「Instagram 貼文」→ 1080）|
| `height` | `int` | 畫布高度（pixel） | LLM 從描述推理 |
| `background_asset_ref` | `Optional[str]` | 背景圖檔案路徑 | 使用者上傳；有圖時優先於 background_color |
| `background_embedding_key` | `Optional[str]` | 背景圖的 CLIP 向量索引 | Analyst 一律輸出 `null`，由 CLIP 前處理填入 |
| `background_color` | `Optional[str]` | 純色背景 hex（例：`"#F5E6D3"`） | 無背景圖時由 LLM 推理出愉悅色；Pydantic validator 強制 `#RRGGBB` 格式並自動轉大寫 |

**背景優先級**（renderer 消費順序）：
1. `background_asset_ref`（有可載入圖片時用圖片）
2. `background_color`（無圖片時用純色 hex）
3. Renderer 預設純白（兩者皆無時的最終 fallback）

> **Step 7 新增：** `background_color` 是實驗迭代中加入的欄位。實驗發現三元素設計放在純白畫布上，`visual_coherence` 和 `layout_balance` 會 plateau 在 ~17/25，加一個愉悅色就能改善。

---

### Elements（元素清單）

每個素材（圖片或文字）對應一筆 element。

| 欄位 | 類型 | 說明 | 填入者 |
|------|------|------|--------|
| `id` | `str` | 元素識別碼 | LLM 命名，例：`headline_1`、`logo_1` |
| `semantic_type` | `SemanticType` | 語意角色（closed enum，見下） | LLM 從素材內容與描述推理 |
| `visual_type` | `VisualType` | 渲染類型：`"image"` 或 `"text"` | LLM 從素材類型判斷 |
| `content` | `Optional[str]` | 文字內容（文字元素專用） | 使用者輸入 |
| `asset_ref` | `Optional[str]` | 圖片檔路徑（圖片元素專用） | 使用者上傳 |
| `embedding_key` | `Optional[str]` | CLIP 向量索引 | Analyst 一律輸出 `null` |
| `inferred` | `bool` | `semantic_type` 是使用者明確說的還是 LLM 推理的 | LLM 自己標記 |
| `importance` | `Optional[int]` | 重要性分數 1–5 | **AssetAnalyzer 查表填入**（非 LLM） |
| `semantic_relevance` | `Optional[float]` | 與 style_keywords 的語意相關度 0.0–1.0 | **AssetAnalyzer 填入**（目前為 CLIP stub = 0.5） |

> **與最初規劃不同：**
> - `importance` 和 `semantic_relevance` 不是交給 Agent 2，而是在 Agent 1 內部由 AssetAnalyzer 查表/stub 填入。
> - 規劃中的 `text_hints`（字型、大小、顏色建議）和 `image_hints`（圖片描述、比例）**從未實作**，schema 中不存在這兩個欄位。

**`semantic_type` 可選值（12 值 closed enum）：**

| 值 | importance 查表 | 說明 |
|---|---|---|
| `title` | 5 | 標題 |
| `cta` | 5 | Call-to-Action 按鈕 |
| `product_image` | 5 | 產品圖 |
| `logo` | 4 | Logo |
| `subtitle` | 4 | 副標題 |
| `pricetag` | 4 | 價格標籤 |
| `body_text` | 3 | 內文 |
| `icon` | 3 | 圖示 |
| `other` | 3 | 其他 |
| `decorative_image` | 2 | 裝飾圖（含 underlay） |
| `caption` | 2 | 圖說 |
| `background_image` | 1 | 背景圖 |

**不屬於 Agent 1 的欄位（交給 Agent 3 Layout Generator）：**
- `left` / `top` / `width` / `height` / `angle` / `z_index`（幾何座標）

---

### Constraints（約束）

約束分兩類，分流邏輯如下：

- 使用者描述**有明確幾何意義** → `hard_constraints`（結構化物件）
- 使用者描述**是風格或感覺** → `style_keywords` 或 `soft_constraints`

#### hard_constraints

結構化物件，供 Agent 3 生成時遵守、Agent 4 驗證時由 Quality Checker 程式化檢查。

格式：`{"rule": "...", "targets": ["元素id"], "params": {"hint": "..."}}`

**支援的 rule 類型（`HardConstraintRule` closed enum）：**

| rule | 說明 | params.hint 限制 | 範例 |
|------|------|-----------------|------|
| `position_preference` | 位置偏好 | **必須為九宮格之一**（見下） | `{"hint": "top_right"}` |
| `no_overlap` | 不能與某元素重疊 | 無 | `{}` |
| `z_order` | 誰要疊在誰上面 | `"above_background"` | `{"hint": "above_background"}` |
| `size_preference` | 大小偏好 | `"prominent"` / `"photo-prominent"` | `{"hint": "prominent"}` |

**`position_preference` 的 hint 九宮格 closed enum（case-sensitive）：**

```
top_left    | top_center    | top_right
middle_left | center        | middle_right
bottom_left | bottom_center | bottom_right
```

> **實作教訓：** LLM 容易幻想出 `"below_title"` / `"above_logo"` 這類相對位置 hint，但 Quality Checker 只認這 9 個固定值，其他值會導致 hard pipeline failure。Prompt 裡用 ATTENTION 警告強制 LLM 只能用這 9 個值。

#### soft_constraints

無法用程式驗證的偏好，供 Agent 3 生成時參考、Agent 4 評審時考量。

格式：`{"rule": "...", "weight": 0.0~1.0, "params": {}}`

**`SoftConstraintRule` closed enum（5 值，case-sensitive）：**

| rule | 說明 |
|------|------|
| `visual_hierarchy` | 視覺層級要清楚 |
| `whitespace` | 保留足夠留白 |
| `balance` | 版面要平衡 |
| `color_harmony` | 顏色要和諧 |
| `readability` | 文字可讀性 |

> **實作教訓：** LLM 會自創 `"minimalism"` / `"modern_style"` 等 rule name，Prompt 裡用 ATTENTION 警告這些屬於 `style_keywords`（free-form），不是 `soft_constraints`（closed enum）。

---

### 設計方向

| 欄位 | 類型 | 說明 | 來源 |
|------|------|------|------|
| `style_keywords` | `List[str]` | 風格關鍵字清單 | LLM 從使用者描述萃取，例：`["橘色調", "夏日", "促銷", "活潑"]` |
| `language` | `Optional[str]` | 版面文字語言（BCP-47） | LLM 從文字素材偵測，例：`"zh-TW"` |
| `composition` | `Optional[CompositionDirective]` | 構圖指令（Step 62 新增） | 由 Composition Director 填入，Analyst 輸出時為 `null` |

---

### inferred_fields 標記

Agent 1 需要在輸出中標記哪些欄位是使用者明確說的、哪些是 LLM 自己推理補上的。

```json
"inferred_fields": {
  "canvas.width": true,
  "canvas.height": true,
  "canvas.background_color": true,
  "elements.bg_1.semantic_type": true
}
```

這個標記的用途：Agent 4 回饋重跑時，Agent 1 知道要調整的是推理補上的部分（`true`），而不是使用者的明確要求（`false` 或不存在）。

---

## LLM 後處理

LLM 產出 DesignSpec JSON 後，在回傳給下游前會依序執行兩個 Python 後處理步驟：

### 1. inject_photo_size_prior（Step 60）

針對「尺寸膽怯」問題的 GT 校準修正。實驗發現設計師 GT 的照片 area_ratio p50 = 0.213，但 Generator 候選通常只有 0.083–0.111。

機制：對每個 `product_image` 元素，如果 LLM 沒有主動產出 `size_preference` hard constraint，則自動追加一筆 `{"rule": "size_preference", "targets": [...], "params": {"hint": "photo-prominent"}}`。這樣尺寸下限就透過 `hard_constraints` 通道（Generator prompt 裡最強的約束通道）+ Quality Checker 程式化驗證雙重保障。

### 2. AssetAnalyzer.run(spec)

零 LLM 的 Python 查表工具，對每個 element 填入：

- **`importance`**（1–5）：直接從 `semantic_type` 查 `IMPORTANCE_TABLE`。表格反映典型海報/行銷素材的視覺層級（title/CTA/product_image = 5，background_image = 1）。
- **`semantic_relevance`**（0.0–1.0）：預期由 CLIP cosine similarity（element embedding vs style_keywords text embedding）計算。**目前為常數 stub = 0.5**，待 CLIP Embedder 接上後只需改 `_compute_semantic_relevance` 一個方法。

工具特性：
- **冪等**：已有值的欄位預設不覆蓋，除非傳 `override=True`
- **Fail-fast**：建構時驗證 `IMPORTANCE_TABLE` 是否覆蓋全部 12 個 `SemanticType`，缺任何一個就 raise

填完後呼叫 `spec.assert_enriched()` 確認所有 element 的 `importance` 和 `semantic_relevance` 皆不為 `None`，否則 raise。

---

## 錯誤處理與重試

`AnalyzeBrief.run()` 內建 **MAX_RETRIES = 3** 的重試機制：

1. 組裝 prompt → 呼叫 `self.llm.aask(prompt)`
2. 嘗試用 `CodeParser.parse_code(lang="json")` 剝離 markdown code fence
3. 用 `DesignSpec.model_validate_json(text)` 做 Pydantic 完整驗證
4. 如果 `ValueError` 或 `ValidationError` → log warning → 重新呼叫 LLM（同 prompt）
5. 三次都失敗 → raise `ValueError` 終止 pipeline

> 目前重試是 blind retry（同一個 prompt 重送），未來可能把上一次的 validation error 注入 prompt 讓重試 error-aware。

---

## 完整輸出範例

使用者輸入：「設計一張夏日促銷海報，整體橘色調，Logo 放右上角，標題要很顯眼」
素材：bg.jpg（背景圖）、logo.png（Logo）、文字「夏日限定 5 折起」

```json
{
  "canvas": {
    "width": 1080,
    "height": 1920,
    "background_asset_ref": "bg.jpg",
    "background_embedding_key": null,
    "background_color": null
  },
  "elements": [
    {
      "id": "bg_1",
      "semantic_type": "background_image",
      "visual_type": "image",
      "content": null,
      "asset_ref": "bg.jpg",
      "embedding_key": null,
      "inferred": false
    },
    {
      "id": "headline_1",
      "semantic_type": "title",
      "visual_type": "text",
      "content": "夏日限定 5 折起",
      "asset_ref": null,
      "embedding_key": null,
      "inferred": false
    },
    {
      "id": "logo_1",
      "semantic_type": "logo",
      "visual_type": "image",
      "content": null,
      "asset_ref": "logo.png",
      "embedding_key": null,
      "inferred": false
    }
  ],
  "hard_constraints": [
    {
      "rule": "position_preference",
      "targets": ["logo_1"],
      "params": {"hint": "top_right"}
    },
    {
      "rule": "size_preference",
      "targets": ["headline_1"],
      "params": {"hint": "prominent"}
    },
    {
      "rule": "z_order",
      "targets": ["headline_1"],
      "params": {"hint": "above_background"}
    }
  ],
  "soft_constraints": [
    {"rule": "color_harmony", "weight": 0.9, "params": {}},
    {"rule": "visual_hierarchy", "weight": 1.0, "params": {}}
  ],
  "style_keywords": ["橙色調", "夏日", "促銷", "活潑"],
  "language": "zh-TW",
  "inferred_fields": {
    "canvas.width": true,
    "canvas.height": true,
    "elements.bg_1.semantic_type": true
  }
}
```

> **注意：** 上面是 LLM 原始輸出。經 AssetAnalyzer 後處理後，每個 element 會多出 `"importance": N` 和 `"semantic_relevance": 0.5`（stub 值）。

---

## PROMPT_TEMPLATE 結構

以下是 `actions/analyze_brief.py` 中實際使用的 prompt 模板（已簡化排版，完整版見原始碼）。與最初規劃相比，實作中新增了多個 ATTENTION 區塊來防止 LLM 幻覺。

```
Role: You are a professional graphic design analyst.
Your goal is to parse a user's design request and asset list,
and output a structured Design Spec JSON for downstream layout agents.

# Context
User brief: {user_brief}
Asset list (each item has asset_ref or content, and embedding_key): {asset_list}
Previous feedback from Aesthetic Judge (if any): {feedback}

# Constraint extraction rules
- Descriptions with clear geometric meaning -> hard_constraints
  Supported rules: position_preference / no_overlap / z_order / size_preference
  ATTENTION: params must be semantic hints, NOT pixel coordinates.
  ATTENTION: position_preference params.hint 必須為九宮格之一：
    top_left | top_center | top_right | middle_left | center |
    middle_right | bottom_left | bottom_center | bottom_right
    （禁止自創 "below_title" / "above_logo" 等相對 hint）
  For z_order: hint 必須為 "above_background"
- Style/feeling -> style_keywords (free-form)
- Soft preferences -> soft_constraints
  ATTENTION: rule 必須為以下五值之一：
    visual_hierarchy | whitespace | balance | color_harmony | readability
    （"minimalism" / "modern_style" 屬於 style_keywords，不是 soft_constraints）

# Inference rules
- Canvas 尺寸未指定 → 從用途推理
- semantic_type 必須為 12 值 closed enum 之一：
  title | subtitle | body_text | caption | logo | product_image |
  background_image | decorative_image | icon | cta | pricetag | other
  （禁止 "headline" / "header" / "tagline" → 用 "title" 或 "subtitle"）
- visual_type 必須為 "image" 或 "text"
- 推理的欄位在 inferred_fields 標 true
- 有 feedback 時只調整推理欄位，不覆蓋使用者明確要求

# Background color inference (canvas.background_color)
ATTENTION: 三元素設計放純白畫布 → visual_coherence ~17/25 plateau。
- 有背景圖 → 設 background_asset_ref，background_color = null
- 無背景圖且使用者未要求純白 → 推理愉悅色 hex，匹配 style_keywords
  （附色票建議：warm→"#F5E6D3"、cool→"#E8F1F8"、vibrant→"#FFE5B4" 等）
- 使用者明確要白 → 可出 "#FFFFFF"，但 inferred_fields 標 false

# Underlay assets (asset filename heuristic)
ATTENTION: asset_ref 以 _underlay.png 結尾的素材 → 已被 pipeline 分類為
可放置的裝飾形狀（低色彩複雜度/透明邊緣），設計用來墊在文字或產品圖下方。
- 必須標 semantic_type: "decorative_image"
- 不可標 background_image / product_image / logo / icon
- 可搭配 z_order hard_constraint hint "above_background"

# Format example
{format_example}

# Instruction
ATTENTION: Do NOT output geometry (left/top/width/height/angle/z_index).
ATTENTION: Do NOT output importance, text_hints, or image_hints.
ATTENTION: embedding_key must always be null.
ATTENTION: hard_constraints params must use semantic hints, not pixel values.
Output carefully referenced "format example" in JSON format, nothing else.
```

### PROMPT_TEMPLATE 各段設計原理

**`# Constraint extraction rules` 的 ATTENTION 行**
經實驗迭代加入的防幻覺機制。`position_preference` 的九宮格 closed enum 是因為 Quality Checker 只認這 9 個值，LLM 自創的相對位置 hint 會讓整條 pipeline hard fail。`soft_constraints` 的五值限制同理。

**`# Background color inference`**
Step 7 加入。附帶色票建議是為了給 LLM 一個錨定範圍，避免它產出不協調的顏色。色票按 style_keywords 分群（warm/cool/vibrant/dark/nature），LLM 可以選但不必照抄。

**`# Underlay assets`**
Step 27 加入。Crello dataset 的 type 0 元素中有大量 underlay（幾何形狀底色塊），pipeline 用檔名後綴 `_underlay.png` 做啟發式分類，Prompt 教 LLM 把這類素材正確標為 `decorative_image`。

**`# Instruction` 最後一行 `Output ... in JSON format, nothing else.`**
禁止 LLM 加任何前言或說明。後端直接用 `DesignSpec.model_validate_json()` 解析，多一個字就會 parse fail（有 CodeParser 做 code fence 剝離作為保險）。

---

## 回饋迴路

```
UserRequirement ──► AnalystRole ──► DesignSpec
                         ▲
                         │ RetryAnalyst Message
                         │ (carries RetryPayload)
                         │
              IterationStateRole
                    ▲
                    │ AestheticFeedback
                    │ (target = "analyst")
              AestheticJudgeRole
```

1. Agent 4（Aesthetic Judge）判定 `reject` 且 `feedback_target = "analyst"` 時，feedback 傳給 IterationStateRole
2. IterationStateRole 發布 `RetryAnalyst` Message，payload 包含 `AestheticFeedback` + 迭代輪次
3. AnalystRole 收到後用 `_find_layout_brief()` 從 env history 回溯原始 `LayoutBrief`
4. 拿原始 `user_brief` + `asset_list` + 新的 `feedback` 重新呼叫 LLM
5. 產出新的 DesignSpec → AssetAnalyzer → 發布給下游

> **設計決策：** 重跑是「從頭重建 DesignSpec」而非「局部 patch」。因為 LLM prompt 已包含 inference rules（只調整推理欄位、不動使用者明確要求），重建比 patch 更穩健且不需額外維護 diff 邏輯。

---
