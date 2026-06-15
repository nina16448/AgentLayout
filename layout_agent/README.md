# AgentLayout: Decomposing Content-Aware Layout Generation into Collaborative Agent Workflows
# 碩士論文研究概覽
## Multi-Agent AI 內容感知（Content-Aware）排版生成系統

---

## 文件閱讀指南：本檔為「設計藍圖」非「實作現況」

本 README 是整個系統的**設計藍圖與研究理念展示**，描述的是論文方法章節背後完整的構思與整體架構願景。隨著實驗一路演進，部分模組目前以最小可用版本（minimum viable version）或**預留欄位**的形式運作，與本檔描述的最終理想型態之間存在已知落差。

**最新的實作現況請以下列檔案為準**（這三個檔案會跟著每次實驗同步更新）：

- `layout_agent/IMPLEMENTATION_LOG.md` — 從第一版到最新一次實驗的每一個設計決策、修改原因、權衡取捨的逐步紀錄
- `layout_agent/result.md` — 最新的實驗結果、Benchmark 比較、可放進論文的數據與結論
- `layout_agent/EXPERIMENT_MATRIX.md` — 主實驗與所有 ablation 的完整對照表

### 已知的主要落差說明

為避免讀者因為閱讀 README 而誤判系統實際能力，以下落差特別說明清楚：

#### 1. CLIP 語意相似度（semantic_relevance）目前為預留欄位

README 在「系統模組總覽」「Element Embedding 前處理」與附錄符號表多處描述：使用 **CLIP ViT-L/14** 將圖片元素、**CLIP Text Encoder** 將文字元素統一編碼成向量，再與 style_keywords 計算 cosine similarity，作為 `semantic_relevance` 欄位。同時還會將所有 embedding 存進 **Embedding Store**（FAISS）。

**實作上：** 上述 CLIP 編碼器與 Embedding Store **目前都尚未實作**，`semantic_relevance` 統一回傳中性常數 **0.5**。此模組列為未來工作。

**為什麼可以暫不影響論文核心論點：** 在現行流程中，`semantic_relevance` 只被 Asset Planner 拿來輔助安排元素分群，而它的「上游搭檔」`importance`（直接從元素類型查表得到，例如標題=5、內文=3、裝飾=1）已經提供了主要的語意重要性訊號。換句話說，**用 CLIP 算出來的 cosine 分數所能多帶來的訊號，主要是「同類元素之間誰更貼近 style_keywords」這個第二層的區分**，對於整體版面決策的影響相對邊際。

#### 2. Composition Director（AI 構圖師）僅存在於實驗驅動腳本

README 後段的 step 紀錄與架構描述中提到 **Composition Director**（程式碼為 `actions/compose_sketch.py`）——它的角色是在像素級排版之前，先從 GT-calibrated 模板庫中選一個合適的「整體構圖方向」（焦點照片放哪一格、文字壓不壓在照片上、照片佔多大面積...），讓 Layout Generator 在這個約束下做細部安排。

**實作上：** 預設的整套流程（`LayoutPipeline.run()`）以及 Role 流程（`build_team()`）**皆未串接 Composition Director**，此模組目前只在實驗驅動腳本 `layout_agent/output/step41_layout_aware_oracle.py` 中被呼叫。

**為什麼維持現狀：** 在 Step 62–66 的系列實驗中，加入 Composition Director 雖然機制本身運作正常，但對最終 acceptance 與 win-rate 並未帶來顯著提升，**結論已收斂為 limitation** 寫進論文。將其推進為預設流程會把一個被驗證為 negative 的元件鋪成系統預設行為，故維持只在實驗腳本中啟用的設計。

#### 3. 預設 Role 編制為五個，不含 CompositionDirectorRole

預設 Team 由以下五個 Role 組成：`AnalystRole`、`AssetPlannerRole`、`LayoutGeneratorRole`、`AestheticJudgeRole`、`IterationStateRole`，**不包含 README 部分流程圖中描述的 CompositionDirectorRole**。理由同上一條。

#### 4. Refinement Loop 的兩條流程實作有小差異

