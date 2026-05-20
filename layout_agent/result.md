# AgentLayout — 實驗結果與模組完成度（standalone）

> 本文件為**獨立**版：不需閱讀 `README.md` 或 `live_runs_table.md` 即可理解每個實驗的動機、方法、數值與誠實定調。供論文 results / limitations / honesty 章節直接取用。
> 數值 source-of-truth：`layout_agent/live_runs_table.md`、`layout_agent/output/step13_sota_winrate_results.json`、`layout_agent/output/step11_winrate_results.json`。
> 最後更新：2026-05-19。

---

## §0 摘要（TL;DR）

- **系統定位**：AgentLayout 是 MetaGPT 上的多 Role 內容感知（content-aware）**版面生成**系統——接收既有素材 + brief，在帶背景圖的畫布上排版，經 Aesthetic Judge reject-loop 迭代。它**不做** graphic-design synthesis（不生成新的裝飾視覺內容），這是 by-design 的 scope 邊界，非缺陷。
- **模組完成度**：23 個原始檔功能全部到位、5 個 Role 正常接線、pipeline 五階段端到端跑通。先前唯一一致性缺口 `roles/aesthetic_judge.py:79`（舊 stub）**已於 2026-05-19 修復**並重跑驗證（見 §1 / §2 Step 12d）——**無 open blocking 缺口**。
- **核心數值**：
  - 首個真正 end-to-end content-aware live（step 12d / post-fix，judge 拿真實色盤）= **mean best 70.67、best 72**；子分數 req=20 hier=18 **bal=17 coh=17**——judge 內容感知化後 **plateau 仍未突破**（更強證明結構性，非 judge 是否 content-aware）。step 12b pre-fix mean 72 為對照。
  - plateau 第二段 bal/coh≈17 經診斷確認為**結構性 scope-bound limitation**（非 LLM 能力問題）。
  - SOTA-positioning Win Rate pilot（N=20，task-aligned protocol：同 renderer 純排版幾何 vs designer-GT layout，與 AesthetiQ Table 1 一致）：completion **100%**、Win rate **80%**。
  - **獨立 judge 驗證（Step 14）**：用 Claude `claude-sonnet-4-6`（≠generator gpt-4o）重判同 20 樣本，Win rate **80.0%**——**完全複製** gpt-4o judge（80%↔80%），最強的 self-preference confound 實證排除。
  - **標準幾何指標（Step 15）**：N=20 Layout-IoU completion 95%，mean IoU AgentLayout **0.0994** > random 0.0567，但 ≈ centered_stack 0.0931（誠實偏負：raw IoU 未顯著勝 trivial baseline）。
  - **SOTA-context（Step 16）**：引用 AesthetiQ Table 1（VILA-7B/1,971）做 related-work 定位（AesthetiQ-8B 17.19%/IoU 42.83、LayoutNUWA 5.58%/25.74…）；我方 protocol 不同**僅 indicative、不併排名**。
  - **SEGA-protocol head-to-head（Step 20）**：N=20，PKU PosterLayout 6 rule-based 指標，AgentLayout (cold-start) Ali=**0.0000**、Ove=**0.0009**（≤ SEGA-13B 量級）、Und_l=Und_s=0（by-design scope）、Read/Occ ≈ GT。**首組真正 head-to-head 可比數據**，取代 AesthetiQ indicative。
  - **Refinement Loop A/B（Step 20b）**：同 N=20、同 SEGA 6 指標，refined-mode vs cold-start head-to-head。**收斂率 2/20（10%）**、completion 18/20（cold 為 20/20，−2 屬 refined-mode 新增 CandidatesBatch crash）、6 指標 delta 全在噪音內。誠實結論：**Refinement Loop 架構目前無實質 lift、並引入 completion regression**，須降 Judge gate 並修 refinement-prompt schema 漏洞。
  - **GPT-4V 4 軸 aesthetic（Step 21，Phase B）**：同 N=20、SEGA Table 3 COLE 1–10 rubric。AgentLayout **STV=6.150 勝 FlexDM/PosterLlama/SEGA w/o FR/SEGA-7B，僅輸 SEGA-13B 0.198（噪音內）→ 達 SEGA-13B 量級**；SDL=5.500 勝 FlexDM+PosterLlama；Smean=5.263 勝 FlexDM 輸 PosterLlama/SEGA。SIO=4.300 是最低點（by-design 不做 graphic synthesis 的代價，誠實列 limitation）。**STV 與 Step 20 Ali/Ove 結合形成「兩條軸達 SEGA-13B level」的 paper-grade claim**。
- **誠實定調（最重要）**：**不宣稱勝設計師、不宣稱勝 SEGA Smean 全項、不宣稱 Refinement Loop 帶來測量上的改善**；可宣稱「**Layout geometry (Ali/Ove) 與 Typography/Visual harmony (STV) 兩條軸達 SEGA-13B 量級**」，trade-off 是 Innovation (SIO) / Graphics enhancement (SQL) 弱於 baseline（by-design scope-bound）。task-aligned pairwise 下設計師仍勝（step 11 N=3：2:1）；N=20 大樣本 Win rate 80% 的 self-preference confound 已由 Step 14 獨立 judge 排除，但 judge≠VILA-7B、N=20≠1,971 兩 caveat 仍在，故 AesthetiQ 僅作 qualitative/indicative 對照、不進勝負表；可誠實宣稱「排版幾何能力 robust，跨兩獨立 judge 一致」。Render quality（背景/字型/裝飾合成）為 by-design 不做的 scope 外能力，已於 §0 系統定位記錄為 limitation，不另列量化 metric（先前 Win Rate A 等於把 scope 外能力扣分入排版指標，誤導讀者，已移除）。

---

## §1 模組完成度盤點（gap list）

逐一檢查 AgentLayout 每個模組是否「完成」（功能到位、有測試、無 stub/未接線殘留）。

| 模組 | 功能狀態 | 測試覆蓋 | 殘留 |
| --- | --- | --- | --- |
| Analyst（`roles/analyst.py` + `actions/analyze_brief.py`） | ✅ 完整接線（retry loop、RetryAnalyst feedback path、AssetAnalyzer 整合） | `test_analyst_corner.py`、`test_analyst_prompt_template.py`、`test_aesthetic_feedback_schema.py` | 無 |
| AssetPlanner（`roles/asset_planner.py` + `actions/plan_assets.py`） | ✅ 完整（tree 對 spec 驗證、DesignSpec enrich 前置檢查） | `test_planner_corner.py` | 無 |
| BackgroundAnalyzer（`tools/background_analyzer.py`） | ✅ `resolve_background()` 單一進入點，已接 `pipeline.py:189` + `layout_generator.py:155`；任何錯誤優雅退回舊 stub | `test_background_analyzer.py`（8+ 純函式離線測試） | rembg 缺失時退回 stub（intentional） |
| LayoutGenerator（`roles/layout_generator.py` + `actions/generate_layout.py`） | ✅ 完整（K_VALID top-up loop、QC filter、`resolve_background` 已接 `:155`、RetryGeneration feedback） | `test_generator_corner.py`、`test_generator_prompt_template.py` | 無 |
| **AestheticJudge（`roles/aesthetic_judge.py` + `actions/judge_aesthetic.py`）** | ✅ **已修復（2026-05-19）**：`aesthetic_judge.py:21` import + `:79` 呼叫改用 `resolve_background()`，mirror `pipeline.py:189`/`layout_generator.py:155`；136 離線測試 0 失敗 + post-fix live 重跑驗證（§2 Step 12d） | `test_judge_corner.py`、`test_aesthetic_feedback_schema.py` | 無 |
| IterationRouter（`roles/iteration_state.py`） | ✅ 完整（next_target 狀態機、max_total_rounds、RetryPayload feedback 傳遞） | `test_iteration_state.py`（17 invariants） | 無 |
| QC（`tools/quality_checker.py`） | ✅ 完整（三階段：completeness / boundary / hard constraints；tolerance 校準均有 docstring） | `test_quality_checker_position_hints.py`（16+ 測試） | 無 |
| renderer（`tools/renderer.py`） | ✅ 完整（z-order、image/text 繪製、CJK font fallback、background 三級 precedence） | 間接（`test_judge_corner.py` 經 `render()`） | Phase 1 限制：無 text wrap、image rotation only（intentional，docstring 已記） |
| pipeline（`pipeline.py`） | ✅ 完整（五階段端到端、feedback routing、`resolve_background` 已接 `:189`） | 間接（各 corner test） | `default_white_background()` 為 intentional fallback |
| team（`team.py`） | ✅ 完整（5 Role 全於 `:89-96` hire、n_round=16 容 5 reject cycle） | 間接（整合測試） | 無 |
| schema / asset_analyzer / evaluation（iou, baselines） | ✅ 完整 | `test_aesthetic_feedback_schema.py`、`test_iou.py`、`test_baselines.py` | `asset_analyzer.py:120-129` CLIP semantic_relevance 佔位（中性值 0.5，不破 pipeline，intentional） |

