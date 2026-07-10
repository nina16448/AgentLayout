# AgentLayout A3 實作與實驗紀錄

> 建立日期：2026-07-10  
> 適用範圍：`A3-MLLM / P-Full / R3 / L0 or L1-Gated` 及其受控 ablation  
> 規格來源：`layout_agent/new_plam.md`

---

## 0. 紀錄邊界

本檔是 AgentLayout A3 新架構唯一的實作與實驗流水帳。

從本檔建立後：

1. 新架構的 code audit、設計決策、實作變更、smoke、gate、正式實驗、失敗與成本全部記錄在本檔。
2. 不再把 A3 的新內容追加到 `layout_agent/IMPLEMENTATION_LOG.md` 或 `layout_agent/result.md`。
3. 舊檔與舊輸出只作歷史證據，不視為 A3 的實驗結果。
4. 每筆付費實驗必須對應獨立 `run_id`、run manifest、sample IDs 與 artifact 目錄。
5. 失敗、parse error、missing element、skipped sample 與 exhausted run 必須和成功結果一起記錄。
6. 在 N=5 smoke 與 N=20 gate 通過前，不執行 N=100 正式實驗。
7. 論文更新不記在此階段；架構與實驗 freeze 後另行處理。

### 舊資料隔離

下列資料不得直接混入 A3 主實驗：

- `layout_agent/demo/`
- `layout_agent/demo_v2/`
- `layout_agent/full_result/`
- `layout_agent/output/`
- `layout_agent/output2/`
- 舊 GPT-4o、GPT-5.2、o4-mini、raw-asset、text-only、SEGA-style、R2 renderer 或多輪 refinement 結果

預定新輸出根目錄：

```text
layout_agent/runs/a3/
```

---

## 1. 執行階段

| Phase | 內容 | 狀態 |
| --- | --- | --- |
| A3-00 | 現行程式 audit 與新舊邊界建立 | in progress |
| A3-01 | Manifest、資料協定與 run directory 基礎設施 | complete |
| A3-02 | P-Full input protocol | pending |
| A3-03 | R3 text bitmap normalization 與 leakage tests | pending |
| A3-04 | Analyst MLLM 與 contact sheet | pending |
| A3-05 | Layout Tree contract 更新 | pending |
| A3-06 | L0、Judge-Select 與 Judge-Critic | pending |
| A3-07 | L1-Gated、repair verifier 與 B0/B1 guard | pending |
| A3-08 | N=5 smoke | pending |
| A3-09 | N=20 Analyst／Tree／Loop gates | pending |
| A3-10 | N=100 正式實驗 | blocked by gates |

---

## 2. A3-00：初始 audit 與紀錄切分

**日期：** 2026-07-10  
**Code baseline：** `fd81922a`  
**修改性質：** documentation only；尚未修改 pipeline 或執行付費實驗。

### 已確認

- 核心程式位於 `metagpt/ext/agentlayout/`。
- 現行 Analyst 沒有觀看 background 與 foreground images，不符合 Analyst MLLM 規格。
- `AssetAnalyzer` 仍使用固定 `semantic_relevance=0.5` placeholder。
- 舊 Crello/SEGA protocol 會把非文字 foreground 依 designer GT 位置合進 background，不符合 P-Full。
- text bitmap 已可同時保存 `asset_ref` 與 `content`，但尚未完成 alpha-tight crop、固定 long-edge normalization 與 GT size leakage 防護。
- 現行 pipeline 預設最多五輪，包含 ACCEPT 後強制 refinement、連續兩次 ACCEPT、issue ledger 與回 Analyst 路由，不符合 L0/L1-Gated。
- Judge selection 與 critique 尚未解耦。
- 現有 trace 不等同 A3 run manifest。

### 第一個實作目標

先完成 A3-01，不直接改生成 prompt：

1. 定義 versioned run manifest schema。
2. 建立不可覆寫的 `run_id` 與輸出目錄規則。
3. 固定 architecture、foreground protocol、renderer、loop、model snapshot、prompt hash、schema version 與 sample IDs 欄位。
4. 建立失敗與 skipped sample 的統一紀錄格式。
5. 為後續 P-Full、R3、L0/L1-Gated 加入明確 feature/config boundary。

這一步完成後，才依序實作 P-Full 與 R3，避免新舊資料在尚無 provenance 的情況下再次混合。

