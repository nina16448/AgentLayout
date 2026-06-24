# Agent 2：Asset Planner 設計規格

---

## 職責說明

Asset Planner 的工作是分析前景素材之間的語意關係，把相關的元素分組，輸出一棵 Layout Tree。

**Asset Planner 只負責「分析素材關係、建立群組結構」，不決定任何座標或視覺屬性。**

Layout Generator 拿到這棵樹之後，知道哪些元素要放在一起，哪些元素屬於同一個視覺群組。

---

## 設計參考

Layout Tree 的概念參考自 PosterO（CVPR 2025）的 Hierarchical Node Representation。

與 PosterO 的差異：
- PosterO 的樹結構是從資料集訓練出來的，需要大量標注資料
- 本系統用 LLM（Asset Planner）直接推理出樹的結構，不需要訓練

---

## 實作架構

Asset Planner 遵循 MetaGPT 的 Role → Action 結構：

```
AssetPlannerRole (roles/asset_planner.py)
  ├── watches: AnalyzeBrief (接收 Analyst 產出的 enriched DesignSpec)
  └── action: PlanAssets     (actions/plan_assets.py)
                └── LLM 呼叫 → JSON 解析 → LayoutTree
                └── 兩層驗證：Pydantic schema + _validate_against_spec
```

### 相關檔案

| 層級 | 檔案 | 說明 |
|------|------|------|
| Role | `roles/asset_planner.py` | MetaGPT Role 殼：從 Message 取 DesignSpec、呼叫 PlanAssets Action |
| Action | `actions/plan_assets.py` | Prompt 模板 + LLM 呼叫 + JSON 解析 + 雙層驗證 + 重試邏輯 |
| Action | `actions/compose_sketch.py` | Composition Director（Step 62 新增，見下方獨立章節） |
| Tool | `tools/composition_templates.py` | GT 校準的構圖模板庫（供 ComposeSketch 使用） |

---

## 輸入

| 輸入 | 類型 | 說明 | 來源 |
|------|------|------|------|
| `spec` | `DesignSpec` | 含所有元素的 id、semantic_type、importance、semantic_relevance | Agent 1 輸出 + AssetAnalyzer 填入 |

**前置條件：** `PlanAssets.run()` 進入後立即呼叫 `spec.assert_enriched()`，確認每個 element 的 `importance` 和 `semantic_relevance` 皆已填入，否則 raise。

**Prompt 只餵前景元素：** `_build_prompt()` 呼叫 `spec.foreground_elements()` 過濾掉 `background_image`，只把前景元素的 `id / semantic_type / importance / semantic_relevance` 四欄 JSON 送入 LLM。

---

## 輸出：Layout Tree

### Schema 定義（schema.py）

```python
class LayoutTreeNode(BaseModel):
    model_config = ConfigDict(extra="forbid")  # 只允許 id + children
    id: str
    children: List[LayoutTreeNode] = []

class LayoutTree(BaseModel):
    root: LayoutTreeNode  # root.id 必須為 "root"（Pydantic validator 強制）
```

### 結構規則

- 節點只有兩個欄位：`id` 和 `children`（`extra="forbid"` 保證 LLM 多輸出的欄位直接 parse fail）
- 所有節點都是**真實素材節點**，id 沿用 Design Spec 的 id
- 沒有虛擬群組節點，樹的結構本身就表達語意關係
- 根節點固定為虛擬節點 `root`（唯一的虛擬節點）
- **背景圖不放進樹裡**，Layout Tree 只描述前景元素的關係
- **decorative_image（underlay）必須放進樹裡**——它們是前景堆疊層（在背景之上、文字之下），不是背景本身
- 樹可以有多層深度，不限於兩層

### 父子關係的語意與建樹邏輯

**語意關係為主要判斷依據：**
- 「這個元素在說明、補充哪個元素」→ 前者是後者的子節點
- 例如：標題在說明產品圖 → `headline_1` 是 `product_img_1` 的子節點
- 例如：地點在補充日期 → `location_1` 是 `date_1` 的子節點

**importance 為輔助參考：**
- importance 高的元素傾向放在上層（靠近 root）
- importance 低的、附屬說明性的元素傾向放在下層
- 但語意關係優先，importance 不是硬性規則

**decorative_image（underlay）的處理：**
- 與它所支撐的文字/產品元素配對，作為 sibling 或 child
- 作為葉節點出現（`children: []`）

### 輸出範例