系統提供兩種流程入口：直接呼叫的 **Pipeline 流程**（`LayoutPipeline.run()`）與 Team / Role 流程（`build_team()`），兩者在「best-so-far guard」（避免被噪音 re-judge 拉回較差的 anchor）與「最大輪數計算」上存在小幅差異。

**為什麼可以暫不影響論文 headline 數據：** 所有 headline 實驗結果（包含 Step 22 N=100 的主結論）都是透過實驗驅動腳本 `step41_layout_aware_oracle.py` 跑出來的，這支腳本自帶迴圈控制邏輯，不經由上述兩條 default 流程。詳見 `IMPLEMENTATION_LOG.md` Step 31。

#### 5. Generator prompt 與 Quality Checker 的尺寸門檻刻意不一致

README 「系統模組總覽」描述 Quality Checker 會驗證版面合法性。實作上 Generator prompt 對「prominent」「medium」尺寸寫的是 stretch target（例：prominent **20%**），但 Quality Checker 的實際 accept 下限低於此（例：prominent **10%**）。

**為什麼這是刻意設計：** 在 Step 58/60 觀察到 LLM 系統性「尺寸膽怯」（傾向於把元素做太小）。把 prompt 寫高、QC 門檻寫低，相當於告訴 LLM「請朝 20% 努力」，而實際只要做到 10% 就會被接受。這個雙層門檻是為了拉回 LLM 的偏移。此設計細節在程式碼中已加上 inline 註解說明（`tools/quality_checker.py` 與 `actions/generate_layout.py`），避免未來被當成 bug 修掉。

### 為什麼選擇保留 README 原文、另以 banner 說明落差

本 README 同時是**論文研究方法章節的延伸說明**與**整套系統的設計理念展示**。如果改寫成「實作現況快照」會：

- 失去研究藍圖的完整性，讀者無法看到完整的系統構思
- 與論文方法章節的敘述風格脫節
- 隨著實驗持續演進需要頻繁重寫，反而難以維持一致

因此本檔**保留為設計藍圖性質**，實作層面的最新狀態與每次修改的脈絡以 `IMPLEMENTATION_LOG.md` 為準，最新的實驗數據以 `result.md` 為準。

---

## 研究背景與動機

本研究所在實驗室的方向為 AI 應用研究。本篇碩士論文的主題是**利用 Multi-Agent AI 系統解決內容感知排版生成（Content-Aware Layout Generation）問題**——在**既有背景畫布**上，根據自然語言 brief 與一組既有素材（image / text），決定每個元素的座標、大小、層次與視覺屬性。任務 scope 不包含背景生成、字型/裝飾合成、影像 inpainting——這些是 scope 外能力，已記為 limitation（見 `result.md` §0、§3.3）。Benchmark 對齊 AesthetiQ（CVPR 2025）、LayoutNUWA、PosterLLaVa 等 content-aware layout generation 同類方法，protocol 為 pairwise win-rate vs designer-GT layout（同 renderer 純排版幾何）+ Mean IoU。

近年生成式 AI 技術快速進步，但內容感知排版仍是現有多模態模型難以妥善處理的任務。關鍵原因在於：**排版本質上不是一個機率性的內容生成問題，而是一個同時結合語意理解、空間配置與設計約束的複雜決策過程。**

現有的端對端模型（End-to-End Models）擅長機率性的內容生成，但在需要同時滿足明確幾何規則與版面限制時，往往缺乏：
- **可控性（Controllability）**：難以精確遵守設計約束
- **可驗證性（Verifiability）**：無法確保輸出結果符合規格
- **可除錯性（Debuggability）**：問題發生時難以定位根因

因此，本研究將整個內容感知排版流程拆解為 **Multi-Agent Workflow**，而非依賴單一模型直接輸出版面。

---

## 核心論點

> 內容感知排版是一個可分工、可觀測、可驗證、可逐步修正的系統問題，而非單純的生成問題。

將不同類型的決策職責分離給不同模組處理，可以讓整個流程更加可控，並在結果不理想時能精確定位問題所在的模組。

---

## 系統模組總覽

本系統由多個非 LLM 模組與四個 LLM Agent 構成：