---

## 3. 實驗紀錄模板

後續每個 smoke、gate 或正式 run 使用以下格式追加：

```text
### [run_id] 實驗名稱

- 日期：
- Git commit / dirty diff hash：
- Architecture：
- Model snapshot：
- Foreground protocol：
- Renderer：
- Loop：
- Internal Judge：
- Evaluation Judge：
- Dataset / sample IDs：
- Seed：
- Exact command：
- Output directory：
- Prompt hashes：
- Schema versions：
- 成本與 wall time：
- Completion / errors / skipped：
- 指標：
- 結論：
- 是否通過 gate：
- 後續決策：
```

---

## 4. A3-00：完整 code audit（read-only）

**日期：** 2026-07-10  
**Repository：** `/home/hui0705/MetaGPT`  
**HEAD：** `fd81922a88fba111700258f768652cd2081523d2`  
**範圍：** 實際 executable path、Crello preprocessing、R2/R3 renderer、loop、trace/provenance、tests。  
**修改性質：** 只追加本 audit；未修改核心程式、未執行 LLM/API、未修改論文或舊 log。

### 4.1 實際 executable architecture

目前有兩條入口，但不能視為完全等價：

```text
LayoutPipeline.run（主要、較新的 orchestrator）
  user_brief + List[AssetInput(asset_ref/content)]
  -> AnalyzeBrief [text-only LLM, DesignSpec, parse retry x3]
  -> AssetAnalyzer [deterministic importance + semantic_relevance=0.5]
  -> PlanAssets [text-only LLM, LayoutTree, parse/coverage retry x3]
  -> resolve_background [deterministic CV]
  -> ComposeConcept [background image only, normally 3 concepts, retry x3/fallback]
  -> for each concept: GenerateLayout [background image only, 1 candidate,
                                      retry x3; vision refusal adds text-only retry]
  -> deterministic QC
       any pass: keep all passing candidates
       none pass: rank and keep least-violating up to k_valid
  -> JudgeAesthetic [all rendered candidates, combined rank+score+feedback,
                     parse retry x3]
       optional reject-only visual observer [feature flag, retry x2]
  -> ACCEPT: mandatory CoordinateMapper polish; two consecutive ACCEPTs stop
     REJECT design/innovation: CompositionDirector -> Mapper
     REJECT after routing budget: Analyst -> re-plan -> Director -> Mapper
     other REJECT: Mapper
  -> at max_total_rounds=5: return most recent ACCEPT, otherwise PipelineError
```

```text
Team/Role path
  AnalystRole -> AssetPlannerRole -> CompositionDirectorRole
  -> LayoutGeneratorRole -> AestheticJudgeRole -> IterationStateRole
  -> retry routing / IterationStop
```

Role path mirrors the broad stages but has separate state/control code and is not proven byte-for-byte equivalent to `LayoutPipeline`. A3 should freeze one canonical runner; otherwise fixes must be duplicated and can drift.

The old `layout_agent/run_demo.py` is only a wrapper around
`layout_agent/output/step74_n1897_full_trace.py`. That driver contains its own legacy top-up/refinement implementation and calls `GenerateLayout.run()` without the now-required `concept` argument. It is therefore **stale/conflicting with the current CoordinateMapper API** and must not be used as the A3 smoke runner.

### 4.2 Agent-by-Agent contract audit