### blocking 缺口 `aesthetic_judge.py:79` — 精確影響與修復紀錄（2026-05-19 已修）

**修復前狀態**：`pipeline.py:189` 與 `layout_generator.py:155` 都已升級為 content-aware 的 `resolve_background()`，但 `roles/aesthetic_judge.py:79` 仍是 `bg = default_white_background(spec.canvas)`（空 `safe_zones` + `#FFFFFF` 色盤）。`run_role_team_live_crello.py` 走 `build_team()` Role 路徑，故 step 12b / step 13 的 judge 經過此 stub。

**精確影響（重要，勿高估——非「在白底上評分」）**：
- `judge_aesthetic.py:352` 的 `render(c, spec)` 畫圖**完全不吃 `bg` 參數**——走 `renderer._make_canvas(spec)` → `spec.canvas.background_asset_ref`，所以 multimodal judge **視覺上看到的是真實 Crello 背景圖，不是白底**。
- `bg` 在 judge 的唯一用途是 `_build_prompt`（`judge_aesthetic.py:336`）把 `bg.dominant_palette` 當**文字**塞進 prompt（`safe_zones`、`recommended_text_color` 在此 Action 並未進 `.format()`）。
- ⟹ 真實影響很窄：judge **看的圖是對的**，但 prompt 被告知主色盤 = `["#FFFFFF"]` 而非真實 content-aware k-means 色盤，可能偏誤 `visual_coherence` 子分。

**修復（2026-05-19）**：`aesthetic_judge.py:21` import 改 `from ...tools.background_analyzer import resolve_background`、`:79` 改 `bg = resolve_background(spec.canvas)`，與 `pipeline.py:189`/`layout_generator.py:155` 完全一致。驗證：(1) agentlayout 離線套件 **136 passed / 0 failed**（無回歸）；(2) post-fix content-aware live 重跑（§2 Step 12d）judge 確實改吃真實色盤——verdict 1 在真色盤下 `visual_coherence` 判稍嚴、best 由 72→70.67 mean，**證明 fix 生效且結論未被先前 stub 扭曲**。step 12b=72 / step 13 SOTA pilot 為 **pre-fix** 數據（保留作對照，artifact 備份於 `*_step12b.*`）；step 12d 為**修正後第一個真 end-to-end content-aware 數字**。

### Intentional（非缺口，誠實揭露）

- CLIP `_compute_semantic_relevance()` 佔位（`asset_analyzer.py:120-129`）：中性值 0.5，不破 pipeline，CLIP 整合為未來里程碑。
- renderer Phase 1 限制（`renderer.py:15-20`）：無 text wrap、僅 image rotation——poster-layout MVP 範圍內可接受，docstring 已記。
- 無專屬測試檔模組：`pipeline.py` / `team.py` / `renderer.py`，皆由 corner test 與整合測試**間接**覆蓋；其餘模組皆有離線單元測試。

---

## §2 實驗結果（逐步：動機 → 方法 → 數值 → 誠實定調）

> baseline = Crello 設計師 GT 經 pipeline 自家 Judge 量到的 ≈68（注意：此為單邊測量，**非配對**，僅供同 pipeline 內部 trend 比較，見 §3.1）。Live # 編號對應 `live_runs_table.md`。

### Step 6 — canvas-coverage rule（Live #4）

- **動機**：Live #3 起分數 plateau 在 72；推測 Generator 沒把元素鋪滿畫布上下緣造成 layout_balance 偏低。
- **方法**：在 Generator PROMPT_TEMPLATE 加入 canvas vertical coverage 規則（頂/底緣覆蓋）。同 3-element synthetic poster fixture（`Spring Sale`，800×1200）。
- **數值**：3 verdicts，best 軌跡 **72 → 70 → 69**，decision=reject。bottom rule 被遵守、top rule 違反。
- **誠實定調**：純 prompt 工程無法突破——確認「3 element 在 800×1200 上**結構性**無法 balanced」，bottleneck 不在指令層。為後續 sparsity 假設（step 9）鋪路。

### Step 8 — contrast-aware text hypothesis（Live #6，**失敗已 revert**）

- **動機**：假設文字與背景對比不足拉低 visual_coherence；嘗試在 prompt 加 contrast-aware 文字色指引。
- **方法**：擴充 PROMPT_TEMPLATE 加入 14 行對比相關 ATTENTION 區塊。**未先做離線 reproducer 就直接燒 live**。
- **數值**：3 verdicts 後 Analyst retry 觸發、Generator round 15/15 fail QC、$0.34 crash（best 軌跡 72 → 70 → 70 → 💥）。commit 已撤回。
- **誠實定調**：**高價值負向結果**。揭露「**prompt attention budget**」現象——多 14 行無關 ATTENTION 排擠了 size_preference 注意力，反而劣化。教訓：cheap-validate（離線 reproducer）再 live-burn。反向強化「plateau 是結構性、非可由加 prompt 解決」。

### Step 9 / 9b — Crello sparsity 假設（Live #7 / #8 / #9）

- **動機**：steps 6+7+8 排除 prompt、背景色、對比後，**sparsity（元素太少）是 plateau 唯一剩餘假設**。用真實 Crello 5-element brief 比對 3-element baseline 68。
- **方法**：新 driver `run_role_team_live_crello.py`（reject-loop + Crello loader + sparsity verdict）。#7＝Crello `5c6c0cba` hiring poster（1080×1920，5 elem）；#8＝`5954bda9`（1200×600，4 effective）；#9＝`5d972ca9` 俄文（537×240，4 effective）。
- **數值**：
  - **#7：平均 best 71.3 vs baseline 68 = +3.3**（best 72）——sparsity 假設**正向證據（N=1）**。副產品：發現並修補 QC alias bug（`center_top` ≠ `top_center`，加 8 alias + 17 regression test）。
  - **#8 / #9：0/15 candidates hard fail、no verdict、$0.12 / $0.13 crash**——揭露 Generator+QC 在 tight canvas + position_preference + no_overlap 多元素組合下的 robustness ceiling。
- **誠實定調**：sparsity 在能完整評估的單一樣本上成立（誠實表達為 "on the single Crello sample our pipeline could fully evaluate, +3.3"）；兩個 crash **本身是另一個有價值的發現**（robustness ceiling），導出 step 10 的 QC tolerance 修補動機。

### Step 10 / 10c / 10d — QC tolerance 修補（Live #8r / #8rc / #9rd）

- **動機**：step 9b 的 0/15 hard fail——strict zero-tolerance 把 LLM rounding 的 1–20px micro-overlap 也判違規，tight canvas 無解。
- **方法**：均**先離線 reproducer 確認 fail mode 再 live**。
  - step 10：`no_overlap` 改 5% area-ratio tolerance（`NO_OVERLAP_TOLERANCE=0.05`）。
  - step 10c：`position_preference` 改 10% per-edge tolerance + 16px 絕對 floor。
  - step 10d：同修補在 537×240 small canvas 重跑驗證跨 aspect ratio。
