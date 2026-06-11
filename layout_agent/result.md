# AgentLayout — 實驗結果與模組完成度（standalone）

> 本文件為**獨立**版：不需閱讀 `README.md` 或 `live_runs_table.md` 即可理解每個實驗的動機、方法、數值與誠實定調。供論文 results / limitations / honesty 章節直接取用。
> 數值 source-of-truth：`layout_agent/live_runs_table.md`、`layout_agent/output/step13_sota_winrate_results.json`、`layout_agent/output/step11_winrate_results.json`、`layout_agent/output/step23_phasea_full.json`、`layout_agent/output/step23_phaseb_full.json`、`layout_agent/output/step23_phaseb_designer_gt_full.json`。
> 最後更新：2026-06-12（補錄 Step 30–64 oracle-loop / QC / renderer / 構圖師實驗鏈；§2 後半全部為新 judge schema 鏈）。
>
> ⚠️ **Step 30 Baseline 警示（2026-06-09）：** in-pipeline Aesthetic Judge 已從 4 軸 0-25 / total 0-100 改為 COLE 5 軸 1-10 / total 5-50（`ACCEPT_THRESHOLD` 75 → 35）。本文件目前所有 Phase A/B/win-rate 數值都是 **pre-Step 30 schema 產出**（git tag `step29-baseline-pre-judge-migration`、commit `0956f2bb`），跟 Step 30+ 之後的新 trace JSON / Phase B 結果**不可直接比較**。Step 30 動機與設計見 `IMPLEMENTATION_LOG.md`「Step 30」一節；若要跑新 baseline 並 cross-compare，需重跑 Phase B（~$30 / N=100）。

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
  - **GPT-4V 4 軸 aesthetic（Step 21，Phase B）**：同 N=20、SEGA Table 3 COLE 1–10 rubric。AgentLayout 在 SEGA paper 的 cross-paper 數值對照看似 STV=6.150 達 SEGA-13B 量級——**但這個 claim 已被 Step 21b 推翻**（見下）。
  - **Judge-config 校準對照（Step 21b）**：把 Crello designer 原圖跑過完全相同的 gpt-4o + 4 軸 prompt config，**designer GT Smean = 7.525**，比 SEGA paper 自報的 SEGA-13B 6.320 還高——證明 **judge calibration 跨 paper 顯著漂移，SEGA Table 3 數值不能直接 cross-paper 比較**。
  - **N=100 scale-up（Step 22）**：推翻 N=20 兩個 claim：(a) Smean ratio 70% → 64.8%（N=20 高估 5 pp）；(b) STV 不是最強軸、SIO 才是（N=100 STV 70% < SIO 75%）。Phase A geometric claim 仍 robust。
  - **N=1,897 完整 Crello test split（Step 23/23b，最新，2026-05-27）**：對齊 SEGA paper full coverage。pre-launch 四項校準（MAX_ELEMENTS=inf / max_token=16000 / BASNet+ISNet saliency / COLE single-call JSON）後完成 render 1,895/1,897（99.89%）；Phase A N=1,896、Phase B N=1,895、Designer GT within-judge N=1,897。
    - **Phase A**（N=1,896）：Ali=0.0004 < GT 0.0010（勝 ~2.2×）、Ove=0.0050 << GT 0.1038（勝 ~20.6×）、Occ=0.1249 < GT 0.1279（**flipped 勝**，saliency 校準後）、Read 近平手、Und_l/Und_s = 0（已知 limitation）。**Ali/Ove 跨 N=20/100/1,897 三 scale 全部勝、N=1,897 還多 Occ 勝——這是論文最 robust 的 contribution**。
    - **Phase B Smean within-judge ratio**：N=100 64.8% → **N=1,897 65.8%**（1pp 內、跨 scale 穩定）。**Smean capability ratio 跨三個 scale robust**——第二個論文可宣稱 claim。
    - **🚨 Per-axis ranking 再次 flip**：N=100「SIO 75% 最強、SQL 56% 最弱」→ N=1,897「**SQL 69.1% 最強、SIO 63.4% 最弱**」。**axis-ranking 又一次被推翻**，small-sample selection bias systematically misleads per-axis claim → 第三個 methodology contribution。
  - **Underlay-enabled 端到端（Step 29，最新，2026-05-28）**：把 Step 23「Und=0」當 baseline、underlay redesign 後重跑 N=1,895 cold-start 當 **ablation 對照**。AL Und_l 0→**0.5518** > designer 0.3542、Und_s 0→**0.4428** > 0.2674（4 幾何指標 Ali/Ove/Und_l/Und_s 全勝 designer），Ali/Ove 雙勝跨三設置（Step 23 舊 GT / Step 28 cached / Step 29 re-render）維持；但 Read/Occ 略退（over-containment），**Und 勝是 metric-level containment、非視覺更好**，視覺品質（Phase B COLE 5-axis）尚未重評。流程先 N=5 smoke gate（0 role-reversal）才燒 $110 全跑。
- **誠實定調（最重要，post-N=1,897 final）**：**不宣稱勝設計師 aesthetic、不宣稱勝 SEGA Smean、不宣稱 Refinement Loop 帶來測量上的改善、不宣稱跨 paper SEGA Table 3 數值可直比、不宣稱 per-axis ranking（SIO 最強 / STV 最強等都已被 N=1,897 推翻）**；可宣稱「**(1) Phase A Ali/Ove 純幾何勝 designer GT 跨 N=20/100/1,897 三個 scale 全部維持，N=1,897 還多 Occ 勝（Step 20+22+23 三重 robust，judge-drift-free）；(2) Within-judge Phase B Smean AL 達 designer ceiling 65.8%（N=1,897，與 N=100 64.8% 跨 scale 穩定）；(3) 三個 methodology contribution：judge 跨 paper 漂移（Step 21b）+ N=20→100 STV selection bias（Step 22）+ N=100→1,897 SIO/SQL selection bias（Step 23b）→ full-scale validation that per-axis claims need ≥1,000 sample**」。task-aligned pairwise 下設計師仍勝（step 11 N=3：2:1）；N=20 Win rate 80% 的 self-preference confound 已由 Step 14 獨立 judge 排除，但 judge≠VILA-7B caveat 仍在；N=1,897 ≈ SEGA full Crello (1,971) 的 96.2% coverage 消除「N=20≠1,971」caveat。AesthetiQ 仍僅作 qualitative/indicative 對照、不進勝負表。Render quality（背景/字型/裝飾合成）為 by-design 不做的 scope 外能力。

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

> **⚠️ 本節結論已被 Step 21b 部分推翻**：直接拿 SEGA Table 3 published numbers 跟我們 N=20 數值併排比較會因「GPT-4V judge configuration drift」混淆。Step 21b 把 Crello designer 原圖跑過同 judge config 之後，發現 designer 拿 Smean 7.525——比 SEGA paper 報的 SEGA-13B 6.320 還高，**證明我們的 judge 整體給分較鬆，SEGA Table 3 數值不能直接 cross-paper 比**。請以 Step 21b 的 within-judge 對照為準。Step 21 內容保留作 honest research history（包含當初被 judge drift 誤導的 inflated claim）。

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

### Step 22 — N=100 scale-up：再次推翻 N=20 的 STV 排名 + 確認 selection-bias inflation（N=100，2026-05-20）

> **本節是論文最重要的方法論章節**：N=20 → N=100 把 Step 20/21/21b 的 4 個 sample-bound claim 全部重新校準了一次，發現 N=20 平均高估 ~5 pp。這是「為什麼要 scale up」的 textbook 案例。

- **動機**：Step 21+21b 在 N=20 下有兩個關鍵 claim：(a) Smean 達 designer ceiling 69.9%；(b) STV (80.4%) 是相對最強軸。但 N=20 small-sample variance 可能把這兩條 claim 都灌水了。為驗證，把樣本從 20 擴到 100（新抽 80 個 disjoint Crello test ids，seed=43，max_inspect=2000）、用同 cold-start 管線 + 同 judge config 全跑一次。
- **方法**：
  - `step22_sample_extra80.py`：seed=43、max_inspect=2000、結構過濾（≥1 img + ≥1 text + 2–5 elements）、避開原 20 ids、下載 80 個 crello_<id>/ 資產。
  - `step22_coldstart_render.py`：對 N=100 ids 跑 cold-start pipeline（AnalyzeBrief → AssetAnalyzer → PlanAssets → resolve_background → GenerateLayout 單 batch → render_to_file），save `step22_coldstart_crello_<id>_{spec,candidate,render}` 三件。**100/100 0 crash**。
  - `step20_sega_eval.py --ids-file step22_n100_ids.json`：Phase A SEGA 6 rule-based metrics on N=100 cold-start。
  - `step21_phaseb_eval.py --ids-file step22_n100_ids.json --source agent / designer-gt`：Phase B GPT-4V 4 軸 on AL render 與 designer GT 各跑一次（same judge config）。
  - `step22_compare.py`：N=20 vs N=100 head-to-head 對照表。
  - 成本：pipeline ~$8 gpt-4o + Phase B 兩次 ~$4 = **~$12 total**；時間 ~50 min pipeline + ~25 min 兩跑 Phase B。
- **Phase A SEGA rule-based（N=100，judge-drift-free）**：

  | Method | Ali ↓ | Ove ↓ | Und_l ↑ | Und_s ↑ | Read ↓ | Occ ↓ |
  | --- | --- | --- | --- | --- | --- | --- |
  | **AgentLayout (cold-start)** | **0.0055** | **0.0013** | 0.0000 | 0.0000 | 0.0217 | 0.0016 |
  | Designer GT | 0.0066 | 0.1104 | 0.0584 | 0.0250 | 0.0179 | 0.0019 |
  | random (5 seeds avg) | 0.0309 | 0.0527 | 0.0000 | 0.0000 | 0.0192 | 0.0015 |
  | centered_stack | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0194 | 0.0019 |

  - ✅ **AL Ali=0.0055 < Designer GT 0.0066**：仍勝 designer，跨 N=20 → N=100 robust。
  - ✅ **AL Ove=0.0013 << Designer GT 0.1104**：勝 designer **85×**，明顯好；QC 的 no_overlap 5% tolerance 主動過濾真有效。
  - ✅ **AL Ove=0.0013 ≈ SEGA-13B 0.0025 量級**：rule-based 不受 judge 漂移影響，**這條 cross-paper claim 仍成立**——SEGA-13B 0.0025 是 SEGA paper 的純幾何計算數值，與我們同公式。
  - ≈ **Read/Occ 全 method 持平**（0.018-0.022 / 0.0015-0.0019）。
  - ❌ Und_l/Und_s=0 仍是 by-design scope 結果（AL schema 不出 decoration）；designer GT 也只 0.058/0.025（Crello dataset 本身 underlay annotation 稀疏）。