| Stage | Actual input | Actual output | Image attachment | Retry / fallback | Model/config status | A3 status |
| --- | --- | --- | --- | --- | --- | --- |
| Analyst (`AnalyzeBrief`) | brief, serialized `AssetInput` paths/content, optional old Judge feedback | `DesignSpec`; then deterministic GT-calibrated photo-size prior is injected | none; asset paths are text only | same prompt up to 3 parse/schema attempts | inherits global MetaGPT LLM; no per-Agent frozen snapshot/effort/detail/token manifest | **conflicting**: not MLLM; cannot visually identify assets |
| Asset Analyzer | enriched-in-place `DesignSpec` | importance and semantic relevance fields | none | none | Python; semantic relevance is fixed `0.5` | **conflicting** |
| Asset Planner (`PlanAssets`) | enriched `DesignSpec` | `LayoutTree` containing each foreground ID once | none | up to 3 parse/schema/coverage attempts | global LLM; no frozen per-Agent config | **partial**: tree precedes coordinates, but node contract is only `id/children` |
| Composition Director (`ComposeConcept`) | `DesignSpec`, `BackgroundAnalysis`, optional prior concepts/Judge feedback | normally 3 natural-language `CompositionConcept`s | background only, if model supports images | 3 attempts; unsupported temperature kwarg is silently removed; final fallback is one centered concept | global LLM; requests a temperature but actual applied value is not persisted | **partial**: sees background, not foreground contact sheet; diversity is prompted, not verified |
| Coordinate Mapper (`GenerateLayout`) | spec, tree, background analysis, one concept, optional feedback/previous bbox/scores | one-candidate `CandidatesBatch` | background only | 3 parse attempts; vision refusal drops image and adds one text-only attempt | global LLM; actual model parameters not frozen/persisted | **partial/conflicting**: consumes tree, but receives natural bitmap size and no asset thumbnails |
| Deterministic QC | candidate, spec, background analysis | pass/fail violations/warnings | loads background for some diagnostics | no retry; if all fail, pipeline degrades to least-violating candidates | Python thresholds accumulated from legacy experiments | **partial**: reusable checks exist, but A3 hard/warning policy must be frozen |
| Internal Judge (`JudgeAesthetic`) | candidates, spec, tree, background analysis | combined scores, B0 ID, ACCEPT/REJECT and critique | rendered candidate images | 3 parse attempts; optional reject observer x2 | same global LLM; no independent Judge-Select/Critic configs | **conflicting**: selection and critique combined; threshold 35; accept polish mandatory |
| Iteration state | Judge verdict/feedback and accumulated state | retry target or stop | none | up to legacy cap | duplicated between pipeline and Role path | **conflicting**: multi-round, Analyst reroute, consecutive accepts |

No action defines a model snapshot itself. `Action.llm` inherits runtime/global configuration; only `ComposeConcept` attempts an explicit temperature and silently falls back if unsupported. Exact reasoning effort, max tokens, image detail, response schema mode and actual temperature are not recorded.

### 4.3 Schema and Layout Tree gap

Implemented and reusable:

- typed Pydantic contracts for canvas, element, DesignSpec, LayoutTree, concepts, candidates, feedback, judgement and iteration state;
- validation that the current tree has root `root`, no duplicate IDs, and exactly covers non-background spec elements;
- stable IDs are carried through spec/tree/candidate/QC in the core path.

Missing/conflicting for A3:

- no explicit schema-version constants or versions persisted with objects;
- `LayoutTreeNode` only carries `id` and `children`; no semantic role, group label, relation type, ordering/priority or confidence;
- no T0/T1/T2/T3 contract switch and no human-reference-tree input path;
- decorative exclusions/coverage policy is not versioned;
- current semantic metrics can consume a predicted tree, but this is not valid primary evidence under the A3 protocol.

### 4.4 Crello input and GT leakage trace

The active legacy SEGA-style entrance is
`tools/crello_preprocessor.preprocess_sample(sample_dir, out_dir)`, reading cached
`layout_agent/output/crello_<id>/meta.json`.

Direct GT use found:

- `_paste_layer`: reads each non-text element's GT `left`, `top`, `width`, `height`, resizes the asset and composites it at that position;
- `BAKED_KINDS = background_candidate, image, underlay`: all non-text foreground is removed from placement and baked into `bg_composite.png`;
- `_clamped_bbox`: derives GT LTRB from the same four geometry fields;
- `text_bboxes`: reads GT text bbox for every text element;
- `_holds_gt_text`: uses those GT text bboxes to decide whether an underlay is a text holder;
- `UnderlayRegion.bbox`: exports the designer-positioned underlay bbox downstream;
- `text_assets`: exports each text bitmap plus the designer `width` and `height` as its “natural canvas size”.

Therefore the old preprocessing is intentionally SEGA-compatible but **conflicts with P-Full and A3 leakage rules**. Both the precomposed background and the underlay hints reveal designer placement. P-Full needs a new deterministic classifier/extractor that selects only a base background without using candidate output or GT geometry, preserves every placeable foreground asset separately, and writes an asset manifest.

The cache snapshot script `step80_snapshot_text_assets.py` mutates cached `meta.json` to attach rendered text assets. These caches are legacy inputs, not immutable A3 source-of-truth assets, and must be isolated.

