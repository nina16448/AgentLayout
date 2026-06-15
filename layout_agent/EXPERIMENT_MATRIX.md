# AgentLayout — 論文實驗矩陣（架構確認用 main + ablation）

**用途**：把 `result.md` 的 Step 6~66 散落數據,重組成論文需要的兩張表——**main experiments（整體成績）**與 **ablation studies（逐元件拔掉看貢獻）**——並標清每格的變因（N / judge / renderer 版本）與**衡量軸**。零成本整理,不含新實驗。

**權威來源**：所有數字回溯 `result.md` 對應 Step 段落與 `IMPLEMENTATION_LOG.md`;本檔僅做映射,不產生新數字。

---

## 核心框架：multi-axis,不要全綁 aesthetic win rate

這條探索線最後收斂為 **Generator-bounded**（見 memory `project_generator_bounded_line_closed`）：在 **aesthetic pairwise win rate** 這根軸上幾乎每個元件 ablation 都是 negative。**若整張 ablation 表只用這根軸,會誤讀成「每個元件都沒用」。** 正確做法是讓每個元件在**它該負責的軸**上展現貢獻：

| 衡量軸 | 量什麼 | 哪些元件在這軸有正貢獻 |
|---|---|---|
| **A. 幾何指標**（Ali/Ove/Und/Read/Occ） | 版面排列的數學品質 | content-aware、underlay placement |
| **B. Aesthetic 絕對分**（COLE Smean 1-10） | MLLM 對單張的美學打分 | （全元件持平/落後 designer ~1 分,Generator-bounded） |
| **C. Aesthetic pairwise**（blind win rate） | 盲判 vs designer GT | renderer（回收一段）；其餘 negative |
| **D. Completion / robustness** | 跑完不 crash 的比例 | QC tolerance、crash fix、graceful degradation |
| **E. Render parity 天花板** | GT layout 過我方 renderer 的盲判勝率 | renderer 升級 |
| **F. Layout-IoU** | 與 GT 位置的重疊 | 整體 pipeline（勝 random、平 centered） |

---

## 表一 — Main experiments（整體成績 vs Designer GT）

| # | 實驗 | 軸 | N | judge / 協定 | AgentLayout | Designer GT | 證據 |
|---|---|---|---|---|---|---|---|
| M1 | 幾何六指標（underlay-enabled） | A | 1,895 | zero-LLM rule-based | Ali **1.26e-05** / Ove 0.0035 / Und_l **0.5518** / Und_s **0.5285** / Read 0.0311 / Occ 0.1620 | 0.0010 / 0.0449 / 0.3542 / 0.2674 / 0.0235 / 0.1371 | Step 29（Ali/Und_s 為 2026-06-15 SEGA Audit A1/A4 修正後重算值） |
| M1' | 幾何六指標（N=100 fresh，bug-fixed pipeline） | A | 100 | zero-LLM rule-based | Ali 6.85e-03 (1.22×) / Ove **5.66e-04** (0.02×) / Und_l **0.532** (1.18×↑) / Und_s **0.523** (1.19×↑) / Read 0.0270 (**1.93×**) / Occ 0.172 (1.17×) → **3/6 勝、1/6 真實落後 Read、2/6 略輸 Ali/Occ** | 5.62e-03 / 2.34e-02 / 0.452 / 0.440 / 0.0140 / 0.147 | Step 68 (2026-06-15) |
| M3' | COLE aesthetic 絕對分（N=100 fresh，**單邊基準**） | B | 100 | gpt-4o **JudgeAesthetic** multi-cand prompt 5-axis 1-10 | **Smean 6.32** (DL 6.28 / CR 7.00 / TC 6.00 / GI 6.16 / IO 6.17) | — *（無 matched designer GT、見 M3''）* | Step 68 |
| **M3''** | **COLE aesthetic matched H2H**（N=100 fresh，**論文 B 軸 headline**） | B | 100 | gpt-4o **COLE single-call** 5-axis 1-10（SEGA/COLE literature 標準 prompt） | **Smean4 6.598 / Smean5 6.740**（SDL 6.81 / SQL 7.45 / STV 6.09 / SGI 7.31 / SIO 6.04）→ **86.6% / 87.5% of designer** | **Smean4 7.617 / Smean5 7.700**（SDL 7.84 / SQL 8.33 / STV 7.53 / SGI 8.03 / SIO 6.77）→ **5 軸全勝、Δ −0.7 ~ −1.4** | Step 70 (2026-06-15) |
| M8 | PKU 997 跨資料集 indicative | A | 997 | zero-LLM rule-based (Path A) | Ali **1.42e-03** (0.62×) / Ove **4.79e-04** (0.25×) / Und=0 (scope forfeit) / Read 0.0234 (**1.80×**) / Occ 0.101 (**1.42×**) → **2/6 勝、2/6 forfeit、2/6 真實落後**（詳 result.md §68.3b） | 2.27e-03 / 1.94e-03 / 0.784 / 0.781 / 0.0130 / 0.0710 | Step 68 (2026-06-15) |
| M9 | **high-score subset A 軸**（best-case showcase，幾何） | A | 28 | zero-LLM rule-based | Ali **2.88e-03** / Ove **2.93e-03** / Und_l **0.394** / Und_s **0.271** / Read **0.012** / Occ 0.097 → **5/6 軸勝 designer**（subset 把 Read 1.93× 落後翻成勝場） | 3.17e-03 / 9.47e-03 / 0.264 / 0.208 / 0.018 / 0.085 | Step 69 (2026-06-15) |
| **M9'** | **high-score subset B 軸 matched H2H** | B | 28 | gpt-4o COLE single-call 5-axis 1-10 | **Smean4 6.705 / Smean5 6.814**（SDL 7.00 / SQL 7.21 / STV 6.54 / SGI 7.25 / SIO 6.07）→ **86.4% / 86.9% of designer**（**subset 不縮 B 軸 gap**） | **Smean4 7.759 / Smean5 7.843**（SDL 8.00 / SQL 8.29 / STV 7.71 / SGI 8.18 / SIO 7.04） | Step 70 (2026-06-15) |
| M2 | 幾何六指標（live, post-composition 子集） | A | ~19 | zero-LLM | 5/6 軸達標;**Rea 唯一落後 ~2×** | — | Step 58c/58d |
| M3 | COLE aesthetic 絕對分 | B | 19 | gpt-4o 四軸 | Smean **6.78**（S_IO 6.16 反超） | **7.53**（S_IO 5.85） | Step 58d |
| M4 | COLE 校準絕對分 | B | 20 | gpt-4o J5/J6/J7 校準 | **3.73** | **4.75**（Δ=−1.0） | Step 39 |
| M5 | Pairwise win rate（**blind**, 新 renderer） | C | 18 | blind pairwise | design_layout **13.9%** / typography 19.4% / graphics 27.8% / overall 11.1% | 其餘 = GT | Step 51 + 56 |
| M6 | Layout-IoU + baseline | F | 20 | BypassJudge | **0.0994** > random 0.0567,≈ centered 0.0931 | — | Step 15 |
| M7 | SOTA-context 對照（**非 head-to-head**） | — | — | published | IoU ~9.94%（最弱段量級） | AesthetiQ-8B 17.19 / LayoutNUWA 5.58 | Step 16 |