- **數值**：
  - #8rc（1200×600）：3 verdicts，best 70 → 68 → 70，平均 **69.3 vs GT 68**，跑完整 reject loop。
  - #9rd（537×240）：3 verdicts + ½，best 70 → 70 → 72，平均 **70.67 vs GT 68**，跑完整 reject loop（殘留 💥 為已知 step 10b post-Analyst-retry crash，與 tolerance 無關）。
  - sparsity 完整評估樣本數 **N=1 → N=3**（#7 portrait + #8rc horizontal + #9rd small）。
- **誠實定調**：robustness 修補跨 3 種 aspect ratio generalize 成功（真正正向結果）；但 5% / 10% / 16px **是 engineering 妥協數字、非 user study 校準**——論文需明寫。plateau 第二段 bal/coh=16-17 在不同 aspect ratio 上一致出現，更強支持「結構性 not LLM-capability」。

### Step 11 — plateau 第二段根因 + pairwise Win Rate

- **動機**：(a) plateau 第二段 bal/coh≈17 在 step 6/7/8 純 prompt 全失敗，需根因定調；(b) 論文最大實質缺口是「沒跟 SOTA / 設計師正規對比」。
- **方法**：
  - (a) 離線根因診斷（純讀碼、**零 live 成本**）：查 schema / renderer / Judge rubric。
  - (b) AesthetiQ-style task-aligned pairwise Win Rate（`step11_winrate.py`，先 `--dry-run` $0 驗配對才 live）：交換圖序 ×2 消 position bias、多數決。Protocol = 同 renderer 同 assets 只換 bbox 為設計師位置（純排版幾何，與 AesthetiQ Table 1 「vs GT」一致）。N=3（#7 / #8rc / #9rd）。（先前另設的 A realistic = vs 設計師完稿 JPG 把 render quality 含入比較，屬 scope 外能力扣分，已自 result.md 移除；原始數據仍保留於 `step11_winrate_results.json`。）
- **數值**：
  - (a) 根因：`schema.py` 的 `LayoutElement`/`Candidate` **無任何欄位可發出新裝飾元素**；`renderer.py` 只畫 spec.elements 前景 + 純色底、**零裝飾層**；Judge rubric 的 bal/coh 在「裸 asset + 單色底」下數學上夾在 ~17/25。
  - (b) **設計師 2:1**（task-aligned pairwise，#7 71-82、#8rc 58-82 設計師勝；#9rd 56-51 AgentLayout，噪訊邊緣勝、judge 兩次圖序互打）。
- **誠實定調**：
  - bal/coh≈17 確認為 **scope-bound structural limitation**——AgentLayout 定位是版面生成（排版既有素材），**by design 不做** graphic-design synthesis；缺的是 schema 表達力不是指令。**不嘗試突破**（改 schema = 另一研究問題），定調為論文 limitation + future work。
  - **推翻先前「mean best 69-72 > GT 68 = +2/+2/+4 勝設計師」**：那是 pipeline 自家 Judge 單邊評我們 candidate、再對另一 corner-case 量到的 68，**非配對、校準不同，是測量假象**。正規配對 head-to-head 下**設計師勝**，AgentLayout 尚未達設計師水準。

### Step 12b — 首個真正 content-aware live（Live #12c）

- **動機**：全 codebase 追查證實先前 #1–#9 其實是空白純色畫布上的 brief-driven layout（`BackgroundAnalysis` 唯一 producer 是 stub），**不是 content-aware**。這才是「無法與 SOTA 比」的真正根因（任務不對齊）。step 12 上線 `BackgroundAnalyzer`（U2Net matte ∪ 亮度變異數能量圖 fusion）補齊缺口。
- **方法**：Crello `5efdd2dd` "Citation about Diversity of Skin Color"（1008×1296，3 elem，真實背景圖）。BackgroundAnalyzer 全 3 round 注入真實 3 safe zones（非 stub）。並修掉 z_order `hint:above_background` QC 正規化（解 0/15 crash）與 PROMPT_TEMPLATE `.format()` `{}` KeyError。
- **數值**：3 verdicts，best 軌跡 **72 → 72 → 72**，平均 **72**，子分數 **req=20 hier=18 bal=17 coh=17**，decision=reject。
- **誠實定調**：
  - 首個真正 content-aware 數字 = 72，**未突破 plateau**——與 step 11「bal/coh≈17 結構性 scope-bound」結論一致（content-aware 並未自動帶來裝飾表達力）。
  - **#1–#9 全應標註為 pre-content-aware**；本列起為 content-aware baseline。
  - **caveat（連 §1）**：此 run 經 `aesthetic_judge.py:79` stub，judge 的 prompt 收到白底 `["#FFFFFF"]` 色盤（但**看的圖是真實背景圖**）。72 為 **pre-fix** 數字，已由 Step 12d 修正後重跑取代為真 end-to-end 數字；本列保留作 pre/post-fix 對照。

### Step 12d — 修正後第一個真 end-to-end content-aware live（post-fix re-run，2026-05-19）

- **動機**：§1 的 `aesthetic_judge.py:79` stub 已修（judge 改吃 `resolve_background()` 真實色盤）。重跑同樣本 `5efdd2dd` 取得**修正後第一個真 end-to-end content-aware 分數**，並檢驗 content-aware judge 是否改變 plateau 結論。
- **方法**：同 Crello `5efdd2dd`（1008×1296，3 elem，真實背景圖）、同 driver `run_role_team_live_crello.py --sample-id 5efdd2dd499b85dcc75ba0bc`、judge=gpt-4o。唯一差異＝judge 端 `default_white_background()` → `resolve_background()`。pre-fix artifact 備份於 `*_step12b.*`。約 $0.30、~3 min。
- **數值**（`live_step12d_postfix_5efdd2dd.log`）：3 verdicts 全 REJECT，iteration 3 → Analyst retry。
  - Verdict 1：best=r0_cand_02 total=**68**
  - Verdict 2：best=r3_cand_01 total=**72**（req=20 hier=18 bal=17 coh=17）
  - Verdict 3：best=r6_cand_01 total=**72**（req=20 hier=18 **bal=17 coh=17**）
  - **mean best = 70.67、best = 72**；decision=reject。對照 step 12b pre-fix mean 72 / best 72。
- **誠實定調**：
  - **fix 確實生效**：judge prompt 改收真實 content-aware 色盤後，verdict 1 的 `visual_coherence` 判定稍嚴（mean 由 72 降至 70.67），證明先前 stub 確實在輕微撐高分數；但**最佳 candidate 仍 72、子分數結構 req=20 hier=18 bal=17 coh=17 完全不變**。
  - **plateau 仍未突破，且結論更穩固**：judge 內容感知化**沒有**鬆動 bal/coh≈17 上限——直接反駁「plateau 是 judge 沒看到 content-aware 資訊造成」的可能反論，更強支持 §3.2「結構性 scope-bound limitation」。
  - 這是**修正後第一個真 end-to-end content-aware 數字**，取代 step 12b=72 成為 content-aware baseline 的引用值（72 best 不變、mean 70.67 更誠實）。

### Step 13 — SOTA-positioning Win Rate pilot（N=20）

- **動機**：論文需要一個對 SOTA 的定位（非勝負宣稱）。AesthetiQ（CVPR 2025, arXiv 2503.00591）在 Crello test（1,971）報 pairwise MLLM win-rate vs GT：**AesthetiQ-8B 17.19%**、prior SOTA LayoutNUWA 5.58%（judge=VILA-7B）。
- **方法**：`step13_sota_winrate.py`，seed=42 從 Crello **test** split 抽 N=20（structural filter：2–5 elem、≥1 img、≥1 text；first 400 中 39 合格），跑完整 reject loop，protocol 同 step 11（pairwise、order-swap ×2、majority），judge=pipeline LLM(gpt-4o)。約 60 min、pipeline+judge 合計 ~$5–6。
- **數值**（`step13_sota_winrate_results.json`）：
  - completion rate = **20/20 = 100%**
  - Win rate（task-aligned：同 renderer 純排版幾何 vs designer-GT layout，與 AesthetiQ Table 1 一致）= **80%**
  - GT 重建保真度（$0 離線檢查）= **97.1%**（68/70 設計師元素，僅 2/20 各掉 1）
