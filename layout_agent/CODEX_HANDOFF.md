# AgentLayout Codex 交接文件

> 更新日期：2026-07-11  
> Repository：`/home/hui0705/MetaGPT`  
> 分支：`feat/step76-89-sega-pipeline`（remote：`nina/feat/step76-89-sega-pipeline`）  
> 目前 HEAD（已 push）：`c453746595f4a95de966d47aa9a0f43f14b6cea5`  
> 目的：接續 A3 架構工作。A3-00～A3-08 與 Gate C 已完成；目前卡在人工標註（使用者自己在做），標註回收後跑 Gate A/B。

---

## 1. 新 session 首先要讀的文件（依序讀到 EOF）

1. `layout_agent/CODEX_HANDOFF.md`（本檔）
2. `layout_agent/new_plam.md`（唯一的新架構與實驗規格；worktree 中有使用者未 commit 的修改，**以 worktree 版為準**）
3. `layout_agent/A3_EXPERIMENT_LOG.md`（A3 唯一實作與實驗流水帳，§2～§17 是完整歷史）

紀錄規則不變：A3 一切內容**只**追加到 `A3_EXPERIMENT_LOG.md`；不得寫 `IMPLEMENTATION_LOG.md`、`result.md`；不得修改論文。

---

## 2. 目前狀態總表

| 階段 | 狀態 | 關鍵結論 |
| --- | --- | --- |
| A3-00 audit | complete | log §4 |
| A3-01 manifest/provenance | complete | commit `1ec0d05a` |
| A3-02 P-Full | complete | `cd61fc8f`；＋text bitmap sidecar `fbe41f42` |
| A3-03 R3 | complete | `59f9dcbe`（bitmap 協定＋renderer contain＋prompt 無尺寸洩漏） |
| A3-04 Analyst MLLM | complete | `d6f2f84b` |
| A3-05 Layout Tree v3 | complete | `25ed3716`；＋smoke 修正 `fe4cef54` |
| A3-06 Judge-Select/Critic 分離＋L0 pipeline | complete | `8fa67aa4` |
| A3-07 L1-Gated＋verifier＋B0/B1 guard | complete | `24ffb8be` |
| A3-08 N=5 smoke | complete | log §14：L0 5/5、L1 5/5；修 5 個 contract 缺陷 |
| **A3-09C Gate C** | **complete** | **log §15：L1 compliance 34.2% 未過門檻 → A3 最終 loop 配置定案 L0。勿再提 loop 方案** |
| A3-09 Gate A/B 前置 | complete | log §16–17：sidecar 補齊、annotation 工具鏈、text-only 臂、pilot 標註包 |
| **Human annotation** | **in progress（使用者親自標註）** | pilot N=20，指南在 run 目錄 |
| Gate A/B 付費 runs | blocked on annotation | 需使用者授權 |
| A3-10 N=100 | blocked by gates | 一般池另需 bitmap snapshot 擴充 |

---

## 3. A3 程式碼全圖（全部已 commit）

核心（`metagpt/ext/agentlayout/`）：

- `a3_config.py` — `a3.run-config.v1`
- `run_manifest.py` — immutable run store、`write_json_once`、ErrorRecord、provenance
- `a3_pipeline.py` — **canonical L0 orchestrator**（`A3L0Pipeline`、`_run_r0_phase`、`R0PhaseOutcome`、exactly-3 candidate contract、all-QC-fail degradation 標記）
- `a3_pipeline_l1.py` — `A3L1GatedPipeline`（含 `run_from_r0`：Gate C 的 R0 重用 tail）
- `a3_stage_binding.py` — 真實 Actions↔pipeline callables 綁定＋per-call `stage_calls.json`（wall time；provider token usage 恆為 0 是已知現象）＋`hydrate_from_r0`
- `layout_tree_v3.py` — `a3.layout-tree.v1`、T0/T1/T2/T3 `TreeCondition`、`condition_prompt_payload`、`apply_analyst_semantics`（Planner 語意欄位由 Analyst 確定性覆寫）、root-relation parser 正規化
- tools/：`pfull_preprocessor.py`（含 **text bitmap sidecar** `a3_text_bitmaps.json`，不動 meta.json）、`text_bitmap_normalizer.py`、`analyst_vision.py`（vision＋text-only 兩套 prompt）、`judge_select.py`、`judge_critic.py`（closed issue enum）、`repair_gate.py`（routing＋B0/B1 guard）、`issue_verifier.py`（STRICT/PROXY 分類）、`director_contract.py`、`mapper_contract.py`、`annotation.py`（packet/agreement/adjudication/oracle-tree）
- actions/：`analyze_a3.py`、`analyze_a3_text_only.py`、`plan_assets_a3.py`、`compose_concept_a3.py`、`generate_layout_a3.py`（含 L1 revision mode）、`judge_select_a3.py`、`judge_critic_a3.py`