- **Phase B GPT-4V 4 軸 aesthetic（N=100 within-judge ratios）**：

  | 來源 | SDL | SQL | STV | SIO | Smean |
  | --- | --- | --- | --- | --- | --- |
  | Designer GT (N=100) | 8.080 | 8.780 | **7.940** | 5.670 | **7.617** |
  | AgentLayout (N=100) | 4.990 | 4.930 | 5.560 | 4.250 | **4.933** |
  | ratio (AL / Designer GT) | 61.8% | 56.2% | **70.0%** | **75.0%** | **64.8%** |

  **N=20 → N=100 ratio 變化**：

  | Axis | N=20 ratio | N=100 ratio | Δ |
  | --- | --- | --- | --- |
  | SDL | 69.2% | 61.8% | −7.4 pp |
  | SQL | 59.0% | 56.2% | −2.8 pp |
  | STV | **80.4%** | **70.0%** | **−10.4 pp** |
  | SIO | 73.5% | **75.0%** | +1.4 pp |
  | **Smean** | **69.9%** | **64.8%** | **−5.2 pp** |

- **誠實定調（最重要，推翻多項 Step 21+21b claim）**：
  - 🚨 **「STV 是最強相對軸」claim 被推翻**：N=20 STV 80.4% 是 sample-bias artefact；N=100 真值 **STV 70.0% < SIO 75.0%**——**SIO 才是最強相對軸**。Step 21b 把 STV 寫成 hero claim 是過早的結論。
  - 🚨 **「Smean 達 designer ceiling 70%」也被推翻**：N=100 真值 **64.8%**；AL 離設計師還有 35.2% gap，比 N=20 估的 30% 多 5 pp。
  - ✅ **「Phase A geometric 勝 designer」claim 仍成立**：Ali 0.0055 < 0.0066、Ove 0.0013 << 0.1104，且 N=20 → N=100 一致。**Ove 的 85× margin 是真實的 robust strength**。
  - ✅ **「Ove 對齊 SEGA-13B 量級」claim 仍成立**：SEGA-13B Ove=0.0025 是純幾何計算（judge-drift-free），AL 0.0013 與之同數量級。**這是唯一沒被 scale-up 推翻的 cross-paper claim**。
  - 🆕 **新意外發現：SIO 反而是相對最強軸**（75.0% ratio）。原因猜測：N=100 下 designer GT SIO 平均下降（從 5.85 → 5.67），而 AL SIO 幾乎不變（4.30 → 4.25），ratio 自然上升。實際解讀：**AL 與 designer 在 originality 軸的差距不像 N=20 看起來那麼大**，因為 designer 在大樣本下也沒那麼「原創」。Crello dataset 整體 originality 偏低。
  - 🆕 **「N=20 自帶 ~5 pp positive selection bias」是 paper-grade methodology finding**：所有用 random N=20 / N=50 small sample 報 GPT-4V aesthetic 的 layout/design paper（包含 AesthetiQ N=20 pilot 條件）都應該被 reviewer 質疑——**有 ~5 pp upward bias 的可能**。
- **真實可寫進論文的 contribution（取代 Step 21+21b 的所有 inflated claim）**：
  1. **唯一 cross-paper SOTA-level claim**：Ali/Ove 純幾何勝 designer GT + 對齊 SEGA-13B 量級（Step 20 + Step 22 Phase A，N=20+N=100 雙重 robust，judge-drift-free）。
  2. **Within-judge benchmark**：AL Smean 達 designer ceiling **64.8%**（N=100）；4 軸 ratio 範圍 56.2%-75.0%，SIO 最強、SQL 最弱。
  3. **Methodology contributions（兩個）**：(a) GPT-4V judge calibration 跨 paper 漂移、cross-paper Smean 不可直比（Step 21b）；(b) N=20 small-sample aesthetic eval 自帶 ~5 pp positive selection bias，所有相關 paper 需重評（Step 22）。
  4. **honest negative**：不勝設計師（aesthetic 各軸）、不勝 SEGA Smean、Refinement Loop 無 lift、N=20 報的 ratio 偏高 ~5pp。
- **caveat**：(1) 仍只 100 vs SEGA 全 Crello（~1971 samples），統計力進步 5× 但未到 SEGA scale；(2) judge=gpt-4o config 一致但 cross-paper baseline 仍不可直比；(3) Phase A cold-start vs SEGA full-pipeline（可能含 FR）方法論不完全對等。

### Step 21b — Judge-config 校準對照：designer GT 跑同 judge 推翻 Step 21 的 cross-paper claim（N=20，2026-05-20）

- **動機**：Step 21 把 SEGA Table 3 數值（FlexDM/PosterLlama/SEGA 系列）直接跟 AgentLayout 的 N=20 結果並排比較，得出「STV 達 SEGA-13B 量級」的 paper-grade claim。但 SEGA paper 的 GPT-4V judge **不是**我們的 gpt-4o——SEGA 用的可能是更早的 GPT-4V API、不同 temperature、不同 prompt wording。為驗證 cross-paper 數字是否可直接比，**把 Crello designer 原圖（`crello_<id>/ground_truth_preview.jpg`）跑過完全相同的 judge config**——若 designer GT Smean 接近 SEGA paper 的 SEGA-13B 6.320，則 cross-paper 可比；若 designer GT 顯著高於 6.320，則 judge 漂移成立，Step 21 的 cross-paper claim 無效。
- **方法**：
  - `step21_phaseb_eval.py --source designer-gt`：對 20 個 `ground_truth_preview.jpg`（Crello 設計師實際發佈圖），用 Step 21 完全相同的 gpt-4o + 4 軸 prompt + temperature=0 + max_tokens=8 + only-integer 指令打分。
  - 唯一變數＝source PNG（AL render vs designer GT），judge config 完全 frozen。
  - 成本 ~$0.40；輸出 `step21b_phaseb_designer_gt.json`。
- **數值**（**all 在同一 judge config 下**）：

  | 來源 | SDL ↑ | SQL ↑ | STV ↑ | SIO ↑ | Smean ↑ |
  | --- | --- | --- | --- | --- | --- |
  | **Designer GT（Crello 原圖，我們 judge）** | **7.950** | **8.650** | **7.650** | **5.850** | **7.525** |
  | AgentLayout（cold-start，我們 judge） | 5.500 | 5.100 | 6.150 | 4.300 | 5.263 |
  | Δ (AL − Designer GT) | −2.450 | −3.550 | −1.500 | −1.550 | **−2.262** |
  | ratio (AL / Designer GT) | 69.2% | 59.0% | 80.4% | 73.5% | **69.9%** |
  | SEGA-13B（**SEGA paper 自己的 judge**） | 6.149 | 6.745 | 6.348 | 6.038 | 6.320 |

- **核心發現（推翻 Step 21）**：
  - 🚨 **Judge calibration drift 確實存在**：我們的 gpt-4o judge 給 designer GT 7.525；SEGA paper 報自己 13B 模型在他們 judge 下是 6.320。**Designer GT 在我們 judge 拿的分比 SEGA-13B 在他們 judge 拿的還高 1.2 分**。可能解釋：(a) SEGA 用較舊的 GPT-4V API（更 conservative）、(b) prompt wording 差異、(c) 我們 max_tokens=8 + only-integer 指令使 LLM 跳過保守的「中間值偏好」。**不論哪個，Step 21 直接拿 SEGA Table 3 跟 AL N=20 比都不成立**。
  - ❌ **「STV 達 SEGA-13B 量級」這條 claim 無效**：原本看 6.150 vs 6.348 = −0.198 像噪音內，但 within-judge 真實對照是 6.150 vs designer GT 7.650 = **−1.500**——relative gap 19.6%。
  - ❌ **「Smean 勝 FlexDM」也無效**：FlexDM/PosterLlama published Smean 是 SEGA judge 下的數字，跨 judge 不可比。
  - ✅ **「STV 是我們相對最強的軸」這條 finding 仍成立**：4 條軸的 within-judge ratio 排名 STV (80.4%) > SIO (73.5%) > SDL (69.2%) > SQL (59.0%)，**STV 確實 gap 最小、對應 BackgroundAnalyzer + contrast-aware text color 系統設計真有效**。只是不再敢說「達 SOTA 量級」，改說「is our strongest axis with smallest gap to designer」。
  - ✅ **意外發現：SIO 弱不全是 by-design**：原本歸因「我們不做 graphic synthesis」，但 designer GT 在 SIO 也只拿 5.85（4 軸最低）——**Crello dataset 本身就不獎勵 Innovation**，judge 對任何 Crello-style 海報都打不高 SIO。Step 21 把 SIO 弱完全歸咎 scope-bound 是過度自責；至少一半是 dataset 特性。
  - 🆕 **methodology contribution**：「**GPT-4V judge calibration drifts across papers; direct cross-paper aesthetic-score comparison requires running the same judge config on a shared reference (e.g. designer GT)**」——所有用 GPT-4V judge 的 layout/design paper 都應該標但都沒標的 caveat，我們可以在 paper methodology 章節寫一個小節示警，對審稿人來說是有用的 contribution。

- **真實 paper claim 收斂到三條（取代 Step 21 原本的四條）**：
  1. **Within-judge benchmark**：AL Smean = 5.263 / Designer GT Smean = 7.525，AL 達 designer ceiling 的 **69.9%**（gap −2.262）；逐軸 STV 80.4% > SIO 73.5% > SDL 69.2% > SQL 59.0%。
  2. **STV is our relatively strongest axis**：BackgroundAnalyzer + contrast-aware text color 設計使 STV ratio 顯著高於其他三軸，與 system design intent 一致。
  3. **Judge-calibration drift caveat（方法論）**：cross-paper GPT-4V Smean 比較不可信，必須跑同 judge config 才能 head-to-head；所有後續論文應該效法此 control 設計。

- **caveat**：(1) N=20 vs full Crello 統計力仍有限；(2) 我們的 designer GT 7.525 不能直接跟 SEGA paper 的 6.320 比較 “whose GT designer 較強”——因為兩邊 GT 是同一個（Crello 原圖），這證明的是 judge 漂移而非 designer 差異；(3) 若 paper 要報 cross-method 數字，建議全部 baseline 都應在我們 judge 下重新 score（這要 baseline 作者開源 renders 或 layout JSON）。

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

### Step 23 — N=1,897 完整 Crello test split：對齊 SEGA paper full coverage（2026-05-26）

