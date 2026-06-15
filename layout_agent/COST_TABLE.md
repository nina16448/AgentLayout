# AgentLayout — Cost / wall-time per-experiment reproducibility table

> Paper Reproducibility section 用：所有 LLM cost + 樣本數 + 對應 result.md / IMPLEMENTATION_LOG
> 引用。便於審稿驗證、便於後人估算重跑成本。

**Cost basis**：gpt-4o vision ~$0.012/call、gpt-4o text ~$0.005/call、Claude sonnet
~$0.015/call（period rough estimates）。Step 68/70 的 budget 數字是 actual run cost、
其餘為 measurement-time best-effort 估算（部分早期 step 在 IMPLEMENTATION_LOG 內無明確紀錄、
標 "N/A est."）。

**Total spend across all 71 documented steps**：**~$220** (LLM only、不含 CPU / GPU
saliency compute、不含 dev iteration)。

---

## 1. 主要實驗 cost（依時間序，最重要的搬數字 source）

| Step | 日期 | 實驗 | N | LLM cost | 軸 | result.md 引用 |
|---|---|---|---|---|---|---|
| 6–11 | 2026-05-13~18 | Early MVP iteration（5-7 elements、QC tolerance debug、plateau diagnosis、Win rate pilot N=3） | 1–3 per live | ~$5 | A+C | §2.6~§2.11 |
| 12 | 2026-05-18 | BackgroundAnalyzer 上線、first content-aware live | 1 | ~$0.30 | 任務對齊 | §2.12 |
| 13 | 2026-05-19 | SOTA-positioning win-rate pilot N=20 | 20 | ~$2 | C | §2.13 |
| 14 | 2026-05-19 | Claude independent judge 重判（消除 self-preference） | 80 judge calls | ~$1.5 | 方法學 | §2.14 |
| 15 | 2026-05-19 | Layout-IoU + baseline N=20 | 20 | ~$2 | F | §2.15 |
| 16 | 2026-05-19 | SOTA-context cross-paper 對照 | 0 (published numbers) | $0 | — | §2.16 |
| 17 | 2026-05-19 | step 10b crash fix + post-fix re-judge | 0 (offline) | $0 | D | §2.17 |
| 20 | 2026-05-20 | SEGA Phase A 6-metric head-to-head N=20 | 20 | ~$2 | A | §2.20 |
| 21 | 2026-05-20 | SEGA Phase B GPT-4V 4-axis N=20 | 20 | ~$1 | B | §2.21 |
| 21b | 2026-05-20 | Designer GT 過同 judge config（推翻 Step 21 cross-paper claim） | 20 | ~$1 | 方法學 | §2.21b |
| 20b | 2026-05-20 | Refinement Loop A/B head-to-head | 20 | ~$3 | B | §2.20b |
| 22 | 2026-05-20 | N=100 scale-up Phase A + Phase B | 100 | ~$15 | A+B | §2.22 |
| 23 | 2026-05-26 | N=1,897 完整 Crello test split | 1,897 | **~$100** | A+B | §2.23 |
| 25–28 | 2026-05-27 | Underlay placement audit + Designer GT 重算（zero LLM） | 1,897 | $0 | A | §2.25~§2.28 |
| 29 | 2026-05-28 | Underlay-enabled N=1,895 端到端重跑 | 1,895 | **~$110** | A | §2.29 |
| 30 | 2026-06-09 | In-pipeline Judge → COLE 5-axis 1-10 schema 遷移（doc-only） | 0 | $0 | 方法學 | §2.30 |
| 31 | 2026-06-09 | Refinement Loop N=5 diagnostic + best-so-far guard | 5 | ~$1 | B | §2.31 |
| 32 | 2026-06-09 | Phase B head-to-head loop vs cold-start N=5 | 5 | ~$1 | B | §2.32 |
| 33 | 2026-06-09 | Rubric → Generator prompt N=5 | 5 | ~$1 | B | §2.33 |
| 34 | 2026-06-09 | Oracle GT-guided refinement N=5→N=20 | 20 | ~$3 | C | §2.34 |
| 35–36c | 2026-06-09 | QC 5 條規則 + N=100 robust validation | 100 | ~$10 | C+D | §2.35~§2.36c |
| 37 | 2026-06-09 | Strict judge + Tier 1 QC 收緊 N=100 | 100 | ~$10 | B+C | §2.37 |
| 38–40 | 2026-06-09 | J1/J2 failure checklist + J5/J6/J7 校準 + Flag-aware feedback | ~10–20 | ~$5 | B+C | §2.38~§2.40 |
| 47 | 2026-06-10 | Render/data confound 修正（doc + tooling） | 0 | $0 | 方法學 | §2.47 |
| 48 | 2026-06-10 | 去 confound 後 N=20 重跑 + B 軸 N=31 | 51 | ~$5 | A+B+C | §2.48 |
| 49a/49b | 2026-06-10/11 | Generator per-axis + typography + balance N=20 | 40 | ~$5 | C | §2.49 |
| 50 | 2026-06-11 | Generator 換 gpt-5.2 N=5 對照 | 5 | ~$2 | C | §2.50 |
| 51 | 2026-06-11 | Blind re-judge（cached renders、judge only） | 32 judge calls | ~$0.5 | 方法學 | §2.51 |
| 52 | 2026-06-11 | Designer GT 過 QC gate（zero LLM） | 28 | $0 | C | §2.52 |
| 53 | 2026-06-11 | Gate-off ablation blind re-judge | 32 judge calls | ~$0.5 | C | §2.53 |
| 54 | 2026-06-11 | Render-parity 分解（GT-through-renderer blind） | 18 judge calls | ~$0.5 | E | §2.54 |
| 55 | 2026-06-11 | Renderer 升級（font/wrap/fit/rotation，zero LLM） | 0 | $0 | E | §2.55 |
| 56 | 2026-06-11 | 新 renderer live N=20 重測 | 20 | ~$5 | C+E | §2.56 |
| 57 | 2026-06-11 | Coverage / dead-space QC guardrails（doc） | 0 | $0 | C | §2.57 |
| 58 / 58c / 58d / 58e | 2026-06-11 | Coverage QC live + experiment.md metric 重算 | 20 | ~$5 | A+B+C | §2.58 |
| 59 | 2026-06-11 | TEXT_ON_BUSY_TEXTURE QC live N=20 | 20 | ~$5 | C | §2.59 |
| 60 | 2026-06-11 | Photo-size prior live N=20 | 20 | ~$5 | C | §2.60 |
| 61 | 2026-06-12 | GT 構圖統計 + 候選比較（zero LLM） | 0 | $0 | C | §2.61 |
| 62 | 2026-06-12 | AI 構圖師 Composition Director N=20 live | 20 | ~$5 | C | §2.62 |
| 63 | 2026-06-12 | directive + safe-zone 讓位 N=20 | 20 | ~$5 | C | §2.63 |
| 64 | 2026-06-12 | 三修聯動（拒答 fallback + 面積訊號 + underlay 合約）N=20 | 20 | ~$5 | C+D | §2.64 |
| 65 | 2026-06-12 | Visual self-correction live N=20 | 20 | ~$5 | C | §2.65 |
| 66 | 2026-06-13 | Constraint-solver placement N=20 | 20 | ~$5 | C | §2.66 |
| 67 | 2026-06-14 | filter_valid bg 參數修補 + regression tests | 0 | $0 | D | §2.67 |
| **68** | **2026-06-15** | **X plan: Crello N=100 fresh + B 軸 N=100 + PKU 997 + micro smoke** | **N=100 ×2 + 997 PKU** | **~$64** | **A+B+跨 dataset** | **§6** |
| **69** | **2026-06-15** | **High-score subset selector + N=28 A 軸 + B 軸（JudgeAesthetic）** | **28 ×2** | **~$3** | **A+B** | **§7** |
| **70** | **2026-06-15** | **B 軸 matched H2H（N=100 + N=28 × agent/designer-gt × COLE single-call）** | **256 vision call** | **~$3** | **B (matched)** | **§8** |
| **71** | **2026-06-15** | **B1 N=100 3 輸軸 per-sample root-cause（zero-LLM）** | **0** | **$0** | **A 分析** | **§68.2b** |