> **誠實定調（Step 70 後更新）**：
> - **A 軸（幾何）**：AgentLayout 跨 N=20/100/1,897 三 scale 勝/平 designer robust（論文最強 claim）；high-score subset 5/6 勝（M9）。**Read 落後是跨 dataset 一致現象**（Crello 1.93× ≈ PKU 1.80×、saliency-aware 視覺處理缺口，非 placement engine 問題）。
> - **B 軸（aesthetic matched H2H，Step 70）**：**reaches 86.6% of designer Smean ceiling**（N=100 matched COLE single-call）；5 軸全輸 Δ −0.7~−1.4，STV (typography) 最大 gap −1.44。**N=28 subset 不縮 B 軸 gap**（86.4% ≈ 86.6%）→ **gap is render-channel-bound, not placement-bound**（M9 vs M9' + Step 54/55/56 render parity 一致）。
> - **C 軸（pairwise blind）**：主軸大輸 = Generator-bounded。
> - **不可宣稱勝設計師 aesthetic、不可宣稱勝 SOTA、不可宣稱「prompt-only 系統匹敵 designer aesthetics」**（matched H2H 直接打臉）。

---

## 表二 — Ablation studies（拔掉/換掉元件看影響）

| # | 架構元件 | 對照（off → on / 換版本） | 主軸 | 結果 | 證據 |
|---|---|---|---|---|---|
| A1 | **BackgroundAnalyzer**（content-aware） | white-stub → 真 saliency | A/任務對齊 | ✅ 先前 live 其實非 content-aware;補上後任務才對齊 SOTA scope | Step 12 |
| A2 | **Underlay placement** | Und=0（Step 23 baseline）→ Und=0.55 | A | ✅ Und_l 0→0.55、Und_s 0→0.44,超 designer;但 Read/Occ 略退（over-containment） | Step 23 vs 29 |
| A3 | **Renderer** | 舊 → 升級（font/wrap/fit/rotation） | E | ✅ GT-through-renderer 盲判天花板 design_layout **22.5%→55%**;live 候選 5%→13.9% | Step 54/55/56 |
| A4 | **QC safe-zone gate** | gate-on → gate-off | C | ⚠️ blind 判決**一票未變**（2/32/0）→ gate 不是 gap 成因;貢獻在 D 軸非 C 軸 | Step 52/53 |
| A5 | **Refinement loop** | cold-start → loop | B | ⚠️ loop **−0.35** vs cold-start;loop 不會 climb（rubric 飽和 + judge noise > signal） | Step 31/32/34 |
| A6 | **QC tolerance / crash fix** | strict → tolerance + degradation | D | ✅ completion 0/15 crash → **100% N=20**、0 degradation | Step 10/10c/17 |
| A7 | **Prompt 指引上限** | 無 → typography/balance/rubric 進 prompt | B/C | ⚠️ graphics tie 16%→41% 為**唯一移動軸**;主軸不動 | Step 49 |
| A8 | **Composition Director** | 無 → AI 構圖師（GT 模板庫） | C | ⚠️ 機制 20/20 成功但假設未測到（judge 曝光崩 28→6,雙重束縛） | Step 62/63 |
| A9 | **Feedback 模態** | 文字 QC → 視覺自我修正 | C | ⚠️ 兩者皆 negative;self-render 還使拒答率翻倍 | Step 59/65 |
| A10 | **Placement 由誰算** | LLM 生座標 → constraint-solver（幾何去 LLM） | C | ✅ **最強反證**:幾何全移出 LLM、數學構造最優,judge 仍 **55/55 全敗** → 瓶頸不在幾何合規 | Step 66 |
| A11 | **Generator 模型** | gpt-4o → gpt-5.2 | C | ⚠️ 同型失敗 → 改名「LLM-coordinate-generation-bounded」,非單一模型 | Step 50 |
| A12 | **Judge self-preference** | gpt-4o judge → Claude judge | 方法學 | ✅ 80%↔80% 完全複製 → self-preference confound 排除 | Step 14 |
| A13 | **Judge label bias** | label-aware → blind | 方法學 | ✅ innovation 有 label bias（60%→真值）;design_layout/typography 輸是真的 | Step 51 |