- **動機**：Step 22 N=100 已驗證 N=20 selection-bias inflation（STV claim 70% < SIO 75%），但 N=100 vs SEGA full Crello (N=1,971) 仍有 5× 統計力 gap、且「Smean 64.8%」claim 是基於 small-sample within-judge ratio。為了得到 paper-grade、跨 paper 直接 comparable 的 final claim，把 pipeline 對齊 SEGA 全 Crello test split。
- **Pre-launch 四項校準（2026-05-25 → 26）**：
  1. **`MAX_ELEMENTS=5 → float("inf")`**（`layout_agent/output/run_iou_eval.py:49`）— 5-element 上限把 qualifying pool 砍到 N=210（10.7%），是 sampling-time 過濾器（pipeline 本身無 hardcode），無痛拔除。
  2. **`max_token: 4096 → 16000`**（`~/.metagpt/config2.yaml`）— smoke test N=23 樣本 GPT-4o output 7,189 completion tokens、原 4,096 上限 mid-stream truncation；GPT-4o 支援 16,384 output，加 4× headroom 修復。
  3. **BASNet+ISNet 兩階段 saliency**（`metagpt/ext/agentlayout/evaluation/saliency_basnet_isnet.py`）— 取代 step20 原本 rembg alpha + Sobel gradient fallback（不是真 saliency），對齊 SEGA paper Occ 指標定義。
  4. **COLE single-call JSON**（`step21_phaseb_eval.py` 重寫）— 修正之前 4 次獨立 API call + S_QL 軸名對錯，改成一次 call 拿 5 軸（DL/CR/TV/IO + Graphics），對齊 COLE paper Table 3。
- **Smoke test（N=8 stratified, 含 N=23/26 極端壓力）**：`step24_pick_smoke_ids.py` 按 element count 分 5 個 bucket 各取 1-2 個 deterministic 樣本。render 8/8 ✅、Phase A 8/8 ✅、Phase B 8/8 ✅、無 JSON 解析失敗、BASNet+ISNet 跑得動。
- **N=1,897 抽樣 + 下載**：`step23_sample_full.py` streaming Crello test split 全集（1,971 raw → 1,897 通過 `>=1 img + >=1 text + >=2 elems` filter，96.2% pass rate）；1,797 個新樣本 + 100 已快取 = 全部 1,897 個 `crello_<id>/` 目錄寫到 disk，0 fail、69 秒下載。
- **Cold-start render 結果（17 小時、N=1,897）**：

  | 狀態 | 數量 | 比例 |
  | --- | --- | --- |
  | ok（新生成） | 1,788 | 99.89% |
  | cached（step13/step22/smoke） | 107 | — |
  | crash | 2 | 0.11% |
  | **可評估樣本** | **1,895** | **99.89%** |

  **crash ids**: `599ecda11350e83293007945`, `5f3a63f1a637ee11e3d600fc`（極少數樣本 LayoutGen 即使 max_token=16000 仍 retry 3 次都 JSON 不合法；對 paper claim 無影響、未刻意排除以保 reproducibility）。

- **Phase A：SEGA 6 rule-based 指標 head-to-head（N=1,896，1 個 sample step20 fallback GenerateLayout 也 crash）**：

  | Method | Ali ↓ | Ove ↓ | Und_l ↑ | Und_s ↑ | Read ↓ | Occ ↓ |
  | --- | --- | --- | --- | --- | --- | --- |
  | **AgentLayout** | **0.0004** | **0.0050** | 0.0000 | 0.0000 | 0.0144 | **0.1249** |
  | Designer GT | 0.0010 | 0.1038 | 0.2207 | 0.1383 | 0.0129 | 0.1279 |
  | random (5 seeds avg) | 0.0086 | 0.1887 | 0 | 0 | 0.0137 | 0.1374 |
  | centered_stack | 0.0000 | 0.0000 | 0 | 0 | 0.0143 | 0.1219 |

  - **Ali 勝 designer ~2.2×**（0.00045 vs 0.00100）— alignment 純幾何優勢，N=100 → N=1,897 維持。
  - **Ove 勝 designer ~20.6×**（0.0050 << 0.1038）— overlay (IoU) 極端優勢、跨 N 維持並**加大 gap**（N=100 是 85×→ 此處 ~20.6× 因為 designer GT pool 變大、平均 Ove 從 0.1104 降至 0.1038）。
  - **Occ flipped 勝**（0.1249 < 0.1279）— 飽和度遮擋，N=100 還是略輸 designer，此處因 BASNet+ISNet saliency 校準 + 大樣本平均後**反向**。
  - Read 近平手（0.0144 vs 0.0129）— text readability 差距收斂到 0.0015。
  - Und_l/Und_s = 0 — 已知 limitation：cold-start pipeline 不生 underlay decoration、designer GT 含真 underlay。
  - **跨 N stable 結論**：Ali/Ove 純幾何勝是 robust claim，從 N=20 → N=100 → N=1,897 三個 scale 全部維持、gap 不縮反穩或擴大。
- **Phase B：COLE 4 軸 GPT-4V aesthetic（N=1,895，agent renders）**：

  | Method | SDL | SQL | STV | SIO | **Smean** |
  | --- | --- | --- | --- | --- | --- |
  | **AgentLayout (N=1,895)** | 5.167 | **5.924** | 4.899 | 4.304 | **5.073** |
  | SEGA-13B (Table 3 ref) | 6.149 | 6.745 | 6.348 | 6.038 | 6.320 |
  | delta (AL − SEGA-13B) | −0.98 | −0.82 | −1.45 | −1.73 | **−1.247** |

  - **絕對最強軸：SQL**（5.924，Content Relevance）；**最弱軸：SIO**（4.304，Innovation）。
  - **vs N=100 absolute 跨 N 變化**：N=100 within-judge SIO 最強 75%、SQL 最弱 56% → N=1,897 **absolute 排名顛倒**（SQL > SDL > STV > SIO）。但 absolute scores 不能直接跟 within-judge ratio 比，需要 Step 23b（designer GT 過同 judge）才能算 N=1,897 within-judge ratio 確認 N=100 SIO 最強 claim 是否被推翻。
  - vs SEGA-13B Table 3 anchor：4 軸全部低 0.82–1.73 分；cross-paper 比較僅資訊性（SEGA paper 未明示 4 軸選擇）。
- **Step 23b — Designer GT within-judge calibration（完成，N=1,897/1,897，2026-05-27）**：用同一 COLE GPT-4V judge 評 1,897 個 designer GT (`ground_truth_preview.jpg`)，得 within-judge AL/Designer ratio。

  **Designer GT 絕對分（N=1,897）**：

  | Method | SDL | SQL | STV | SIO | **Smean** | SGI |
  | --- | --- | --- | --- | --- | --- | --- |
  | Designer GT | 7.932 | **8.577** | 7.560 | 6.792 | **7.715** | 8.149 |

  **Within-judge ratio（AL / Designer GT，N=1,895 配對）**：

  | 軸 | AL | Designer GT | **Ratio** | 排名 |
  | --- | --- | --- | --- | --- |
  | **SQL**（Content Relevance）| 5.924 | 8.577 | **69.1%** | 🥇 **最強** |
  | SDL（Design & Layout）| 5.167 | 7.932 | 65.1% | 🥈 |
  | STV（Typography & Color）| 4.899 | 7.560 | 64.8% | 🥉 |
  | **SIO**（Innovation）| 4.304 | 6.792 | **63.4%** | **最弱** |
  | **Smean** | 5.073 | 7.715 | **65.8%** | — |
  | SGI（Graphics，非主指標）| 4.404 | 8.149 | 54.0% | — |

  **跨 N 推翻表**：

  | 結論 | N=100 claim | N=1,897 真值 | 結果 |
  | --- | --- | --- | --- |
  | Smean within-judge ratio | 64.8% | 65.8% | ✅ **穩定**（1pp 內、跨 scale robust） |
  | 最強軸 | **SIO 75%** | **SQL 69.1%** | ❌ **被推翻** |
  | 最弱軸 | SQL 56% | SIO 63.4% | ❌ **被推翻** |

  **重大發現**：N=100 → N=1,897 再次重現 N=20 → N=100 的 axis-ranking flip pattern：**small-sample selection bias 會系統性誤導 per-axis ranking**，但 Smean 整體 capability ratio 跨三個 scale 穩定在 ~65% × designer ceiling。Step 23b 是 paper-grade 第二個 methodology contribution（Step 22 是第一個，N=20→N=100 STV flip）。

- **誠實定調（final，post-Step 23b）**：
  - ✅ **Phase A 三層 robust（N=20/100/1,897）**：Ali/Ove 純幾何勝 designer，N=1,897 還多 Occ flipped 勝（BASNet+ISNet saliency 校準後）。**這是論文最 robust 的 contribution**。
  - ✅ **Phase B Smean 跨 scale 穩定（~65% × designer）**：N=100 64.8% → N=1,897 65.8%、1pp 內，capability ratio 穩固。**這是 paper 第二個 robust claim**。
  - ❌ **Per-axis ranking 不可宣稱**：SIO 最強 / STV 最強 等小樣本軸排名 claim 在 N=1,897 全部 flip → 寫進論文僅可說「per-axis ranking 在 N=1,897 下 SQL > SDL > STV > SIO，與 small-sample 排名差異反映 selection bias 而非真實能力結構」。
  - ✅ **N=1,897 ≈ SEGA full Crello (1,971)** — 只差 74 個未通過 `>=1 img + >=1 text` schema 要求的樣本，**對 paper claim 是「approximately full coverage, 96.2%」**，consistent with SEGA scope。
  - ⚠️ 仍**不可宣稱**勝 designer overall（Phase A Und_l/Und_s 結構性輸；Phase B Smean 65.8% < 100%）。
  - 🆕 **paper-grade methodology contribution 確認三項**：
    1. judge calibration drift（Step 21b N=20）
    2. N=20 → N=100 selection bias（Step 22 STV flip）
    3. **N=100 → N=1,897 selection bias（Step 23b SIO/SQL flip）** — full-scale validation that even N=100 per-axis claims need ≥1,000 sample validation
- **Trade-off**：✅ Pre-launch 四項校準全部 smoke 通過；✅ N=1,897 ≈ SEGA full coverage、99.89% Phase A completion + 100% Phase B completion；✅ Phase A Ali/Ove/Occ 三勝 + 跨三個 scale 一致；✅ Phase B Smean 65.8% × designer 跨 scale 穩定；✅ 成本控制 ~$142 / $246.75（$104 餘 buffer 可做後續 ablation）；❌ Per-axis ranking 三個 scale 各有不同 → 寫進論文需 sample-size disclosure；❌ 2 個 render crash 未做 root-cause（max_token=16000 已 GPT-4o 4× headroom 邊際效益遞減）。