使用者素材：背景圖、產品圖、標題文字、價格標籤、活動日期、活動地點、Logo

```json
{
  "layout_tree": {
    "id": "root",
    "children": [
      {
        "id": "product_img_1",
        "children": [
          {
            "id": "headline_1",
            "children": [
              {"id": "price_1", "children": []}
            ]
          }
        ]
      },
      {
        "id": "date_1",
        "children": [
          {"id": "location_1", "children": []}
        ]
      },
      {"id": "logo_1", "children": []}
    ]
  }
}
```

> `bg_1` 不出現在樹裡，Layout Generator 從 Design Spec 的 `background_asset_ref` 取得背景圖資訊。

> **JSON key 轉換：** LLM 輸出的外層 key 是 `"layout_tree"`（配合 format example），`_parse_response()` 會把它 rename 為 `"root"` 後再餵入 `LayoutTree.model_validate()`。

---

## 驗證機制

PlanAssets 對 LLM 輸出做**兩層驗證**，任一層失敗都觸發重試：

### 第一層：Pydantic Schema 驗證

| 驗證項目 | 機制 |
|------|------|
| 根節點 id = "root" | `LayoutTree` 的 `@model_validator` |
| 節點只有 id + children | `LayoutTreeNode` 的 `extra="forbid"` |
| JSON 格式合法 | `json.loads()` + `model_validate()` |

### 第二層：語意對照驗證（`_validate_against_spec`）

| 驗證項目 | 說明 |
|------|------|
| 元素完整性 | Design Spec 的所有前景元素 id 都必須出現在樹裡，不多不少 |
| 無重複 | 每個 id 只能出現一次 |
| 無多餘 | 樹裡不能出現 Design Spec 沒有的 id |

> 孤立節點檢查是隱含的——樹的遞迴結構天然禁止孤立節點。

失敗時拋出 `_LayoutTreeValidationError`（自訂的 `ValueError` 子類），被重試迴圈捕獲。

---

## 錯誤處理與重試

`PlanAssets.run()` 內建 **MAX_RETRIES = 3** 的重試機制：

1. 組裝 prompt → 呼叫 `self.llm.aask(prompt)`
2. 用 `CodeParser.parse_code(lang="json")` 剝離 markdown code fence
3. JSON parse → unwrap `"layout_tree"` key → `LayoutTree.model_validate()`
4. `_validate_against_spec()` 語意對照
5. 任何 `ValueError` / `ValidationError` → log warning → 重試
6. 三次都失敗 → raise `ValueError` 終止 pipeline

---

## PROMPT_TEMPLATE 結構

以下是 `actions/plan_assets.py` 中實際使用的 prompt 模板：

```
Role: You are a graphic design content analyst.
Your goal is to analyze the semantic relationships between design elements
and organize them into a hierarchical Layout Tree.
The tree structure tells the layout generator which elements are related
and should be placed near each other.

# Context
Elements (id / semantic_type / importance / semantic_relevance):
{elements_summary}

# Tree building rules
- Parent-child = "this element describes or supplements its parent"
- Elements with no clear relationship → direct children of root
- importance score is a reference, semantic relationship takes priority
- Do NOT include background_image in the tree
- DO INCLUDE decorative_image (underlay) as leaves — they are foreground
  stacking layers above background, NOT background themselves
- Tree can have multiple depth levels

# Format example
{format_example}

# Instruction
ATTENTION: Every foreground element id must appear exactly once.
ATTENTION: Do NOT include background_image in the tree.
ATTENTION: decorative_image (underlay) MUST appear in the tree as leaves.
           Omitting them triggers a hard validation error.
ATTENTION: Do NOT create virtual group nodes — every node must be a real element id.
ATTENTION: Nodes only have two fields: "id" and "children".
ATTENTION: Leaf nodes must have an empty children list [].
ATTENTION: Semantic relationship takes priority over importance score.
Output carefully referenced "format example" in JSON format, nothing else.
```

### PROMPT_TEMPLATE 設計原理

**`# Context` 的 `{elements_summary}`**
每個前景元素的 `id / semantic_type / importance / semantic_relevance` 四欄 JSON。這些資訊足夠讓 LLM 推理元素之間的關係，不需要看使用者原始描述（user_brief 已由 Analyst 消化為結構化欄位）。