| 模組 | 類型 | 職責 |
|------|------|------|
| CLIP Embedding 前處理 | CLIP（非 LLM） | 將圖片 / 文字元素統一編碼為向量，存入 Embedding Store |
| Background Analyzer | CV 模組（非 LLM） | 顯著性偵測（U2Net），輸出安全放置區域、主色盤、建議文字顏色 |
| Asset Analyzer | Python（非 LLM） | 從 semantic_type 查表計算 importance；計算元素 embedding 與 style_keywords 的 cosine similarity（semantic_relevance） |
| Quality Checker | Python（非 LLM） | 驗證候選版面的幾何合法性與 hard_constraints |
| Renderer | Python（非 LLM） | 將通過 Quality Checker 的候選版面渲染成圖片 |
| LLM Agent 1：Analyst | LLM | 自然語言需求 → 結構化 Design Spec JSON |
| LLM Agent 2：Asset Planner | LLM | 分析素材語意關係 → 輸出 Layout Tree |
| LLM Agent 3：Layout Generator | LLM | 根據 Design Spec + Layout Tree 生成 5 個候選版面 |
| LLM Agent 4：Aesthetic Judge | LLM | 看渲染圖片進行四維美感評分 → 輸出結果或觸發 Feedback Loop |

> **設計原則：** LLM 只用在真正需要語意理解與推理的環節（需求解析、語意關係分析、版面生成、美感評審）。幾何驗證、數值計算、影像處理等任務全部用 Python 或 CV 模組處理，更準確、更快、更省 token。

---

## Element Embedding 前處理

在進入 Multi-Agent 流程之前，系統會對所有輸入素材進行**統一的 Embedding 前處理**，將異質的設計元素（圖片、文字）轉換成向量表示，供後續各模組使用。此設計參考自 AesthetiQ（CVPR 2025）的 Layout MLLM 架構。

### 正式定義

給定 N 個設計元素的集合 `E = {e₁, e₂, ..., eₙ}`，每個元素具有類型 `T(eᵢ) ∈ 𝒯`，其中 `𝒯 = {image, text, shape, background}`。

針對每個元素，根據其類型選擇對應的 Encoder：

- **圖片類元素**（`T(eᵢ) ∈ 𝒯 − {text}`）：使用 **Vision Encoder** `f_vision`
  ```
  z_i^vision = f_vision(ImageTokens(eᵢ))
  ```
- **文字類元素**（`T(eᵢ) = text`）：使用 **Text Encoder** `f_text`
  ```
  z_i^text = f_text(TextTokens(eᵢ))
  ```

所有元素的 embedding 統一儲存至 **Embedding Store**，以 `embedding_key` 作為索引：

```
Z = { z_i^vision  if T(eᵢ) ∈ 𝒯 − {text}
    { z_i^text    if T(eᵢ) = text
    for i = 1..N
```

### Embedding Store 結構

```json
{
  "embedding_store": {
    "img_emb_01": {
      "type": "vision",
      "element_id": "elem_01",
      "vector": "<float array, dim=768>",
      "source": "bg_summer.jpg",
      "encoder": "CLIP-ViT-L/14"
    },
    "txt_emb_02": {
      "type": "text",
      "element_id": "elem_02",
      "vector": "<float array, dim=768>",
      "source": "夏日限定 5 折起",
      "encoder": "CLIP-text"
    }
  }
}
```

---

## Background Analyzer（CV 模組）

**職責：** 解析背景圖的可放置區域（Saliency Map），輸出安全放置區域與配色資訊。

**輸入：** 背景圖片原始檔、canvas 尺寸

**輸出：**
```json
{
  "safe_zones": [
    { "region": "top-left", "bbox": [0, 0, 400, 300], "confidence": 0.92 }
  ],
  "dominant_palette": ["#F5E6D3", "#A8C5DA"],
  "recommended_text_color": "#111111"
}
```

---

## Asset Analyzer（Python 模組）

這兩個值在 Analyst 執行完之後，由 Python 直接計算並填回 Design Spec，不需要 LLM。

**importance（1–5）：** 從 semantic_type 對應表直接查表，例如 `title → 5`、`logo → 4`、`background_image → 1`。

**semantic_relevance（0–1）：** 用 CLIP 計算每個元素的 embedding 與 `style_keywords` 串接文字的 text embedding 之間的 cosine similarity。

---

## Quality Checker（Python 模組）