---

### Step 25 — Underlay placement headroom analysis（oracle upper bound，**非 end-to-end LLM 結果**，2026-05-27）

- **動機**：Step 23 Phase A 報出 AL Und_l/Und_s = 0 vs designer GT 0.2209/0.1384，原本誤判為 "decoration synthesis is out of scope"。實際 Crello 95% 樣本提供 type 2/3/4 underlay/shape PNG + color metadata（dataset-given asset，不是要 synthesize 的新裝飾），sampling filter 把它們過濾掉、AssetSchema 又沒這個 kind → AL 從來沒機會碰到 underlay。**改造 pipeline 支援 underlay placement 估 1 工作天 + $131 重跑**——在投入前，先做 oracle headroom analysis 量化「最好情況」拿到的 Phase A 數字。
- **方法（zero-cost oracle）**：`layout_agent/output/step25_oracle_underlay.py`
  1. 對 N=1,895 個 Step 23 已 render 樣本，從 `step22_coldstart_crello_<id>_candidate.json` 取 AL 的 image+text bbox（不變動）
  2. 從 `crello_<id>/meta.json` 取 designer GT 的 underlay bbox（type 2/3，full-canvas 排除）
  3. **直接合成 hybrid layout = AL image+text + designer underlay bbox**（NO LLM call，NO re-render）
  4. 用 SEGA `sega_metrics.py` 算 Ali/Ove/Und_l/Und_s
  - **本實驗 NOT end-to-end LLM placement，是「if LLM placed underlay at exactly designer's coords」的 upper bound**，不可寫成 AL 真實能力。
- **結果（N=1,895，587 個樣本含 designer underlay）**：

  | Method | Ali ↓ | Ove ↓ | Und_l ↑ | Und_s ↑ |
  | --- | --- | --- | --- | --- |
  | AL (image+text only, Step 23 reality) | 0.0004 | 0.0050 | 0.0000 | 0.0000 |
  | **Oracle hybrid（AL i+t + designer underlay）** | **0.0004** | **0.0050** | **0.2787** | **0.2326** |
  | Designer GT（完整布局） | 0.0010 | 0.1038 | 0.2209 | 0.1384 |

- **解讀**：
  - 🟢 **Ali / Ove 完全不變**（0.0004 / 0.0050）—— 確認加 underlay **不會傷害** Phase A 兩個最強 claim：(a) Ove metric 定義本就排除 underlay class、(b) Ali 用 pairwise min-distance、designer underlay bbox 本身對齊得好。
  - 🟢 **Oracle Und_l = 0.2787 > designer 0.2209、Und_s = 0.2326 > designer 0.1384**（1.26×、1.68×）—— AL 把 image+text 排得比 designer 更緊湊，所以 designer 的 underlay bbox 不小心 contain 得更完整。
  - ⚠️ **Oracle 是上限**，真 LLM placement 預估只能到 ~70-80%（Und_l ~0.20、Und_s ~0.14，達到或微微小贏 designer 量級）。
- **誠實定調**：
  - 本實驗**僅做為 metric architecture 健全性的 evidence**：證明「SEGA 6 指標 framework 不會因為 AgentLayout 多放 underlay 而懲罰 Ali/Ove」，Und 由 0 變正是 in-scope 擴充而非架構天花板。
  - **本實驗不寫成 end-to-end AL 結果**；論文 Phase A table 仍以 Step 23 真實數字（Und = 0）為準，oracle 數字以 **"oracle headroom"** 標籤明確區分。
  - Future work 段落明確記載：若實作 underlay placement，預期 Und_l 達 0.20–0.23 量級（接近或微小贏 designer），Ali/Ove 維持。
- **Trade-off**：✅ 零成本 / 零 API / 10 分鐘跑完，量化了「underlay 改造的真實 headroom」；✅ 確認 Ali/Ove 主 claim 對 underlay 改造 robust（不會 regression）；✅ 為 Future work 段落提供 quantified ceiling；❌ **不可** 寫成 AL real run；❌ Oracle 因「designer underlay bbox + AL image+text 緊湊位置」的偶然耦合，反而比 designer 自己的 Und_l 還高 → 實際 LLM placement 達到此上限不現實；❌ Read / Occ 未做 oracle 估算（需重算 saliency map，已被 Step 23 BASNet+ISNet pass 標準完成）。

---

### Step 26–28 — Underlay redesign：Crello dataset critique + 真實 Designer GT baseline 重算（zero LLM，2026-05-27）

**動機**：Step 25 oracle 預測 underlay 改造可 push Und_l 到 ~0.20–0.23（接近 designer）。實作前先驗證 dataset 假設正確。

**Step 26 — dead-end（已 REVERTED，commit `510a52ef`）**

- 5 個檔案改動把 Crello `type 2/3/4` 元素當 placeable underlay：`save_sample` 存 `asset_NN_underlay.png`、`build_pipeline_inputs` emit 進 asset_list、`analyze_brief` PROMPT 加「underlay → decorative_image」、`generate_layout` PROMPT 加「decorative_image z_index 低於前景」、`step20._cls_from_spec_element` 加 decorative_image → CLS_UNDERLAY 分支
- Smoke N=8 stratified samples：metric 看似漂亮（Und_l 0→0.67、Und_s 0→0.625、Ali/Ove 不變），但 8/8 視覺品質都不及 designer GT；其中 sample `5de51f659fea0cc374ae59e8`（"Graphic Designer Working on Tablet" 廣告）發生明顯 **role-reversal**：desk photo 被當 underlay（z=2、middle 層），紅色 SALE pill 被當 product_image（z=3、前景）
- 視覺 role-reversal 觸發深查 dataset 假設、最後決定整批 revert

**Step 27 — dataset audit（保留作為 paper-worthy methodology contribution）**

- 新增 `layout_agent/output/step27_audit_underlay_assets.py`：image content classifier（PIL `unique_colors` + `alpha_var` + `area_ratio`）跑 1,897 個 Step 23 qualifying samples 的 type 0/2/3/4 element
- 全 corpus（12,274 個 raster element）分類結果：

  | Label | count | 占比 |
  | --- | --- | --- |
  | shape (placeable underlay) | **8,087** | **65.9%** |
  | photo | 2,319 | 18.9% |
  | full_canvas | 1,821 | 14.8% |
  | ambiguous | 47 | 0.4% |

- Per-type-code 分布揭露：

  | Type | Total | shape % | photo % | full_canvas % |
  | --- | --- | --- | --- | --- |
  | **type 0 (image)** | **9,780** | **82%** | 13% | 5% |
  | type 2 (svgImage) | 1,661 | 1% | 47% | 52% |
  | type 3 (coloredBackground) | 503 | 0% | 0% | 100% |
  | type 4 (graphic) | 330 | 5% | 92% | 3% |

- **核心 dataset finding：Crello 沒有「underlay 專屬 type code」。82% 的 shape underlay 藏在 type 0；type 2/3/4 反而幾乎沒有可 placement 的 shape underlay（type 3 全 full_canvas、type 4 全 photo）**
- **這同時暴露 Step 23 `_build_gt_layout` 嚴重低估 Designer underlay 能力**：舊 logic line 250-253 把所有 type 0 → CLS_IMAGE_LOGO，**漏算 8,058 個 shape underlay**

**Step 28 — classifier-driven redesign + 真實 Designer GT 重算（zero LLM、commit `510a52ef`）**

- 6 個檔案改動（5 個是 Step 26「正確設計面向」保留 + 1 個新加）：
  1. `run_iou_eval.save_sample`：對 type 0/2/3/4 element 跑 `_classify_underlay` → shape→`kind=underlay` / photo→`kind=image` / full_canvas→`kind=background_candidate`；descriptor 加 `classifier_label` + `classifier_signals`
  2. `run_role_team_live_crello.build_pipeline_inputs`（+ `run_iou_eval` 鏡像版）：emit `kind=underlay` 進 asset_list；bg 偵測優先 `kind=background_candidate`
  3. `analyze_brief.PROMPT_TEMPLATE`：`_underlay.png` 後綴 → `semantic_type=decorative_image`（措辭改為「pre-classified shape plate」）
  4. `generate_layout.PROMPT_TEMPLATE`：`decorative_image` z_index 嚴格低於前景、bbox extend 10-20%、面積 < 60% canvas
  5. `step20._cls_from_spec_element`：`semantic_type=decorative_image` → `CLS_UNDERLAY`
  6. **新增** `step20._build_gt_layout`：從 type_code-driven 改為 meta.json `kind`-driven（`kind=underlay`→CLS_UNDERLAY、`kind=image`→CLS_IMAGE_LOGO、`kind=background_candidate`→skip；保留 95% full-canvas defense）
- 新增 driver：`step28_resnapshot_with_classifier.py`（重抓 1,897 sample 用新 save_sample；8.5 分鐘、$0；underlay=8,087、image=2,366、background_candidate=1,821、text=8,016 跟 audit 完全對齊）
- 新增 driver：`step28_phasea_cached_ids.json`（1,887 個有 step22 cached candidate 的 ids；過濾掉 10 個 missing cache 避免重打 LLM）
- 重算 Phase A（`step20 --mode cold --ids-file step28_phasea_cached_ids.json --out step28_phasea_classifier_redesign.json`，cached AL spec + 新 GT classifier、LLM-free）：

  | Method | Ali ↓ | Ove ↓ | **Und_l ↑** | **Und_s ↑** | Read ↓ | Occ ↓ |
  | --- | --- | --- | --- | --- | --- | --- |
  | AgentLayout (cached spec, pre-redesign) | 0.0005 | 0.0015 | 0.0241 | 0.0076 | 0.0029 | 0.0478 |
  | **Designer GT (NEW classifier)** | 0.0010 | 0.0448 | **0.3536** | **0.2667** | 0.0023 | 0.0490 |
  | random (5 seeds avg) | 0.0086 | 0.1031 | 0.2482 | 0.0486 | 0.0030 | 0.0529 |
  | centered_stack | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0031 | 0.0468 |

- **對比 Step 23 reality（舊 type_code-driven GT）**：

  | Metric | Step 23 GT (old) | Step 28 GT (new) | 變化 |
  | --- | --- | --- | --- |
  | Designer Und_l | ~0.125 | **0.3536** | **+2.83×** |
  | Designer Und_s | ~0.125 | **0.2667** | **+2.13×** |

