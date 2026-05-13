# Agent 1：Analyst 設計規格

---

## 職責說明

Agent 1 是整個 pipeline 的入口。它的工作是把使用者的自然語言描述和素材，整理成一份後續 Agent 都能解析的結構化 Design Spec JSON。

**Agent 1 只負責「整理使用者說了什麼」，不做視覺判斷。**

---

## 輸入

### 冷啟動（第一次執行）

| 輸入 | 說明 |
|------|------|
| 使用者自然語言描述 | 例：「設計一張夏日促銷海報，整體橘色調，Logo 放右上角，標題要很顯眼」 |
| 素材清單 | 每個素材包含：檔案路徑（圖片）或文字字串（文字）+ CLIP 前處理產出的 embedding_key（圖片用 vision encoder、文字用 text encoder） |

### 被 Agent 4 回饋後重新執行

| 輸入 | 說明 |
|------|------|
| 使用者自然語言描述 | 同上，不變 |
| 素材清單 | 同上，不變 |
| Agent 4 feedback | 說明哪個維度不好、建議如何調整設計規格 |

> **注意：** Agent 1 收到的圖片和文字素材都已經是 embedding_key（索引鍵），不是原始資料。圖片用 vision encoder、文字用 text encoder，兩者都在同一個 CLIP 向量空間，CLIP 編碼由上游前處理完成。

---

## 輸出：Design Spec JSON

### Canvas（畫布）

| 欄位 | 說明 | 來源 |
|------|------|------|
| `width` | 畫布寬度（pixel） | LLM 從描述推理（例：「Instagram 貼文」→ 1080）|
| `height` | 畫布高度（pixel） | LLM 從描述推理 |
| `background_asset_ref` | 背景圖檔案路徑 | 使用者上傳 |
| `background_embedding_key` | 背景圖的 CLIP 向量索引 | CLIP 前處理填入（Agent 1 輸出時為 `null`）|

> `format` 欄位不需要，width/height 已足夠。LLM 直接從使用者描述推理出尺寸數值。

---

### Elements（元素清單）

每個素材（圖片或文字）對應一筆 element。

| 欄位 | 說明 | 來源 |
|------|------|------|
| `id` | 元素識別碼 | LLM 命名，例：`headline_1`、`logo_1`、`bg_1` |
| `semantic_type` | 語意角色 | LLM 從素材內容與描述推理 |
| `visual_type` | 渲染類型 | 從素材類型直接判斷（圖片→`image`，文字→`text`）|
| `content` | 文字內容（文字元素專用） | 使用者輸入 |
| `asset_ref` | 圖片檔路徑（圖片元素專用） | 使用者上傳 |
| `embedding_key` | CLIP 向量索引 | CLIP 前處理填入（Agent 1 輸出時為 `null`）|
| `inferred` | 這筆 element 的 semantic_type 是使用者明確說的還是 LLM 推理的 | Agent 1 自己標記 |

**`semantic_type` 可選值：**
`title` / `subtitle` / `body_text` / `caption` / `logo` / `product_image` / `background_image` / `decorative_image` / `icon` / `cta` / `pricetag` / `other`

**不屬於 Agent 1 的欄位（交給 Agent 2）：**
- `importance`（重要性分數）
- `text_hints`（字型、大小、顏色建議）
- `image_hints`（圖片描述、比例）

**不屬於 Agent 1 的欄位（交給 Agent 3）：**
- `left` / `top` / `width` / `height` / `angle` / `z_index`（幾何座標）

---

### Constraints（約束）

約束分兩類，分流邏輯如下：

- 使用者描述**有明確幾何意義** → `hard_constraints`（結構化物件）
- 使用者描述**是風格或感覺** → `style_keywords` 或 `soft_constraints`

#### hard_constraints

結構化物件，供 Agent 3 生成時遵守、Agent 4 驗證時參考。

格式：`{"rule": "...", "targets": ["元素id"], "params": {"hint": "..."}}`

**支援的 rule 類型：**

| rule | 說明 | 範例 |
|------|------|------|
| `position_preference` | 位置偏好 | `{"rule": "position_preference", "targets": ["logo_1"], "params": {"hint": "top_right"}}` |
| `no_overlap` | 不能與某元素重疊 | `{"rule": "no_overlap", "targets": ["headline_1", "bg_1"], "params": {}}` |
| `z_order` | 誰要疊在誰上面 | `{"rule": "z_order", "targets": ["headline_1"], "params": {"above": "bg_1"}}` |
| `size_preference` | 大小偏好 | `{"rule": "size_preference", "targets": ["headline_1"], "params": {"hint": "prominent"}}` |

> **重要：** `params` 裡的值是語意描述（`"top_right"`、`"prominent"`），不是固定座標。實際的像素範圍由 Agent 3 根據整體版面情境決定，Agent 4 驗證時也是驗證相對位置，不是死板的座標範圍。這樣設計是為了保持彈性，讓約束可以配合整體素材分布做調整。

#### soft_constraints

無法用程式驗證的偏好，供 Agent 3 生成時參考、Agent 4 評審時考量。

格式：`{"rule": "...", "weight": 0.0~1.0, "params": {}}`

| rule | 說明 |
|------|------|
| `visual_hierarchy` | 視覺層級要清楚 |
| `whitespace` | 保留足夠留白 |
| `balance` | 版面要平衡 |
| `color_harmony` | 顏色要和諧 |
| `readability` | 文字可讀性 |

---

### 設計方向

| 欄位 | 說明 | 來源 |
|------|------|------|
| `style_keywords` | 風格關鍵字清單 | LLM 從使用者描述萃取，例：`["橘色", "活潑", "促銷"]` |
| `language` | 版面文字語言 | LLM 從文字素材偵測，例：`"zh-TW"` |