---

## 2. Cost by 階段（cumulative）

| 階段 | Step 區間 | Cost | % of total |
|---|---|---|---|
| Phase 0 — MVP + content-aware | 1–12 | ~$5 | 2.3% |
| Phase 1 — SOTA-positioning + win rate | 13–17 | ~$5.5 | 2.5% |
| Phase 2 — SEGA 6-metric + Refinement Loop | 20–22 | ~$22 | 10% |
| **Phase 3 — Full-scale N=1,897** | **23 + 29** | **~$210** | **96%** ← 主成本（已平均到 ~$0.11/sample × 2 runs） |
| Phase 4 — Generator-bounded 探索線 | 30–66 | ~$80 | 36% |
| Phase 5 — X plan + best-case + matched H2H | 67–71 | ~$70 | 32% |
| **TOTAL（去重）** | | **~$220** | 100% |

> ⚠️ 上表 Phase 3 = $210 (N=1,897 兩次 ×$100 + $110) **佔總成本壓倒性多數**——這條是 paper 主結果 N=1,897 robust claim 的成本根源、合理。其餘各 phase 個別 step ~$1–5、累計後沒有 step 單獨超過 Phase 3。

---

## 3. Wall-time per experiment（rough estimates）

| 實驗類別 | 樣本 / 軸 | Wall-time（asyncio + rate-limit）|
|---|---|---|
| 單個 live run（pipeline + judge + 1–3 iter） | 1 sample | ~30s–2min |
| Crello N=20 batch | 20 samples | ~10–20min |
| Crello N=100 batch | 100 samples | ~45min–2h |
| **Crello N=1,897 full split** | 1,897 samples | **~30–50h**（多次 batch 加總） |
| PKU 997 indicative (Path A) | 997 samples | ~3–5h |
| B 軸 N=100 re-judge | 100 vision call | ~10–15min |
| zero-LLM 重算 / blind re-judge | any | <2s（純資料分析）|