- **核心 paper finding（取代 Step 25 oracle 預估）**：
  - 🔴 **舊 `_build_gt_layout` 嚴重低估 Designer Und 2-3 倍**：type_code-driven 漏算 8,058 個 type 0 內的 shape underlay。Step 23「designer underlay 不算強」的數字是 measurement artifact，**真實 designer 大量使用 underlay**
  - 🔴 **真實 AL → Designer capability gap：Und_l ~14.7×、Und_s ~33×**（之前以為兩邊都 0 或 ~1× gap、實際大一個 order of magnitude）
  - 🟢 **Step 23 Ali/Ove「AL 勝 designer」claim 不受影響**：AL Ali 0.0005 / Ove 0.0015 vs GT Ali 0.0010 / Ove 0.0448 跟舊計算對齊。Ove gap 從 ~20.8× (0.104/0.005、Step 23 老 type_code GT) 變為 ~30× (0.045/0.0015、Step 28 新 classifier GT)：新 `_build_gt_layout` 把 GT underlay 也算進 overlap 後 GT Ove 降，但 AL 仍維持 Ove 大勝、方向不變
  - 🟢 **Ali/Ove/Read/Occ 為 Step 23 reality 鎖定的 main paper claim 提供獨立驗證**：跨「舊 type_code-driven GT」與「新 classifier-driven GT」兩種 GT 設置，AL 仍維持 Ali/Ove 雙勝
- **沒做的事（成本 + 時間考量）**：
  - 修 AL 端讓它真實能 emit underlay（`build_pipeline_inputs` 改動已 commit `510a52ef`，但**沒重跑 1,887 step22 cold-start**，預估 ~$110 / 5-6h LLM）
  - Phase B（COLE 5-axis）GPT-4V 重評（~$30）
  - 上面兩項都會讓 AL Und 從 0.024 升到某個值（也許接近 0.20-0.30，但需實證）
- **誠實定調**：
  - 本 step 是 **dataset critique 性質的 methodology contribution**（PKU PosterLayout 在 Crello 上 cohort 需 image content classifier，不是 type code）
  - **可寫進論文** §method：「Crello dataset 沒有 underlay 專屬 type code、需 image content classifier 區分 photo vs shape；既有 SEGA-style 評估若 type_code-driven 會嚴重低估 Designer 真實 Und 約 2-3 倍」
  - **可寫進論文** §results：「我們的 AgentLayout cold-start 在 Ali/Ove 跨兩種 GT classifier 設置均勝 designer；但在 Und 上 capability gap ~15-33× 為 limitation，pipeline 改造（underlay placement）為明確 future work」
  - **不可宣稱**：(a) AL 真實能 emit underlay（cached AL spec 仍是 pre-redesign）；(b) 拿 0.354 跟 0.024 的 ratio 當「方法不足」的最終 evidence（需 AL re-render 才知道改造後真實 gap）
- **Trade-off**：✅ Zero LLM cost、$0、commit 進 git、pytest 154 passed 0 regression；✅ 拿到「真實 Designer GT 0.354」這個 paper-grade baseline；✅ Step 23 main claim 不受影響；❌ AL 端真實能力還沒驗證（待 ~$110 LLM 重跑）；❌ Step 25 oracle 數字部分作廢（587 個 sample 含 photo-mislabeled underlay；舊 oracle 0.2787 偏低估值，真實 designer 是 0.3536）；❌ [[feedback-underlay-is-placement]] memory 中「type 2/3/4 dataset 提供 underlay PNG」這句作廢，已新建 [[project-crello-underlay-in-type0]] 取代

---

### Step 29 — Underlay-enabled AL 端到端重跑：N=1,895 paper-grade Phase A（2026-05-28）

**動機**：Step 28 拿到真實 Designer GT（Und_l 0.354）但 AL 端仍是 pre-redesign cached spec（Und_l 0.024）。Step 28「沒做的事」第一項——重跑 step22 cold-start 讓 AL 真實 emit underlay——本 step 完成，補完 capability gap claim。

**方法**：

1. **N=5 redesign smoke gate（先擋再燒）**：從 1,802 個含 `kind=underlay` 的 sample 挑 5 個 stratified by element count (3/7/9/11/14)，清 cache 跑新版 cold-start。結果 5/5 ok、0 crash、**0 role-reversal**（對比 Step 26 type-code-driven 的 8/8 role-reversal）；AL Und_l 0.024→0.584、Und_s 0.008→0.533，視覺檢查確認 photo 仍 photo、shape 仍 underlay。N=5 過關才啟動全集（避免重蹈 Step 26 燒完才發現 role-reversal）。
2. **F 全集 cold-start re-render**：清 1,882 個 pre-redesign cache（保留 5 個 smoke render）、跑 `step22_coldstart_render --ids-file step23_full_ids.json`（~$110、~6h）。結果 1,890 ok + 5 cached = **1,895 / 1,897（99.89%）**，2 crash（0.1%，ids `5f3a63f1a637ee11e3d600fc`、`5889aa8395a7a863ddcc361a`）。
3. **Phase A 重算（zero-LLM）**：`step20 --mode cold --ids-file step23_full_ids.json --out step29_phasea_full_redesign.json`，用新 underlay-enabled AL spec/candidate vs 新 classifier GT。

**結果（N=1,895，underlay-enabled AL vs Designer GT）**：

| Method | Ali ↓ | Ove ↓ | **Und_l ↑** | **Und_s ↑** | Read ↓ | Occ ↓ |
| --- | --- | --- | --- | --- | --- | --- |
| **AgentLayout (underlay-enabled)** | 0.0000 | 0.0035 | **0.5518** | **0.4428** | 0.0311 | 0.1620 |
| Designer GT (new classifier) | 0.0010 | 0.0449 | 0.3542 | 0.2674 | 0.0235 | 0.1371 |

- AL Und 軌跡：Step23 `0.000` → Step28 cached `0.024` → N=5 smoke `0.584` → **full `0.5518`**
- 4 個幾何指標（Ali / Ove / Und_l / Und_s）全勝 designer；2 個 content-aware（Read +33% / Occ +18%）略輸
- Designer GT 0.3542 vs Step 28 的 0.3536：N=1,895 vs N=1,887 的 8 樣本差異、一致

**誠實定調**：

- 🟢 underlay capability gap 已從 Step 28 的「AL 0.024 vs GT 0.354（~15× 落後）」反轉為「AL 0.55 vs GT 0.35（達/超過 designer 幾何水準）」——pipeline redesign 證實有效
- 🟢 Ali/Ove 雙勝在 underlay-enabled 配置下維持（跨三種設置：Step 23 舊 GT、Step 28 cached AL、Step 29 re-render AL，方向一致）
- 🔴 **Und 勝是 metric-level containment、不等於視覺品質更好**：Read/Occ 同時略退（over-containment trade-off），N=5 smoke 也觀察到 underlay 偶爾過大 / 底部留空。論文把 baseline AL（Und=0）vs underlay-enabled AL（Und=0.55）當 **ablation 對照**，不覆寫 Step 23、不宣稱視覺勝 designer
- 🟡 underlay-enabled 配置的視覺品質（COLE 5-axis GPT-4V）**尚未重評**（~$30，§4 open 項）——「Und 高是否=視覺好」目前只有 Phase A 幾何證據

**Trade-off**：✅ 補完 Step 23/28 的 AL 端缺口、capability gap claim 完整；✅ N=5 smoke gate 在燒 $110 前擋掉 role-reversal 風險；✅ Ali/Ove 雙勝跨三設置 robust；❌ Read/Occ 略退（over-containment）；❌ 視覺品質（Phase B）未重評

---

> ⚠️ **以下 Step 30–64 全部使用新 judge schema（COLE 5 軸 1-10）與 oracle pairwise 協定，數值與上方 Step 29 以前的 Phase A/B/win-rate 不可直接比較**（見頁首 Step 30 Baseline 警示）。原始數據：`layout_agent/output/step3x..step64` 各 log / results JSON（gitignored）；完整技術細節見 `IMPLEMENTATION_LOG.md` 對應條目。

### Step 30 — in-pipeline Judge 遷移 COLE 5 軸（2026-06-09）

**動機**：自製 4 軸 0-25 / total 0-100 rubric 與 COLE/SEGA 文獻不對齊，loop 內 Judge 與 Phase B 評估用不同尺度。

**方法**：judge schema 改 COLE 5 軸 1-10（total 5-50）、`ACCEPT_THRESHOLD` 75 → 35。pre-migration 基線封存於 git tag `step29-baseline-pre-judge-migration`（commit `0956f2bb`）。

**誠實定調**：純 schema 遷移、無品質 claim；唯一後果是 Step 30+ 的 trace / Phase B 數值與本文件前半不可直比（頁首警示即此事）。

---

### Step 31 — Refinement loop 不會 climb：root-cause + best-so-far guard（N=5，2026-06-09）

**結果**：0/5 accept、mean best-so-far 34.8 / threshold 35；唯一過 threshold 的 5e72 是 **round 1 cold-start 拿 38**，後續 3 輪 refinement 無法重現。

**四個 root cause**：(1) COLE rubric 飽和於 Crello-grade（CR=7、IO=6 跨樣本一致、reward gradient ≈ 0）；(2) Judge noise（~1-2 點）> signal gap（1-3 點）；(3) suggestion→action 鬆耦（動 1 元素破壞別軸）；(4) Markov-chain 退步——best-so-far guard 修掉後 mean +0.8（噪音內）。

**誠實定調**：loop「不會往上爬」是結構性，guard 只止血不增益。

---

### Step 32 — Phase B head-to-head：loop 真實落後 cold-start（N=5，2026-06-09）

**結果**：同 5 樣本同輪 COLE GPT-4V 評分——cold-start Smean **6.10** vs live-loop **5.75**、Δ=**−0.35**；3/5 退步、1/5 改善、1/5 持平；最大退軸 CR −0.80。

**誠實定調**：對齊 Judge 的 refinement loop 不只沒幫忙、還略傷。Source：`step32_phaseb_compare.json`。

---

### Step 33 — COLE rubric 寫進 Generator prompt：+0.05（噪音內）（N=5，2026-06-09）

**結果**：rubric 當生成 prior（非 Judge filter），PRE33 6.15 vs POST33 6.20、Δ=**+0.05**、IO 軸完全不動。

**誠實定調**：「rubric 位置不是 bottleneck」初步證據。Source：`step33_phaseb_compare.json`。

---

### Step 34 — Oracle GT-guided pairwise refinement：決定性 Generator-bounded 證據（N=5 → N=20，2026-06-09）