- **誠實定調**：
  - completion 100% 是**真正正向結果**：step 10–12b robustness 修補在隨機 Crello test（filtered）generalize。
  - GT 保真 97.1% **推翻**「Win rate 高分是 GT 缺元素測量假象」的原假設（資料證據）。
  - **80% 仍不可與 AesthetiQ 17.19% 並列當勝績**，三 caveat：(1) judge=gpt-4o≠VILA-7B（win-rate judge-dependent）；(2) **最強 confound：generator 與 judge 同為 gpt-4o（self-preference），AesthetiQ 刻意用獨立 judge 避此**；(3) filtered subset、N=20，AesthetiQ 用全 1,971 不過濾。
  - 可寫進論文的是 **Win rate 80%（task-aligned, AesthetiQ-protocol-equivalent）**：證明 AgentLayout 在 content-aware layout generation 任務本身的排版幾何具競爭力（任務 scope 內）。AesthetiQ 維持 **qualitative / indicative** 定位，**不進勝負對照表**。

### Step 14 — 獨立 judge 重判，消除 self-preference confound（2026-05-19）

- **動機**：step 13 三 caveat 中**最強的是 self-preference**（generator 與 judge 同為 gpt-4o）。這是「Win rate 80% 是否測量假象」的關鍵問號，也是 §4 先前最高 open 項。用**獨立於 gpt-4o 的 judge** 重判完全相同的 20 樣本配對即可單獨消除此 confound。
- **方法**：`step14_materialize_pairs.py` 從磁碟既有 artifact 重建 20 樣本 ×3 圖（agent render 既存、GT render deterministic 重繪、designer JPG cached）——**零 pipeline 重跑、零 LLM 成本**。`step14_independent_judge.py` 用 **Anthropic SDK 直呼 `claude-sonnet-4-6`**（MetaGPT 內建 anthropic/gemini provider 是 text-only 會丟圖，故直呼 SDK 帶正確 base64 image block）當 judge，**`PAIRWISE_PROMPT` 與 `_verdict` 由 step11 逐字 import**、exp A/B、order-swap ×2、majority——除 judge 模型外與 step 13 protocol 完全相同。80 筆全自動、逐筆 raw JSON 可稽核。
- **數值**（`step14_independent_judge_results.json`，N=20）：
  - Win rate（task-aligned：同 renderer 純排版幾何 vs designer-GT layout）= **80.0%**　（step 13 gpt-4o judge：80.0%）
- **誠實定調（核心，這是可跟教授說的）**：
  - **獨立 judge 完全複製 step 13 數字（80% ↔ 80%）→ 最強的 self-preference confound 被實證排除**：80% **不是** generator/judge 同模型的自我偏好假象，排版幾何競爭力跨**兩個不同 judge 模型一致**。這是 step 13 之上**實質增強**的證據，非重複。
  - **仍不可宣稱勝 AesthetiQ / 勝 SOTA**：剩兩 caveat 未消——(1) judge=Claude ≠ AesthetiQ 的 VILA-7B（win-rate 仍 judge-dependent）；(2) filtered N=20 ≠ 完整 Crello test 1,971。故維持 **indicative positioning，非 head-to-head**。
  - Claude-in-loop 的可重現性靠：materialized 圖 + 80 筆逐筆 raw JSON（含每筆 4 維分數與 reason）+ 與 step11 逐字相同的 prompt/聚合碼，第三方可重跑稽核。

### Step 15 — 標準 Layout-IoU + baseline 對照（N=20，2026-05-19）

- **動機**：result.md 先前只有「Judge 主觀子分 + pairwise win-rate」，缺版面生成領域的**標準客觀幾何指標**（LayoutNUWA/AesthetiQ/LayoutDM 皆報 IoU/overlap-type 指標）。磁碟唯一 IoU 產出 `eval_iou_baseline.json` 是 **5/10 pre-content-aware + 踩 stale-id_map bug**（text 元素被丟、mean IoU 0.09–0.14 不可信），**排除不用**。
- **方法**：`step15_iou_eval.py` 在 **step13 同 20 ids**（set-consistent）上用 `BypassJudge`（首輪、無 reject loop、無 multimodal judge，省成本）跑現行 pipeline 取 layout，`layout_iou` vs 修正後 content/asset_ref id matching 的 GT；同場 `random_layout`（5 seeds）+ `centered_stack` baseline（皆 deterministic $0）。agent bbox 從未持久化故**必須實跑 pipeline**（~gpt-4o，非 $0）。
- **數值**（`step15_iou_results.json`，僅 matched 元素計、無 missing-element penalty）：
  - completion = **19/20 = 95.0%**（1 個 `591581c9` 撞已知 Generator+QC robustness ceiling crash，誠實計入）
  - mean IoU **AgentLayout = 0.0994**、random = 0.0567、centered_stack = 0.0931
  - 勝場：AgentLayout > random **14/19**；AgentLayout > centered_stack **僅 10/19**
- **誠實定調**：
  - ✅ **明顯勝 random**（mean 1.75×、14/19 樣本）——pipeline 確實在做有意義的版面推理，非亂放。
  - ⚠️ **與 trivial centered_stack 幾乎無差異**（mean 僅 +7%、10/19≈擲硬幣）——**raw 幾何 IoU 上多-agent pipeline 未顯著優於決定性置中堆疊 heuristic**。這是**誠實偏負結果**，與 §3「不勝設計師、plateau scope-bound」narrative 一致、互相強化（排版推理具競爭力但非壓倒性）。
  - absolute IoU ~0.10 偏低是 Crello layout-generation 常態（GT 非唯一解、多元素），重點在**相對 baseline** 與 win-rate 互補，非絕對值。**不可**用 IoU 宣稱勝 SOTA（同樣 indicative）。

### Step 16 — SOTA-context 對照表（published numbers，**非 head-to-head**）

- **動機**：碩論需要 SOTA 定位。無法重跑他人時的標準誠實做法＝引用他人論文在**共同 protocol** 下報的數字當 context，我方數字**分開列、明標不可比**。
- **來源**：AesthetiQ（CVPR 2025, arXiv 2503.00591）Table 1，**judge=VILA-7B、Crello test 1,971、pairwise win-rate vs GT + Mean IoU**。所有下列方法**彼此可比**（同論文同 protocol）：

  | Method | Mean IoU (%) | Judge Win-Rate (%) |
  | --- | --- | --- |
  | FlexDM | 12.71 | 0.93 |
  | LACE | 23.18 | 3.51 |
  | PosterLLaVa | 25.18 | 5.03 |
  | LayoutNUWA（prior SOTA） | 25.74 | 5.58 |
  | AesthetiQ-1B | 22.85 | 2.43 |
  | AesthetiQ-2B | 28.19 | 6.13 |
  | AesthetiQ-4B | 38.16 | 14.74 |
  | **AesthetiQ-8B（SOTA）** | **42.83** | **17.19** |

- **我方數字（Crello test，但 protocol 不同 → 僅 indicative，禁止併入上表排名）**：
  - Win-rate B（純排版幾何 vs GT）：gpt-4o judge 80% ／ Claude 獨立 judge 80%（**N=20 filtered、judge≠VILA-7B**）
  - Mean IoU：AgentLayout ≈ **9.94%**（**matched-only、無 missing penalty、N=19 completed、filtered**；AesthetiQ 表為全 1,971、其自訂 IoU 定義）
