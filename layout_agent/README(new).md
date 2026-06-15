# AgentLayout: Decomposing Content-Aware Layout Generation into Collaborative Agent Workflows
# 碩士論文研究概覽（實作現況版）
## Multi-Agent AI 內容感知（Content-Aware）排版生成系統

---

> **本檔說明：** 這份 README 描述的是**截至 2026-06-14 系統的實際實作狀態**，與原始 `README.md`（設計藍圖版）有所不同。原檔保留為設計理念展示與論文方法章節的延伸說明；本檔則對齊每個模組「目前真的做到什麼、為什麼這樣做」。最新的逐步實作紀錄請見 `IMPLEMENTATION_LOG.md`，最新的實驗數據請見 `result.md`。

---

## 研究背景與動機

本研究所在實驗室的方向為 AI 應用研究。本篇碩士論文的主題是**利用 Multi-Agent AI 系統解決內容感知排版生成（Content-Aware Layout Generation）問題**——在**既有背景畫布**上，根據自然語言 brief 與一組既有素材（image / text），決定每個元素的座標、大小、層次與視覺屬性。

任務 scope 不包含背景生成、字型/裝飾合成、影像 inpainting——這些是 scope 外能力，已記為 limitation（見 `result.md` §0、§3.3）。Benchmark 對齊 AesthetiQ（CVPR 2025）、LayoutNUWA、PosterLLaVa 等 content-aware layout generation 同類方法，protocol 為 pairwise win-rate vs designer-GT layout（同 renderer 純排版幾何）+ Mean IoU。

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

## 系統實際架構

本系統由 **4 個 LLM Agent + 5 個非 LLM 模組**組成（內容感知主流程）。另有一個**選用 LLM 模組** Composition Director，目前僅在實驗驅動腳本中啟用。

### 主流程模組（預設執行）

| 模組 | 類型 | 職責 |
|------|------|------|
| Background Analyzer | CV 模組（BASNet + ISNet） | 顯著性偵測，輸出安全放置區域、主色盤、建議文字顏色 |
| Asset Analyzer | Python（非 LLM） | 從 semantic_type 查表計算 importance；目前 semantic_relevance 為預留欄位（見下文說明） |
| Quality Checker | Python（非 LLM） | 驗證候選版面的幾何合法性、hard_constraints、可讀性規則 |
| Renderer | Python + PIL | 將通過 QC 的候選版面渲染成圖片 |
| Saliency 評估模組 | CV（BASNet + ISNet） | 評估指標計算用，獨立於 Background Analyzer |
| **Agent 1：Analyst** | LLM | 自然語言需求 → 結構化 Design Spec JSON |
| **Agent 2：Asset Planner** | LLM | 分析素材語意關係 → 輸出 Layout Tree |
| **Agent 3：Layout Generator** | LLM | 根據 Design Spec + Layout Tree + 背景分析生成 5 個候選 |
| **Agent 4：Aesthetic Judge** | LLM | 對渲染好的版面圖片進行美感評分 → 輸出結果或觸發 Feedback Loop |

### 選用模組（實驗腳本啟用）

| 模組 | 類型 | 啟用方式 |
|------|------|---------|
| Composition Director | LLM | 僅在實驗腳本 `layout_agent/output/step41_layout_aware_oracle.py` 啟用，預設 Pipeline / Team 流程未串接 |

### 控管模組（流程協調）

| 模組 | 類型 | 職責 |
|------|------|------|
| IterationStateRole | Python（非 LLM 推理） | 接收 Judge 結果，路由下一輪到 Generator（refinement）或 Analyst（重規劃） |

**設計原則：** LLM 只用在真正需要語意理解與推理的環節（需求解析、語意關係分析、版面生成、美感評審）。幾何驗證、數值計算、影像處理等任務全部交給 Python 或 CV 模組，更準確、更快、更省 token。

---

## 模組詳細說明

### Background Analyzer（CV 模組）

**職責：** 解析背景圖的可放置區域，輸出安全放置區域與配色資訊。

