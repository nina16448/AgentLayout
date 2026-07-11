# AgentLayout 遠端 Session 交接與實驗重跑規格

> 狀態日期：2026-07-10  
> 用途：直接貼給遠端 session，作為架構矯正、實作盤點與正式重跑的唯一工作基準。  
> 重要：目前先不要修改 `Thesis.tex` 或論文 PDF。必須先完成架構、資料協定、Judge 與實驗矩陣的同步。

---

## 0. 遠端 Session 必須先遵守的指令

1. 先讀完整份文件，再檢查實際程式碼；不要根據舊論文、舊架構圖或舊 log 推測現行行為。
2. 第一階段只做 code audit、實作缺口清單與重跑計畫，不修改論文。
3. 不得把舊 GPT-4o、raw-asset、text-only、R2 renderer 或五輪 refinement 的結果混入新主實驗。
4. 新實驗不得覆寫舊輸出。每一個 run 必須有獨立資料夾與完整 manifest。
5. 不得使用 Crello GT 的座標、bbox、字級或版面位置作為 AgentLayout 的輸入。
6. 不得使用固定 `0.5` 的 CLIP placeholder 作為有效指標；未實作就移除該 claim。
7. 所有 ablation 必須只改一個變因，使用相同 sample IDs、模型 snapshot、renderer、Judge protocol 與 seeds。
8. 所有成本、失敗、parse error、missing element 與 skipped sample 都要留下紀錄，不能只報成功樣本。
9. 遇到舊文件與程式碼衝突時，以程式碼和實際 trace 為準，並把衝突列入 audit。
10. 沒有完成 N=20 gate 前，不直接花錢跑 N=100。

---

## 1. 研究定位

論文名稱暫定為 **AgentLayout**。研究問題是：給定 background image、foreground assets 與 user brief，如何先理解 foreground elements 的 semantic relations，再產生 content-aware foreground layout。

核心定位不是「使用多個 Agents」，而是：

> A training-free, explicit-structure approach for content-aware foreground layout generation.

中文理解：一個以 explicit structure 為核心、training-free 的 content-aware foreground layout generation 方法。

方法主張：

1. 現有方法可能做到幾何整齊，卻沒有明確表示 foreground elements 之間的 semantic dependency。
2. AgentLayout 在決定座標前先建立可檢查的 Layout Tree。
3. Layout Tree 將 semantic reasoning 與 coordinate placement 分開，形成 reason-then-place 流程。
4. Deterministic verification 負責可精確檢查的幾何條件；MLLM 負責內容理解與視覺判斷。
5. 系統是 training-free，不訓練新的 projection layer、adapter、layout model 或 Judge。

任務邊界：

- 是 foreground layout generation。
- 輸入為已存在的 background 與 foreground assets。
- 不生成新的產品圖、裝飾圖或完整 graphic design。
- R3 將文字視為可放置的 bitmap asset，但仍保留原始文字內容供語意理解。
- 不應宣稱 designer parity、aesthetic SOTA 或完整 graphic-design synthesis。

---

## 2. 已確認的主要問題

### P0-1：架構與實驗版本混用

舊實驗涵蓋多種不同架構：舊 LayoutGenerator、Step75 Composition Director、raw foreground、SEGA-style text-only、不同 renderer、不同 Judge prompt、不同 loop 深度。這些結果不能直接放在同一張主表。

必須建立明確版本矩陣，至少記錄：

- Architecture version
- Model snapshot
- Foreground protocol
- Renderer version
- Loop policy
- Internal Judge
- Evaluation Judge
- Dataset IDs
- Prompt hash
- Code commit/hash

### P0-2：LLM Judge 的可信度不足

論文已證明 LLM Judge 有 calibration drift，卻又大量依賴單一 LLM Judge。這會形成自我削弱的 evaluation design。

處理原則：