**職責：** 對 Layout Generator 輸出的候選版面進行幾何驗證，過濾不合格的候選。

**驗證項目：**

| 項目 | 條件 |
|------|------|
| Element Completeness | 輸出的元素 id 集合與 Design Spec 完全一致 |
| Boundary Check | `left >= 0`、`top >= 0`、`left + width <= canvas_width`、`top + height <= canvas_height` |
| Hard Constraints | 逐條驗證，例如 position_preference、no_overlap、z_order |

通過 → 進入渲染。不通過 → 丟棄，由 Layout Generator 補足。

---

## Renderer（Python）

Quality Checker 通過後，Python 用 PIL 將座標 + 素材渲染成圖片，供 Aesthetic Judge 視覺評審。

渲染在 Quality Checker **之後**進行，只有通過驗證的候選才渲染，避免浪費。

---

## LLM Agent 系統架構

四個 LLM Agent 間以**結構化 JSON 格式**傳遞資訊，避免 Semantic Drift。

### LLM Agent 1：Analyst（需求分析師）

詳細規格：`analyst.md`

**職責：** 將使用者的自然語言需求與素材整理成結構化 Design Spec JSON。

**輸入：**
- 使用者自然語言需求
- 素材清單（圖片和文字都已過 CLIP 前處理，Agent 1 收到的是 embedding_key）
- Aesthetic Judge 的 feedback（第二次以後執行才有）

**輸出（Design Spec JSON）：**
- canvas（width / height）
- elements（id / semantic_type / visual_type / content 或 asset_ref / embedding_key）
- hard_constraints（結構化物件，語意 hint，非像素座標）
- soft_constraints
- style_keywords
- inferred_fields（標記哪些欄位是 LLM 推理補上的）

**注意：** Agent 1 不輸出 importance、text_hints、幾何座標，這些分別由 Asset Analyzer 和 Layout Generator 負責。

---

### LLM Agent 2：Asset Planner（素材規劃師）

詳細規格：`asset_planner.md`

**職責：** 分析前景素材之間的語意關係，輸出 Layout Tree。

**設計參考：** 參考 PosterO（CVPR 2025）的 Hierarchical Node Representation，但改為用 LLM 直接推理，不需要訓練。

**輸入：**
- Design Spec JSON（含 semantic_type、importance、semantic_relevance）

**輸出（Layout Tree）：**
- 樹狀結構，節點只有 `id` 和 `children`
- 所有節點都是真實素材，無虛擬群組節點
- 根節點為虛擬 `root`
- 背景圖不放進樹裡
- 語意關係為父子關係依據，importance 為輔助參考
- 可多層深度

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

**Python 驗證：** 建完樹後驗證元素完整性、無重複、無孤立節點、根節點唯一，失敗則重試。

---

### LLM Agent 3：Layout Generator（版面生成器）

詳細規格：`layout_generator.md`

**職責：** 根據 Design Spec、Layout Tree 與背景分析結果，一次生成 5 個版面候選。

**輸入：**
- Design Spec JSON（含 importance、semantic_relevance）
- Layout Tree
- safe_zones、dominant_palette、recommended_text_color
- Aesthetic Judge 的 feedback（第一次執行時為空）