### 4.5 Renderer / R3 audit

Current bitmap path treats a pre-rendered text PNG as a generic image because Analyst labels it `visual_type=image`:

- no alpha-tight crop;
- no fixed padding;
- no fixed long-edge normalization;
- no normalized-size/hash manifest;
- Mapper prompt opens the file, reveals its native pixel dimensions, explicitly asks for `0.8x-1.2x` natural-size placement, and locks aspect ratio;
- reject observer also reports natural bitmap size;
- renderer independently resizes width and height to the predicted bbox, so aspect ratio is **not actually locked**;
- generic image renderer caps each axis independently at `MAX_UPSCALE=2`, centers the smaller raster in the declared bbox, and can therefore leave transparent/empty bbox area;
- renderer does not crop source alpha before scaling.

This is **conflicting**, not partial R3: it preserves bitmap typography but leaks GT size and does not guarantee aspect-preserving fit. A3 R3 requires preprocessing the RGBA bitmap once (tight alpha crop + fixed padding + deterministic long-edge), removing all original/natural size cues from prompts, and using a contain/fit rule that derives one scale factor from the Mapper bbox.

Plain text (non-bitmap legacy path) uses predicted bbox/font metadata, word-wrap and iterative shrink-to-fit down to 8 px. This code may remain useful for diagnostics but is outside the frozen R3 bitmap protocol.

### 4.6 Candidate generation, QC and loop findings

- `PipelineConfig.k_valid` and `max_topup_rounds` remain in schema/config, but the current pipeline no longer tops up. It maps one candidate per concept and degrades to least-violating candidates only when **all** candidates fail QC.
- `LayoutGeneratorRole` has the same no-top-up approach, while Step74 still implements the old top-up loop. Documentation/driver claims about top-up are stale and version-dependent.
- There is no guarantee of exactly three judgeable candidates: Director can fall back to one concept; individual Mapper calls can fail; QC passes are not topped up. This violates A3 smoke expectations unless candidate completeness is made explicit.
- Judge prompt still requires scores and structured suggestions for both accept and reject, defines best score `>=35` as ACCEPT, and performs ranking plus critique in one call.
- ACCEPT is not terminal: it routes to mandatory Mapper polish. Termination normally requires two consecutive ACCEPTs.
- REJECT can recreate concepts, re-run Analyst/Planner after reject budget, or micro-adjust Mapper; issue ledger and regression reopening persist across rounds.
- `max_total_rounds` defaults to five. If the cap contains any ACCEPT, the pipeline returns the most recent accepted result even without convergence; otherwise it raises with the last rejected best candidate attached.

### 4.7 Minimal L0 / L1-Gated intervention points

Recommended minimal boundaries, in dependency order:

1. Add an explicit A3 run/config object before `LayoutPipeline.run`; reject ambiguous legacy defaults.
2. Freeze Analyst and tree once before concepts; remove runtime Analyst/Planner rerouting.
3. Make `_generate_from_concepts` return an explicit R0 bundle with exactly three slots and per-slot failure/QC records; define whether a missing slot aborts or gets a reliability retry.
4. Split `JudgeAesthetic` into `JudgeSelect` (three renders -> B0/ranking only) and `JudgeCritic` (B0 only -> at most two actionable issues).
5. **L0:** stop immediately after JudgeSelect and persist B0.
6. **L1-Gated:** deterministic/actionability gate -> one routed repair -> verifier -> B0/B1 guard -> unconditional stop.
7. Reuse `feedback_verifier` and QC comparisons where their issue types match, but replace the multi-round ledger with a single repair record plus KEEP constraints.
8. Keep Role path disabled for A3 until it either delegates to the same orchestration service or has parity tests.

### 4.8 Provenance, trace, prompt and cost reuse audit

Reusable pieces:

- `layout_agent/output2/provenance.py`: HEAD, tracked diff SHA-256, selected untracked paths, global model config, AGENTLAYOUT env flags and Python version;
- pipeline `TraceEntry`: round, decision, route, candidate count, QC filtered count, ledger/compliance summary;
- Step74 artifact layout: inputs/spec, per-round candidates/renders/selection, raw Judge JSON, QC violations, timing and sample status;
- prompts are module constants and can be hashed deterministically;
- MetaGPT runtime owns LLM instances and may expose usage/cost, but current A3 path does not persist per-call usage.