---

## 表三 — 變因一致性風險（搬數字進論文前必看）

這些 Step 散在不同條件,**直接並排會被審稿人打**。同一張表內必須同條件：

| 變因 | 出現過的值 | 影響 |
|---|---|---|
| **N** | 5 / 18 / 19 / 20 / 100 / 1,895 | per-axis ranking 在 N<1,000 會 selection-bias flip（Step 22/23b 實證） |
| **Judge** | gpt-4o / gpt-5.2 / Claude sonnet / VILA-7B（未跑） | 跨 judge 校準漂移（Step 21b: designer 同 prompt 7.525 vs SEGA 自報 6.32） |
| **Judge 協定** | label-aware / blind | headline 一律須 blind（Step 51） |
| **Renderer 版本** | 舊（≤Step 54）/ 新（Step 55+） | 跨版本 live 數字不可比;Step 51 的 5% 已被 Step 56 13.9% 取代 |
| **Judge rubric** | 自製 4軸0-25 / COLE 5軸1-10 / 校準 J5-J7 | Step 30 遷移後與前半不可直比;絕對分 6.10 vs 3.73 是校準差 |
| **COLE prompt 變體** | JudgeAesthetic multi-cand prompt（M3'，Smean 6.32）/ COLE single-call literature 標準 prompt（M3''/M9'，Smean 6.598/6.705） | Step 68 vs Step 70 數字不可直比；論文主表用 **M3'' / M9'**（matched H2H、literature-aligned） |

---

## 表四 — 覆蓋度與缺口（要不要補實驗的決策點）

| 元件 / claim | 現有覆蓋 | 缺口 | 補的成本 |
|---|---|---|---|
| 幾何 head-to-head | ✅ N=1,895 robust | 無 | $0 |
| **B 軸 matched H2H** | ✅ **Step 70 N=100 + N=28（M3''/M9'）** | 無（matched COLE single-call 已補齊） | $0 |
| **N=28 selector defensibility** | ✅ result.md §7.5b + selector docstring DEFENSIBILITY block | 無 | $0 |
| **PKU 任務範疇誠實段** | ✅ result.md §68.3b | 無 | $0 |
| underlay ablation | ✅ Step 23 vs 29 | Phase B（COLE）對 underlay-enabled 未重評 | ~$30 |
| renderer ablation | ✅ Step 54/55/56 | 無 | $0 |
| gate ablation | ✅ Step 53 blind | 無 | $0 |
| loop ablation | ✅ Step 32 N=5 | N 偏小（5） | ~$10 擴 N |
| **Composition Director** | ⚠️ 假設未測到 | hero 樣本進 judge 太少（2 輪） | 需重跑、見探索線結案結論已封 |
| **統一條件主表** | 🟡 部分（B 軸 matched 已 Step 70 補齊；A 軸 N=100 fresh 已 Step 68 補齊） | A+B 軸尚無「同 N=1,897 大樣本 matched」表 | ~$80（B 軸 N=1,897 × 2 source） |
| VILA-7B head-to-head | ❌ 未跑 | 消 judge≠VILA caveat | 重（裝環境/checkpoint） |