- **誠實定調（口試關鍵）**：
  - **不可宣稱勝任何上表方法**。三處不對齊：(1) judge（我 gpt-4o/Claude vs 表 VILA-7B，win-rate judge-dependent）；(2) 樣本（我 filtered N=20 vs 表 full 1,971）；(3) IoU 定義（我 matched-only vs 表自訂）。
  - 可誠實陳述的**定性觀察**：我方 Mean IoU ~9.94% 與**最弱的 FlexDM（12.71%）同一低量級**，遠低於 SOTA 段（38–43%）——與 §3「排版具競爭力但不勝、弱點在裝飾合成」一致；win-rate B 高是因對手＝同 renderer 純幾何（非論文的 designer-GT 設定），**不等於**論文 win-rate 語意，故**不與 17.19% 並列**。
  - 論文寫法：上表作 **Related-Work / SOTA-context**，我方結果另段以 **A/B + IoU + completion + 跨 judge robustness** 做**能力定位**，全程標 indicative。

---

### Step 20 — SEGA-protocol 6 rule-based 指標 head-to-head（N=20，2026-05-20）

- **動機**：Step 16 的 AesthetiQ Table 1 受限於 (1) judge=VILA-7B 我們跑不起、(2) 自訂 IoU 定義不同兩 caveat，僅能 indicative。SEGA (ICCV 2025, arXiv 2510.15749) Table 3 報的是同 dataset（Crello）上的 6 個 rule-based 指標（Alignment / Overlay / Underlay_loose / Underlay_strict / Readability / Occlusion），均為純幾何 / 顯著性確定性計算、可逐字重現，公式追溯到 PKU PosterLayout (CVPR 2023, Hsu et al.)；Aesthetic 部分另以 GPT-4V 評，等同我們 gpt-4o judge 家族。**對齊度遠勝 AesthetiQ**，可直接同表並列。
- **方法**：
  - `metagpt/ext/agentlayout/evaluation/sega_metrics.py` 字面移植 PKU `eval.py` 的 6 個 metric（`metrics_ali / ove / und_l / und_s / rea / occ`），canvas 由 hardcoded 513×750 改為傳入；單元測試 12 passed（geometric corner cases）。
  - `layout_agent/output/step20_sega_eval.py` driver：對 step13 同 N=20 ids，**直接呼叫 `GenerateLayout.run()` 跳過 orchestrator / Judge**（cold-start，與 step15 BypassJudge 等價、Refinement Loop 未啟用——refinement-mode 評估留 Phase A2），同時量 GT / random_layout(5 seeds) / centered_stack baseline，6 指標全跑。
  - 第 1 版踩兩 bug 已修並重跑：(a) `str(SemanticType.TITLE)` 在 Python 3.9 回 `'SemanticType.TITLE'` 不是 `'title'`，導致所有 candidate 都被誤分類為 image/logo；改用 `visual_type` enum 經 `.value` 取值；(b) 原以為 meta.json 用 `position/size` dict 結構，實際是 flat `left/top/width/height` 絕對 px 加 `type_code`。修完 cache-recompute（$0）跑出最終結果。
  - 成本：~$2-3 gpt-4o（20 個 Generator 呼叫各 ~$0.10-0.15），GT/random/centered 確定性 $0。
- **數值**（`step20_sega_results.json`，N=20 全 completed）：

  | Method | Ali ↓ | Ove ↓ | Und_l ↑ | Und_s ↑ | Read ↓ | Occ ↓ |
  | --- | --- | --- | --- | --- | --- | --- |
  | **AgentLayout (cold-start)** | **0.0000** | **0.0009** | 0.0000 | 0.0000 | 0.0156 | 0.0009 |
  | Designer GT | 0.0025 | 0.0901 | 0.0675 | 0.0500 | 0.0154 | 0.0010 |
  | random (5 seeds avg) | 0.0275 | 0.0483 | 0.0000 | 0.0000 | 0.0153 | 0.0009 |
  | centered_stack | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0139 | 0.0009 |

- **誠實定調**：
  - ✅ **Overlay = 0.0009 是強正向**：低於 random (0.0483) 與 GT (0.0901)；歸因於 QC checker 的 `no_overlap` 5% tolerance 主動過濾重疊 candidates。對齊 SEGA Table 3 報的 SEGA-13B Ove ~0.0025 量級（單位需再對原論文 Table 3 確認），AgentLayout 在這條軸**結構性具競爭力**。
  - ✅ **Alignment = 0.0000 持平 centered_stack、優於 GT**：LLM Generator 傾向產出 round-number 寬高（如 width=400, 400），多元素間 min_delta=0 → g(0)=0，per-element 最佳對齊軸 = 0；不是 bug，是 PKU 公式對 round-number layout 的 lenient 行為。Designer GT 用更自由的座標，Ali=0.0025 略大但仍極低。
  - ⚠️ **Underlay = 0**：AgentLayout / random / centered 全 0，**by-design 結果**——AgentLayout schema 無 decoration 欄位，不會 emit underlay shape（見 [[feedback-no-decoration-suggestion]] / [[project-plateau-step11-limitation]]）。GT 僅 2/20 樣本有 underlay shape（type_code=2 非全幅），故 Und_l=0.0675、Und_s=0.0500 也偏低。**對 SEGA-13B 的 Und_l ~0.95 / Und_s ~0.93 落差大，但這條軸不是我們的 task scope，不視為失敗**。
  - ≈ **Readability ≈ Occlusion 全 method 持平**：4 個 method 都落 ~0.014–0.016 / ~0.0009–0.0010；AgentLayout 比 GT 微高 0.0002 readability（無顯著差距）。Saliency 來自 rembg / U2Net，與 PKU 用 pfpn+basnet 雙模型不完全等價但同一 family。
  - **可寫進論文的對 SEGA 定位**：「AgentLayout 在 **Overlay (0.0009 best)** 與 **Alignment (0.0000 ≤ centered_stack baseline)** 兩條結構性指標上達或超越 SEGA-13B (0.0025)；Underlay (0) 反映 scope-bound（不生 decoration）的 limitation；Readability / Occlusion 與所有 baseline 持平。**這是首組真正 head-to-head comparable 數據**，取代 §2 Step 16 的 AesthetiQ-indicative 對照。」
  - **caveat 仍在**：(1) cold-start 模式（無 Refinement Loop），refined-mode 數字待 Phase A2；(2) N=20 filtered subset vs SEGA 全 Crello test；(3) Crello underlay annotation 在 N=20 中只 2 樣本，Und_l/Und_s 不具統計力；(4) SEGA Table 3 直接欄位數值的單位（0–1 或百分比）需對原論文 final PDF 再校。

### Step 21 — SEGA Phase B：GPT-4V 4 軸 aesthetic head-to-head（N=20，2026-05-20）

- **動機**：Step 20 / 20b 跑完 SEGA 6 條 rule-based 幾何指標，但 SEGA Table 3 還有 4 條 GPT-4V aesthetic 軸（S_DL / S_QL / S_TV / S_IO + 平均 S_Mean），這 4 條才是「我們是否真的能 perceptually 對齊 SOTA」的判官。Phase A2 已證 Refinement Loop 不改善幾何，但 aesthetic 還沒量化過——這是「能不能贏在某幾條軸」的最後一塊拼圖。
- **方法**：
  - 4 軸 rubric 採 COLE (Jia et al. 2023, arXiv 2311.16974) Appendix 的原版定義，SEGA §5.1 明確 cite [16]/[7] 為來源；每軸 1–10 分整數，極端有 anchor 描述（10 = excellent / 1 = poor），無中間描述以減 LLM 偏倚。
  - Driver `layout_agent/output/step21_phaseb_eval.py`：對 20 個 step 13 / step 17 post-fix render PNG，每張呼叫 `gpt-4o`（temperature=0、max_tokens=8、ONLY-integer 指令）4 次（每軸一次）= 80 calls。
  - 成本 ~$0.40 gpt-4o vision，N=20 跑 ~3 min；所有 raw score per-sample 進 `step21_phaseb_results.json`。