**實作：** 使用 **BASNet + ISNet 雙模型 saliency map**（先前使用 U2Net，已升級）。

**輸出範例：**
```json
{
  "safe_zones": [
    { "region": "top-left", "bbox": [0, 0, 400, 300], "confidence": 0.92 }
  ],
  "dominant_palette": ["#F5E6D3", "#A8C5DA"],
  "recommended_text_color": "#111111"
}
```

**為什麼用 BASNet + ISNet：** 雙模型 ensemble 在 Crello 多元設計類型（社群媒體、海報、卡片）上比單一 U2Net 更穩定，特別是面對純色背景與抽象插畫時 saliency 邊界更清晰。Step 23 完整 Crello test split 校準時納入此升級。

---

### Asset Analyzer（Python 模組）

**職責：** 在 Analyst 執行完之後，由 Python 直接計算並填回 Design Spec 上的兩個欄位，不需要 LLM。

**importance（1–5）：** 從 semantic_type 對應表直接查表，例如 `title → 5`、`logo → 4`、`background_image → 1`。

**semantic_relevance（0–1）：** **目前為預留欄位，固定回傳 0.5。**

> **為什麼可以暫時用 0.5 常數：**
> 原本設計上，這個欄位是用 CLIP encoder 對元素 embedding 與 style_keywords 做 cosine similarity。實作上 CLIP 編碼器與 Embedding Store 尚未串接。
> 但 `semantic_relevance` 在現行流程中**只被 Asset Planner 拿來輔助安排元素分群**，它的上游搭檔 `importance`（直接從元素類型查表）已經提供了主要的語意重要性訊號。CLIP 多算出來的分數所能帶來的，主要是「同類元素之間誰更貼近 style_keywords」這層細部區分，對整體版面決策的影響相對邊際。完整實作列為未來工作。

---

### Quality Checker（Python 模組）

**職責：** 對 Layout Generator 輸出的候選版面進行幾何驗證、約束驗證與可讀性驗證，過濾不合格的候選。

**驗證項目（截至 Step 67）：**

| 類別 | 規則 |
|------|------|
| 完整性 | 候選的元素 id 集合與 Design Spec 完全一致 |
| 邊界 | 元素須在畫布內，`left+width ≤ canvas_width` 等 |
| Hard Constraints | 逐條驗證 position_preference、size_preference、no_overlap、z_order |
| 尺寸下限 | 依 size hint（hero / large / prominent / medium / small / caption）查表的最小面積比 |
| 可讀性 | 文字疊在背景顯著區域時須有 underlay；高紋理區域上的文字會觸發警告 |
| 安全區域 | 主要文字元素（title / subtitle / body）須與 safe_zone 有足夠重疊（**僅在沒有 Composition Director 介入時生效**） |
| 構圖契約 | 當 Composition Director 已選定模板時，驗證候選遵循該模板的構圖約束 |
| 覆蓋率 | 候選整體前景覆蓋率須 ≥ 10%，避免極端疏散版面 |

通過 → 進入渲染。不通過 → 丟棄，由 Layout Generator 補足。當所有候選都不過時，**graceful degradation** 機制會把違規最少的候選送進評審而非整個 sample crash。

> **為什麼採 graceful degradation：**
> 早期版本只要 QC 全部退件就 raise PipelineError，導致實驗時 evaluable N 縮小。Step 10b 起改成「退件全滿時，把違規最少的候選送進 Judge」，至少讓 Judge 還能評分、Refinement Loop 還能繼續路由 feedback，不至於整個 sample 被丟棄。

---

### Renderer（Python + PIL）

Quality Checker 通過後，Python 用 PIL 將座標 + 素材渲染成圖片，供 Aesthetic Judge 視覺評審。

渲染在 Quality Checker **之後**進行，只有通過驗證的候選才渲染，避免浪費 CPU 與 I/O。

Renderer 經過 Step 55 升級：字型支援、文字 wrap、bounding box fit、rotation 都已從早期最簡版進化為接近設計師輸出的渲染品質。