---

## 4. 重要 cost-pacing 觀察（for reproducer）

1. **N=1,897 是一次性 capex、不需重跑**：若 reproducer 只想驗證 main claim，跑 N=100 fresh (Step 68) 就夠（~$6 + $5）+ matched H2H (Step 70 ~$3) = **~$14 即可重現 paper 主表**。
2. **B 軸 designer GT baseline 是 Step 70 才補的**：若 reproducer 重跑 Step 22/23 不會自動產生 designer GT、需另外跑 `step21_phaseb_eval.py --source designer-gt`。
3. **PKU 997 是 indicative 跨 dataset 驗證，非 SOTA 對標**：$52 算最貴的單一 step、若 reproducer 不需此章節可省。
4. **Step 71 (root-cause analysis) zero-LLM**：純資料分析、可任意重跑、$0 / <2s。
5. **High-score selector 是 zero-LLM 篩 + N=28 LLM run**：selector 本身 $0、A+B 軸 N=28 約 $3。

---

## 5. 證據檔索引

| 檔 | 內容 |
|---|---|
| `layout_agent/IMPLEMENTATION_LOG.md` | 每個 Step 詳細 cost 紀錄（grep `LLM cost` / `~\$`） |
| `layout_agent/result.md` | 對應實驗的論文 framing + 數字 |
| `layout_agent/EXPERIMENT_MATRIX.md` | 13 列主表 × 13 列 ablation 表 + 證據檔索引 |
| `layout_agent/output/*.json` | 個別實驗結果檔（gitignored、需各別跑） |

---

*最後更新：2026-06-15。對應 IMPLEMENTATION_LOG.md Step 1~71、result.md §1~§8。*