- Internal Judge 只作系統內候選選擇與一次修復建議。
- Offline Evaluation Judge 不得是唯一證據。
- 最終主張需要 human preference study。
- 自動評估必須 matched：同一個 Judge、prompt、session 共同評 candidate 與對照。
- 若生成與評估都使用 GPT-5.4 mini，必須明確揭露 self-preference 風險；不建議把它作為唯一 Evaluation Judge。

### P0-3：舊 refinement loop 是 net negative

既有證據顯示：

- N=20 refinement 無明顯 lift，並有 completion regression。
- N=178 full trace 多數樣本走滿五輪仍不收斂，整體品質下降。
- Step89 顯示主要增益來自 R0 best-of-3；第一輪修復只有小幅正向趨勢，後續輪次會破壞 alignment。
- 舊的 `threshold=35`、連續兩次 ACCEPT、最多五輪與 issue ledger 不應直接成為新系統預設。

因此新系統只能測試 **單輪、gated、best-so-far** 的修復，不再使用開放式多輪 reject loop。

### P0-4：CLIP semantic relevance 尚未實作

現有 `semantic_relevance=0.5` 是 placeholder，不是實驗結果。CLIP embedding 也不能直接交給一般 LLM 理解，除非訓練 projection layer，這會破壞 training-free 定位。

新架構決策：

- Analyst 直接定位為 MLLM，觀看 foreground assets。
- CLIP 不負責主要 semantic understanding。
- 若未來實作 CLIP，只可作 relevance、retrieval、duplicate detection 等輔助訊號。
- 未實作前，從正式架構圖、方法 claim 與實驗表移除。

### P1-1：Crello 一般樣本難以顯示 Layout Tree 效果

目前 Step90 的 SGC/TLC/PCA 只有約 34--40/100 樣本有效。大量樣本只剩 1--2 個文字元素，或 Layout Tree 退化成全 singleton／單一 group。

根因不是只有資料集本身，也包含舊 SEGA-style preprocessing：非文字 foreground 被合進 background，只留下文字 placement，導致 product--price--headline 關係消失。

目前 Step90 只能作 preliminary self-consistency evidence，不能作正式 Layout Tree superiority claim，因為：

- 使用 Agent 自己推論的 tree 評估 Agent 自己的 layout。
- 缺少 human reference tree。
- 缺少 No Tree / Flat Roles / Oracle Tree ablation。
- 有效樣本太少，三個 metric 方向有利但未達顯著。

### P1-2：目前缺少 Layout Tree 的直接因果證據

核心 claim 是 semantic structure，但主要指標長期集中在 alignment、overlap、readability、occlusion 與 COLE aesthetic。這些不能直接回答「Layout Tree 是否讓相關元素形成更合理的群組」。

必須補：

- Tree prediction accuracy
- Layout realization metrics
- Controlled Layout Tree ablation
- Human semantic-grouping preference

### P1-3：R3 可能產生 GT leakage

Text-as-image 可以保留字型外觀，但若直接保留 Crello 原始文字 bitmap 尺寸與位置，可能洩漏 designer 的字級與 bbox。

公平規則：

- 保留文字像素、字型風格、顏色與 aspect ratio。
- alpha-tight crop，移除原始空白與位置資訊。
- 所有文字 bitmap 用相同 deterministic normalization，例如 long edge 統一到固定像素。
- 不輸入 GT x/y、GT final bbox、GT font size 或原始 placement。
- 最終 bbox、scale 與位置由 AgentLayout 預測。
- 原始文字 `content` 必須保留給 Analyst/Planner 理解。

### P1-4：Baseline 與主結果 protocol 不一致

引用其他論文表格的數字，只能作 indicative reference，不能當 direct comparison。若 baseline 沒有在相同資料、renderer、metrics 與 evaluation protocol 下重跑，不可宣稱勝過 SOTA。

### P1-5：readability、occlusion 與 saliency-aware placement 仍弱

Crello 與 PKU 都出現相似缺口，顯示問題在系統而非單一資料集。需要至少檢查：

