# AgentLayout Codex 交接文件

> 更新日期：2026-07-10  
> Repository：`/home/hui0705/MetaGPT`  
> 目前 baseline commit：`fd81922a`  
> 目的：更換 Codex 帳號／session 後，從相同狀態繼續 A3 架構矯正與實驗。

---

## 1. 新 session 首先要讀的文件

依序完整閱讀：

1. `layout_agent/CODEX_HANDOFF.md`（本檔）
2. `layout_agent/new_plam.md`（唯一的新架構與實驗規格）
3. `layout_agent/A3_EXPERIMENT_LOG.md`（A3 唯一實作與實驗紀錄）

舊的 `layout_agent/IMPLEMENTATION_LOG.md`、`layout_agent/result.md` 與舊論文只能作歷史參考，不是新架構的工作基準。

---

## 2. 使用者已確認的紀錄規則

從現在開始：

- 不得再把新的實作或實驗結果寫入 `layout_agent/IMPLEMENTATION_LOG.md`。
- 不得再把新的實作或實驗結果寫入 `layout_agent/result.md`。
- A3 的 audit、設計決策、修改、測試、smoke、gate、正式結果、成本與失敗，全部追加到 `layout_agent/A3_EXPERIMENT_LOG.md`。
- 新實驗輸出預定統一放在 `layout_agent/runs/a3/`。
- 每一個 run 必須有獨立 `run_id`、manifest、sample IDs、exact command、artifacts、成本與錯誤紀錄。
- 不得覆寫或混用舊 output/cache。

`layout_agent/A3_EXPERIMENT_LOG.md` 已建立，但尚未開始修改 pipeline，也尚未執行任何新付費實驗。

---

## 3. 研究與配置目標

候選正式配置：

```text
A3-MLLM / M-5.4mini / P-Full / R3 / L1-Gated
```

核心定位：

```text
A training-free, explicit-structure approach for
content-aware foreground layout generation.
```

不得重新引入的舊設計：

- 固定 `semantic_relevance=0.5` 作為有效指標
- SEGA-style text-only foreground protocol
- 把非文字 foreground 按 designer GT 位置預合進 background
- 舊 R2 renderer 結果
- 固定 35/50 acceptance threshold
- 連續兩次 ACCEPT
- 最多五輪 aesthetic refinement
- reject 後回 Analyst 的 runtime loop
- 使用 predicted tree 評估同一 predicted layout 的自我一致性作主要 tree 證據

---

## 4. 已完成工作

目前只完成 read-only 初步 audit 與交接文件建立，沒有修改核心程式。

已確認：

- 核心程式位於 `metagpt/ext/agentlayout/`。
- 現行流程仍主要是舊系統：

```text
text-only Analyst
  -> AssetAnalyzer (semantic_relevance=0.5)
  -> Asset Planner / Layout Tree
  -> Composition Director
  -> Coordinate Mapper
  -> deterministic QC
  -> combined Aesthetic Judge
  -> old multi-round refinement loop
```

- Analyst 目前沒有觀看 background 與 foreground images。
- Background Analyzer 已有 deterministic saliency、safe-zone 與 palette 分析。
- Layout Tree 已在座標前產生，但 contract 尚未符合新規格的 role/relation/confidence 欄位。
- 現行 text bitmap 可同時保存 `asset_ref` 與 `content`，但缺 alpha-tight crop、固定 long-edge normalization 與 GT size leakage 防護。
- 舊 Crello/SEGA preprocessing 會把非文字 foreground 合進 background，不符合 P-Full。
- Judge selection 與 critique 尚未拆分。
- pipeline 預設仍可跑五輪，並包含 ACCEPT 後強制 refinement、連續兩次 ACCEPT、issue ledger 與回 Analyst 路由。
- 現有 trace/metadata 不等同新規格要求的 versioned run manifest。

---

## 5. 正式工作順序

必須遵循 `new_plam.md` 第 8 節，不得跳過 N=5/N=20 gates：