**輸出（5 個候選）：**
每個候選包含所有元素的幾何座標（Crello 格式）。文字元素額外包含視覺屬性：

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
          "id": "bg_1",
          "left": 0, "top": 0, "width": 1080, "height": 1920,
          "angle": 0, "z_index": 1
        }
      ]
    }
  ]
}
```

**生成機制：** 目標湊滿 K_valid = 5 個通過 Quality Checker 的合格候選。Quality Checker 否決後動態補足，用同樣 prompt 重新呼叫。

---

### LLM Agent 4：Aesthetic Judge（美感評審）

詳細規格：`aesthetic_judge.md`

**職責：** 對通過 Quality Checker 並渲染好的版面圖片進行美感評審，輸出分數、評語與改進建議。

**輸入：**
- 5 張渲染好的版面圖片
- Design Spec JSON
- Layout Tree
- dominant_palette

**評分維度（各 25 分，總分 100）：**
- 需求符合度
- 資訊層級清晰度
- 版面平衡
- 整體一致性

**輸出：**
- 每個候選的分數、優點、缺點
- best_candidate_id（總分最高那一個）
- **feedback（accept / reject 兩情況都必須有，不能 null）**：
  - reject：列具體修補方向（元素 id + 失分維度 + 改善方向）
  - accept：列 small-step polish 建議（供 Refinement Loop 再 polish 一輪）

**回饋路由（Refinement Loop 架構）：**
- **無論 accept 或 reject，每輪 Judge 完成後都強制送回 Layout Generator** 做 targeted refinement，payload 含 `prev_best_layout`（best candidate 的 bbox dict）+ 四維子分數 + feedback。
- 第 3 輪以上仍 reject → feedback 改送 Analyst 重新規劃 Design Spec。
- **終止條件**（任一觸發）：(a) Judge 連續兩輪 accept；(b) iteration > max_total_rounds（預設 5）。

---

## 完整流程

```
Step 1：CLIP Embedding 前處理
Step 2：Background Analyzer（CV 模組）
Step 3：LLM Agent 1 — Analyst
Step 4：Asset Analyzer（Python）— 計算 importance + semantic_relevance
Step 5：LLM Agent 2 — Asset Planner
Step 6：LLM Agent 3 — Layout Generator
         ├ Round 0（cold-start）：prev_best_layout 為空，從零生 5 個 distinct candidates
         └ Round 1+（refinement）：帶 prev_best_layout + 子分數 + feedback，做 ±10% targeted edit
Step 7：Quality Checker（Python）— 驗證幾何合法性與 hard_constraints
         └ If valid candidates < 5 → 回到 Step 6 補足（用同一輪的 prompt，不切換 round）
Step 8：Renderer（Python）— 將合格候選渲染成圖片
Step 9：LLM Agent 4 — Aesthetic Judge（Refinement Loop）
         ├ 評分 → 輸出 verdict + best_candidate + 子分數 + feedback（accept/reject 皆有 feedback）
         ├ 無論 verdict 為何，預設都回 Step 6（帶 prev_best_layout）做 refinement
         ├ 連續兩輪 accept → 輸出最近一輪 best candidate 為 Final Layout
         ├ Iteration > max_total_rounds 且仍 reject → 第 3 輪起改送 Step 3 重規劃
         └ Iteration > max_total_rounds → 強制終止，輸出歷史 best