- shrink-to-fit
- text bbox 與 bitmap scaling
- safe-zone / saliency coupling
- banner / portrait aspect-ratio handling
- text contrast 與 busy texture

這些改動必須各自做 ablation，不能和模型、renderer、loop 同時更換後共同歸因。

### P1-6：closed-source single-model dependence

新系統可以使用 GPT-5.4 mini，但論文要誠實說明 closed-source dependency。至少保存完整 prompts、model snapshot、structured outputs、rendered artifacts 與 deterministic metrics，降低不可重現風險。

### P1-7：架構圖、文件與實作存在 stale descriptions

舊文件仍可能描述：

- Analyst 看不到圖
- Judge 是唯一 MLLM
- Generator 有不存在的 +/-10% drift cap
- 舊五輪 loop
- 舊四軸或五軸 Judge
- Asset Planner / Analyst 的責任範圍不一致

完成 code freeze 後才能同步論文與架構圖。

---

## 3. 準備凍結的新架構

### 3.1 配置代號

候選最終配置：

```text
A3-MLLM / M-5.4mini / P-Full / R3 / L1-Gated
```

代號定義：

- `A3-MLLM`：reason-then-place 多階段架構，Analyst 為 MLLM。
- `M-5.4mini`：系統內生成與 Internal Judge 使用固定 GPT-5.4 mini snapshot。
- `P-Full`：所有 foreground elements 保持分離並由 AgentLayout 放置。
- `R3`：文字以 bitmap asset 渲染，同時保留文字內容供語意理解。
- `L1-Gated`：最多一次、有明確觸發條件的修復；無開放式多輪 loop。

注意：`R3` 是 renderer/protocol 版本，不是 refinement round 3。Loop round 必須寫成 `R0`、`R1`。

### 3.2 Model freeze

建議固定：

```text
gpt-5.4-mini-2026-03-17
```

Analyst、Asset Planner、Composition Director、Coordinate Mapper 與 Internal Judge 都使用這個 exact snapshot。Offline Evaluation Judge 另行固定，不跟著系統模型自動更換。

不要直接使用會更新的 `gpt-5.4-mini` alias 跑正式實驗。

每個 Agent 的 reasoning effort、temperature、max tokens、image detail 與 structured-output schema 必須寫入 manifest。若 API 不支援某參數，不得用近似值默默替代，必須記錄實際設定。

### 3.3 Foreground protocol：P-Full

輸入應包含：

- Background image
- Text elements
- Product/person/object images
- Logos
- Underlays/panels
- Other placeable foreground decorations
- User brief

規則：

1. 所有 placeable foreground assets 保持分離。
2. 不把 product、logo、underlay 或 decoration 按 GT 位置預先合進 background。
3. 唯一可作 background 的是明確的 base/background layer。
4. 全畫布裝飾若本質上是背景，可依事先固定規則分類，但不得看模型輸出後改分類。
5. 每個 asset 使用穩定 `asset_id`，所有 Agent、renderer、metrics 共用。
6. 每個 input sample 保存 `asset_manifest.json`，記錄來源、類型、hash、原始尺寸與 normalized 尺寸。

### 3.4 Text-as-image protocol：R3

每個文字元素同時具有：

```json
{
  "asset_id": "text_01",
  "content": "SUMMER SALE",
  "bitmap_ref": ".../text_01.png",
  "bitmap_aspect_ratio": 3.42,
  "semantic_type": "headline"
}
```

正式規則：

1. 使用 RGBA text bitmap 保留字型、字重、顏色與形狀。
2. 對 alpha channel 做 tight crop。
3. 加固定 padding，避免邊緣像素被裁掉。
4. 依固定 long-edge 尺寸正規化；正式值必須在 smoke 前凍結並寫入 manifest。
5. 不提供原始 Crello x/y 或 final bbox。
6. 不用原始 bitmap pixel size 當作最終字級。
7. Mapper 預測 final bbox；renderer 將 bitmap 等比例縮放進 bbox。
8. 所有 ablation 與 GT comparison 使用同一套 bitmap renderer。