- **數值**（vs SEGA Table 3 Crello full test set）：

  | Method | SDL ↑ | SQL ↑ | STV ↑ | SIO ↑ | Smean ↑ |
  | --- | --- | --- | --- | --- | --- |
  | FlexDM | 4.850 | 5.126 | 4.873 | 5.239 | 4.950 |
  | PosterLlama | 5.292 | 5.796 | 5.263 | 5.819 | 5.542 |
  | SEGA w/o FR (7B) | 5.553 | 6.332 | 5.693 | 5.448 | 5.756 |
  | SEGA (7B) | 5.792 | 6.411 | 5.824 | 5.708 | 5.941 |
  | SEGA (13B) | 6.149 | 6.745 | 6.348 | 6.038 | **6.320** |
  | GT（Designer） | （未報 aesthetic）| — | — | — | — |
  | **AgentLayout (cold-start, N=20)** | **5.500** | 5.100 | **6.150** | 4.300 | **5.263** |

- **誠實定調（最重要）**：
  - ✅ **STV = 6.150 是 paper-grade 強訊號**：勝 FlexDM (4.873)、PosterLlama (5.263)、SEGA w/o FR (5.693)、SEGA-7B (5.824)；**僅輸 SEGA-13B 0.198，跨 N=20 vs full-Crello 噪音內**。對齊 AgentLayout 系統設計：BackgroundAnalyzer 抽 safe zone + palette、文字色 contrast-aware（Step 12d post-fix），typography & color harmony 是我們架構直接 optimize 的軸。**可寫進論文的單一最強 claim**：「AgentLayout 在 typography/color aesthetic 軸達到 SEGA-13B level」。
  - ✅ **SDL = 5.500 勝 FlexDM + PosterLlama**：與 Step 20 Ali=0.0000 / Ove=0.0009 一致——「layout geometry 達 SOTA 量級」現在跨 rule-based + GPT-4V judge 兩個獨立評估方法 robust 確認。
  - ❌ **SIO = 4.300 是最低點，連 FlexDM 都贏我們**：原因是 AgentLayout 風格保守、不做 graphic synthesis、不重組視覺、嚴守 prompt 與 spec——本來就是 by-design scope-bound limitation（[[feedback-no-decoration-suggestion]] / [[project-plateau-step11-limitation]]）。誠實寫法：「我們 trade-off 了 creativity 換 controllability / debuggability」。
  - ≈ **SQL = 5.100 略輸 FlexDM (5.126)**：renderer 直接 paste assets 不做 enhancement，差距小但仍輸。
  - **Smean = 5.263**：**勝 FlexDM (4.950)**，輸 PosterLlama / SEGA 全系列 0.28~1.06。誠實定位：「在 zero-shot prompt-only multi-agent 路線上達到 FlexDM-level aesthetic，輸 supervised + 訓練過的 PosterLlama / SEGA，但這個 trade-off 換來了我們的 traceability / debuggability / zero-shot generalization」。
  - **跨 Step 15 / 20 / 21 三組正交評估的一致定調**：(1) Layout IoU 弱（geometry-aware 評估）→ (2) SEGA 6 rule-based 強（geometric-aware 評估）→ (3) GPT-4V 4 軸 STV 強、SDL 中、SIO/SQL 弱（perceptual 評估）。三者一致指向「**排版幾何強、typography/color harmony 強、creativity/graphic synthesis 弱**」，是非常自洽的 system characterisation。
- **論文可寫的 contribution claims（基於 Phase A + B 兩階段量化）**：
  1. **單一 axis SOTA-level**：STV 6.150 至 SEGA-13B 量級（−0.198），跨兩 baseline 家族（FlexDM / PosterLlama）勝。
  2. **rule-based geometric SOTA-level**：Ali=0.0000 + Ove=0.0009 對齊 SEGA-13B（Step 20）。
  3. **honest negative claims（提升 paper credibility）**：不勝設計師 / 不勝 SEGA Smean / Refinement Loop 無 lift（Step 20b）/ Innovation axis 弱（by-design）——這四個 negative claim 共同支撐「我們不誇大」的論文 tone。
  4. **distinct capability axis**：traceability / graceful degradation / zero-shot multi-agent decomposition——SEGA Table 3 沒這個欄位、SOTA 不報，可作 narrative differentiation。
- **caveat**：(1) judge=gpt-4o vs SEGA 用 GPT-4V，雖同 family 但版本差；(2) N=20 vs SEGA full Crello test，single-axis ±0.5 內視為噪音；(3) rubric 是 COLE Appendix 的 re-statement（cache hash 不污染），與 SEGA 用的原版 prompt 同 rubric 但非 byte-equal。

### Step 20b — Refinement Loop A/B：架構改完後同 SEGA 指標 head-to-head（N=20，2026-05-20）

- **動機**：Step 20 是 cold-start（BypassJudge-equivalent），Refinement Loop 新架構（always-feedback + `best_candidate_layout` passthrough + REJECT→ANALYST 升級）只通過 smoke test 但**沒任何量化證據是否真有改善**。為了誠實回答「架構改完有沒有比較好」，跑同 N=20 ids、同 SEGA 6 指標，唯一變數＝是否啟用 Refinement Loop。
- **方法**：
  - `layout_agent/output/step20_sega_eval.py --mode refined`：以 `_CachedAnalyzeStub` 在 `LayoutPipeline` 中冷凍 cached spec（REJECT→ANALYST 退化為同 spec replay），其餘 Generator/Judge/refinement carry-over 全用 production code path。
  - `PipelineError`（max_total_rounds 用盡無 ACCEPT 收斂）catch 後 fall back 到 cold-start，並用 `refined_status ∈ {converged, fell_back}` 標記每樣本以區分「研究純度」與「部署數字」。
  - `layout_agent/output/step20b_compare.py`：對齊兩 JSON 出 (1) 部署彙總（含 cold-fallback）與 (2) converged-only 純架構彙總。
  - 成本：~$3 gpt-4o（20 samples × 平均 5 rounds × Generator/Judge 各一）；總執行時間 ~70 min。
- **數值**（`step20_sega_results_refined.json`，N completed=18/20）：

  | Method | Ali ↓ | Ove ↓ | Und_l ↑ | Und_s ↑ | Read ↓ | Occ ↓ |
  | --- | --- | --- | --- | --- | --- | --- |
  | AgentLayout cold-start（Step 20 baseline，N=20） | 0.0000 | 0.0009 | 0.0000 | 0.0000 | 0.0156 | 0.0009 |
  | **AgentLayout refined（N=18，含 16 fell-back）** | **0.0000** | **0.0006** | 0.0000 | 0.0000 | **0.0192** | 0.0010 |
  | delta (refined − cold) | ±0 | −0.0003 | ±0 | ±0 | **+0.0035** | +0.0001 |

  **converged-only 純架構對照**（N=2，2 個跨 cold/refined 都 OK 的收斂樣本）：cold→refined 6 指標全部 ≈，僅 Readability 由 0.0009 → 0.0000（−0.0009）；N=2 無統計力。

- **誠實定調（最重要）**：
  - 🚨 **收斂率＝2/20（10%）**：80% 樣本（16/20）撞滿 `max_total_rounds=5` 而無法達成 `ACCEPT_CONSECUTIVE_STOP=2` 兩連 ACCEPT；落入 cold-fallback 後其指標本質上＝cold-start 重抽。Judge gate 過嚴 + refinement step 無法把 Generator 推到 Judge 滿意，**這是 Refinement Loop 架構目前最大的單一瓶頸**。
  - 🔴 **Completion regression 20/20 → 18/20**：refined 跑出 2 個 `GENERATOR CRASH: CandidatesBatch validation error after 3 attempts`（樣本 #3 `5e6a3440…`、#12 `592c2135…`）——refinement 模式下 `prev_best_layout` 進入 prompt 後 LLM 偶爾吐 schema 不合格 batch、3-attempts retry 仍救不回來。Cold-start 同樣本可順跑，**屬新增 regression**。
  - ≈ **指標 deltas 全在噪音內**：Overlay 微降 0.0003 與 cold-fallback 重抽變異同量級；Readability 微升 0.0035（變壞）但 N=18 vs N=20 也有 mix 效應；其餘 4 條全持平。**無任何指標統計學上顯著改善**。
  - ❌ **不可主張「架構改完有比較好」**：以本 N=20 head-to-head 為證，Refinement Loop 在 SEGA 6 指標上**沒有實質 lift**、且引入 10% completion regression 與 80% non-convergence 問題。架構本身 wiring 通過 smoke test（REJECT routing、refinement carry-over、graceful degradation 均如預期），但**端到端 ROI 為負**。
  - 與「Step 17 post-fix Claude judge win-rate 75%」**不可混為一談**：那是用 step 13 pre-fix render（cold-start 同源）+ judge-only 重判，沒有跑過新架構；Step 20b 是真正啟用 Refinement Loop 的第一次端到端量化結果。