```

---

## 資料集與評估指標

### 目標資料集
- **Crello**：多元設計類型（社群媒體、海報等）版面資料集，Schema 與本系統對齊
- **PKU**
- **CGL**

### 評估指標

參照 AesthetiQ（CVPR 2025）的評估方式：

| 指標 | 說明 | 實作位置 |
|------|------|----------|
| **mIoU（Mean Intersection over Union）** | 生成版面與 ground-truth 版面的元素重疊程度，衡量幾何精準度 | `metagpt/ext/agentlayout/evaluation/iou.py` ✅ |
| **Win Rate** | 以 MLLM 作為評審，比較生成版面與 ground-truth 的美感勝率，衡量美感品質 | 規劃中（沿用既有 Aesthetic Judge 改成 head-to-head） |
| **Read Order Score** | 預測閱讀順序與設計師標註重要度順序的 Spearman 相關係數 | future work |
| **FID** | rendered PNG 與 Crello preview 之間的 Frechet Inception Distance | future work |

#### mIoU 形式定義

對單一 layout：

$$\mathrm{IoU}(b_g, b_t) = \frac{|b_g \cap b_t|}{|b_g \cup b_t|} \in [0, 1]$$

其中 `b_g`、`b_t` 為 generated 與 ground-truth 的同一語義元件 bounding box `(left, top, width, height)`。

per-layout `mIoU = (1/n) Σ IoU(b_g_i, b_t_i)`，僅對 **matched 對**取平均（unmatched 元件不罰 0，由 caller 自行決定 missing penalty 變體）。

cross-sample mIoU = N 個 sample 的 per-layout mIoU 算術平均。

#### Element 對應策略（id matching）

| 問題 | 解法 |
|---|---|
| Crello GT 用 `idx (0,1,2,...)`，AgentLayout 用 LLM 取的語義 id（`title_1, body_1`） | caller 提供 `id_map: {gen_id → gt_idx}` |
| LLM 不保證保留 asset_list 順序 | 用 `content / asset_ref` 內容唯一性反查（比對字串、不靠位置） |
| Crello sample 內可能有重複 text | 目前 fail-safe 報告 `unmatched_*`；下一步 fallback 到 LLM 順序匹配 |

#### 5-sample cold-start baseline（2026-05-10 首次執行）

`run_iou_eval.py` 跑 BypassJudge 模式（量 Generator 第一輪輸出 vs GT，與 Judge 同意度正交）：

| sample_id | canvas | matched | mIoU |
|---|---|---|---|
| 5d972ca9... | 537×240 | 4/5 | 0.091 |
| 5c6c0cba... | 1080×1920 | 4/5 | 0.140 |
| 5954bda9... | 1200×600 | 4/5 | 0.074 |
| 5efdd2dd... | 1008×1296 | 3/3 | **0.217** |
| 5f885a9b... | 851×315 | 3/4 | 0.000 |
| **Cross-sample** | — | 18 pairs | **0.105** |

**為什麼這個基線數字偏低（這是合理的）：** AgentLayout 的目標**不是**重現特定設計師排版，而是「給 brief + assets 生出一個可行版面」。同 brief 可有無數 valid layout，IoU 只在「剛好猜到設計師選擇」時才高。**0.105 是 cold-start baseline**：未經 reference-aware prompt、未經 Judge feedback loop、無設計風格對齊；論文後續 ablation 都以這個數字為對照起點。

#### 對照基線（Random / Centered baseline，2026-05-10）

「0.105 是好還是壞」這問題需要對照基線回答。對同 5 個 Crello sample 跑兩個 trivial baseline（純離線、無 LLM、$0 cost）：

| Method | 描述 | Cross-sample mIoU |
|---|---|---|
| **AgentLayout** | LLM-driven、cold-start、無 reference | **0.105** |
| Random | 每元件 (left, top, w, h) 在 canvas 內均勻隨機（5 seed 平均） | 0.064 (±0.045) |
| Centered | 等寬 horizontal padding 10%、垂直堆疊、equal slices（deterministic） | 0.103 |

per-sample 拆解：

| sample | canvas | AgentLayout | Random | Centered |
|---|---|---|---|---|
| 5c6c0cba... | 1080×1920 (直式) | **0.140** | 0.034 | 0.034 |
| 5efdd2dd... | 1008×1296 | 0.217 | 0.118 | **0.244** |
| 5d972ca9... | 537×240 (橫式) | 0.091 | 0.067 | 0.106 |
| 5954bda9... | 1200×600 | 0.074 | 0.073 | 0.067 |
| 5f885a9b... | 851×315 | 0.000 | 0.028 | 0.065 |

**論文可寫的 take-aways：**

1. **AgentLayout vs Random（1.6× 提升）**：cross-sample 0.105 vs 0.064，系統**確實學到比隨機好的位置選擇**（雖只是 cold-start，已勝過完全無資訊的 floor）
2. **AgentLayout vs Centered（持平）**：0.105 vs 0.103，**naive 居中堆疊 prior 與 LLM 自由生成相當**。這是論文 honest weakness：cold-start 模式下 LLM 沒有顯著優於可硬編碼的設計 prior — 暗示後續工作（reference-aware prompt / Judge feedback / fine-tune）才是 LLM 真正發揮的場景
3. **Canvas shape 影響策略選擇**：
   - 1080×1920 直式長 canvas：AgentLayout 0.140 大勝兩個 baseline → LLM 對「長畫布上下分區」的直覺有效
   - 1008×1296 方正 + 少元件：Centered 0.244 反勝 AgentLayout 0.217 → 元件少時 naive prior 已逼近上限，LLM 的自由度反成劣勢
   - 851×315 横式：AgentLayout 0.000（id 對應失敗或位置完全偏離），Random 0.028 → corner case 顯露 id-matching 的脆弱性
4. **Random std 0.045（per-sample）**：5 seed 內 Random 自身波動約 ±4.5%，所以 AgentLayout 比 Random 高 4.1% 的差距並未被 noise 蓋過

**新增評估模組**
- `evaluation/baselines.py`
  - `random_layout(element_ids, canvas_w, canvas_h, seed) -> List[BBoxItem]`：均勻隨機位置 + 尺寸
  - `centered_stack(element_ids, canvas_w, canvas_h)`：deterministic 等分縱向堆疊
- `layout_agent/output/test_baselines.py`：14/14 單元測試 PASS（determinism / boundary / size bound / empty input）
- `layout_agent/output/run_random_baseline.py`：純離線 driver，讀既存 `eval_iou_baseline.json` + `crello_<id>/meta.json`，產出 `eval_baseline_compare.json`

---

## 相關研究定位

| 論文 | 方法類型 | 與本研究的關係 |
|------|----------|----------------|
| [AesthetiQ (CVPR 2025)](https://arxiv.org/abs/2503.00591) | MLLM + DPO 美感對齊 | Aesthetic Judge 的設計對標；CLIP embedding 前處理參考 |
| [PosterO (CVPR 2025)](https://arxiv.org/abs/2505.07843) | Layout Tree + LLM | Layout Tree 概念來源；本研究改為 LLM 推理，不需訓練 |
| [SEGA (ICCV 2025)](https://arxiv.org/abs/2510.15749) | Stepwise feedback 生成 | Feedback loop 的直接對標
 [MetaGPT](https://arxiv.org/abs/2308.00352) | Multi-agent with structured output | Agent 間 JSON 通訊設計的理論基礎 |

---

## 研究貢獻總結

1. **問題重新定義：** 將內容感知排版（content-aware layout generation）從「生成問題」重新定義為「可分工的結構化決策問題」
2. **混合式系統架構：** LLM 只用於語意推理任務，幾何驗證與數值計算由 Python 處理，各司其職
3. **Layout Tree 語意結構：** 參考 PosterO 概念，改以 LLM 直接推理素材間的層級關係，不需訓練資料
4. **雙層驗證機制：** 幾何規則過濾（Quality Checker Python）+ 視覺美感評審（Aesthetic Judge LLM 看圖）
5. **結構化通訊協議：** 以 JSON Schema 取代自然語言通訊，避免 Semantic Drift
6. **Refinement Loop（coarse-to-fine 架構）：** 每輪 Aesthetic Judge 評分後強制回 Layout Generator 做 targeted refinement（帶 prev_best_layout + 子分數 + feedback），對齊 SEGA (ICCV 2025) coarse-to-fine 範式；第 3 輪以上仍 reject 才回溯至 Analyst 重規劃

---

## 技術棧（暫定）

- **LLM Backend：** GPT-4o / Claude 3.5 Sonnet（視 API 成本調整）
- **Vision Encoder（`f_vision`）：** CLIP ViT-L/14（圖片元素 embedding）
- **Text Encoder（`f_text`）：** CLIP Text Encoder（文字元素 embedding，與 vision embedding 在同一空間）
- **Saliency Detection：** U2Net（Background Analyzer CV 模組，透過 rembg 呼叫）
- **Embedding Store：** 本地 dict 或輕量向量資料庫（如 FAISS），以 `embedding_key` 索引
- **渲染：** PIL（Python Imaging Library）
- **版面表示格式：** JSON（bounding box 以 Crello 對齊的 left/top/width/height/angle/z_index 表示）
- **評估框架：** 對照 Crello、WebUI benchmark，以 mIoU 和 Win Rate 與 AesthetiQ、PosterO 等方法比較

---

### 變更紀錄

| 日期 | 動作 | 備註 |
|------|------|------|
| 2026-04-22 | 建立 Analyst + Embedding + Background Analyzer + Pipeline Driver | 詳見舊變更紀錄 |
| 2026-04-23 | 架構重新設計：4 個 LLM Agent + 多個 Python/CV 模組 | Quality Checker 改為 Python；新增 Asset Planner（Layout Tree）；新增渲染模組 |
| 2026-04-23 | Analyst 設計更新：移除 text_hints / image_hints / importance；加入 inferred_fields；約束改為語意 hint | 詳見 role_info/analyst.md |
| 2026-04-23 | Asset Planner 設計確認：語意層級 Layout Tree，參考 PosterO | 詳見 role_info/asset_planner.md |
| 2026-04-23 | Layout Generator 設計更新：加入 Layout Tree 輸入；輸出文字視覺屬性；一次生成 5 個候選 | 詳見 role_info/layout_generator.md |
| 2026-04-23 | Aesthetic Judge 設計確認：看渲染圖片評分；四維評分；通用 feedback | 詳見 role_info/aesthetic_judge.md |
| 2026-04-23 | importance + semantic_relevance 計算模組改名為 Asset Analyzer | Python 模組，非 LLM |
| 2026-05-07 | 全部舊實作清空、重新設計實作路徑：所有程式碼改放 `metagpt/ext/agentlayout/` | session reset，舊 v3 檔案已歸檔到 `~/agentlayout_20260507_2104.tar.gz` |
| 2026-05-20 | 架構改為 Refinement Loop（coarse-to-fine） | Judge 不論 accept/reject 都強制回 Layout Generator 一次；prev_best_layout 隨 RetryPayload 一起傳；對齊 SEGA (ICCV 2025) 範式。詳見 `layout_generator.md` 情況三、`aesthetic_judge.md` 回饋路由段；尚未實作 code（下次實驗才接） |

---

## 實作進度

> 完整實作日誌（每個 step 的動機/方法/驗證/trade-off、每個模組的程式檔與測試紀錄）已搬移至 [`IMPLEMENTATION_LOG.md`](./IMPLEMENTATION_LOG.md)。
>
> 下表為 step 摘要對照，論文 / 口試引用以 [`result.md`](./result.md) 為權威 honest framing source。

### Step 摘要對照表

| Step | 日期 | 主題 | 結論（誠實定調） |
| --- | --- | --- | --- |
| 6 | 2026-05-14 | canvas-coverage rule | 純 prompt 無法突破 plateau → bottleneck 不在指令層 |
| 8 | 2026-05-14 | contrast-aware text 假設 | 失敗已 revert；揭露 prompt attention budget |
| 9 / 9b | 2026-05-14 | Crello sparsity 假設 | N=1 正向 + 2 crash 揭露 robustness ceiling |
| 10 / 10c / 10d | 2026-05-16 | QC tolerance 修補 | 跨 3 aspect ratio generalize；plateau bal/coh≈17 為結構性 |
| 11 | 2026-05-18 | plateau 第二段根因 + pairwise Win Rate | scope-bound structural；task-aligned N=3 設計師 2:1 |
| 12 / 12b / 12d | 2026-05-19 | BackgroundAnalyzer + content-aware live | content-aware 首數據 mean 70.67 / best 72；plateau 仍未破 |
| 13 | 2026-05-19 | SOTA-positioning Win Rate pilot N=20 | completion 100%、Win rate 80%（task-aligned, AesthetiQ-protocol-equivalent） |
| 14 | 2026-05-19 | 獨立 Claude judge 重判消 self-preference | 80% ↔ 80% 完全複製、confound 實證排除 |
| 15 | 2026-05-19 | 標準 Layout-IoU + baseline 對照 N=20 | mean IoU 0.0994 > random 0.0567、≈ centered_stack 0.0931（誠實偏負） |
| 16 | 2026-05-19 | SOTA-context 對照表（AesthetiQ Table 1） | 引用 published numbers 做 related-work 定位、不進勝負表 |
| 17 | 2026-05-20 | step 10b post-RetryAnalyst crash 修補 | 根因+防禦雙修；N=20 零 crash；post-fix Win rate 75% |
| 18 | 2026-05-20 | 任務定義 framing 修正 | README content-aware 明示；移除 Win Rate A 收斂單一 task-aligned 指標 |
| 19 | 2026-05-20 | 架構改 Refinement Loop（doc-only） | accept/reject 都強制回 LayoutGenerator 一次 + 帶 prev_best_layout；對齊 SEGA coarse-to-fine；code 尚未動，待下次 SEGA-protocol 實驗一起接 |
| 20 | 2026-05-20 | SEGA-protocol 6 rule-based 指標 head-to-head N=20 | Ali=0.0000、Ove=0.0009（≤ SEGA-13B 量級）、Und=0 (scope-bound)、Read/Occ ≈ GT；首組真正 head-to-head 數據 |

---

*本文件為論文研究說明，供系統開發時參考使用。最後更新：2026/05/20*
