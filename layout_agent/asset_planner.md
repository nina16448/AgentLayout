# Asset Planner 設計規格

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

## 輸入

| 輸入 | 說明 | 來源 |
|------|------|------|
| Design Spec JSON | 含所有元素的 id、semantic_type、importance、semantic_relevance | Agent 1 輸出 + Python 前處理 |

---

## 輸出：Layout Tree

### 結構規則

- 節點只有兩個欄位：`id` 和 `children`
- 所有節點都是**真實素材節點**，id 沿用 Design Spec 的 id
- 沒有虛擬群組節點，樹的結構本身就表達語意關係
- 根節點固定為虛擬節點 `root`（唯一的虛擬節點）
- **背景圖不放進樹裡**，Layout Tree 只描述前景元素的關係
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

---

## Python 驗證

Asset Planner 輸出 Layout Tree 之後，Python 立即驗證合理性，失敗則重試。

| 驗證項目 | 說明 |
|------|------|
| 元素完整性 | Design Spec 的所有前景元素 id 都必須出現在樹裡，不多不少 |
| 無重複 | 每個 id 只能出現一次 |
| 無孤立節點 | 每個節點都必須連接在樹上 |
| 根節點唯一 | 只能有一個 root |

驗證失敗 → 直接重試，讓 Asset Planner 重新生成。

---

## PROMPT_TEMPLATE 結構

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
- Parent-child relationship means: "this element describes or supplements its parent"
  (e.g. a title describing a product image → title is child of product_img)
  (e.g. a location supplementing a date → location is child of date)
- Elements with no clear relationship to others → direct children of root
- importance score is a reference: higher importance tends to be closer to root,
  but semantic relationship takes priority over importance
- Do NOT include background image (semantic_type: background_image) in the tree
- The tree can have multiple levels of depth — do not limit to two levels

# Format example
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

# Instruction
ATTENTION: Every foreground element id from the Design Spec must appear exactly once.
ATTENTION: Do NOT include background image (semantic_type: background_image) in the tree.
ATTENTION: Do NOT create virtual group nodes — every node must be a real element id.
ATTENTION: Nodes only have two fields: "id" and "children". No other fields allowed.
ATTENTION: Leaf nodes must have an empty children list [].
ATTENTION: Semantic relationship takes priority over importance score.
Output carefully referenced "format example" in JSON format, nothing else.
```

---

### PROMPT_TEMPLATE 各段說明

**`Role:`**
告訴 LLM 它是一位內容分析師，工作是分析元素之間的語意關係並建立 Layout Tree。強調樹的結構要告訴 Layout Generator 哪些元素應該放在一起。

**`# Context`**
把每個元素的 id、semantic_type、importance、semantic_relevance 整理成清單餵給 LLM。這些資訊足夠讓 LLM 推理元素之間的關係，不需要看使用者原始描述。

**`# Tree building rules`**
告訴 LLM 建樹的判斷邏輯：
- 父子關係代表「說明或補充」的語意關係
- importance 只是參考，語意關係優先
- 背景圖不放進樹裡
- 樹可以有多層深度

**`# Format example`**
完整的多層 Layout Tree JSON 範例，讓 LLM 照著輸出正確的格式。

**`# Instruction` 的 ATTENTION 行**
- 每個前景元素 id 必須恰好出現一次
- 背景圖不放進樹裡
- 不能創建虛擬群組節點，每個節點都必須是真實素材 id
- 節點只能有 `id` 和 `children` 兩個欄位
- 葉節點的 `children` 必須是空陣列 `[]`
- 語意關係優先於 importance 分數

**最後一行**
直接輸出 JSON，不加說明文字。

---

*最後更新：2026/04/23　討論範圍：Asset Planner 設計規格 v1*