> **為什麼 Renderer 在 QC 之後：**
> 渲染是整個流程裡最耗 I/O 與 CPU 的單一步驟（文字 wrap、字型 fallback、圖層合成、字距計算）。把它放在 QC 之後可以避免渲染已知不合格的候選——一輪 5 個候選裡若 3 個被 QC 退件，等於省下 60% 渲染成本。

---

## LLM Agent 系統架構

四個 LLM Agent 間以**結構化 JSON 格式**傳遞資訊，避免 Semantic Drift。

### LLM Agent 1：Analyst（需求分析師）

**職責：** 將使用者的自然語言需求與素材整理成結構化 Design Spec JSON。

**輸入：**
- 使用者自然語言需求
- 素材清單（圖片與文字 raw assets；目前無 embedding_key 機制）
- Aesthetic Judge 的 feedback（第二次以後執行才有）

**輸出（Design Spec JSON）：**
- `canvas`（width / height）
- `elements`（id / semantic_type / visual_type / content 或 asset_ref）
- `hard_constraints`（結構化物件，語意 hint，非像素座標）
- `soft_constraints`
- `style_keywords`
- `inferred_fields`（標記哪些欄位是 LLM 推理補上的）

**注意：** Agent 1 不輸出 importance、text_hints、幾何座標——這些分別由 Asset Analyzer 與 Layout Generator 負責。

> **為什麼 hard_constraints 是「語意 hint」而非像素座標：**
> Analyst 不知道 canvas 實際渲染後的視覺重心、背景顯著區、字型實際佔寬。若它直接輸出像素座標，等於越過 Layout Generator 替整個流程拍板，而它能拿到的資訊又是最不完整的。所以 Analyst 只給「靠左對齊」「比 X 大」這種語意層級的約束，把像素級決策留給拿到背景分析與 Layout Tree 的 Generator。

---