> 「整體橘色」、「字要很大」、「感覺活潑」這類描述，無法被程式驗證，統一放進 `style_keywords`，讓 Agent 3 生成時參考、Agent 5 評審時考量。

---

### inferred_fields 標記

Agent 1 需要在輸出中標記哪些欄位是使用者明確說的、哪些是 LLM 自己推理補上的。

```json
"inferred_fields": {
  "canvas.width": false,       // 使用者明確說「Instagram 貼文」→ LLM 推理得出
  "canvas.height": false,
  "elements.bg_1.semantic_type": true,  // 使用者沒說，LLM 自己判斷
  "hard_constraints[0]": false  // 使用者明確說「Logo 放右上角」
}
```

這個標記的用途：Agent 4 回饋時，Agent 1 知道要調整的是推理補上的部分，而不是使用者的明確要求。

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
    "background_embedding_key": null
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
    }
  ],
  "soft_constraints": [
    {"rule": "color_harmony", "weight": 0.9, "params": {}},
    {"rule": "visual_hierarchy", "weight": 1.0, "params": {}}
  ],
  "style_keywords": ["橘色調", "夏日", "促銷", "活潑"],
  "language": "zh-TW",
  "inferred_fields": {
    "canvas.width": true,
    "canvas.height": true,
    "elements.bg_1.semantic_type": true
  }
}
```

---

## PROMPT_TEMPLATE 結構

```
Role: You are a professional graphic design analyst.
Your goal is to parse a user's design request and asset list,
and output a structured Design Spec JSON for downstream layout agents.

# Context
User brief: {user_brief}
Asset list (each item has asset_ref or content, and embedding_key): {asset_list}
Previous feedback from Aesthetic Judge (if any): {feedback}

# Constraint extraction rules
- Descriptions with clear geometric meaning → hard_constraints (structured object)
  Supported rules: position_preference / no_overlap / z_order / size_preference
  ATTENTION: params values must be semantic hints (e.g. "top_right"), NOT pixel coordinates.
- Style and feeling descriptions → style_keywords list
- Soft preferences → soft_constraints

# Inference rules
- If canvas size is not specified, infer from context (e.g. "poster" → 1080×1920).
- If semantic_type of an element is unclear, infer from asset content and user brief.
- Mark all inferred fields in inferred_fields with true.
- If feedback is provided, adjust only the inferred fields, never override explicit user requirements.

# Format example
{format_example}

# Instruction
ATTENTION: Do NOT output any geometry (left/top/width/height/angle/z_index).
ATTENTION: Do NOT output importance, text_hints, or image_hints — these belong to Agent 2.
ATTENTION: embedding_key must always be null.
ATTENTION: hard_constraints params must use semantic hints, not pixel values.
Output carefully referenced "format example" in JSON format, nothing else.
```

### PROMPT_TEMPLATE 各段說明

**`Role:`**
告訴 LLM 它的身份是「版面設計規格分析師」，目標是解析使用者需求並輸出 Design Spec JSON。這行會影響 LLM 回答的風格和角度，讓它用設計師的視角思考，而不是隨意發揮。

**`# Context`**
把這次執行需要的所有資料餵進來，共三個變數：
- `{user_brief}`：使用者輸入的自然語言描述，原文放入
- `{asset_list}`：素材清單，每個素材包含檔案路徑或文字內容，以及 CLIP 已產出的 `embedding_key`
- `{feedback}`：Agent 4 傳回來的回饋，第一次執行時這個變數是空的

**`# Constraint extraction rules`**
告訴 LLM 如何把使用者的描述轉換成約束。分流邏輯是：
- 有幾何意義的描述（「Logo 放右上角」）→ 轉成結構化 `hard_constraints`，`params` 裡只能用語意描述（`"top_right"`），不能是像素座標
- 風格感覺的描述（「整體橘色」「活潑」）→ 放進 `style_keywords`
- 模糊的偏好（「版面要平衡」）→ 放進 `soft_constraints`

**`# Inference rules`**
告訴 LLM 當使用者沒說清楚時要怎麼辦：
- 沒指定畫布尺寸 → 從用途推理（例如「海報」推出 1080×1920）
- 素材的語意角色不清楚 → 從素材內容和描述自己判斷
- 所有推理補上的欄位都要在 `inferred_fields` 標記為 `true`
- 如果這次有收到 Agent 4 的 feedback → 只調整推理的部分，絕對不能覆蓋使用者明確說的東西

**`# Format example`**
放入一個完整的 Design Spec JSON 範例（`{format_example}` 是佔位符，執行時會填入前面「完整輸出範例」那段）。這是整個 Prompt 最重要的部分，LLM 看了範例之後會照著格式輸出，比用文字解釋規格還有效。

**`# Instruction` 的 ATTENTION 行**
這幾行是用全大寫強調「最容易犯的錯」，一條一條列出來：
- 不可以輸出任何幾何座標，那是 Agent 3 的工作
- 不可以輸出 `importance`、`text_hints`、`image_hints`，那是 Agent 2 的工作
- `embedding_key` 一律輸出 `null`，由 CLIP 前處理填入
- `hard_constraints` 的 `params` 只能用語意描述，不能是像素值

**最後一行 `Output carefully referenced "format example" in JSON format, nothing else.`**
這行是在告訴 LLM：不要加任何說明、不要說「好的我來幫你」，直接輸出 JSON。因為後面的程式會用 `json.parse()` 解析輸出，多一個字都會讓解析失敗。

---

*最後更新：2026/05/27　討論範圍：Agent 1 設計規格 v1*