### 3.5 Agent 與 deterministic modules 的責任

| Stage                | 類型             | 看到的資訊                                                   | 主要輸出                                                    | 不負責                |
| -------------------- | ---------------- | ------------------------------------------------------------ | ----------------------------------------------------------- | --------------------- |
| Background Analyzer  | Deterministic CV | background                                                   | saliency、safe zones、palette、contrast、panels             | semantic grouping     |
| Analyst              | MLLM             | brief、background overview、所有 foreground thumbnails、text content | DesignSpec、asset descriptions、semantic roles、constraints | 座標、最終美學評分    |
| Asset Planner        | LLM/structured   | DesignSpec、asset descriptions、stable IDs                   | Layout Tree                                                 | pixel-level placement |
| Composition Director | MLLM             | background、Layout Tree、DesignSpec、foreground summaries    | 3 個 spatially distinct composition concepts                | 精確 bbox             |
| Coordinate Mapper    | MLLM             | concept、Layout Tree、background、CV cues、asset geometry    | 每個元素的 bbox/scale/z-order                               | 修改語意角色          |
| Quality Checker      | Deterministic    | candidate、canvas、CV cues                                   | violations、valid/invalid                                   | aesthetic preference  |
| Internal Judge       | MLLM             | rendered candidate images、structured context                | candidate selection、最多一次具體 critique                  | 論文最終客觀評估      |
| Renderer             | Deterministic    | assets、candidate bbox/z-order                               | final PNG                                                   | semantic reasoning    |

### 3.6 Analyst MLLM 的固定輸入與輸出

Analyst 必須看到 foreground image content。建議每個 sample 產生帶 `asset_id` 標籤的 contact sheet，並同時提供單張 background overview。

Analyst 應輸出：

- `visual_content`
- `object_category`
- `dominant_colors`
- `orientation`
- `contains_text/logo/person/product`
- `semantic_role`
- `role_confidence`
- `relation_candidates`
- `hard_constraints`
- `soft_constraints`

Analyst 不輸出 bbox。Asset Planner 根據 Analyst 的結構化結果建立 Layout Tree。

同一份 asset description 以 file hash 快取，避免重跑時重複花費，但 cache key 必須包含 model snapshot 與 prompt hash。

### 3.7 Layout Tree contract

Layout Tree 必須是座標生成前的明確中間表示。每個 node 至少包含：

- stable element/group ID
- node type
- semantic role
- parent ID
- children IDs
- relation type
- ordering/priority（若適用）
- confidence

純裝飾元素可標為 `decorative`，但不可因為難評估就從 renderer 或 completeness metric 中消失。語意 metric 可以事先規定排除哪些 decorative relation，但規則要固定。

---

## 4. 新 Judge 與 Loop 規格

### 4.1 必須區分兩種 Judge

#### Internal Judge

- 模型：`gpt-5.4-mini-2026-03-17`
- 用途：R0 候選選擇、一次修復建議、B0/B1 擇優
- 屬於系統架構
- 可以和生成 Agents 使用同一模型
- 不能作為論文唯一 Evaluation Judge

#### Offline Evaluation Judge

- 不參與生成或 loop
- 優先保留既有 matched GPT-4o protocol 以維持評估連續性，但正式 run 必須記錄 exact model ID
- 所有 candidate、GT、baseline 必須在同一 protocol 下重新評
- 若成本允許，增加第二個獨立 MLLM 做 robustness check
- 最終主張仍需 human preference study

### 4.2 L0 定義

```text
L0 = best-of-3 candidate selection, no aesthetic repair
```

允許 schema parse retry 與 invalid-output retry；這些是執行可靠性機制，不算 aesthetic refinement。

### 4.3 L1-Gated 定義

完整流程：