### LLM Agent 2：Asset Planner（素材規劃師）

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
      {"id": "logo_1", "children": []}
    ]
  }
}
```

**Python 驗證：** 建完樹後驗證元素完整性、無重複、無孤立節點、根節點唯一，失敗則重試。

---

### LLM Agent 3：Layout Generator（版面生成器）

**職責：** 根據 Design Spec、Layout Tree 與背景分析結果，一次生成 5 個版面候選。

**輸入：**
- Design Spec JSON
- Layout Tree
- safe_zones、dominant_palette、recommended_text_color
- 構圖契約（若 Composition Director 已執行）
- Aesthetic Judge 的 feedback（第一次執行時為空）
- prev_best_layout（refinement 模式才有）
- 渲染好的背景圖（vision channel，Step 46 起）

**輸出（5 個候選）：**
每個候選包含所有元素的幾何座標與 z_order。文字元素額外包含視覺屬性（字型、字級、顏色、對齊）。

**生成機制：** 目標湊滿 K_valid = 5 個通過 Quality Checker 的合格候選。Quality Checker 否決後動態補足，用同樣 prompt 重新呼叫（top-up rounds，預設上限 3）。

**Vision channel：** Generator 不只接收 safe_zone 的數值描述，還會收到一張渲染好的背景圖，讓它能直接「看」到背景上有什麼，避免文字壓到主體。

> **為什麼 prompt 與 QC 的尺寸門檻刻意不一致：**
> Generator prompt 對「prominent」「medium」尺寸寫的是 stretch target（例：prominent **20%**），但 Quality Checker 的實際 accept 下限低於此（例：prominent **10%**）。這個雙層門檻是為了拉回 LLM 的「尺寸膽怯」偏移——在 Step 58/60 觀察到 LLM 系統性把元素做得太小，把 prompt 寫高、QC 門檻寫低，等於告訴 LLM「請朝 20% 努力」，實際只要做到 10% 就會被接受。此設計細節在程式碼中已加上 inline 註解（`tools/quality_checker.py` 與 `actions/generate_layout.py`），避免被當成 bug 修掉。

---

### LLM Agent 4：Aesthetic Judge（美感評審）

**職責：** 對通過 Quality Checker 並渲染好的版面圖片進行美感評審，輸出分數、評語與改進建議。

**輸入：**
- 5 張渲染好的版面圖片
- Design Spec JSON
- Layout Tree
- dominant_palette、safe_zones

**評分維度（Step 30 起採用 COLE 5 軸）：**
- Design & Layout（DSL）
- Content（CTT）
- Typography（TYP）
- Style（STV）
- Innovation（INO）

每軸 1–10 分，總分 5–50。`ACCEPT_THRESHOLD` 為 35。

**輸出：**
- 每個候選的分數、優點、缺點
- best_candidate_id（總分最高那一個）
- **feedback（accept / reject 兩情況都必須有）**：
  - reject：列具體修補方向（元素 id + 失分維度 + 改善方向）
  - accept：列 small-step polish 建議（供 Refinement Loop 再 polish 一輪）

> **為什麼從 4 軸 0-100 改為 COLE 5 軸 5-50：**
> Step 30 之前採用自訂 4 軸 0-25 / 總分 100 制。為了跟 SEGA / COLE 等同類研究的評分系統對齊，便於 cross-paper 比較，Step 30 起改採 COLE 5 軸 1-10 / 總分 5-50。注意：**這個 schema 變更導致 pre-Step 30 與 post-Step 30 的所有 Phase A/B 數值不可直接比較**（`result.md` §0 有警示）。

---

### Composition Director（選用 LLM 模組）

**職責：** 在像素級排版之前，先從 GT-calibrated 模板庫中選一個合適的「整體構圖方向」——焦點照片放哪一格、文字壓不壓在照片上、照片佔多大面積、文字主體中心放哪。

**輸出：** 一個 `CompositionDirective` 物件（template_id + relation + photo_cell + photo_size + text_cell + rationale），寫進 `spec.composition`，後續 Generator prompt 與 QC 都會讀。

**啟用狀態：** **僅在實驗驅動腳本 `layout_agent/output/step41_layout_aware_oracle.py` 中被呼叫**。預設的 `LayoutPipeline.run()` 與 Team 流程（`build_team()`）都未串接此模組。

> **為什麼維持「只在實驗腳本中啟用」：**
> Step 62–66 的系列實驗對 Composition Director 做過深入測試。機制本身運作正常（template 模板選擇成功率 20/20），但對最終 acceptance 與 win-rate **沒有顯著提升**——Step 66 的 constraint-solver placement 甚至以 55/55 的比數全部輸給設計師 GT。結論已收斂為 paper limitation。
> 將其推進為預設流程會把一個被驗證為 negative 的元件鋪成系統預設行為（多一個 LLM call、改變 Generator prompt 結構），故維持只在實驗腳本中啟用的設計。Code 保留下來作為論文 evidence 與未來 ablation 重跑用。

---

### IterationStateRole（流程協調，非 LLM）

**職責：** 接收 Aesthetic Judge 的判決，決定下一輪要把 feedback 路由給誰，並追蹤 best-so-far layout 防止 noisy re-judge 把 anchor 拉回較差版本。

**路由邏輯：**

| Judge 判決 | 第幾輪 | 路由目標 |
|---|---|---|
| ACCEPT | 任何輪 | Layout Generator（mandatory refinement pass） |
| REJECT | 第 1–2 輪 | Layout Generator（targeted refinement） |
| REJECT | 第 3 輪起 | Analyst（重規劃 Design Spec） |

**終止條件：**
- Judge 連續兩輪 ACCEPT → 輸出最近一輪 best candidate 為 Final Layout
- iteration > max_total_rounds（預設 5） → 強制終止，輸出歷史 best-so-far

> **為什麼 best-so-far guard 重要：**
> 早期版本下一輪 Generator 永遠 anchor 到「這輪 Judge best」。但 Judge 評分有噪音——這輪的 best 可能比上輪 best 還差。Step 31 起改成只有「strictly 比歷史 best 高」才更新 anchor，避免 Markov-chain regression。
> 注意：這個 guard 目前**只在 Role 流程中實作**，直接呼叫的 `LayoutPipeline.run()` 流程尚未補上。所有 headline 實驗數據都是透過實驗驅動腳本 `step41_layout_aware_oracle.py` 跑的，不經由上述兩條 default 流程，所以實驗結果不受此差異影響。

---

## 完整流程

### 預設流程（Pipeline / Team）

```
Step 1：Background Analyzer（CV 模組）— BASNet + ISNet saliency
Step 2：Agent 1 — Analyst（brief + assets → Design Spec）
Step 3：Asset Analyzer（Python）— 補上 importance + semantic_relevance(=0.5)
Step 4：Agent 2 — Asset Planner（Design Spec → Layout Tree）
Step 5：Agent 3 — Layout Generator
         ├ Round 0（cold-start）：從零生 5 個 candidates
         └ Round 1+（refinement）：帶 prev_best_layout + 子分數 + feedback，targeted edit