Insufficient/missing:

- provenance watches only two legacy roots, ignores untracked file **contents**, and hashes only tracked diff; a dirty run is not reconstructible when relevant code/config is untracked;
- no immutable `run_id`, atomic/non-overwrite directory creation, run-level/sample-level manifest schema or manifest version;
- no exact resolved model snapshot per call, model parameters, image detail, structured-output mode, prompt hash map or schema version map;
- no canonical sample-ID file copied/hashed into a run;
- no uniform error/skipped/cost record; streamed vision usage was explicitly reported unavailable in an old driver;
- trace is returned in memory and drivers selectively serialize it; pipeline does not guarantee artifact persistence;
- old Step74 trace predates the current concept API and cannot be promoted directly.

### 4.9 Specification compliance matrix

| A3 requirement | Status | Evidence / gap |
| --- | --- | --- |
| training-free | implemented | no training path found in AgentLayout core |
| explicit tree before coordinates | partial | ordering exists; contract lacks roles/relations/confidence/version |
| P-Full | conflicting | legacy preprocessor bakes all non-text at GT geometry |
| R3 bitmap typography | partial/conflicting | bitmap preserved, but GT size leak/no crop/no normalization/non-uniform scaling |
| Analyst MLLM sees background + all foreground | missing | Analyst is text-only; paths are not images |
| Background Analyzer | implemented/partial | deterministic saliency/safe zones/palette exist; version/provenance not frozen |
| 3 spatially distinct concepts | partial | asks for 3; no diversity verifier; fallback/mapper failures reduce count |
| exactly 3 complete R0 renders | missing | no slot-completeness contract/top-up policy |
| versioned Layout Tree | missing | no schema version or required semantic fields |
| deterministic QC | implemented/partial | extensive checks exist; threshold set is legacy and unversioned |
| Judge-Select separated from Critic | missing | one combined verdict/scoring/feedback call |
| L0 | missing | ACCEPT still forces polish; no stop-after-select mode |
| L1-Gated | missing | old open multi-round loop only |
| Analyst/tree frozen per sample | conflicting | reject routing can rerun both |
| B0/B1 best-so-far guard | partial/conflicting | legacy score/layout guard exists, not specified one-repair verifier guard |
| immutable A3 run manifest | missing | legacy provenance/trace fragments only |
| matched offline evaluation | outside core / missing A3 runner | old evaluators exist but are not bound to A3 manifests |
| human reference tree / T0-T3 | missing | no canonical annotation/arm interface |

### 4.10 Dirty worktree freeze record

At audit time:

- tracked modifications: `layout_agent/IMPLEMENTATION_LOG.md`, `layout_agent/output2/step91_o4mini_ab.py`, `metagpt/provider/constant.py`;
- untracked top-level status entries: 17, including A3 documents, demo/output data and drivers;
- tracked diff summary before this audit: 3 files, 26 insertions, 1 deletion;
- `A3_EXPERIMENT_LOG.md` itself was already untracked and is the only file changed by this audit;
- no reset, checkout, clean, commit or overwrite was performed.

All are treated as user-owned. A3-01 must capture both tracked and relevant untracked content hashes without copying legacy output into the new run.

### 4.11 Freeze plan: expected files and tests (no core edit yet)

Likely new files:

- `metagpt/ext/agentlayout/a3_config.py` — explicit architecture/protocol/model/loop versions;
- `metagpt/ext/agentlayout/run_manifest.py` — immutable manifest and per-sample status contracts;
- `metagpt/ext/agentlayout/tools/a3_crello_preprocessor.py` — P-Full extraction and asset manifest;
- `metagpt/ext/agentlayout/tools/text_bitmap_normalizer.py` — R3 crop/pad/normalize/hash;
- `metagpt/ext/agentlayout/actions/judge_select.py` and `judge_critic.py`;
- `metagpt/ext/agentlayout/a3_pipeline.py` or a sharply isolated A3 branch in `pipeline.py`;
- `layout_agent/run_a3.py` — sole A3 CLI with `--dry-run`, explicit run ID/config/sample IDs;
- versioned schemas under `layout_agent/schemas/a3/` if JSON Schema artifacts are required.

Likely modified files:

- `schema.py`, `analyze_brief.py`, `plan_assets.py`, `compose_concept.py`, `generate_layout.py`, `renderer.py`, `quality_checker.py`, `feedback_verifier.py`, and package exports;
- `pipeline.py` only if a separate `a3_pipeline.py` is not used;
- Role/team files only after the canonical orchestrator is stable.

Required tests:

- manifest validation, non-overwrite/atomic creation, dirty/untracked hashes and failure records;
- P-Full: all placeable IDs preserved, only allowed base background selected, no GT x/y/bbox passed or baked;
- R3: alpha-tight crop, fixed padding/long edge, deterministic hash, no natural-size prompt leak, aspect-preserving render;
- Analyst image/contact-sheet attachment and asset-ID mapping;
- Layout Tree vA3 validity/coverage/roles/relations/confidence plus T0/T1/T2/T3 adapters;
- exactly-three R0 candidate slot contract, diversity diagnostics and missing-slot behavior;
- Judge-Select/Critic prompt and schema separation;
- L0 immediate stop; L1 maximum one repair; frozen Analyst/tree; actionable gate; verifier; completeness/hard-violation B0 guard; unconditional stop;
- per-call prompt/model/config/usage artifact persistence;
- parity tests if Team/Role remains supported.

Existing tests under `tests/metagpt/ext/agentlayout/` cover many legacy prompts, schemas, QC, renderer, feedback verifier and routing cases. They should remain as regression evidence but several encode legacy behavior and must not define A3 acceptance criteria.

### 4.12 Planned N=5 smoke contract (not executable until A3-01..A3-07)

No paid smoke was run. The current repository has **no valid A3 CLI**, and the apparent `run_demo.py` command is API-stale. The exact command to freeze during A3-01 is:

```bash
python layout_agent/run_a3.py plan \
  --config layout_agent/configs/a3_smoke_l0.json \
  --sample-ids layout_agent/sample_ids/a3_smoke_n5.json \
  --run-id a3-smoke-n5-l0-<timestamp>

python layout_agent/run_a3.py run \
  --config layout_agent/configs/a3_smoke_l0.json \
  --sample-ids layout_agent/sample_ids/a3_smoke_n5.json \
  --run-id a3-smoke-n5-l0-<timestamp>

python layout_agent/run_a3.py run \
  --config layout_agent/configs/a3_smoke_l1_gated.json \
  --sample-ids layout_agent/sample_ids/a3_smoke_n5.json \
  --reuse-r0-from layout_agent/runs/a3/a3-smoke-n5-l0-<timestamp> \
  --run-id a3-smoke-n5-l1-<timestamp>
```

Planned input is one frozen N=5 ID file plus per-sample P-Full `asset_manifest.json`; planned output is a newly created, non-overwritable `layout_agent/runs/a3/<run_id>/` containing run manifest, copied/hashed ID list, per-sample inputs/agent outputs/tree/R0 renders/QC/Judge/B0/B1/final/error and cost records.

Call budget for L0, assuming no reliability retries: 5 samples x (Analyst 1 + Planner 1 + Director 1 + Mapper 3 + Judge-Select 1) = **35 system calls**. L1 adds Critic for each B0 and at most one repair plus optional B0/B1 selection; maximum planned incremental budget is 15 calls if every sample triggers and needs pairwise selection. Actual dollar cost is **TBD, not safely estimable from current code**, because the exact snapshot price, token/image usage and supported parameters are neither frozen nor metered. The `plan` command must calculate an estimate from explicit price inputs before `run` is permitted; actual usage and retry calls must be recorded separately.

### 4.13 A3-00 conclusion

**A3-00 status: complete.** The current code is a useful legacy substrate but not an A3 implementation. The highest-risk blockers are GT geometry leakage in preprocessing/R3, text-only Analyst, absent versioned manifest, combined Judge, and the old multi-round loop. Per the dependency order, the next allowed stage is **A3-01 manifest/provenance infrastructure**; no model run should occur before A3-08.

---

## 5. A3-01：Manifest / provenance infrastructure

**日期：** 2026-07-10  
**HEAD：** `fd81922a88fba111700258f768652cd2081523d2`（dirty worktree）  
**性質：** infrastructure only；0 API calls、0 paid tokens、未建立正式 A3 run。

### 5.1 新增 contracts