```text
Analyst (run once)
  -> Asset Planner (Layout Tree, freeze)
  -> Composition Director (3 concepts)
  -> Coordinate Mapper (3 candidates)
  -> Deterministic QC
  -> Judge-Select chooses B0
  -> Gate
       no actionable issue -> output B0
       actionable issue    -> one targeted repair -> B1
  -> verify B1
  -> keep B0 or B1
  -> unconditional stop
```

### 4.4 Judge-Select 與 Judge-Critic 必須解耦

`Judge-Select`：

- 同時看 3 個 R0 renders。
- 只排序與選出 B0。
- 不要求努力找缺陷。
- 不使用舊 `total >= 35` acceptance threshold。

`Judge-Critic`：

- 只看 B0。
- 不打 overall score。
- 最多輸出 2 個具體、element-level、可執行問題。
- 模糊意見如「不夠漂亮」「缺少創意」不得觸發 repair。
- 問題必須指出 target element(s)、issue type、desired change，能轉成 verifier 或明確 revision instruction。

這兩次呼叫不能合併，避免「同時要求找缺陷」污染 candidate selection。

### 4.5 Repair gate

允許觸發的問題：

- overlap / clipping / out-of-bounds
- alignment / spacing / lockup
- text too small / illegible / poor contrast
- text on high saliency / busy region
- 明確 composition hierarchy 錯誤
- 與 Layout Tree 可驗證地不一致

不得觸發：

- 只有低分，沒有具體問題
- innovation/originality 等無法定向修復的批評
- 同一問題需要多輪探索
- 要求重新解釋素材 semantic role

### 4.6 Repair routing

| 問題                                                  | 路由                                            |
| ----------------------------------------------------- | ----------------------------------------------- |
| bbox、spacing、alignment、scale、contrast             | Coordinate Mapper                               |
| group placement、global hierarchy、composition region | Composition Director -> Coordinate Mapper       |
| semantic role / Layout Tree 推論錯誤                  | 不進 runtime loop；記為 Analyst/Planner failure |
| schema / missing field                                | schema retry，不計 aesthetic repair             |

Analyst 與 Layout Tree 在一個 sample 中只執行一次並凍結。這可避免 Judge 每輪推翻語意計畫，也讓 ablation 可比較。

### 4.7 Best-so-far guard

B1 必須同時符合：

1. 原問題已被 deterministic verifier 或明確 check 判定改善。
2. 沒有新增 hard violation。
3. completeness 不下降。
4. 若 B0/B1 優劣仍不明確，再做一次 pairwise internal selection。

不符合就輸出 B0。修復後無論結果如何都停止。

### 4.8 明確移除的舊 loop 行為

- 移除連續兩次 ACCEPT 才停止。
- 移除最多五輪的 aesthetic loop。
- 移除多次 reject 後回 Analyst。
- 移除固定 35/50 threshold。
- 移除為多輪設計的 open-issue ledger；單輪可保留 target issue 與 KEEP constraints，但不循環累積。
- 不再把 `loop off` 用來同時表示「visual observer off」與「完全沒有 refinement」。一律使用 `L0` / `L1-Gated`。

---

## 5. Crello 資料與 Layout Tree 評估

### 5.1 兩條資料軌

#### Crello-General

- 目的：整體 foreground layout 品質。
- 固定 random N=100 test samples。
- 不用 semantic richness 篩選。
- 評估 geometry、readability、occlusion、completion、cost、overall preference。

#### Crello-Relation

- 目的：直接測 Layout Tree。
- 目標 N=100；先做 N=20 pilot。
- selection 在生成前完成，不能看 candidate 或 model score。
- 由完整 test split 先用 input metadata 篩選，再做人類 semantic annotation。

### 5.2 Crello-Relation 自動初篩

建議固定條件：