Step 6：Quality Checker（Python）— 驗證幾何 + hard_constraints + 可讀性
         └ 若 valid < 5 → 回到 Step 5 補足（top-up，預設上限 3 輪）
         └ 若全退件 → graceful degradation，挑違規最少送下去
Step 7：Renderer（Python + PIL）— 渲染合格候選成圖片
Step 8：Agent 4 — Aesthetic Judge（COLE 5 軸 + Refinement Loop）
         ├ 評分 → 輸出 verdict + best_candidate + 子分數 + feedback
         ├ 預設都回 Step 5 做 refinement（帶 prev_best_layout）
         ├ 連續兩輪 accept → 輸出 Final Layout
         ├ Iteration ≥ 3 且仍 reject → 改送 Step 2 重規劃
         └ Iteration > max_total_rounds → 強制終止，輸出歷史 best
```

### 實驗驅動腳本流程（step41_layout_aware_oracle.py）

論文 headline 數據（Step 22 N=100、Step 23 N=1,897）都是透過此腳本跑的。它額外串接：

- **Stage 2.5：Composition Director**（在 Step 4 與 Step 5 之間插入）
- 自帶迴圈控制與重試邏輯
- per-axis attribution log（Step 49c 起）
- 構圖契約寫入 `spec.composition`

> **為什麼有兩條流程：**
> 預設 Pipeline / Team 是給未來使用者的最小可用版本，提供穩定 API。實驗腳本則是研究過程中為了快速迭代各種 ablation 而堆出來的，包含許多論文 evidence 用的偵測 hook 與診斷輸出，但相對「侵入性高」、不適合放進預設流程。

---

## 資料集與評估指標

### 已整合的資料集

- **Crello**：多元設計類型（社群媒體、海報等）版面資料集，Schema 與本系統對齊。N=1,897 完整 test split 已跑過（Step 23）。

### 規劃中但未實作的資料集

- **PKU PosterLayout**：可行性已評估（見 `layout_agent/PKU_FEASIBILITY.md`），最小實作 ~150–200 行，但 underlay GT = 0、judge 無訊號，且 Generator-bounded 探索線已結案，目前**不打算對標**。
- **CGL Dataset V2**：可行性已評估（見 `layout_agent/CGL_FEASIBILITY.md`）。

### 實作中的評估指標

| 指標 | 說明 | 實作位置 |
|------|------|----------|
| **mIoU** | 生成版面與 ground-truth 版面的元素重疊程度，衡量幾何精準度 | `metagpt/ext/agentlayout/evaluation/iou.py` ✅ |
| **SEGA 6 指標** | PKU PosterLayout 的 rule-based 指標（Ali / Ove / Und_l / Und_s / Read / Occ）| `metagpt/ext/agentlayout/evaluation/sega_metrics.py` ✅ |
| **Saliency map** | BASNet + ISNet 雙模型 ensemble，用於 SEGA Occ 等指標 | `metagpt/ext/agentlayout/evaluation/saliency_basnet_isnet.py` ✅ |
| **Baseline 對照** | random / centered_stack / GT layout 三組 baseline 供 cross-check | `metagpt/ext/agentlayout/evaluation/baselines.py` ✅ |
| **Win Rate（pairwise）** | 以 MLLM 作為評審，比較生成版面與 ground-truth 的美感勝率 | 沿用既有 Aesthetic Judge 改成 head-to-head |
| **Read Order Score** | 預測閱讀順序與設計師標註重要度順序的 Spearman 相關係數 | future work |
| **FID** | rendered PNG 與 Crello preview 之間的 Frechet Inception Distance | future work |

> **為什麼 SEGA 指標的對齊有 caveat：**
> Step 67 的 metric alignment audit（見 `layout_agent/METRIC_ALIGNMENT_AUDIT.md`）發現本系統的 SEGA 實作與 PKU 原版有幾項刻意保留的差異：
> - Ali feature 用 left/top/center 而非 width/height（已修正，A1）
> - Occ 用 saliency mean 而非 PKU 的 max（已修正，A2）
> - Sobel 梯度用 float64 而非 PKU 的 uint8（**刻意不複製 PKU overflow bug**，因為 QC 校準依賴正確的梯度）
> - underlay 協定差異（協定層級議題，無單一正確選項）
> 這些 caveat 都已寫進 `result.md` §3.1 paper honesty 章節。

---

## Refinement Loop 設計

### 為什麼 ACCEPT 也要送回 Generator

直覺上 ACCEPT 應該就結束流程。但實驗中發現：第一次 ACCEPT 常常是「剛好通過 threshold」，距離真正的好設計還有距離。

Step 20 起改為：**無論 accept 或 reject，每輪 Judge 完成後都強制送回 Generator** 做一次 refinement。終止條件改為「連續兩輪 ACCEPT」——讓系統有機會再 polish 一輪，確認結果真的穩定，而不是運氣好通過。

### 為什麼第 3 輪起 REJECT 才送回 Analyst

如果連續 2 輪都被 REJECT，問題很可能不在 Generator（已經給過 feedback 還是不行），而在更上游的 Design Spec 本身——例如元素分組錯了、constraints 設定衝突、style_keywords 偏離了使用者意圖。

所以前 2 輪 REJECT 給 Generator targeted refinement 的機會；第 3 輪起改路由給 Analyst 重規劃，避免在錯誤的 spec 上反覆 polish。

---

## 已知限制與設計權衡（Limitations）

本節整理所有「為什麼這樣做」的設計決策與 by-design scope 邊界，避免被誤判為實作 bug 或能力缺陷。

### 1. 不做 graphic-design synthesis

本系統**不生成新的裝飾視覺內容**（背景圖、字型、裝飾插圖、紋理）。這是 by-design 的 scope 邊界，對齊 AesthetiQ / LayoutNUWA / PosterLLaVa 等同類 content-aware layout generation 研究。

### 2. CLIP / Embedding Store 為未來工作

`semantic_relevance` 目前固定回傳 0.5；CLIP encoder 與 FAISS Embedding Store 未實作。原因：上游 `importance` 已提供主要語意訊號，CLIP 帶來的邊際資訊量小（見前文 Asset Analyzer 章節）。

### 3. Composition Director 不在預設流程

僅在實驗腳本中啟用。Step 62–66 證明加入後對 headline 指標無顯著提升，故不推進為預設行為（見前文 Composition Director 章節）。

### 4. Generator-bounded 探索線已結案

Step 62–66 嘗試了多種突破 Generator 上限的架構（AI 構圖師 / 視覺自我修正 / constraint solver），全部 negative。剩下唯一未試的方向是 fine-tuning。

### 5. Prompt / QC 尺寸門檻刻意不一致

用來拉回 LLM 的「尺寸膽怯」傾向（見前文 Layout Generator 章節）。

### 6. Refinement Loop 兩條流程實作有小差異

`LayoutPipeline.run()` 流程的 best-so-far guard 尚未補上、最大輪數判定與 Role 流程有 off-by-one 差異。論文 headline 數據由實驗腳本跑出，不受影響。

### 7. SEGA Und_l / Und_s ≈ 0 為 by-design

本系統 scope 不含 underlay synthesis（不生成新的裝飾 plate），故這兩個指標近 0 屬正常，非實作缺陷。

---

## 相關研究定位

| 比較對象 | 同類關鍵 | 與本研究的差異 |
|---|---|---|
| **AesthetiQ (CVPR 2025)** | MLLM + DPO 美感對齊 | 我方用多 Agent 分工 + 結構化 feedback，而非 DPO；Win Rate protocol 對齊 |
| **LayoutNUWA** | content-aware layout LLM | 我方 multi-agent decomposition，LayoutNUWA 是 monolithic LLM 直接輸出座標 |
| **PosterLLaVa** | poster 領域 content-aware | 我方分離設計約束與空間決策，PosterLLaVa 端對端 |
| **PosterO (CVPR 2025)** | Hierarchical Node Representation | 我方借用層級表示法但改為 LLM 直接推理，不訓練 |
| **SEGA** | layout 評估指標 | 採用 SEGA 6 指標做 head-to-head 數據對照（見 `result.md` §3.1） |

---

## 研究貢獻總結

1. **將 content-aware layout generation 解構為可分工、可驗證的多 Agent 工作流**，提出 4-Agent 主架構 + Python 驗證模組 + Refinement Loop 的具體實作。
2. **N=1,897 完整 Crello test split 結果**：Phase A 幾何指標（Ali / Ove）跨 3 個 scale（N=20 / 100 / 1,897）全部勝設計師 GT，Phase B Smean ratio 65.8% robust。
3. **誠實揭露 N=20 small-sample selection bias**：per-axis ranking 在 N=20/100/1,897 三個 scale 中兩次 flip，揭示 small-sample 結論不可作為 SOTA-positioning 依據。
4. **Generator-bounded 探索線完整 ablation**：Step 62–66 系統性測試了 AI 構圖師、視覺自我修正、constraint solver 三種突破 Generator 上限的架構，全部收斂為 negative，留下完整 paper limitation evidence。
5. **SEGA / PKU 指標對齊 audit**：逐行檢視本系統與 PKU 原版的 6 指標實作差異，並文件化所有刻意保留的差異（包括拒絕複製 PKU 的 uint8 overflow bug）。

---

## 技術棧

- **框架：** MetaGPT（Multi-Agent SOP framework）
- **LLM：** GPT-4o（Generator / Judge），Claude `claude-sonnet-4-6`（獨立 judge 驗證用）
- **CV 模組：** BASNet + ISNet（saliency），PIL（渲染）
- **資料集：** Crello（已對接）；PKU / CGL 已評估可行性但不對標

---

## 文件導讀

| 檔案 | 用途 |
|---|---|
| `README.md`（原檔） | 系統設計藍圖與研究理念展示，論文方法章節延伸說明 |
| `README(new).md`（本檔） | 實作現況版，每個模組現在做到什麼 + 為什麼這樣做 |
| `IMPLEMENTATION_LOG.md` | 從第一版到最新一次實驗的每一個設計決策、修改原因 |
| `result.md` | 最新的實驗結果、Benchmark 比較、可放進論文的數據與結論 |
| `EXPERIMENT_MATRIX.md` | 主實驗與所有 ablation 的完整對照表 |
| `METRIC_ALIGNMENT_AUDIT.md` | SEGA / PKU 指標對齊逐行 audit |
| `CGL_FEASIBILITY.md` | CGL Dataset V2 可行性評估 |
| `PKU_FEASIBILITY.md` | PKU PosterLayout 可行性評估 |

---

*本檔最後更新：2026-06-14。實作層面的最新狀態與每次修改的脈絡以 `IMPLEMENTATION_LOG.md` 為準。*