CLI：`layout_agent/run_a3.py` 子命令：
`plan / init / prepare-pfull / normalize-r3 / prepare-analyst-vision / prepare-annotation / snapshot-text-bitmaps / run / run-l1-tail`。
**付費命令（run / run-l1-tail）沒有 `--allow-api-calls` 時只印 budget 並 exit 2**；`run` 另有 `--tree-arm T0..T3` 與 `--analyst-arm vision|text-only`。

凍結檔：`layout_agent/configs/a3_{smoke_l0,smoke_l1_gated,gatec_l0,gatec_l1_tail,gateab_pilot_l0}.json`；`layout_agent/sample_ids/a3_{smoke_n5,gatec_n20,gateab_pilot_n20}.json`。全部凍 `gpt-5.4-mini-2026-03-17`（runtime config2.yaml 已相符；`MULTI_MODAL_MODELS` 已含 `gpt-5.4-mini`）。

---

## 4. 測試基準

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run \
  --with pytest --with 'pydantic>=2' --with pillow \
  pytest -q -o addopts='' \
  --confcutdir=tests/metagpt/ext/agentlayout \
  tests/metagpt/ext/agentlayout/test_a3_run_manifest.py \
  tests/metagpt/ext/agentlayout/test_pfull_preprocessor.py \
  tests/metagpt/ext/agentlayout/test_text_bitmap_normalizer.py \
  tests/metagpt/ext/agentlayout/test_analyst_vision.py \
  tests/metagpt/ext/agentlayout/test_layout_tree_v3.py \
  tests/metagpt/ext/agentlayout/test_judge_select_a3.py \
  tests/metagpt/ext/agentlayout/test_judge_critic_a3.py \
  tests/metagpt/ext/agentlayout/test_a3_l0_pipeline.py \
  tests/metagpt/ext/agentlayout/test_a3_repair_gate.py \
  tests/metagpt/ext/agentlayout/test_a3_l1_pipeline.py \
  tests/metagpt/ext/agentlayout/test_a3_director_mapper.py \
  tests/metagpt/ext/agentlayout/test_a3_issue_verifier.py \
  tests/metagpt/ext/agentlayout/test_a3_stage_binding.py \
  tests/metagpt/ext/agentlayout/test_a3_annotation.py