**方法**：K=1 + pairwise judge vs Crello designer GT + 3 retry——給 Generator 最強 reward signal（直接跟真 GT 比、含 axis-level reason feedback）。

**結果**：N=5 全滅（0 commit、15/15 verdict B=GT 勝）。N=20 robust 驗證：**2/20（10%）ok**、60 verdicts 中 GT 勝 55（91.7%）；真正贏 GT 的 verdict 僅 **2/60（3.3%）**（`592c2135` r1a2、`589d7bd9` r1a1），另 2 個 A-win 是 tie-break-by-prompt-rule、不算實質。

**誠實定調**：bottleneck 在 Generator（gpt-4o zero-shot）、不在 Judge 設計。Oracle 在 inference time 用了 GT＝upper-bound ablation，**不能與 SEGA/PosterO 數字並列**，放 limitation/ablation section。

---

### Step 35/36/36b — 視覺工程：5 條 QC 規則 + metadata-leak fix，成功率 10%→20%（N=20，2026-06-09）

**動機**：逐張看完 Step 34 的 17 個失敗 PNG，發現多為具體 fixable bug（41% underlay 過大、18% Analyst 把 Crello 目錄描述當 title、18% asset 利用不當、12% 配色錯）——**Generator-bounded 結論部分撤回**。

**方法**：5 條新 QC 規則（`TEXT_OBSCURED_BY_OVERLAY`、`LOW_TEXT_CONTRAST`、`DECORATIVE_IMAGE_OVERSIZED`、`TITLE_UNDERSIZED`、`TITLE_PERIPHERAL`）＋ metadata-leak prompt fix（visible heading 必須來自 text snippets）。

**結果**：2/20（10%）→ 3/20（15%）→ **4/20（20%）**，doubled；GT 勝率 91.7%→86.2%。清楚 win：5e72 [B,B,B]→[A,A,A]，標題正確用 text snippet 而非 metadata。4 個 success case（5e8d/592c/589d/5e72）可當 showcase figure。

---

### Step 36c — N=100 robust 驗證 + title peripheral top-band（2026-06-09）

**結果**：Step 35/36 規則鏈在 **N=100** 重測 = **14%（95% CI 7–21%）**；N=20 的 20% 在大樣本下回落但量級維持；14 個 success sample 可作 showcase。`TITLE_PERIPHERAL` 加 top-band 豁免（標題貼頂是合法設計）。

**誠實定調**：14% 是 charitable judge 下的數字——下一步 Step 37 證明其中多數是 tie-break 偽影。

---

### Step 37 — strict judge + Tier 1 QC 收緊：14%→2%（N=100，2026-06-09）

**動機**：逐張檢查發現 N=100 的 14 個「成功」中 9/14（64%）是 pairwise prompt「prefer Image A by default」tie-break 規則造成的虛胖。

**方法**：P1 Generator prompt 加 5 條 layout constraints；P2 tie-break 反轉（prefer B by default、A 只在 specific objective improvement 才贏）；P3 QC `TEXT_OBSCURED` 閾值 0.30→0.20＋same-z 觸發；P4 sequential text y-order。

**結果**：ok 14/100 → **2/100（2%）**；GT 勝 90.7%→98.7%。

**誠實定調**：**2% 才是 paper 該報的數字**——14% 是 LLM judge 校準偽影。

---

### Step 38 — 失敗 checklist + CoT 絕對打分：反向失敗（N=5，2026-06-09）

**結果**：「列 flag 再算分」（`score = 10 − len(flags)`）讓 Smean 從 6.10 **反向衝到 8.80**——LLM 用 hedging 詞描述問題但只勾 1-2 個 flag，charitable bias 未被突破。

---

### Step 39 — J5/J6/J7 校準絕對打分：終於跟視覺一致（2026-06-09）

**方法**：公式倒轉（從中點 5 出發、`5 + |strengths| − |flags|`）＋明確 anchor（award 9-10 / Crello GT 5-6 / AL typical 3-4 / broken 1-2）＋反 hedging（用模糊詞就必須勾 flag）。

**結果（三組數字、各自語意）**：(1) 同 5 樣本三方對照：原 COLE 6.10 / Step 38 8.80 / **Step 39 3.90**，且樣本級 ranking 與視覺判斷一致；(2) GT anchor check：5 個 designer preview Smean **4.75**（calibration 健康）；(3) N=20 cold-start Smean **3.73**；(4) 同 5 ids head-to-head：GT 4.75 vs AL 3.75、**Δ=−1.00**。

**誠實定調**：先前「Phase B 6.10 vs 6.6 ≈ noise」站不住——校準後 **AL 落後 designer GT 約 1.0 點**，原 0.5 點是 mean-regression 偽影；Step 39 保留 ordinal 訊號、揭穿 magnitude 失真。

---

### Step 40 — Flag-aware 結構化 reject feedback：0/20、whack-a-mole（2026-06-09）

**方法**：Judge 逐軸列 closed 21-flag catalog，reject 時把 unique-to-A flag 翻成 concrete action 餵 Generator——feedback specificity 的上界測試。

**結果**：**0/20 ok、60/60 verdict 全敗 GT**。Trace 顯示 whack-a-mole：核心 flag（composition_unbalanced 等）3 次重生都消不掉、還冒新 flag。

**誠實定調**：feedback channel 的 specificity 不是 bottleneck，Generator 本身做不到。

---

### 八實驗收斂結論（Step 20b→30→31→32→33→34→37→40，paper main finding）

| Step | 假設 | 結果 |
|---|---|---|
| 20b | refinement loop A/B controlled | 無 lift |
| 30 | COLE 5-axis Judge alignment | 無 lift |
| 31 | best-so-far guard | mean +0.8 noise |
| 32 | Phase B loop vs cold | loop **−0.35** |
| 33 | rubric in Generator prompt | +0.05 noise |
| 34 | oracle pairwise vs GT | 10%（charitable judge） |
| 37 | strict judge + Tier 1 QC | **2%**（校準後） |
| 40 | flag-aware structured feedback | **0%**（whack-a-mole） |

**Main numbers**：strict pairwise N=100 = **2%**；calibrated absolute Smean = **3.73 AL vs 4.75 GT（Δ=−1.0）**。八實驗逐一消除 alternative explanation（judge alignment / Markov regression / rubric 位置 / tie-break bias / feedback specificity），收斂於 **gpt-4o zero-shot Generator 在 Crello commercial-design band 的本質能力上界**。
（後註：此結論其後被 Step 47 發現的 render confound 要求重審，並在 Step 48 去 confound 後重新成立。）

---

### Step 41–46 — GT-anchored refinement 工具鏈（commit `7ee833f1`，2026-06-10）

單一 commit、無逐步 log：`place_in_bbox`（QC 退件自動回填）、typography 決策通道、**Generator vision channel**（Generator 直接看渲染後背景圖）。Step 46 smoke 撞 21 次 LLM 安全拒答——後由 Step 47 確認與 render confound 連動（修正後該 smoke 拒答歸零，但拒答自 Step 58 起以背景噪音形式回歸，見 Step 62 更正）。細節以 IMPLEMENTATION_LOG Step 47/48 條目內的引用為準。

---

### Step 47 — 三項 render/data-channel confound 修正：Generator-bounded 需重審（2026-06-10）

**發現的三個 confound**：(1) **composite background plates**——17.3% 樣本有多張背景 plate、舊管線只渲染第一張（Generator/Judge 看到的背景不完整）；(2) **字型四方不一致**——GT 預覽 / AL render / prompt 宣稱 / 實際 fallback 各用不同字型，修法是 bundle 5 個 OFL Google Fonts 進專案；(3) `MAX_UPSCALE=2.0` 上限防小圖拉糊。

**誠實定調**：Steps 30–40 的結論全部在帶 confound 的 render 下取得，須加註記；N=5 post-fix smoke 拒答 0（vs Step 46 的 21）。是否影響結論方向 → Step 48 重測。

---

### Step 48 — 去 confound N=20 重跑：Generator-bounded 成立（2026-06-10）

**結果**：**0/20 acceptance、judge 31/31 全判 B（GT）勝**、所有樣本 round1_exhausted——與 pre-fix 同型。

**誠實定調**：三項 confound 修正後結論方向不變，**Generator-bounded 在乾淨條件下重新成立**；論文引用本步而非 Step 34/37 的帶 confound 版本。

---

### Step 49 — prompt-only 上限 ablation：graphics 是唯一移動軸（N=20，2026-06-11）

**方法**：49a typography 決策通道進 prompt；49b dead-space balance constraints；49c oracle 加 per-axis A/B/tie attribution。

**結果**：**graphics tie 16%→41%（p=0.050）為唯一移動軸**；design_layout / typography 一票不動；QC reject 率 47%→41%。

**誠實定調**：prompt 指引有可達天花板、且搆不到主軸——**勿再提 prompt-only 改進方案**（此結論在 Step 58/60 反覆驗證）。

---

### Step 50 — gpt-5.2 control：同型失敗（N=5，2026-06-11）

**結果**：換更強模型 0/5、失敗形狀與 gpt-4o 相同。結論改名為「**LLM-coordinate-generation-bounded**」——瓶頸是 LLM 以座標文字生成版面這個介面，非單一模型能力。附帶修復：`openai_api.py` 對 o1/o3/o4/gpt-5 prefix 的參數相容。

---

### Step 51 — blind judge audit：pairwise 從來不是 blind（2026-06-11）

**發現**：歷來 pairwise judge prompt 一直標示 A=candidate、B=GT——**所有 headline 勝率都是 label-aware 數字**。Blind 重判：position bias = 0；design_layout / typography 的輸**是真的**（blind 下仍輸）；**innovation 有 label bias**（blind 下 candidate 勝 60% vs labeled 0%）。

**誠實定調**：headline 數字一律須 blind 重跑；本文件 Step 56 之後引用的 blind 數字均源於此協定。

---

### Step 52 — gate 不對稱：GT 70% 被我們自己的 QC gate 退件（2026-06-11）

**結果**：把 20 張 designer GT 餵進自家 QC gate，**14/20（70%）被退**（違規 overlap median 0.062）——gate 對 GT 式解（文字壓主體、大面積重疊）天然不友善。是否為 gap 成因 → Step 53 ablation。

---

### Step 53 — gate-off ablation：gate 不是 gap 成因（2026-06-11）

**結果**：關掉 safe-zone gate 重跑，blind 判決**一票未變（2/32/0）**。