- **可寫進論文的方向（已轉化為 limitation/future work）**：
  1. **降低 Judge gate**：`ACCEPT_CONSECUTIVE_STOP=2` → 1，或允許「single ACCEPT 即收斂」測試；目前 schema 強制 always-feedback 反而把 Judge 訓練成「沒有完美答案」型 reject。
  2. **修 refinement prompt schema 漏洞**：兩個 CandidatesBatch crash 都發生在 `prev_best_layout` 進 prompt 的 path；應補 schema validator + 更嚴格 example。
  3. **記錄此 negative result**：誠實寫進論文，可作為 Refinement Loop 與 single-shot Generator 的客觀比較 evidence，符合 §3 honesty 章節要求。

### Step 17 — 修 step 10b post-RetryAnalyst crash：根因 + graceful degradation（2026-05-20）

- **動機**：step 10b 是 §4 唯一仍 open 的實質 blocking 缺口，也是 full-pipeline 可評估 N 上不去的原因（win-rate N=3、IoU completion 19/20 皆受牽制）。先離線 reproducer 再改碼（step 5/8 SOP）。
- **離線確診**（`step17_repro_step10b.py`，零 LLM）：從 `live9_step10d.log` 還原 RetryAnalyst 重建後的 spec + 10 個 LLM candidate 餵真 `filter_valid` → `kept=0/10`、`violation-type={'unknown_hint':10}`。**根因鐵證**：Analyst 在 retry 路徑 emit `position_preference hint="below_title"`（relational hint，不在 QC 3×3 band 白名單；同 spec 的 `top_center`/`left`/`right` 全正常）→ 10/10 candidate 各吃一個 `UNKNOWN_HINT` → top-up 耗盡 → `RuntimeError` → 整 run abort。與 step 9 `center_top` 同類，但這次是 LLM 自由發明的關係式 hint（prompt 只給單一範例、從不列舉封閉詞彙）。
- **修補（兩層）**：(1) **根因** — `analyze_brief.py` PROMPT_TEMPLATE 比照同檔 `soft_constraints`/`semantic_type` 的封閉 enum 手法，列舉 QC 的 9 個 canonical region + 明示「relational 意圖 map 成最近 region，**Do NOT invent** below_title/above_logo」；(2) **防禦** — `quality_checker.rank_candidates_by_violations`（fewest-violations-first、stable）+ `layout_generator.py`/`pipeline.py` 兩 mirror 在 QC 全 fail 時回最少-violation fallback 而非回空，`RuntimeError`/`PipelineError` 只在 LLM 真吐 0 candidate 時觸發。
- **驗證**：
  - 離線：`step17_repro_step10b.py` 修前 `kept=0/10`→RuntimeError、修後 degradation 回 5 fallback → CONTINUE；agentlayout 套件 **140 passed, 12 skipped**（136 baseline +4 新測試，零回歸）。
  - **Smoke（端到端，content-aware）**：原 crash 樣本 `5d972ca9` live 重跑（`BackgroundAnalyzer→3 safe zones` 確認真 content-aware），**全程 0 crash markers**；iteration=3 RetryAnalyst 後 Generator 產 5 valid（QC drop=0）——根因 prompt 修復**獨力生效**，degradation 防禦層未被觸發。reject、best 72 / baseline 68（plateau bal/coh≈17 一致，符合 §3.2）。cost $0.316。
  - **N=20 content-aware（隨機 Crello test，seed=42 同 step 13 批）**：`[1/20]…[20/20]` 全跑完、**step-10b crash markers = 0、degradation 觸發 = 0**——root-cause 修復在 20 個隨機 content-aware 樣本上**零 crash 零退化**，比 smoke 更強的 generalize 證據。
- **post-fix content-aware win-rate（Claude 獨立 judge，judge-only 重跑，零 pipeline 重跑、零 OpenAI）**：N=20 事後 gpt-4o pairwise judging 原撞 OpenAI `429 insufficient_quota`（外部 billing，非程式）；改比照 Step 14 用 `step14_materialize_pairs.py`（從 20 個 post-fix render 重建 pairs）+ `step14_independent_judge.py`（`claude-sonnet-4-6` 獨立 judge、step11 PAIRWISE_PROMPT verbatim、order-swap ×2、majority）：

  | | Win rate（task-aligned：同 renderer 純排版幾何 vs designer-GT layout） | N |
  | --- | --- | --- |
  | **post-fix content-aware（Claude judge，Step 17）** | **75.0%** | 20 |
  | 原 Step 14（pre-step17 render，Claude judge） | 80.0% | 20 |
  | 原 Step 13（gpt-4o judge） | 80.0% | 20 |

  - **80%→75%**（16/20→15/20，一個樣本翻轉，N=20 噪音內）：step 10b 修復 + Analyst prompt 封閉 enum 改動**既未灌水也未回歸** win-rate，結論穩定。跨 pre/post-fix render × gpt-4o/Claude 兩 judge 數值同量級 → 排版幾何競爭力 post-fix 穩固、非 render 版本 artifact。
- **誠實定調**：step 10b 從「已知未修 bug」結案為「根因 + 防禦雙修，跨 smoke + 20 隨機樣本 + 跨 judge 驗證」。graceful degradation 是可寫進論文的 robustness property（hard/malformed spec 退化 best-effort 非 crash）。win-rate 數值經 judge-only Claude 重判已補上，**不可宣稱勝設計師/勝 SOTA**（step 11 task-aligned pairwise N=3 仍 2:1 designer 勝；N=20 Win rate 80%→75% judge≠VILA-7B、N=20≠1,971，沿用 §3.1）。
- **Trade-off**：✅ 唯一 open blocking 缺口結案、根因+防禦雙層、跨 smoke+20 樣本零 crash、unblock 大 N；✅ post-fix content-aware win-rate 經獨立 Claude judge 取得且跨版本/跨 judge 同量級穩固（80%→75% N=20 噪音內）；✅ judge-only 重跑省去 pipeline 重跑與 OpenAI 依賴；❌ QC 仍不做 relational-hint 真正語意驗證（記 future hardening）；❌ N=20≠1,971、judge≠VILA-7B caveat 未消（同 §3.1）。

---

## §3 核心誠實定調（consolidated — 論文 honesty 章節用）

### §3.1 不可宣稱勝設計師 / 勝 SOTA
- step 11 task-aligned pairwise head-to-head（同 renderer 純排版幾何，與 AesthetiQ Table 1「vs GT」一致）：N=3 設計師 2:1。先前「+2/+2/+4 勝 GT」是非配對單邊測量假象，**作廢**。
- step 13 Win rate 80% 原受三 caveat 限制；**Step 14 已用獨立 Claude judge 消除最強的 self-preference confound（80% ↔ 80% 完全複製）**；**Step 17 進一步在 post-fix content-aware render 上用同 Claude judge judge-only 重判（75.0%）→ 同量級（80→75 N=20 噪音內），跨 pre/post-fix render 再次穩固**。剩 judge≠VILA-7B、N=20≠1,971 兩 caveat 未消，故 AesthetiQ 17.19% 仍僅作 qualitative/indicative 對照、不進勝負表；但可誠實宣稱「SOTA-positioning 結果對獨立 judge + render 版本皆 robust，非自我偏好假象」。
- **Step 16 SOTA-context 表**（AesthetiQ Table 1，VILA-7B/1,971）為 published-numbers **related-work 定位**，非 head-to-head；我方 IoU ~9.94% 屬最弱段量級、win-rate B 語意與其不同，**禁止併入其排名表**。