| A3 階段 | 工作 | 對應正式 Phase |
| --- | --- | --- |
| A3-00 | 完成逐項 code audit、dirty-worktree 盤點與 freeze plan | Phase 0 |
| A3-01 | Run manifest、不可覆寫的 run directory、provenance infrastructure | Phase 0 |
| A3-02 | P-Full input protocol | Phase 0 |
| A3-03 | R3 normalization、renderer 與 leakage tests | Phase 0 |
| A3-04 | Analyst MLLM、background overview 與 asset contact sheet | Phase 0 |
| A3-05 | Layout Tree versioned contract | Phase 0 |
| A3-06 | L0、Judge-Select、Judge-Critic 分離 | Phase 0 |
| A3-07 | L1-Gated、targeted repair、verifier、B0/B1 guard | Phase 0 |
| A3-08 | N=5 smoke，只驗證 pipeline | Phase 1 |
| A3-09A | N=20 Gate A：Analyst vision | Phase 2 |
| A3-09B | N=20 Gate B：T0/T2/T3 Layout Tree | Phase 2 |
| A3-09C | N=20 Gate C：L0 vs L1-Gated | Phase 2 |
| A3-10 | 只有通過 gate 的 N=100 正式實驗 | Phase 3 |
| A3-11 | Matched baseline 或 literature-only 降級說明 | Phase 4 |
| A3-12 | 實驗與架構 freeze 後同步論文 | Phase 5 |

實作依賴上，P-Full 與 R3 先於 Analyst MLLM，因為 Analyst 的 contact sheet 必須建立在無 GT leakage 的正式 asset protocol 上。

---

## 6. 下一個 session 的第一個具體任務

不要立刻跑模型，也不要修改論文。

先完成 **A3-00 code audit**，並把結果追加到 `layout_agent/A3_EXPERIMENT_LOG.md`：

1. 逐檔確認每個 Agent 的實際 input、output、model config、image attachment 與 retry。
2. 找到 Crello input/preprocessing 的真正入口，標出所有 GT x/y、bbox、bitmap size 與預合成路徑。
3. 找到 renderer 對 text bitmap 的 crop、scale、aspect-ratio 與 natural-size 使用位置。
4. 畫出目前 pipeline 的實際 control flow，包括 candidate top-up、Judge、ACCEPT/REJECT routing 與停止條件。
5. 列出 L0/L1-Gated 可以最小改動介入的位置。
6. 盤點現有 manifest、trace、cost、prompt 與 schema version 資料哪些可重用。
7. 列出預計修改／新增的檔案及相依 tests，但 audit 階段先不要修改核心程式。
8. 檢查 dirty worktree；所有既有修改都視為使用者的工作，不得覆寫或 reset。

A3-00 完成並留下可核對的 audit 後，才開始 A3-01 manifest infrastructure。

---

## 7. 已知的重要檔案

核心：

- `metagpt/ext/agentlayout/pipeline.py`
- `metagpt/ext/agentlayout/schema.py`
- `metagpt/ext/agentlayout/actions/analyze_brief.py`
- `metagpt/ext/agentlayout/actions/plan_assets.py`
- `metagpt/ext/agentlayout/actions/compose_concept.py`
- `metagpt/ext/agentlayout/actions/generate_layout.py`
- `metagpt/ext/agentlayout/actions/judge_aesthetic.py`
- `metagpt/ext/agentlayout/roles/iteration_state.py`
- `metagpt/ext/agentlayout/tools/asset_analyzer.py`
- `metagpt/ext/agentlayout/tools/background_analyzer.py`
- `metagpt/ext/agentlayout/tools/quality_checker.py`
- `metagpt/ext/agentlayout/tools/semantic_group_metrics.py`

舊 driver／結果（只讀歷史，不作新實驗）：

- `layout_agent/run_demo.py`
- `layout_agent/output/step74_n1897_full_trace.py`
- `layout_agent/output2/`
- `layout_agent/demo/`
- `layout_agent/demo_v2/`
- `layout_agent/full_result/`

---

## 8. Worktree 注意事項

最近檢查時 worktree 已經是 dirty，包含使用者原有修改及大量未追蹤資料。至少包括：

- 已修改：`layout_agent/IMPLEMENTATION_LOG.md`
- 已修改：`layout_agent/output2/step91_o4mini_ab.py`
- 已修改：`metagpt/provider/constant.py`
- 未追蹤：多份文件、demo/output 與本次新增的 A3 紀錄／交接檔

不得執行：

```text
git reset --hard
git checkout -- <file>
git clean
```

除非使用者明確授權，也不要 commit、push 或修改論文。

---

## 9. 給新 Codex session 的建議開場指令

使用者可直接貼：

```text
請先完整閱讀 layout_agent/CODEX_HANDOFF.md、layout_agent/new_plam.md
和 layout_agent/A3_EXPERIMENT_LOG.md。依交接文件繼續 A3-00 code audit。
這一階段不要修改核心程式、不要跑付費實驗、不要修改論文；audit 結果只追加到
layout_agent/A3_EXPERIMENT_LOG.md，不要再寫 IMPLEMENTATION_LOG.md 或 result.md。
```