```

目前基準：**144 passed**。改動後 ruff（新檔）、`py_compile`、scoped `git diff --check` 也要過。
跑 live Python 一律 `conda activate meta`（isolated uv 只給測試）。

---

## 5. 既有 runs（`layout_agent/runs/a3/`，write-once、未 commit）

| run | 內容 |
| --- | --- |
| `a3-smoke-n5-l0-01/02/03` | smoke 三輪（0/5→4/5→5/5；失敗 run 保留 forensic） |
| `a3-smoke-n5-l1-01` | L1 smoke 5/5 |
| `a3-gatec-n20-l0-01` | Gate C L0 臂 20/20（140 calls） |
| `a3-gatec-n20-l1-tail-01` | Gate C L1 tail 20/20（39 calls）——判定 L0 定案 |
| `a3-gateab-pilot-n20-01` | Gate A/B pilot：prep 全過＋**20 份標註包**＋`ANNOTATION_GUIDE.md` |

---

## 6. 下一個 session 的具體任務（依序）

**任務 1（zero-cost，可立即做）：human-tree 評估指標實作。**
new_plam §7.1/§7.2 需要、但 repo 尚未有 A3 版的：

- tree prediction metrics：predicted tree vs human oracle tree 的 same-group pair P/R/F1、parent-child edge P/R/F1、semantic type/role accuracy（介面吃兩個 `A3LayoutTree`）；
- SGC/TLC/PCA 改用**同一份 human reference tree** 評所有 arms（舊 `semantic_group_metrics.py` 是 predicted-tree 自我一致性版，不可直接沿用——見 log §4.3）；
- 全部純幾何/集合運算，加 tests 進第 4 節基準。

**任務 2（等標註回收）：annotation 驗收管線。**
使用者親自標註中，檔案會出現在
`layout_agent/runs/a3/a3-gateab-pilot-n20-01/samples/<id>/annotation/annotation_<annotator_id>.json`。
回收後：逐份 `HumanAnnotation.model_validate`＋`validate_annotation_coverage`；
兩位標註者都齊的樣本跑 `compute_agreement`；分歧清單交使用者裁決；
以 `AdjudicationRecord`＋`annotation_to_oracle_tree` 產出 T3 oracle trees（write-once 落盤）。
**注意**：若最終只有一位標註者，如實記錄為 single-annotator limitation，agreement 從缺。

**任務 3（需使用者授權付費）：Gate A/B runs。**

- Gate A：同 pilot N=20 sample IDs，兩臂
  `run --analyst-arm vision` vs `run --analyst-arm text-only`（各 ~140 calls）；
  主指標＝兩臂 predicted tree vs human tree 的 F1/role accuracy（任務 1 的指標）；
- Gate B：同批樣本 L0 跑 `--tree-arm T0 / T2 / T3`（T3 用任務 2 的 oracle trees；
  T0 每 sample 6 calls、T2/T3 各 7）；判定條件見 new_plam §8 Gate B；
- 每個 arm 一個獨立 run-id；prep 四連（init→prepare-pfull→normalize-r3→prepare-analyst-vision）每個 run 都要重跑（zero-cost）。

**任務 4（Gate 通過後才排）**：一般池 N=100 的 bitmap snapshot 擴充（`snapshot-text-bitmaps` 已能跑，一般 cache 可用池僅 ~4%）→ A3-10。

---

## 7. 鐵律（不變）

- **付費 API 一律先向使用者報 budget 取得授權**；CLI 的 `--allow-api-calls` gate 不得繞過或移除。
- **Gate C 已定案 L0**：不得重啟 loop/refinement 方案；L1-Gated 只作 controlled negative 引用。
- model snapshot 凍 `gpt-5.4-mini-2026-03-17`；exact-model guard 失敗時不得默默換模型。
- 不動使用者 dirty 檔案：`IMPLEMENTATION_LOG.md`、`new_plam.md`、`output2/step91_o4mini_ab.py`、`metagpt/provider/constant.py` 及所有未追蹤 demo/output；不 reset/checkout/clean。
- 舊 cache 只可加 A3 命名空間 sidecar 檔，`meta.json` 一個 byte 都不能動。
- run 目錄 write-once；新 run 一律新 run-id；失敗 run 保留 forensic 證據。
- commit 粒度：每個 A3 子階段一個獨立 commit，只 stage 該階段檔案，push `nina/feat/step76-89-sega-pipeline` 後驗證 `HEAD == @{upstream}`。
- 所有實作與實驗結果追加 `A3_EXPERIMENT_LOG.md` 並更新 §1 phase table。

---

## 8. 給新 session 的建議開場指令

```text
請先完整閱讀 layout_agent/CODEX_HANDOFF.md、layout_agent/new_plam.md
和 layout_agent/A3_EXPERIMENT_LOG.md（讀到 EOF）。確認 HEAD 是 c4537465
且 tracked dirty 檔案只有交接文件列出的四個。然後從交接文件第 6 節的
任務 1（human-tree 評估指標，zero-cost）開始；標註檔案回收前不要跑任何
付費 API，跑付費前必須先報 budget 等我授權。
```