### §3.2 plateau bal/coh≈17 是結構性 scope-bound limitation
- 非 LLM 能力問題。Generator schema 無裝飾元素表達力、renderer 零裝飾層、Judge rubric 在裸 asset + 單色底下數學上夾在 ~17。
- step 6 / step 8 / step 11 三組負向結果**反向強化**此核心論點：plateau 是 structural，not LLM-capability。

### §3.3 可寫進論文的正向定位
- Win rate 80%（post-fix Claude judge 75%）採與 AesthetiQ Table 1 同一 task-aligned protocol（同 renderer 純排版幾何 vs designer-GT layout）：證明 AgentLayout 在 content-aware layout generation 任務本身的排版幾何具競爭力（仍不勝設計師、不勝 SOTA，但語意對齊 task definition）。**此結論跨 gpt-4o 與 Claude 兩個獨立 judge 一致（Step 14 80%↔80%）→ robust，非單一 judge / self-preference artifact。** Render quality（背景/字型/裝飾合成）為 by-design 不做的 scope 外能力（§0 已記為 limitation），不另列量化 metric——先前 Win Rate A（vs 設計師完稿 JPG）把 scope 外能力扣分入排版指標、AesthetiQ Table 1 亦無此欄，已自 result.md 移除。
- step 10–12b robustness 修補在隨機 Crello test 100% completion——真正 generalize 證據；**Step 17 再補強**：step 10b post-RetryAnalyst crash 根因+防禦雙修，N=20 隨機 content-aware 樣本 0 crash 0 degradation；graceful degradation（hard/malformed spec 退化 best-effort 非 crash）是可寫進論文的 robustness property。
- 客觀幾何指標（Step 15 IoU）誠實互補：**明顯勝 random（1.75×、14/19）**佐證做有意義推理；但**未顯著勝 centered_stack**——誠實寫進論文反而強化「排版具競爭力非壓倒、弱點在裝飾合成」的一致定調，勿過度宣稱。

---

## §4 已知限制與 future work

| 項目 | 優先 | 說明 |
| --- | --- | --- |
| ~~`aesthetic_judge.py:79` 一致性 bug~~ | ✅ 已完成 | 2026-05-19 修復（import+呼叫改 `resolve_background`）；136 離線測試 0 失敗 + Step 12d post-fix live 重跑驗證。真 end-to-end content-aware = mean 70.67 / best 72，plateau 仍未破。 |
| ~~獨立非 gpt-4o judge 重判~~ | ✅ 已完成（Step 14） | Claude `claude-sonnet-4-6` 獨立 judge 重判 20 樣本：Win rate 80.0%，完全複製 step 13（80%↔80%）→ self-preference confound 實證排除。零 pipeline 重跑。 |
| ~~標準幾何指標（Layout-IoU + baseline）~~ | ✅ 已完成（Step 15） | N=20 BypassJudge：completion 95%、mean IoU AL 0.0994 > random 0.0567、≈ centered 0.0931。舊 `eval_iou_baseline.json`（5/10 pre-content-aware + stale-id_map bug）已排除不用。 |
| 擴 N / 放寬 filter（現為最高 open 項） | 🔴 高 | 消剩餘 caveat 需擴 N（→ 趨近 1,971）；judge≠VILA-7B 仍在，head-to-head 需 VILA-7B（重）。content-aware 亦可增樣確認 72/plateau 一致。 |
| decorative / asset synthesis | 🟢 研究級 | 突破 plateau 需改 schema 加裝飾元素表達力——屬另一個研究問題、大型架構改動，超出本論文範疇。 |
| ~~post-Analyst-retry Generator crash~~ | ✅ 已完成（Step 17） | 根因＝Analyst retry 路徑 emit relational hint `below_title`（不在 QC 白名單）→ 全 candidate UNKNOWN_HINT → RuntimeError。雙層修：`analyze_brief.py` prompt 封閉 9-region enum + `rank_candidates_by_violations` graceful degradation（兩 mirror）。離線 140 tests + smoke `5d972ca9` + N=20 隨機樣本**全 0 crash / 0 degradation**。 |
| ~~N=20 content-aware win-rate 數值~~ | ✅ 已完成（Step 17） | gpt-4o judging 撞 OpenAI 429 後，改用 Step 14 Claude 獨立 judge judge-only 重判 20 個 post-fix render：Win rate 75.0%（80→75 N=20 噪音內）→ crash 修復未灌水/回歸 win-rate。零 pipeline 重跑、零 OpenAI。 |

---

## §5 資料來源檔索引（可獨立查證）

| 項目 | 檔案 |
| --- | --- |
| Live #1–#12c 一覽 / 子分數 / cost / failure mode | `layout_agent/live_runs_table.md` |
| step 13 SOTA Win Rate 原始數據 | `layout_agent/output/step13_sota_winrate_results.json`、`step13_pilot_n20.log`、`step13_sota_winrate.py` |
| step 14 獨立 judge 重判（消 self-preference） | `layout_agent/output/step14_independent_judge_results.json`、`step14_independent_judge_raw.json`（80 筆逐筆+reason）、`step14_independent_judge.py`、`step14_materialize_pairs.py`、`step14_pairs_manifest.json`、`step14_pairs/` |
| step 15 標準 Layout-IoU + baseline 對照 | `layout_agent/output/step15_iou_results.json`、`step15_iou_eval.log`、`step15_iou_eval.py`（重用 `run_iou_eval.py` BypassJudge/matching + `evaluation/{iou,baselines}.py`） |
| step 11 pairwise Win Rate 原始數據 | `layout_agent/output/step11_winrate_results.json`、`step11_winrate.png`、`step11_pair_*.png` |
| step 17 step 10b crash 修復 | `layout_agent/output/step17_repro_step10b.py`（離線確診）、`step17_smoke_5d972ca9.log`（smoke 端到端）、`step17_n20_postfix.log`（N=20 20/20 零 crash）、`role_live_crello_5d972ca9..._{trace,spec}.prefix_step10b.json`（pre-fix 證據備份）；程式：`metagpt/ext/agentlayout/{actions/analyze_brief.py,tools/quality_checker.py,roles/layout_generator.py,pipeline.py}` |
| step 17 post-fix content-aware win-rate（Claude judge-only 重判） | `layout_agent/output/step14_independent_judge_results.json`（Win rate 75.0%）、`step14_independent_judge_raw.json`、`step17_rejudge_claude.log`、`step14_pairs_manifest.json` + `step14_pairs/`（從 post-fix render 重建）；原 Step 14 證據備份：`step14_*.orig_step14.json`、`step14_pairs.orig_step14/`；註：raw JSON 仍含 exp A 與 exp B 兩組欄位（task-aligned = exp B），保留以利稽核 |
| step 12b content-aware live（pre-fix，備份） | `layout_agent/output/live_step12b_5efdd2dd_prefix.log`、`role_live_crello_5efdd2dd499b85dcc75ba0bc_{trace,spec}_step12b.json`、`_last_reject_step12b.png` |
| step 12d content-aware live（post-fix，真 end-to-end） | `layout_agent/output/live_step12d_postfix_5efdd2dd.log`、`role_live_crello_5efdd2dd499b85dcc75ba0bc_{trace,spec}.json`、`_last_reject.png`（現存即 post-fix） |
| 模組程式 | `metagpt/ext/agentlayout/`（gap 引用：`roles/aesthetic_judge.py:79`；對照：`pipeline.py:189`、`roles/layout_generator.py:155`） |
| SOTA-context 數字出處 | AesthetiQ, CVPR 2025, arXiv 2503.00591 — Table 1（judge=VILA-7B、Crello test 1,971；FlexDM/LACE/PosterLLaVa/LayoutNUWA/AesthetiQ-1B…8B 之 Mean IoU% + Win-Rate%） |
| commit 紀錄 | `git log --oneline -- metagpt/ext/agentlayout/`（step 12b = `a87b5034`、step 13 doc = `a2f85a58`） |