1. 至少 5 個 placeable foreground elements。
2. 至少 3 個 text elements。
3. 至少 1 個非文字 foreground image/logo/product/object。
4. 排除只有 full-canvas background、純裝飾或無可判斷內容的樣本。
5. 所有條件只看 input metadata 與 asset content，不看 GT coordinates 或 model outputs。

門檻在 pilot 前凍結。若樣本不足，只能放寬並記錄規則版本，不能根據結果挑選。

### 5.3 Human reference tree annotation

標註者只看：

- User brief
- Foreground assets
- Text content
- Asset IDs

標註者不能看 designer GT layout，避免用設計師位置反推 semantic relations。

每個樣本至少兩位標註者。標註：

- semantic role
- same-group relations
- parent-child relations
- group labels
- uncertain/ambiguous flag

分歧由第三位或共同 adjudication 解決。保留原始 annotation 與 final reference tree。

有效 Crello-Relation 樣本至少要有：

- 兩個 semantic groups，且
- 至少一個 non-singleton group，且
- 至少一個可和其他 group 比較的 relation。

### 5.4 Tree complexity strata

依 human reference tree，而不是 predicted tree 分層：

- `Simple`：全 singleton 或只有單一 group。
- `Medium`：一個 non-trivial group。
- `Rich`：至少兩個 non-trivial groups，或 depth >= 2。

主要分析應呈現 Full Tree 相對 No Tree 的增益是否隨 complexity 增加。這比只報全體平均更能直接支撐研究假設。

---

## 6. Layout Tree Ablation

在 `Crello-Relation`、`L0`、同一模型與同一 R0 budget 下比較：

| Arm                  | 設定                                                  | 回答問題                           |
| -------------------- | ----------------------------------------------------- | ---------------------------------- |
| T0 No Tree           | Mapper 不收 tree，只收 assets/brief                   | 完全沒有 explicit structure 時如何 |
| T1 Flat Roles        | 只提供 title/price/logo 等角色，不提供 grouping/edges | 類別標籤是否已足夠                 |
| T2 Predicted Tree    | Analyst + Asset Planner 正常推論                      | 實際系統的 tree 是否有效           |
| T3 Human Tree Oracle | 使用 human reference tree                             | tree 正確時 placement 的上限       |

若預算不足，優先順序：T0、T2、T3；T1 次之。Random Tree 的解釋力低於 Human Tree Oracle，不是第一優先。

診斷方式：

- T2 > T0：predicted Layout Tree 有貢獻。
- T2 > T1：hierarchy/grouping 不只是 semantic labels。
- T3 > T2：主要瓶頸在 Analyst/Planner 的 tree inference。
- T3 仍不 > T0：Mapper 沒有利用 tree，或資料/metric 不適合。

Tree ablation 一律使用 L0，避免 Judge repair 掩蓋 tree 造成的差異。

---

## 7. 評估指標

### 7.1 Tree prediction

以 human reference tree 評估：

- Same-group pair Precision / Recall / F1
- Parent-child edge Precision / Recall / F1
- Semantic role accuracy
- Tree validity / coverage
- Ambiguous samples 單獨報告，不強迫算成錯誤

### 7.2 Layout realization

SGC、TLC、PCA 必須改用同一份 human reference tree 評估所有 arms，不再用各 arm 自己的 predicted tree。
計算方法在 layout_agent/Metrics.md

- SGC：同 group 是否比異 group 緊密。
- TLC：同組元素是否通常比異組元素更接近。
- PCA：parent-child adjacency 是否反映在版面。

所有 skip reasons 必須報告。Simple/Medium/Rich 分層報告有效 N。

### 7.3 General geometry

使用同一實作、同一輸入定義計算：

- Alignment
- Overlap
- Underlay metrics（僅適用時）
- Readability
- Occlusion
- Completeness
- Out-of-bounds / clipping
- text-on-panel / saliency-related diagnostics

不得把不同 pipeline 的同名 metric 當作可直接比較。

### 7.4 Preference evaluation

至少包含：

- Overall preference
- Readability
- Semantic grouping clarity