**誠實定調**：GT 被 gate 誤殺是真的，但放寬 gate 不會改善判決——**勿再提放寬 gate 方案**；gap 在 Generator 構圖本身。

---

### Step 54 — render parity 分解：render channel 佔 blind gap 61–68%（上界）（2026-06-11）

**方法**：把 GT layout 用我們的 renderer 重渲染再 blind 對判，分離「版面」與「渲染」兩個通道。

**結果**：render channel 佔 blind gap **61–68%（上界）**；舊 renderer 天花板（GT layout 過我們 renderer 後的 blind 勝率）僅 **22.5%**——renderer 本身壓死了上限。

---

### Step 55 — renderer 升級：天花板 22.5%→55%（2026-06-11）

**方法**：修 2 個 silent font bug、auto-wrap、text fit、rotation 支援。

**結果**：GT-through-our-renderer blind 天花板 design_layout 22.5%→**55%**、typography →30%——超過 render parity 需求，renderer 不再是 binding constraint。Step 51 量到的 A blind 5% 因此過時 → Step 56 重測。

---

### Step 56 — 新 renderer live 重測（blind N=18，2026-06-11）

**結果**：candidate blind 勝率 design_layout 5%→**13.9%**、typography 2.5%→**19.4%**、graphics **27.8%**（candidate 領先軸）、overall 11.1%。

**誠實定調**：renderer 升級回收了一段 gap，但天花板 55% vs 實測 13.9% 之間仍有 **~41 pts 純 Generator 構圖差距**——Generator-bounded 第四次存活（前三次：Step 34/48/49）。

---

### Step 57 — coverage QC guardrails（GT 校準，2026-06-11）

**方法**：照 GT-first SOP 校準兩條退化防護——`COVERAGE_MIN=0.10`、`DEAD_BAND_MAX=0.60`（GT 20/20 全過、設計師極簡版面合法）。

**結果**：離線重放抓到 step56 候選 **24/70（34%）** 有退化幾何。live 驗證 → Step 58。

---

### Step 58 — coverage QC live：機制成功、效果 negative（2026-06-11）

**過程 bug**：第一輪發現 oracle gate 是白名單 filter、新 violation 被靜默丟掉（step58 實測等於「僅 prompt rule」）；修正後重跑 step58b。

**結果（三條件對照）**：in-loop 退件 6 次、retry **3/3 修好被點名問題但全改踩 safe-zone**（打地鼠）；acceptance **0/20**；judged rounds 28→18（QC retry 互相消耗預算）。step58b blind 下滑（2.6%）是 `pngs[-1]` 協定假象（13/19 最後一張是退件版面）、不可解讀為品質倒退。prompt rule 無預防力（違規率 34%→26%→35% 為 run 間雜訊）。

**深層發現（後續步驟的源頭）**：(1) 5f9917ea a2 與 GT 概念幾乎相同卻被 safe-zone gate 退件——Step 52 誤殺的具象案例；(2) **尺寸膽怯（timid sizing）**：GT 照片 ~75% 畫面、候選只敢 ~25%——比 coverage 更精確描述 ~41 pts 差距。Generator-bounded 第五次確認。

---

### Step 58c/58d — experiment.md 指標重算：幾何六指標 + COLE 四軸（2026-06-11）

**幾何六指標（live oracle run 最後 attempt 版面 vs 同子集 GT）**：AL 在 **5/6 軸達到或超過 GT**（幾何指標飽和、同 Step 29 結論）；**Rea（文字下梯度）是唯一明確落後軸（~2×**，step58b 0.0141 vs GT 0.0066）。

**COLE 四軸絕對分（gpt-4o，N=19）**：AL step58b Smean **6.78** vs Designer GT **7.53**（落後 ~1 分）vs 舊 cold-start 5.26 vs SEGA-13B 6.32（跨論文 informational）。**唯一反超軸 S_IO 6.16 vs GT 5.85**，與 blind innovation candidate 50% 互相印證。

**三套指標合讀**：幾何達標、COLE 差 1 分、blind design_layout 大輸——差距不在幾何合規性而在構圖層次；S_IO 是 AL 真實強項。引用幾何指標用本步、勿用 Step 29。

---

### Step 59 — TEXT_ON_BUSY_TEXTURE QC（GT 校準 T=0.065）+ live：feedback 路線封死（2026-06-11）

**校準發現**：8/20 GT 版面把每個文字元素用 underlay **完全遮蔽**——設計師的紋理防禦是「遮蔽」不是「閃避」。門檻 = GT max 0.0454 + 0.02 = **0.065**（GT 20/20 過、抓候選 74/590）。

**Live N=20**：規則**精準度完美**（開火 3/20 = 校準預測的同三樣本、梯度值一致到第 3-4 位、零誤殺）；但 **retry 零修復**——三個 spec 都有可用 underlay、detail 明確指示「加 underlay 遮蔽」，Generator 連移位都不做。Rea 0.0141→**0.0085**（同子集 GT 1.47×；方向與機制一致、n=15 不可宣稱因果）。acceptance 0/20（第六次確認）。

**誠實定調**：**Generator retry 對明確、可執行的結構化修復指令完全不回應**——「QC feedback 措辭改進」路線封死，殘餘選項收斂到直接改 Generator 輸入端。

---

### Step 60 — GT 校準照片面積 prior：尺寸第一次移動、判決不動（2026-06-11）

**校準（N=1,902 GT）**：photo GT p50 **0.213** / p75 **0.445** vs 候選**退化單點 0.1111**（1/3×1/3 習慣）——photo 是唯一尺寸膽怯 class，prior 只鎖 product_image。

**方法（雙槓桿）**：prompt 敘述 hint＋尾端 ATTENTION（含逐 canvas 像素數學）；`inject_photo_size_prior()` 程式化注入 `photo-prominent` hard constraint（QC bucket 0.20、剛好低於 GT p50 避免誤殺）。

**結果**：smoke 證實「敘述 hint 單獨=零移動、雙槓桿=第一次移動」（大量精確堆積在 0.200——Generator 真的照 ATTENTION 算數學）。Live N=20：尺寸持續移動（max **0.444**=GT p75、≥0.20 比例 ~50%），但 **acceptance 0/20、design_layout B=20 一票未動**。架構發現：oracle gate 白名單把 SIZE_PREFERENCE 算出後直接丟棄——實際生效的只有 prompt 槓桿，0.200 堆積是自願服從。

**誠實定調**：機制成功＋結果 negative 第三連發（58/59/60）；尺寸膽怯這個失效模式**已修復**，但單一可量化失效模式的修復不足以撼動構圖判決（Generator-bounded 第七次）。

---

### Step 61 — GT vs 候選粗構圖統計：草稿層級差距量化（2026-06-12）

**方法**：零 LLM——每張版面壓成 squint-test 簽名（photo 3×3 格位置＋尺寸桶、文字加權質心、photo-text relation），GT n=1,168 vs 五份 live run 候選 n=205。

**結果**：(1) **照片尺寸最大缺口**——GT large+bleed **45.1%** vs 候選 **0%**（small 92.7%）；(2) **設計師最常用的招式候選幾乎不用**——text-on-photo GT **43.3%** vs 候選 **3.9%**，GT top-3 全是「大照片置中＋文字疊上」；(3) 位置習慣相反——GT photo MC 50.7%，候選照片散落底帶、文字推到上下邊緣（迴避模式）；(4) QC 衝突——busy-texture＋safe-zone 規則正好禁止 GT 最大宗構圖。

**誠實定調**：草稿層級差距獲量化證實，「構圖師 Role」有明確數據支撐——本步 GT 構圖統計即其模板庫校準來源。

---

### Step 62 — AI 構圖師（Composition Director）：機制成功、假設未測到（2026-06-12）

**方法**：GT 校準模板庫（8 有照＋3 純文字模板、aspect-ratio 先驗）→ `ComposeSketch` Agent 2.5（art director persona、看背景圖選模板、輸出 directive）→ Generator prompt directive 區段＋尾端 ATTENTION → QC 構圖合約（cell/size/質心/relation 四項數值檢查）＋ text-on-photo 條件豁免。

**結果**：構圖師機制 **20/20 成功**（4 有照樣本全選 hero-center-overlay、零 fallback）；QC 合約有效（hero 照片 attempt 間 0.111→0.333 朝 large 移動）。但 acceptance 0/20、**judge 曝光崩潰 28→6**——4 個 text-on-photo 樣本**一個都沒進 judge**，核心假設（GT 式構圖能否贏 design_layout）實際上未被測到。根因＝**雙重束縛**：text-only 模板 directive（文字質心 MC）與 safe-zone 規則直接矛盾、Generator 乒乓至耗盡。

**拒答更正**：LLM 安全拒答非本步新回歸——step58=74、59=80、60d=59、62=118 行；Step 47「拒答消失」未持續成立，是 step58 起的背景噪音。

---

### Step 63 — directive 存在時 safe-zone 全面讓位（2026-06-12）

**方法**：構圖師選模板時已看過渲染後背景圖，directive 是 informed override——`spec.composition` 存在時 safe-zone 規則直接讓位（取代 step62 較窄豁免）；busy-texture 規則保留（要求的是 underlay 修正、不構成位置矛盾）。

**結果**：雙重束縛解除（safe-zone 違規 0、乒乓消失）；judge 曝光 **6→18**（9/20 樣本）；**acceptance 1/20——Step 48 以來第一個非零**（`589d7bd9` overall tie 過關：content/typography A 勝、design_layout/graphics B 勝、innovation tie）。但 **4 個 text-on-photo 樣本第二次 0 進 judge**——死因換位為 composition_mismatch / 拒答（拒答升至 140 行、直接殺死 hero 樣本 5bbcb749）。per-axis：design_layout A=0 B=18（樣本量足、仍全敗）。

**誠實定調**：機制目標達成、非零 acceptance 靠 content/typography 拉成 tie 而非 design_layout 贏；text-on-photo 假設連兩步未被測到，瓶頸移到 Generator 對 hero 合約的執行力＋拒答噪音。

---

### Step 64 — 三修聯動：拒答 fallback＋面積訊號統一＋underlay 合約（2026-06-12）

**方法（三修均在 `generate_layout.py`）**：64a 視覺拒答偵測→丟圖純文字重試＋一次性 budget+1；64b directive 有 photo_size 時面積 hint 讓位給 bucket（解 step63 hero 全停 0.333=prior 與 directive 的妥協點）＋自洽 WORKED EXAMPLE bbox；64c text-on-photo 規則點名 spec 的 decorative_image id＋80% 覆蓋門檻＋配方（bbox 外擴 10-20%、z 夾中）。