- `metagpt/ext/agentlayout/a3_config.py`
  - `a3.run-config.v1`；
  - 明確記錄 architecture、model stages、P-Full、R3、L0/L1-Gated、Judge、dataset split、seed、image normalization、schema versions 與 price-table version；
  - `extra=forbid`，L0/L1 會驗證必要 model stages；
  - model call contract 可記錄 exact model、reasoning effort、temperature、max tokens、image detail 與 structured-output mode。
- `metagpt/ext/agentlayout/run_manifest.py`
  - `a3.run-manifest.v1`、`a3.sample-record.v1`、`a3.error-record.v1`；
  - config/sample ID frozen snapshots 和 canonical SHA-256；
  - run completion、cost、error、prompt hashes、schema versions 欄位；
  - 每個 sample 初始化獨立 `sample_record.json`；
  - JSON Schema 自動落盤。

### 5.2 不可覆寫與 provenance 規則

- run ID 只允許安全字元，run directory 使用 `mkdir(exist_ok=False)`；已存在即失敗；
- artifact 以 temporary file + hard link 原子發布，目的檔存在即拒絕覆寫；
- 初始化中途失敗不刪除 partial directory，run ID 視為已消耗，保留 forensic evidence；
- sample IDs 禁止空值、重複與 path traversal；
- provenance 記錄 UTC、HEAD、branch、tracked binary diff hash、相關 untracked file **content hashes**、Python/runtime/platform 與 `AGENTLAYOUT_*` flags；
- 不讀取或寫入 API key / secret config value；
- watched untracked scope限制在 A3 code/config/sample-ID roots，避免把舊 demo/output cache 混入 manifest。

### 5.3 Zero-cost CLI

新增 `layout_agent/run_a3.py`：

```bash
python layout_agent/run_a3.py plan \
  --config <a3-config.json> \
  --sample-ids <ids.json> \
  --run-id <unique-run-id>

python layout_agent/run_a3.py init \
  --config <a3-config.json> \
  --sample-ids <ids.json> \
  --run-id <unique-run-id>
```

`plan` 只驗證並輸出 `api_calls=0` 的計畫，不建立目錄；`init` 只建立 immutable skeleton，尚未啟動 pipeline。`run` 子命令刻意保留到後續 pipeline stages，避免 A3-01 誤跑 legacy flow。

### 5.4 Tests

新增 `tests/metagpt/ext/agentlayout/test_a3_run_manifest.py`，涵蓋：

- L1 必要 model stages；
- duplicate/unsafe sample IDs；
- artifact overwrite refusal；
- config/IDs/schema/sample-record 落盤；
- duplicate run ID refusal；
- non-Git graceful degradation；
- Git untracked content hash；
- CLI `plan` zero-cost 且不建立 run。

執行：

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --with pytest --with 'pydantic>=2' \
  pytest -q -o addopts='' \
  --confcutdir=tests/metagpt/ext/agentlayout \
  tests/metagpt/ext/agentlayout/test_a3_run_manifest.py
```

結果：`9 passed in 0.69s`。

完整 repo 的預設 pytest bootstrap 在此執行環境缺少 `aiohttp`，且 `pytest.ini` 預設需要 `pytest-cov`；本階段因此以 isolated pure-unit suite 執行，沒有把環境缺依賴誤記為產品測試失敗。

靜態檢查：

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --with ruff ruff check \
  metagpt/ext/agentlayout/a3_config.py \
  metagpt/ext/agentlayout/run_manifest.py \
  layout_agent/run_a3.py \
  tests/metagpt/ext/agentlayout/test_a3_run_manifest.py
```

結果：`All checks passed`。另執行 `py_compile` 與 `git diff --check`，均通過。

### 5.5 邊界與下一步

- 本階段只提供 provenance-bearing config/store；legacy `LayoutPipeline` 尚未接上 A3 config；
- prompt-hash/schema-version 欄位與落盤能力已存在，但 A3 prompts/contracts 尚未在 A3-04..A3-07 freeze，因此目前不製造假的 hashes；
- cost record schema 已存在，實際 per-call usage capture 要在模型呼叫整合時接入；
- 沒有建立 `layout_agent/runs/a3/` 正式 run，避免在 P-Full/R3 尚未完成前產生可被誤認為 A3 的 artifacts。

**A3-01 status: complete。下一階段：A3-02 P-Full input protocol。**