Human study 最低可行規模：

```text
50 pairs x 3 questions x 3 independent ratings = 450 judgments
約 15 participants，每人 30 judgments
```

建議分配：

- 25 個 Crello-Relation：T2 Predicted Tree vs T0 No Tree。
- 25 個 Crello-General：Final AgentLayout vs designer GT 或最重要 baseline。
- 圖片左右順序隨機、雙盲、不顯示方法名稱。

### 7.5 Statistical reporting

至少報：

- paired mean/median difference
- 95% bootstrap confidence interval
- win/tie/loss
- exact sign test 或適當 paired test
- valid N 與 skipped N

不能只用平均值下結論。

---

## 8. 正式執行順序

### Phase 0：Code audit 與 freeze

遠端 session 第一個 deliverable 必須是：

1. 找到實際 code repository。
2. 對照本文件逐項列出 implemented / partial / missing / stale。
3. 確認每個 Agent 實際 input、output、model 與 image attachment。
4. 確認 R3 是否移除 GT position/size leakage。
5. 確認 P-Full 是否真的沒有預合成非文字 foreground。
6. 確認 legacy loop 可完全關閉，並能實作 L0/L1-Gated。
7. 建立 run manifest schema。
8. 在任何付費實驗前提交實作差距與修改計畫。

### Phase 1：N=5 smoke

目的：只驗證資料與管線，不判斷研究效果。

檢查：

- Analyst 確實看到 background 與所有 foreground。
- Asset IDs 全 pipeline 一致。
- Layout Tree 在座標前產生。
- 3 concepts 真的 spatially distinct。
- 3 candidates 都完整 render。
- R3 字型可讀、alpha crop 正確、無 GT bbox leakage。
- L0 與 L1-Gated 都能停止。
- 所有 trace、cost、model ID、prompt hash 落盤。

### Phase 2：N=20 gates

#### Gate A：Analyst vision

比較：

- Analyst text/metadata only
- Analyst MLLM with foreground/background

主要看 human-tree same-group F1、edge F1 與 role accuracy。若 vision 沒改善，先檢查 contact sheet、asset mapping 與 prompt，不直接上 N=100。

#### Gate B：Layout Tree

在 Crello-Relation N=20 跑 T0/T2/T3，固定 L0。

升級到 N=100 的條件：

- T2 相對 T0 在 SGC/TLC/PCA 至少兩項方向有利；且
- T2 的 human semantic-grouping preference 勝多於負；且
- T3 能提供可解釋的 upper-bound 訊號。

若 T3 也無效，優先檢查 Mapper 是否真的使用 tree，不要擴大樣本。

#### Gate C：Loop

在同一批 Crello-General N=20、相同 R0 candidates 上比較：

- L0：直接輸出 B0
- L1-Gated：從同一 B0 修一次

升級條件：

- L1 win > loss；
- completion 不下降；
- alignment/overlap/completeness 無系統性退化；
- 修復問題 compliance 高；
- 每 sample 成本可接受。

若未通過，最終配置改成 `L0`，不保留 loop 只為符合原始構想。

### Phase 3：N=100 正式實驗

只有通過 gate 的配置可進正式實驗。

必跑：

1. Crello-General N=100 final system。
2. Crello-Relation N=100 T0/T1/T2/T3（預算不足可省 T1）。
3. L0 vs L1-Gated N=100（只有 Gate C 通過才跑）。
4. Deterministic geometry metrics。
5. Human-reference SGC/TLC/PCA。
6. Tree prediction metrics。
7. Matched offline Judge evaluation。
8. Human preference study。
9. Cost、latency、completion 與 failure analysis。

### Phase 4：Baseline

理想情況：在同一 P-Full/R3 input-output contract、同一 renderer、同一 metrics 下重跑可取得的 baseline。

若 baseline 不能重跑：

- 只作 literature reference。
- 不放入 direct win/loss 主張。
- 主要比較改為 controlled ablation、designer GT matched evaluation 與 human study。