**decorative_image ATTENTION 行**
Step 27 加入 underlay 支援後發現，LLM 會把 `decorative_image` 當成背景圖省略掉，導致 `_validate_against_spec` 因 missing id 而 hard fail。兩條 ATTENTION 行（一條在 rules、一條在 instruction）雙重強調這些是前景元素、必須出現在樹裡。

**`extra="forbid"` 與 Prompt ATTENTION 的雙保險**
Prompt 說「Nodes only have two fields: id and children」，但 LLM 偶爾會加 `"type"` / `"importance"` 等多餘欄位。`extra="forbid"` 在 Pydantic 層硬擋，觸發重試。

---

## Composition Director（Step 62 新增）

### 背景

Step 61 的 GT 校準分析發現，設計師 GT 中 photo large+bleed 佔 45% 而 Generator 候選只有 0%，text-on-photo 佔 43% 而候選只有 4%。根本原因是 Generator 在一次 LLM 呼叫中同時決定 thumbnail 級的粗構圖和像素級的細節排布，系統性地選擇業餘構圖。

### 設計

仿照人類設計工作室的分工：**Art Director 先選粗構圖模板（哪種照片-文字關係、照片擺哪個格子、照片多大），然後 Layout Generator 在模板約束下執行像素排布。**

### 實作架構

```
ComposeSketch (actions/compose_sketch.py)
  ├── 輸入: DesignSpec + BackgroundAnalysis (optional)
  ├── 工具: composition_templates.py (GT 校準模板庫)
  └── 輸出: CompositionDirective → 寫入 spec.composition
```

**注意：ComposeSketch 目前只在 team.py（MetaGPT Role 路徑）中使用，pipeline.py（直呼 Action 路徑）中未整合。**

### 模板庫（composition_templates.py）

來源：Step 61 校準分析，N=1,902 筆 Crello 設計師佈局。

**照片+文字模板（8 個）：**

| template_id | relation | photo_cell | photo_size | gt_share |
|---|---|---|---|---|
| `hero-center-overlay` | text-on-photo | MC | large | 19.9% |
| `split-photo-left` | side-by-side | ML | medium | 5.4% |
| `split-photo-right` | side-by-side | MR | medium | 5.2% |
| `stacked-text-top` | stacked | MC | large | 3.2% |
| `hero-overlay-left-text` | text-on-photo | MC | large | 2.4% |
| `centered-mix` | centered-mix | MC | medium | 2.3% |
| `photo-bottom-anchor` | stacked | BC | small | 1.6% |
| `stacked-text-bottom` | stacked | MC | large | 1.5% |

**純文字模板（3 個）：**

| template_id | text_cell | gt_share |
|---|---|---|
| `text-centered` | MC | 62.5% |
| `text-column-right` | MR | 12.5% |
| `text-column-left` | ML | 10.6% |

### 模板選擇邏輯

1. `template_menu(has_photo, aspect)` 根據畫布有無照片和長寬比篩選模板
2. 照片模板按 `(relation_prior, gt_share)` 降序排列（relation_prior 來自 GT 條件機率，按 landscape/portrait/square 分群）
3. LLM 從排序後的模板列表中選一個，可 override photo_size bucket
4. **三次 parse 失敗 → fallback 到 gt_share 最高的模板**（pipeline 不能死在構圖階段）

### GT 條件機率（relation_prior）

| aspect | text-on-photo | stacked | side-by-side | centered-mix |
|---|---|---|---|---|
| landscape (n=518) | 0.45 | 0.08 | 0.42 | 0.05 |
| portrait (n=374) | 0.38 | 0.44 | 0.10 | 0.08 |
| square (n=276) | 0.47 | 0.25 | 0.24 | 0.03 |

### CompositionDirective Schema

```python
class CompositionDirective(BaseModel):
    template_id: str
    relation: Optional[str]      # text-on-photo | stacked | side-by-side | centered-mix
    photo_cell: Optional[str]    # 3x3 grid cell, e.g. "MC"
    photo_size: Optional[str]    # small | medium | large | bleed
    text_cell: Optional[str]     # 3x3 grid cell
    rationale: Optional[str]     # 一句話選擇理由
```

寫入 `spec.composition` 後，下游 Layout Generator prompt 末尾會附加 ATTENTION 區塊，把模板的抽象描述轉換為像素級的數學約束。

### 視覺輸入

如果 LLM 支援圖片輸入（`self.llm.support_image_input()`），會把背景圖的 base64 也一起送入，讓 Art Director 看到背景再選構圖。

---