---

## 給論文的建議寫法

1. **Main results 用 A 軸（幾何）當正面 claim** — 跨三 scale robust、judge-drift-free,是最硬的貢獻。
2. **B 軸用 Step 70 matched H2H 為 headline**（M3''/M9'）— "AgentLayout reaches 86.6% of designer Smean ceiling under matched COLE single-call eval (N=100)"、**勿用 M3' 6.32 當 B 軸 headline**（單邊基準、prompt 不同）。
3. **Best-case showcase 章節用 M9+M9'**：A 軸 5/6 勝 designer、B 軸 subset 不縮 gap（**這是更強的科學發現**：殘餘 gap = render-channel-bound、非 placement-bound）；引 result.md §7.5b 的 a priori 防禦段對抗 cherry-picking 質疑。
4. **PKU 段用 result.md §68.3b** — 「不同類問題、Path A indicative」、Rea/Occ 落後 ~1.4–1.8× 寫明 root-cause；勿與 PKU SOTA 同表對標。
5. **Ablation 表用 multi-axis** — 每個元件標它贏的那根軸（A6 robustness、A3 renderer parity、A2 underlay 幾何）,避免全綁 C 軸顯得元件無用。
6. **Generator-bounded 當 main finding 而非失敗** — A5/A7/A8/A9/A10/A11 是一條因果鏈,逐一排除 alternative explanation（judge alignment / rubric 位置 / feedback specificity / 模型 / 幾何介面）,收斂於「LLM 以座標文字生成版面」這個介面的能力上界。A10（constraint-solver 55/55）是最乾淨的決定性證據。
7. **誠實章節**（`result.md` §3 + §68.3b + §7.5b + §8）照搬 — 不勝設計師、不勝 SOTA、plateau 結構性、B 軸 matched H2H 86.6%。

---

**最後更新**：2026-06-15。對應 `result.md`（Step 6~70，Step 68 X plan / Step 69 high-score subset / Step 70 B 軸 matched H2H + selector a priori defense + PKU 任務範疇誠實段）+ `IMPLEMENTATION_LOG.md`；三份文件已對齊。

---

## 附錄 A：2026-06-15 X plan + Step 69 + Step 70 新增證據檔

| 檔案 | 內容 |
|---|---|
| `layout_agent/output/validate_geometric_metrics_results.json` | A 軸 4 deterministic 指標 vs 6 個歷史 source 重算對照、Ali/Und_s 漂移證據 |
| `layout_agent/output/validate_metrics_report.md` | A 軸驗證報告（人類可讀） |
| `layout_agent/output/validate_metrics_inventory.md` | A+B 軸源檔盤點 |
| `layout_agent/output/step22_sega_n100_fresh.json` | Crello N=100 fresh 6 軸 aggregate（M1'） |
| `layout_agent/output/b_axis_n100_fresh_results.json` | Crello N=100 fresh JudgeAesthetic 5-axis Smean（M3'，單邊基準） |
| `layout_agent/output/pku_run/run_pku_final_n997.json` | PKU 997 indicative（M8） |
| `layout_agent/output/select_high_score_subset.py` | Step 69 selector（zero-LLM、特質濾、含 DEFENSIBILITY docstring） |
| `layout_agent/output/high_score_subset_ids.json` | 28 個通過的 sample IDs + filter 統計 |
| `layout_agent/output/high_score_sega_n28.json` | M9 high-score A 軸 aggregate |
| `layout_agent/output/high_score_n28_results.json` | high-score B 軸 JudgeAesthetic 結果（單邊參考） |
| **`layout_agent/output/step70_n100_agent_5axis.json`** | **M3'' Agent N=100 matched COLE single-call** |
| **`layout_agent/output/step70_n100_designer_gt_5axis.json`** | **M3'' Designer GT N=100 matched COLE single-call（論文 B 軸 baseline）** |
| **`layout_agent/output/step70_n28_agent_5axis.json`** | **M9' Agent N=28 high-score matched COLE single-call** |
| **`layout_agent/output/step70_n28_designer_gt_5axis.json`** | **M9' Designer GT N=28 matched COLE single-call** |
| **`layout_agent/good_result/n28_high_score/`** | **N=28 best-case AL+GT 配對圖（28 對 = 56 張 + README.md 含 per-sample Smean 排序）** |
| **`layout_agent/output/step21_phaseb_eval.py`** | **Step 70 harness（已加 5-axis `Smean5` aggregation，additive）** |