### Phase 5：論文同步

只有實驗與架構 freeze 後才修改：

- Abstract
- Method
- Architecture figure
- Implementation details
- Experimental setup
- Main tables
- Ablations
- Limitations
- Conclusion

每一個數字都要能追到 run manifest 與 artifact。

---

## 9. Run Manifest 最低欄位

每個 run 必須保存：

```json
{
  "run_id": "...",
  "timestamp": "...",
  "code_commit_or_hash": "...",
  "architecture": "A3-MLLM",
  "model_snapshot": "gpt-5.4-mini-2026-03-17",
  "foreground_protocol": "P-Full",
  "renderer": "R3",
  "loop": "L0 or L1-Gated",
  "internal_judge": "...",
  "evaluation_judge": "...",
  "dataset_split": "...",
  "sample_ids_file": "...",
  "seed": 42,
  "prompt_hashes": {},
  "schema_versions": {},
  "image_normalization": {},
  "cost": {},
  "completion": {},
  "errors": []
}
```

候選、tree、DesignSpec、Judge raw JSON、render、QC violations 與 final selection 都要逐樣本保存。

---

## 10. 舊結果的使用規則

舊結果可以用來：

- 說明為何放棄五輪 refinement。
- 說明 Judge drift 與 protocol sensitivity。
- 說明舊 text-only Crello 對 Layout Tree metric 的 coverage ceiling。
- 提供 failure modes 與新實驗設計動機。

舊結果不能用來：

- 直接代表 GPT-5.4 mini 新系統效果。
- 和 P-Full/R3 新結果放在同一欄比較。
- 宣稱 L1-Gated 已改善，因為目前只有舊 loop 證據。
- 宣稱 Layout Tree 勝過 GT，因為 Step90 使用 predicted tree 作自我一致性量測。
- 宣稱跨論文 absolute LLM Judge score 可比。

---

## 11. 預期可支持與不可支持的論文主張

若新實驗通過，可支持：

- Training-free foreground layout generation。
- MLLM-based semantic understanding before placement。
- Explicit Layout Tree as an inspectable intermediate representation。
- Deterministic geometric verification。
- Layout Tree 在 relation-rich samples 上改善 semantic grouping。
- Matched evaluation 與 human study 揭露系統的 strengths and limitations。

除非有新證據，不可支持：

- 全面勝過 designer。
- Aesthetic SOTA。
- 跨論文 absolute score 排名。
- Full refinement loop improves quality。
- CLIP 提供有效 semantic relevance。
- 所有 Crello samples 都需要深層 Layout Tree。
- GPT-5.4 mini Judge 等同人類偏好。

建議誠實表述：

> AgentLayout emphasizes explicit semantic structure and auditable constraint satisfaction. Its advantage is expected to be strongest on layouts containing non-trivial relations among multiple foreground assets, while typography, saliency-aware placement, and expressive composition remain limitations.

中文理解：

> AgentLayout 強調明確的語意結構與可檢查的限制條件。它的優勢預期主要出現在包含多個前景素材、且素材之間具有非平凡關係的版面；字體呈現、saliency-aware placement 與更具表現力的構圖仍是限制。

---

## 12. 遠端 Session 的第一個回覆格式

遠端 session 讀完後，不要立刻改 code。第一個回覆必須包含：

1. 找到的 code repository path。
2. 目前實際架構流程圖或文字流程。
3. 本規格每一項的 `implemented / partial / missing / conflicting` 表格。
4. P-Full、R3、Analyst MLLM、L0/L1-Gated 的實作缺口。
5. 舊 caches/results 哪些必須隔離。
6. 需要修改的檔案清單，但先不要修改。
7. N=5 smoke 的 exact commands、輸入、輸出與預估成本。
8. 任何會造成 GT leakage、版本混用或 self-preference 的風險。

完成以上 audit，確認後才開始實作與重跑。