**結果（live N=20）**：拒答 **140→32 行（−77%）**、fallback 開火 16 次、GenerateLayout 零全滅；judge 曝光 **6→18→25 輪**（12/20 樣本）；**text-on-photo 首次進 judge**（5e7a3506 兩輪——step62/63 皆 0）、**兩輪皆輸 B**。判決全敗：design_layout A=0 B=25、typography A=0 B=25、graphics B=18 tie=7、innovation tie 24；acceptance **0/20**（step63 的 tie 未重現）。剩餘 hero 死因各異（composition_mismatch×3 / off-photo CTA 三攻不動 / mismatch＋no_underlay 混合）。

**誠實定調**：(1) QC 漏斗（曝光鏈）與判決端是**兩個獨立瓶頸**——三步工程把前者疏通、後者紋風不動；(2) 「GT 式構圖能否贏 design_layout」首個答案=否，但僅 2 輪（1 樣本）、下結論需更多 hero 進 judge；(3) **歸因警語：三修同輪，效果須用 per-sample 死因分解拆**、勿整體歸因單一修正；(4) Generator-bounded 首次在「假設被測到」的條件下成立。

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
| ~~underlay placement (AL 端 re-render)~~ | ✅ 已完成（Step 29） | Step 29 端到端跑 1,895 redesigned cold-start：AL Und_l 0.5518 > designer 0.3542（1.56×）、Und_s 0.4428 > 0.2674（1.66×），Ali/Ove 維持雙勝。4 幾何指標全勝、Read/Occ 略輸（over-containment trade-off）。 |
| Phase B（COLE 5-axis）對 underlay-enabled 配置重評 | 🟡 中 | Step 29 只重跑 Phase A 幾何指標；underlay-enabled 配置的視覺品質（COLE GPT-4V 5-axis）尚未重評（~$30），可補強「Und 高是否=視覺好」的證據。 |
| Refinement Loop 在 Crello 上不會 climb（Step 20b + Step 31 N=5 雙重證據） | 🔴 paper limitation | **核心 negative result**：(a) Step 20b A/B 受控實驗、SEGA 6 指標無 lift、completion 20/20→18/20；(b) Step 31 N=5 live（COLE 5 軸對齊 Judge + best-so-far guard）：0/5 accept、mean best-so-far 34.8 / threshold 35、唯一過 threshold 的 sample (5e72) 是 round 1 cold-start 拿到 38、後續 3 輪 refinement 沒辦法重現。<br/>四個 root cause：(1) **COLE rubric 飽和於 Crello-grade** — 5 sample CR=7 一致、IO=6 (4/5) 一致、reward gradient ≈ 0；(2) **Judge noise > signal gap** — threshold gap 1-3 點 / 平均每軸 0.2-0.6、GPT-4V 同圖 noise ~1-2 點；(3) **Suggestion→action 鬆耦** — Generator 動 1 個元素破壞別軸；(4) ~~Markov-chain 退步~~（Step 31 best-so-far guard 修了、mean +0.8）。**Honest framing**：best paper result 是 single-shot cold-start + Judge 從 K candidates 挑 best；refinement loop 寫成「探索性架構、發現受 Judge 解析度上界」。Future work：Judge multi-sample average（cost ×3）、強制 Generator 只 mutate suggested field、或 COLE rubric anchor 7→8 for Crello-grade（治標、plateau 結構不變）。 |
| Phase B head-to-head：loop 真實**落後** cold-start（Step 32 N=5） | 🔴 paper evidence | 同 5 個 sample 同一輪 COLE GPT-4V 評分：cold-start (bypass Judge) Smean=**6.10**、live-loop (Step 31 COLE 5 軸 Judge + best-so-far) Smean=**5.75**、Δ=**−0.35**。3/5 退步、1/5 改善、1/5 持平。最大退軸 CR −0.80（吸收 requirement_alignment 那軸）。**證明對齊 Judge 不只沒幫忙、還略傷**。Source: `step32_phaseb_compare.json`。 |
| Rubric in Generator prompt：+0.05（噪音內）（Step 33 N=5） | 🟡 ablation | 把 COLE 4 軸 rubric 寫進 GenerateLayout PROMPT_TEMPLATE 當生成 prior（不是 Judge filter）：PRE33 vs POST33 cold-start head-to-head Smean=6.15 vs 6.20、Δ=**+0.05**。方向比 Step 32 (loop) 對、但 effect size 在 N=5 雜訊內、IO 軸完全沒動。「rubric 位置不是 bottleneck」的初步證據。Source: `step33_phaseb_compare.json`。 |
| **Step 37/38/39/40 八實驗收斂結論：Generator-bounded 在 Crello commercial design 是 binding constraint** | 🔴 **paper main finding** | **八個實驗的因果鏈**（Step 20b → 30 → 31 → 32 → 33 → 34 → 37 → 40）逐一消除 alternative explanation：judge axis alignment 沒影響、Markov regression 修了沒幫忙、rubric 位置沒差、tie-break bias 砍光後 success rate 從 14% → 2%、最後 Step 40 用「closed-vocab failure flag + 對應 concrete action」最具體的 reject feedback 還是 0/20 → 證明 feedback channel specificity 不是 bottleneck。Trace 看到 whack-a-mole 模式：Generator 收到「fix composition_unbalanced」具體指令、改了但舊 flag 沒消、還冒新 flag。<br/>**Paper main number**：Strict pairwise N=100 = **2%**（Step 37）；calibrated absolute Smean = **3.73 AL vs 4.75 GT，Δ=−1.00**（Step 39，N=20 cold-start）—— 比 Phase B 原 6.10 vs 6.6 (Δ=−0.5) 揭穿 mean-regression 偽影。<br/>**Code**：production `quality_checker.py` Step 37 P3 IoU/same-z；`generate_layout.py` Step 37 P1 + P4 layout constraints；gitignored scripts `step37_oracle_*`, `step38_failure_checklist_eval.py`, `step39_calibrated_eval.py`, `step40_flag_aware_oracle.py`。 |
| **Oracle GT-guided pairwise refinement：5/5 全敗 GT（決定性 Generator-bounded 證據、Step 34 N=5）** | 🔴 **核心 paper finding** | K=1 + pairwise judge vs Crello GT + 3 retry：**5/5 sample 全在 Round 1 用盡 3 次重試、0 commit、15/15 verdict 都判 B (GT) 勝**。每次 summary 一致：「Image B excels in layout, content relevance, typography, AND originality」。給 Generator **最強的 reward signal**（pairwise vs 真 GT、含 axis-level reason feedback）都救不了。**Bottleneck 確認在 Generator（gpt-4o zero-shot）**、不在 Judge 設計。<br/>**Paper framing**：「iterative refinement architecture explored, found Generator-bounded; six-experiment causal chain (Step 20b→30→31→32→33→34) converges on single conclusion: LLM zero-shot 在 Crello commercial design 無法達到 designer-grade quality」。**Caveat**：Oracle 在 inference time 用了 GT、是 upper-bound ablation、不能跟 SEGA/PosterO 數字並列；放在 limitation/ablation section。<br/>**N=20 robust validation 結果（2026-06-09）**：2/20 (10%) ok、18/20 (90%) round1_exhausted；總 60 個 pairwise verdicts，55 (91.7%) judged GT 勝、4 (6.7%) judged AL 勝、1 (1.7%) tie。真正贏 GT 的 verdict 只 2/60 (3.3%)：sample `592c2135` round 1 attempt 2、sample `589d7bd9` round 1 attempt 1。剩 2 個 A-wins verdicts 來自 `589d7bd9` round 2/3 tie-break by prompt rule，不算實質改善。**量化結論**：LLM zero-shot Generator 在 Crello commercial design 約 **10% 案例**能達到/接近 designer 水準、**90% 案例**完全達不到。Source: `step34_oracle_results.json`、`layout_agent/output/step34_oracle_N20.log`、`step34_oracle_crello_*.png`。 |
| **Step 35/36/36b 視覺工程：QC 5 條新規則 + metadata-leak fix，成功率 10%→20% (N=20)** | 🟢 **paper improvement** | Step 34 N=20「Generator-bounded」結論**部分撤回**。逐張看完 17 個失敗 PNG 發現都有具體 fixable bug，非 LLM 本質限制：41% underlay 過大、18% Analyst 用 Crello 目錄描述當 title、18% asset 利用不當、12% 配色錯。針對前兩類做 Step 35/36：<br/>**5 條新 QC 規則**（`quality_checker.py`）：`TEXT_OBSCURED_BY_OVERLAY` (IoU>0.3, z 高於 text)、`LOW_TEXT_CONTRAST` (WCAG AA <4.5 vs canvas bg)、`DECORATIVE_IMAGE_OVERSIZED` (area>40% canvas)、`TITLE_UNDERSIZED` (area<2.5% canvas)、`TITLE_PERIPHERAL` (center_x∉[0.10,0.90] OR center_y>0.85)<br/>**1 個 prompt fix**（`build_pipeline_inputs` in `run_role_team_live_crello.py:91-100`）：「titled '{title}'」→「for the theme '{title}' ... visible heading MUST come from text snippets」、解決 Analyst 把 Crello 目錄描述 (e.g. "Quarantine concept with Man by open Window") 當 title 的 leak。<br/>**N=20 累積對照**：pre-Step35 2/20 (10%) → post-Step35 3/20 (15%) → post-Step36 4/20 (20%)，**doubled**。GT 勝率 91.7%→89.7%→86.2%。<br/>**清楚 win**：sample 5e72 (Quarantine) [B,B,B] → [A,A,A] 全 3 輪贏 GT；視覺確認標題正確用「Don't be an airhead」(text snippet) 而非「Quarantine concept...」(metadata)、跟 GT 構圖幾乎一樣。<br/>**Source**：`quality_checker.py` Step 35/36 region、`test_quality_checker_position_hints.py` 10 個新 case (164 passed)、`step34_oracle_N20_postStep36.log`、`step34_oracle_results.json`。**4 個 success cases (5e8d/592c/589d/5e72)** 可當 paper showcase figure。 |
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
| Step 30–64 oracle / blind / QC / 構圖師實驗鏈原始數據 | `layout_agent/output/step3x..step64` 各 `*.log` / `*_results.json` / renders（gitignored）；逐步技術細節 `layout_agent/IMPLEMENTATION_LOG.md` 對應條目；showcase 圖 `layout_agent/good_result/`（7 組 GT+AL 對） |
| commit 紀錄 | `git log --oneline -- metagpt/ext/agentlayout/`（step 12b = `a87b5034`、step 13 doc = `a2f85a58`） |
