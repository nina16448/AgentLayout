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
| A3-02 | P-Full input protocol | complete |
| A3-03 | R3 text bitmap normalization 與 leakage tests | complete |
| A3-04 | Analyst MLLM 與 contact sheet | complete |
| A3-05 | Layout Tree contract 更新 | complete |
| A3-06 | L0、Judge-Select 與 Judge-Critic | complete |
| A3-07 | L1-Gated、repair verifier 與 B0/B1 guard | complete |
| A3-08 | N=5 smoke（L0 5/5、L1-Gated 5/5） | complete |
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

---

## 6. A3-02：P-Full input protocol

**日期：** 2026-07-10  
**起始 commit：** `1ec0d05ad8f57017f12c4db90e15ebca4472073e`  
**性質：** deterministic input infrastructure；0 API calls、0 paid tokens。

### 6.1 實作

新增 `metagpt/ext/agentlayout/tools/pfull_preprocessor.py`：

- `a3.pfull-asset-manifest.v1` 與 `pfull.crello.pixel-only-background.v1`；
- 每個 source element 以 `asset_<source_index:04d>` 建立穩定 ID；
- raster 原始 bytes 逐一 snapshot 到新 run 的 sample asset directory，保存 SHA-256、MIME 與原始 raster dimensions；
- text 同時保留 `content` 與可用 bitmap snapshot；
- 所有非 background asset 都標為 `placeable`，不得因 decorative/underlay 或分類困難消失；
- `PFullPreparedInput` 提供未來 A3 Analyst/pipeline 的 stable-ID boundary；
- 缺少／損壞 non-text asset 直接失敗，不做 silent skip；
- output directory 與 manifest 不可覆寫；partial failure 保留 forensic evidence。

`layout_agent/run_a3.py` 新增：

```bash
python layout_agent/run_a3.py prepare-pfull \
  --run-dir layout_agent/runs/a3/<run_id> \
  --crello-root layout_agent/output
```

此命令只處理 initialized run 的 frozen sample IDs。每個 sample 輸出至：

```text
samples/<sample_id>/inputs/pfull/
  asset_manifest.json
  assets/asset_NNNN.<ext>
```

並寫入 run-level `pfull_preparation.json`；任何 sample failure 都另寫 versioned `ErrorRecord` 並使 CLI 非零退出。

### 6.2 GT leakage boundary

Extractor 明確不讀取或輸出：

- `left` / `top` / `width` / `height`；
- x/y/bbox/font size；
- legacy `classifier_signals.area_ratio`；
- legacy `kind=background_candidate`；
- GT-derived underlay regions；
- designer z-order compositing。

舊 `kind` 只用來和 `type_code==1` 共同識別 text（向後相容）；對所有 raster 的 background/image/underlay 分類完全不信任舊 kind/classifier。

### 6.3 Conservative base-background rule

Crello cache 沒有可靠的 explicit background semantic label；舊 `full_canvas` 是用 GT bbox area 判斷，不能沿用。凍結規則如下：

1. 只看原始 raster pixels 與公開 canvas dimensions；
2. raster native size 必須 **恰等於 canvas size**；
3. alpha 必須至少 98% 幾乎全不透明；
4. 多個符合者取最小 source index 作唯一 base background；其他仍是 placeable foreground；
5. 沒有符合者使用 blank/solid base，所有 raster 保持 placeable。

這是刻意偏低 recall 的規則：寧可沒有 background，也不能用 designer placement 把 product/logo/decoration 誤當背景。背景規則若要放寬，必須在 gate 前另立 policy version，不能根據生成結果調整。

### 6.4 Tests

新增 `tests/metagpt/ext/agentlayout/test_pfull_preprocessor.py`，連同 A3-01 tests 執行：

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run \
  --with pytest --with 'pydantic>=2' --with pillow \
  pytest -q -o addopts='' \
  --confcutdir=tests/metagpt/ext/agentlayout \
  tests/metagpt/ext/agentlayout/test_a3_run_manifest.py \
  tests/metagpt/ext/agentlayout/test_pfull_preprocessor.py
```

結果：`17 passed in 1.16s`。

涵蓋：

- 全 foreground coverage 與 stable IDs；
- 不預合成；
- manifest 不含 GT geometry/area；
- 任意改變 GT geometry/kind 不影響 raster classification；
- 多 background candidate 固定 tie-break；
- 無 background fallback；
- missing asset fail-closed；
- output non-overwrite；
- initialized run 的 `prepare-pfull` CLI integration。

Ruff：`All checks passed`。`py_compile` 與 `git diff --check` 通過。

### 6.5 Real cached-sample smoke（zero API）

使用 cached sample `5d9720b1abc8ea6d1c37b8d8`，輸出只寫 `/tmp/a3_pfull_real_smoke_5d9720`：

```text
sample_id=5d9720b1abc8ea6d1c37b8d8
source elements=11
manifest assets=11
placeable foreground=11
background_asset_id=None
```

該 sample 的舊 `asset_00_background.png` 是以 GT bbox `area_ratio=4.7395` 分成 background，但 native raster 為 1024x508、canvas 為 1590x400，不符合新 pixel-only rule，因此正確地保持 placeable，沒有依舊 label 預合成。

### 6.6 邊界與下一步

- P-Full manifest 必須記錄 original raster dimensions 供 provenance，但 text bitmap original size 在 R3 runtime input 中仍是 leakage risk；A3-03 必須正規化後才能交給 Analyst/Mapper，且不得把 manifest 的 text native dimensions帶進 prompt；
- 本階段未改 legacy Crello/SEGA preprocessor；A3 runner 只允許新 `prepare-pfull` path，舊 cache/output 不得成為正式 run artifact；
- 尚未把 P-Full assets 交給現行 text-only Analyst，依正式順序等待 A3-03/A3-04。

**A3-02 status: complete。下一階段：A3-03 R3 normalization、renderer 與 leakage tests。**

---

## 7. A3-03：R3 normalization、renderer 與 leakage tests

**日期：** 2026-07-10  
**起始 commit：** `cd61fc8ff3c4daf696d3b8bcc56f58de4a498033`  
**性質：** deterministic bitmap/renderer infrastructure；0 API calls、0 paid tokens。

### 7.1 Frozen R3 normalization

新增 `metagpt/ext/agentlayout/tools/text_bitmap_normalizer.py`：

- manifest schema：`a3.r3-asset-manifest.v1`；
- normalization policy：`r3.alpha-tight-long-edge.v1`；
- 預設／A3 config frozen values：
  - final long edge：512 px；
  - final transparent padding：8 px；
  - alpha threshold：1；
  - resize filter：Lanczos；
- 對 alpha channel 做 tight crop；
- 先將 tight content 等比例縮放到 `long_edge - 2*padding`，再加固定 final padding，因此輸出 long edge 恰為 512；
- normalized PNG 使用固定 RGBA、PNG compress level 9、無 optimize metadata，並保存 SHA-256；
- 全透明、缺檔、無 content 或沒有 bitmap 的 text asset fail-closed，不退回 re-typeset/plain text；
- R3 output directory 與 manifest 不可覆寫。

R3 runtime asset contract保留：stable `asset_id`、文字 `content`、normalized bitmap ref、normalized width/height/aspect、hash。它刻意不包含 original text width/height、GT bbox 或 font size。原 bitmap hash只作 provenance，不提供可反推字級的尺寸。

### 7.2 Renderer contract

`renderer.py` 對 `_r3_text.png` 使用獨立 contain path：

1. Mapper 仍預測 final bbox；
2. renderer 以單一 scale factor 將 bitmap 放入 bbox；
3. 水平／垂直置中；
4. 不分別拉伸 width/height；
5. 不套用 generic image 的 `MAX_UPSCALE=2`，因 512px 是 protocol normalization，不是 natural size；
6. legacy generic image/text renderer 行為維持不變。

### 7.3 Prompt leakage removal

`GenerateLayout._format_element_list` 現在先辨識 R3 bitmap：

- 只提供 fixed normalized bitmap 的 aspect ratio；
- 明確要求 Mapper 依 design context 自行決定 final bbox scale/position；
- 不提供 512px normalized dimensions；
- 不提供 original/natural dimensions；
- 不再套用 legacy `0.8x-1.2x natural size` 指令。

`JudgeAesthetic` reject observer 也排除 R3 bitmap 的 legacy natural-size報告。R3 suffix check 位於一般 `_text.png` branch 之前，避免 `_r3_text.png` 被 suffix overlap 誤導到舊路徑。

### 7.4 CLI integration

`layout_agent/run_a3.py` 新增：

```bash
python layout_agent/run_a3.py normalize-r3 \
  --run-dir layout_agent/runs/a3/<run_id>
```

命令從 immutable run config 讀取 normalization values，逐 sample 讀取 P-Full manifest，輸出：

```text
samples/<sample_id>/inputs/r3/
  r3_asset_manifest.json
  assets/asset_NNNN_r3_text.png
```

run level 寫 `r3_normalization.json`；所有失敗寫 versioned `ErrorRecord`，任何 sample 失敗皆非零退出。

### 7.5 Tests

新增 `tests/metagpt/ext/agentlayout/test_text_bitmap_normalizer.py`，連同 A3-01/A3-02 suite：

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run \
  --with pytest --with 'pydantic>=2' --with pillow \
  pytest -q -o addopts='' \
  --confcutdir=tests/metagpt/ext/agentlayout \
  tests/metagpt/ext/agentlayout/test_a3_run_manifest.py \
  tests/metagpt/ext/agentlayout/test_pfull_preprocessor.py \
  tests/metagpt/ext/agentlayout/test_text_bitmap_normalizer.py
```

結果：`26 passed in 1.46s`。

涵蓋：

- alpha-tight bbox、fixed padding 與 exact long edge；
- 相同 glyph 放在不同原始 canvas/offset 時產生 byte-identical normalized PNG；
- R3 runtime manifest 不含 original dimensions/GT geometry/font size；
- text-only fallback 拒絕；
- prompt descriptor 只有 aspect ratio，沒有 pixel/natural size；
- Generator/observer 確實繞過 legacy natural-size branch；
- contain size 的橫／直向 aspect preservation；
- end-to-end renderer 將 4:1 bitmap 放進 1:1 bbox 後仍為 4:1；
- transparent bitmap fail-closed；
- initialized run 的 `normalize-r3` CLI integration。

Ruff：`All checks passed`。`py_compile` 通過。順帶移除 `generate_layout.py` 既有 unused `Any` import 與三個無插值 f-string，行為不變。

### 7.6 Real cached-sample smoke（zero API）

使用含 cached text bitmap 的 sample `5888cbf695a7a863ddcc214f`，輸出只寫 `/tmp`：

```text
P-Full/R3 assets：4
R3 text bitmaps：2
asset_0002：512x92，aspect=5.5652
asset_0003：512x245，aspect=2.0898
```

兩個 text asset 都有相同 frozen long edge，但依自身 glyph 保留不同 aspect ratio；未沿用 meta.json 的 GT bbox/字級作 final scale。

### 7.7 邊界與下一步

- P-Full manifest 仍保留 original raster dimensions 作 input provenance；A3 runtime/Analyst 必須只讀 R3 manifest，不能把 P-Full text dimensions 放入 prompt；
- 本階段只為 R3 suffix接入現有 renderer/prompt，尚未把 stable-ID R3 contact sheet 交給 Analyst；
- Mapper 預測 bbox 後的 bitmap contain 已凍結，但 typography/readability效果只能在 N=5 smoke 後評估，不能由 unit test 宣稱美學提升；
- 沒有修改 legacy R2 artifacts 或重新評估舊結果。

**A3-03 status: complete。下一階段：A3-04 Analyst MLLM、background overview 與 asset contact sheet。**

---

## 8. A3-04：Analyst MLLM、background overview 與 asset contact sheet

**日期：** 2026-07-10  
**起始 commit：** `59f9dcbeca90fdcdc9a8599a3589d08cff6d55f2`  
**性質：** MLLM contract + deterministic visual input infrastructure；本階段 0 API calls、0 paid tokens。

### 8.1 Vision packet

新增 `metagpt/ext/agentlayout/tools/analyst_vision.py`：

- vision packet：`a3.analyst-vision-packet.v1`；
- Analyst output：`a3.analyst-output.v1`；
- 第一張 attachment 永遠是 background overview；
- 後續 attachment 是 foreground contact-sheet pages；
- 每頁最多 20 個 assets、4 columns；超量自動分頁；
- 每個 cell 使用相同 240x220 frame 與 208x158 thumbnail box；
- bitmap 只做 aspect-preserving contain，原始相對 scale 不進 contact sheet；
- checkerboard 顯示 alpha，stable `asset_id` 與 `IMAGE` / `TEXT BITMAP` 標籤固定放在 cell 下方；
- foreground 順序完全沿用 R3 manifest stable-ID order；
- background overview 只讀唯一 base background，不 composite foreground；沒有 base 時輸出明確 `NO BASE BACKGROUND — blank canvas` overview；
- prompt 與所有 images 可離線落盤，prompt 保存 SHA-256。

Prompt只提供：asset ID、media type、text content、normalized bitmap aspect ratio。它不提供 asset path、original/native dimensions、GT x/y/bbox/font size。Contact sheet uniform cells 也不提供 designer placement/relative scale。

### 8.2 Analyst semantic output contract

`A3AnalystOutput` 包含：

- background summary；
- design intent；
- style keywords / language；
- 每個 foreground 的 stable asset ID、semantic type、description、semantic role、key message 與 semantic constraints。

驗證規則：

- `extra=forbid`，不能偷偷輸出 geometry/path；
- 每個 P-Full foreground ID 必須 exactly once；
- 不得 invent/rename/drop ID；
- placeable foreground 不得被 Analyst 重新分類成 background；
- `analyst_output_to_design_spec` 只從 immutable R3 manifest 注入 asset refs/canvas，LLM 無權回寫路徑；
- 所有 R3 text bitmap 仍以 `visual_type=image` 進 renderer，同時保留 `content` 供語意理解。

### 8.3 Vision-required MLLM Action

新增 `metagpt/ext/agentlayout/actions/analyze_a3.py`：

- `AnalyzeA3Brief` 必須 `support_image_input()`；不支援即失敗，禁止 text-only fallback；
- runtime model 必須等於 manifest/config 傳入的 exact expected snapshot；alias 或不同 model 直接失敗；
- 每次呼叫同時附 background overview 與所有 contact pages；
- schema/coverage parse 最多 3 次；
- retry 會把前次 validation error 加入原 prompt，屬 reliability retry，不是 aesthetic refinement；
- 可將真正送出的 prompt/images 先落盤，便於 prompt hash 與 artifact audit；
- fenced JSON 或周圍 prose 可防禦性解析，最後仍由 Pydantic schema與 coverage驗證。

本階段沒有實際呼叫模型；Action 已就緒，正式 MLLM call 要等 A3-08 N=5 smoke。

### 8.4 CLI integration

`layout_agent/run_a3.py` 新增：

```bash
python layout_agent/run_a3.py prepare-analyst-vision \
  --run-dir layout_agent/runs/a3/<run_id>
```

逐 sample 讀 immutable P-Full + R3 manifests，輸出：

```text
samples/<sample_id>/inputs/analyst_vision/
  background_overview.png
  asset_contact_sheet_01.png
  asset_contact_sheet_NN.png
  analyst_request.json
```

run level 寫 `analyst_vision_preparation.json`；錯誤使用 versioned `ErrorRecord`，任何 sample failure 非零退出。

### 8.5 Tests

新增 `tests/metagpt/ext/agentlayout/test_analyst_vision.py`，並擴充 A3 CLI integration test。執行 A3-01～04 suite：

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run \
  --with pytest --with 'pydantic>=2' --with pillow \
  pytest -q -o addopts='' \
  --confcutdir=tests/metagpt/ext/agentlayout \
  tests/metagpt/ext/agentlayout/test_a3_run_manifest.py \
  tests/metagpt/ext/agentlayout/test_pfull_preprocessor.py \
  tests/metagpt/ext/agentlayout/test_text_bitmap_normalizer.py \
  tests/metagpt/ext/agentlayout/test_analyst_vision.py
```

結果：`35 passed in 2.70s`。

涵蓋：

- overview 只有 background、沒有 foreground pixels；
- no-background 明確 blank overview；
- 23 foreground 分成兩頁且 stable order 不變；
- prompt 含 content/aspect 但無 path/GT geometry/native size；
- packet prompt hash、artifacts 與 non-overwrite；
- exact stable-ID coverage、duplicate/omission/invention防護；
- foreground 禁止重分類為 background；
- Analyst result -> DesignSpec 的 ID/ref 注入；
- fenced JSON parsing；
- Action source強制 vision、exact model、images與error-aware retry；
- initialized run 依序完成 `prepare-pfull -> normalize-r3 -> prepare-analyst-vision`。

Ruff：`All checks passed`。`py_compile` 與 scoped `git diff --check` 通過。

### 8.6 Real cached-sample visual smoke（zero API）

使用 `5888cbf695a7a863ddcc214f` 的 R3 manifest，輸出只寫 `/tmp/a3_analyst_vision_smoke_5888cbf6`：

```text
foreground assets：4
attachments：2
  1. background_overview.png
  2. asset_contact_sheet_01.png
prompt_sha256：a54493fc56a1a18f7c2ad9659f51b01d06d755956ddc60899150601ec98273ef
```

人工檢視結果：

- overview 正確顯示 blank canvas，未混入 Eiffel/image/text foreground；
- contact sheet 有 `asset_0000`～`asset_0003` 四個 cell；
- Eiffel raster、透明線框圖與兩個 R3 text bitmap 均完整可見；
- ID/media labels 清楚，text bitmap aspect preserved；
- 未呈現 designer GT layout 或元素相對位置。

### 8.7 邊界與下一步

- A3 Analyst 已能看 background 與所有 foreground，但本階段沒有付費執行，不能宣稱 vision 改善 semantic accuracy；該因果問題留給 N=20 Gate A；
- output semantic roles/descriptions 已準備給 Planner，A3-05 必須建立 versioned Layout Tree role/relation/confidence contract；
- `DesignSpec` 是 legacy-compatible rendering boundary，A3 Planner 應以 `A3AnalystOutput` 為主要 semantic input，而不是退回固定 `semantic_relevance=0.5`；
- image detail／reasoning effort／actual model settings必須由 A3 run caller按 A3 config記錄，若 provider不支援要 fail或明記，不得默默替代。

**A3-04 status: complete。下一階段：A3-05 Layout Tree versioned contract。**

---

## 9. A3-05：Layout Tree versioned contract

**日期：** 2026-07-10  
**起始 commit：** `d6f2f84bb66eeaf30a63dc9fd7e5c06a6d104dba`  
**性質：** structured semantic contract + Planner Action；0 API calls、0 paid tokens。

### 9.1 Versioned Layout Tree

新增 `metagpt/ext/agentlayout/layout_tree_v3.py`：

- schema：`a3.layout-tree.v1`；
- request：`a3.layout-tree-request.v1`；
- tree source：`predicted` 或 `human_oracle`；
- 每個 foreground asset 對應一個 normalized node；
- 每個 node 必須包含：
  - stable `asset_id`；
  - `semantic_type`；
  - `semantic_role`；
  - `group_id` / `group_label`；
  - `parent_id`；
  - `relation_to_parent`；
  - `ordering_priority`；
  - `confidence`（0..1）。
- relation vocabulary：root、contains、supports、qualifies、identifies、calls-to-action、decorates、sequence-after、peer；
- groups 另有 versioned records：group ID/label、完整 member IDs、ordering priority、confidence。

這個 normalized node/edge contract 同時供 predicted tree、human reference tree、tree metrics 與 Mapper ablation 使用，避免各 arm 自訂不相容 schema。

### 9.2 Structural validation

Pydantic + cross-object validation涵蓋：

- asset IDs unique；
- parent 必須是 root 或現存 asset；
- self-parent 禁止；
- root/non-root relation一致；
- DFS parent-chain cycle detection；
- group IDs unique；
- group members 必須存在且不得重複；
- 每個 asset 必須 exactly one group；
- node `group_id/group_label` 必須和 group record一致；
- group/node confidence 範圍；
- tree 必須 exactly cover Analyst foreground IDs。

Predicted T2 tree 額外必須忠實保留 Analyst 的 semantic type 與 semantic role；Planner只能建立 grouping/edges/order/confidence，不能靜默改寫 Analyst semantics。

Human T3 oracle 只要求相同 stable-ID coverage，不被迫沿用 Analyst predicted type/role。這是必要的因果邊界：oracle 必須能糾正 Analyst，否則 T3 無法定位 tree-inference bottleneck。

### 9.3 Planner Action

新增 `metagpt/ext/agentlayout/actions/plan_assets_a3.py`：

- `PlanAssetsA3` 只讀 `A3AnalystOutput`，不看 coordinates/layout；
- exact runtime model 必須符合 expected snapshot；
- structured schema / semantic coverage 最多 retry 3 次；
- retry 帶前次 validation error，屬 reliability retry；
- Planner 是 text/structured call，不附 images，避免和 Analyst vision變因混合；
- prompt禁止 geometry、bbox、size、font size、z-index與asset paths；
- request prompt + SHA-256、逐 attempt raw response、final tree都可 write-once落盤；
- artifacts directory不可覆寫。

### 9.4 T0/T1/T2/T3 adapters

新增 typed `TreeCondition`：

- `T0`：只有 asset IDs，禁止 roles/tree；
- `T1`：asset IDs + flat semantic type/role，禁止 groups/edges；
- `T2`：必須是 `source=predicted` 的完整 tree，並驗證 Analyst semantic fidelity；
- `T3`：必須是 `source=human_oracle` 的完整 tree，只共享 stable IDs，可糾正 Analyst semantics。

四個 arms 均保持相同 asset ID/order；後續 Mapper adapter只能改 tree information，不得改 dataset、renderer、loop或candidate budget。

### 9.5 Tests

新增 `tests/metagpt/ext/agentlayout/test_layout_tree_v3.py`，執行 A3-01～05 suite：

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run \
  --with pytest --with 'pydantic>=2' --with pillow \
  pytest -q -o addopts='' \
  --confcutdir=tests/metagpt/ext/agentlayout \
  tests/metagpt/ext/agentlayout/test_a3_run_manifest.py \
  tests/metagpt/ext/agentlayout/test_pfull_preprocessor.py \
  tests/metagpt/ext/agentlayout/test_text_bitmap_normalizer.py \
  tests/metagpt/ext/agentlayout/test_analyst_vision.py \
  tests/metagpt/ext/agentlayout/test_layout_tree_v3.py
```

結果：`49 passed in 2.50s`。

涵蓋：

- 完整 role/group/edge/order/confidence contract；
- missing parent、cycle、self/root relation；
- exact/non-overlapping group partition；
- group label consistency；
- confidence bounds；
- Analyst ID/type/role fidelity；
- versioned prompt/hash 與無 geometry/path input；
- fenced JSON parsing；
- request artifact non-overwrite；
- T0/T1/T2/T3 payload isolation；
- wrong predicted/oracle source拒絕；
- T3 可糾正 Analyst semantics但不可改 asset coverage；
- Planner exact-model、error-aware retry與no-image wiring。

Ruff：`All checks passed`。`py_compile` 與 scoped `git diff --check` 通過。

### 9.6 邊界與下一步

- 本階段沒有實際呼叫 Planner，不能宣稱 predicted tree accuracy；human reference annotation與Gate B仍是必要證據；
- normalized contract 已能計算 same-group pairs與parent-child edges，但正式 SGC/TLC/PCA 必須使用同一份 human reference tree評估所有 arms；
- A3-06 應將 Coordinate Mapper 的 T0/T1/T2/T3 input adapter與 Judge-Select/Critic分離接到 canonical A3 pipeline；
- legacy `LayoutTreeNode(id/children)` 不升級為 A3證據，也不應由 adapter悄悄轉回舊 self-consistency protocol。

**A3-05 status: complete。下一階段：A3-06 L0、Judge-Select 與 Judge-Critic 分離。**

---

## 10. A3-06：L0、Judge-Select 與 Judge-Critic 分離

**日期：** 2026-07-10  
**起始 commit：** `25ed3716e1aecdada791e3767980f3f821c2028b`  
**性質：** canonical orchestration + Judge 分離 contract；0 API calls、0 paid tokens、未執行任何 N=5/N=20/N=100。

### 10.1 Judge-Select（獨立 schema/prompt/Action/artifact）

新增 `metagpt/ext/agentlayout/tools/judge_select.py`：

- result schema：`a3.judge-select-result.v1`；request schema：`a3.judge-select-request.v1`；
- `JudgeSelectResult` 只有 `ranking`（恰 3 個、不重複）與 `selected_candidate_id`（必須等於 ranking[0]）；
- `extra=forbid`：score、total、verdict、feedback、suggestions 等欄位一律 ValidationError，critique 在結構上不可表示，而不是只靠 prompt 禁止；
- 沒有 ACCEPT/REJECT、沒有任何 acceptance threshold；每次呼叫必定選出一個 B0；
- prompt 同時看 3 個 R0 renders（附件順序＝候選清單順序）並附 per-candidate deterministic QC 摘要作 structured context；明文禁止輸出 critique/建議/分數；
- `validate_selection` 強制 ranking 是提交候選的 exact permutation；
- request 保存 prompt、prompt SHA-256、candidate IDs、3 個 render refs 與各自 SHA-256；`save_judge_select_request` write-once。

新增 `metagpt/ext/agentlayout/actions/judge_select_a3.py`（`JudgeSelectA3`）：

- 必須 `support_image_input()`，禁止 text-only fallback；
- runtime model 必須 exact match expected snapshot，alias/替代模型直接失敗；
- 強制恰好 3 個 rendered candidates，並以 base64 附上 3 張 render；
- schema/permutation parse 最多 retry 3 次，retry 把前次 validation error 附回原 prompt（reliability retry，不是 aesthetic refinement）；
- artifacts：`judge_select_request.json`、逐 attempt `attempt_NN_response.txt`（`open("x")`）、`judge_select_result.json`，全部 write-once。

### 10.2 Judge-Critic（獨立 schema/prompt/Action/artifact）

新增 `metagpt/ext/agentlayout/tools/judge_critic.py`：

- result schema：`a3.judge-critic-result.v1`；request schema：`a3.judge-critic-request.v1`；
- `JudgeCriticResult` 只有 `issues`（0–2 個）；沒有 overall score、ranking、verdict 欄位；空 issues list 是合法結果；
- 每個 `ActionableIssue` 必須有：`target_asset_ids`（≥1、不可重複）、closed `issue_type`、`observation`、`desired_change`；
- closed issue-type enum 直接取自 new_plam.md §4.5 repair gate 允許清單：overlap / clipping / out_of_bounds / misalignment / spacing / lockup / text_too_small / illegible_text / poor_contrast / text_on_busy_region / hierarchy_error / tree_inconsistency；
- 「不夠漂亮」「缺少創意」不在 enum 內，parse 階段即 ValidationError——模糊意見在結構上不可成為 actionable issue；
- `validate_critic_targets` 強制 target 必須是版面中實際存在的 asset ID；
- request 保存 prompt、prompt SHA-256、B0 candidate ID、B0 render ref 與 SHA-256、known asset IDs；write-once。

新增 `metagpt/ext/agentlayout/actions/judge_critic_a3.py`（`JudgeCriticA3`）：

- 只附一張圖：B0 render；prompt 明示「selection 已結束，禁止 re-rank/re-select」；
- vision-required、exact model match、error-aware retry 3 次，artifacts write-once，同 Select 模式；
- 本階段只建立 gate-ready contract；真正消費 issues 的一次 targeted repair 在 A3-07。

### 10.3 Canonical L0 orchestrator

新增 `metagpt/ext/agentlayout/a3_pipeline.py`（`A3L0Pipeline`）：

```text
Analyst (once, frozen)
  -> Asset Planner (once, frozen; 只有 T2 呼叫)
  -> Composition Director (恰 3 concepts)
  -> Coordinate Mapper (每 concept 一個 candidate)
  -> deterministic QC (逐 candidate 記錄，不過濾、不丟棄)
  -> Judge-Select 選 B0
  -> unconditional stop
```

- pipeline version：`a3.l0-pipeline.v1`；R0 bundle：`a3.r0-bundle.v1`；candidate policy：`a3.l0-candidate-policy.v1`；
- 不走 legacy `LayoutPipeline`：新 orchestrator 原始碼中不存在 ACCEPT/REJECT、threshold、consecutive accepts、max_total_rounds、issue ledger、polish、Analyst reroute 或任何 while loop（有 source-level test 鎖定）；
- stage 全部以注入 callables 提供，離線 fake Actions 即可驗證 orchestration contract，0 API；
- L1-Gated config 直接 `NotImplementedError`（明確標注 A3-07），不會靜默把 L1 當 L0 跑；
- **exactly-three contract**：Director 不是 3 個 concepts → versioned `ConceptCountMismatch` ErrorRecord 落盤並 fail-closed；任一 slot 的 Mapper/render 失敗記入該 slot，完成數 <3 → versioned `CandidateShortfall` ErrorRecord（含逐 slot 失敗原因）落盤、不呼叫 Judge、不降級；
- **all-QC-fail 不降級**：3 個候選帶著明確 `qc_passed=false` 標記進 Judge-Select，結果標 `degradations=["all_qc_failed"]`，永遠不會變成未標記的正式候選；
- **tree ablation boundary**：T2 呼叫 Planner 一次後 freeze；T0/T1/T3 完全不呼叫 Planner；T0/T1 的 `TreeCondition` 不含 tree（T1 只有 flat roles），三個 slot 共用同一個 condition 物件、budget/protocol 一致；T3 必須外部提供 human oracle tree；
- artifacts（write-once）：`analyst_output.json`、`tree_condition.json`、`r0_bundle.json`（含每 slot render SHA-256 與 QC 結果）、`judge_select_result.json`、`l0_result.json`、`errors/error_NNNN.json`；artifacts 目錄 `mkdir(exist_ok=False)`，同一目錄不可重用。

### 10.4 Selection 與 critique 是兩次獨立呼叫

- 兩個 result schema 除 `schema_version` 外欄位完全 disjoint（select 無 `issues`；critic 無 `ranking`/`selected_candidate_id`）；
- 兩個 prompt 各自獨立建構、SHA-256 不同、角色名稱不同（Judge-Select / Judge-Critic）；
- 兩個 Action 各自獨立 artifacts 目錄與 request/response 檔案；
- L0 pipeline 只呼叫 Judge-Select 一次，orchestrator 沒有任何 critic hook（test 以 call counter + `hasattr` 驗證）；critique 只能是對 B0 的第二次獨立呼叫。

### 10.5 Tests

新增：

- `tests/metagpt/ext/agentlayout/test_judge_select_a3.py`（9 tests）；
- `tests/metagpt/ext/agentlayout/test_judge_critic_a3.py`（11 tests）；
- `tests/metagpt/ext/agentlayout/test_a3_l0_pipeline.py`（10 tests）。

執行 A3-01～06 全套：

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
  tests/metagpt/ext/agentlayout/test_a3_l0_pipeline.py
```

結果：`79 passed in 2.88s`（基準 49 + 新增 30）。

涵蓋：

- selection schema 純 ranking、無 score/verdict/critique 欄位、extra 欄位拒絕；
- ranking 唯一性、selected=ranking[0]、exact permutation；
- select prompt 無 ACCEPT/REJECT/threshold/35、恰 3 候選強制；
- critic 最多 2 issues、模糊 issue type 結構性拒絕、target/desired_change 必填、
  targets 必須存在、空 issues 合法、無 overall score 欄位；
- 兩個 contract 欄位 disjoint、prompt hash 不同、pipeline 只呼叫 select 一次；
- L0 happy path：Analyst/Planner 各 1 次、Mapper 3 次、Judge 1 次、B0 選定後
  unconditional stop；
- Director ≠3 concepts 與 candidate shortfall 的 versioned fail-closed 紀錄，
  Judge 不被呼叫；
- all-QC-fail 標記 degradation、仍全數帶標記送 Judge；
- 非法 judge permutation 拒絕；
- T0/T1 不呼叫 Planner、無 tree、三 slot 同一 condition；T3 需外部 oracle；
- artifacts write-once、目錄不可重用；L1-Gated 明確 defer A3-07；
- a3_pipeline.py source 無任何 legacy loop constructs。

Ruff：`All checks passed`（新檔）。`py_compile` 與 `git diff --check` 通過。

### 10.6 成本

- API calls：0；paid tokens：0；所有驗證皆 fake Actions + 合成 PNG。

### 10.7 邊界與下一步

- 本階段未實際呼叫任何 MLLM，不能宣稱 Judge-Select 的選擇品質或 Critic 的 issue 精準度；那是 A3-08 N=5 smoke 與 A3-09C Gate C 的問題；
- `A3L0Pipeline` 的 Director/Mapper/QC stage 目前以 callables 注入；A3-07/A3-08 需把真實 Actions（`ComposeConcept`/`GenerateLayout` 的 A3 版與 `quality_checker`）綁定到此 boundary，並接上 `run_a3.py` 的 `run` 子命令與 per-call usage/cost capture；
- Judge-Critic 已是 gate-ready contract，但 repair gate、targeted repair routing、verifier 與 B0/B1 best-so-far guard 全部屬於 A3-07；
- concept 的 spatial-diversity 驗證仍未實作（audit §4.9 既知 partial），不在本階段範圍。

**A3-06 status: complete。下一階段：A3-07 L1-Gated、targeted repair、verifier 與 B0/B1 guard——入口是把 `JudgeCriticA3` 的 actionable issues 接上 deterministic gate 與單次修復路由，並在 `A3L0Pipeline` 旁建立 L1 variant（維持 unconditional stop 與 best-so-far guard）。**

---

## 11. A3-07：L1-Gated 單次修復、verifier 與 B0/B1 guard

**日期：** 2026-07-10  
**起始 commit：** `8fa67aa4981ed1d8bb45b3466b66496993a3a447`  
**性質：** gated single-revision orchestration；0 API calls、0 paid tokens、未執行任何 N=5/N=20/N=100。

### 11.1 Repair gate 與 routing（tools/repair_gate.py）

- gate policy version：`a3.l1-repair-gate.v1`；guard policy version：`a3.b0b1-guard.v1`；
- `evaluate_repair_gate(critic, known_asset_ids)` 產生 versioned `RepairDecision`：
  - critic 0 issues → `no_actionable_issue`（model validator 禁止此 outcome 攜帶 route/instruction）；
  - 有 issues → `one_targeted_repair`，恰一個 route、一份 revision instruction、KEEP constraints（= 非 target 的全部 asset IDs）；
  - target 不存在 → `validate_critic_targets` 直接拒絕；
- **routing table 完整覆蓋 12 個 closed issue types**（test 鎖定 `set(ISSUE_ROUTING)==set(CriticIssueType)`）：
  - bbox/spacing/alignment/scale/contrast 類 10 種 → `coordinate_mapper`；
  - `hierarchy_error`、`tree_inconsistency` → `director_then_mapper`；混合 issues 時 Director 路由優先；
  - semantic role / tree 推論錯誤依 new_plam §4.6 不進 runtime loop——因為 Judge-Critic 的 closed enum 根本無法表示這類 issue，結構上不可路由；
- revision instruction 明文：「exactly ONE revision pass」、逐 issue 的 targets/observation/desired change、KEEP 清單、「semantic roles 與 Layout Tree frozen」；單輪 KEEP constraints 存在但無跨輪 ledger。

### 11.2 Deterministic verifier 與 B0/B1 guard

- `IssueVerification`：每個 gated issue 一筆 `issue_index`/`improved`/`evidence`；
- `check_b1_against_b0` 實作 new_plam §4.7 條件 1–3，全部 fail-closed：
  1. verifier 覆蓋必須恰等於 issue 集合（缺漏＝`verifier_coverage_mismatch`）且每項 `improved`；
  2. B1 不得新增 hard violation（`set(b1)-set(b0)` 非空即拒）；
  3. completeness 不得下降；B0 有訊號而 B1 缺訊號也拒（`completeness_signal_missing`）；
- `resolve_winner` 實作條件 4：pairwise internal selection **只能把 B1 降回 B0，不能救回未通過 deterministic check 的 B1**；deterministic check 未過時 pairwise 完全不執行；
- `B0B1GuardResult` versioned 落盤：deterministic verdict、全部 reasons、pairwise 是否使用、winner。

### 11.3 A3L1GatedPipeline（a3_pipeline_l1.py）

- pipeline version：`a3.l1-pipeline.v1`；`LOOP="L1-Gated"`；
- 繼承 `A3L0Pipeline` 並共用同一 `_run_r0_phase`（本階段將 L0 的 R0+Select 流程抽成 `R0PhaseOutcome`，`A3L0Result` 行為不變；`QCVerdict`/`R0SlotRecord` 增加 optional `completeness`/`qc_completeness` 供 guard 用）；
- L0/L1 class 都以 `LOOP` classvar 驗證 config：loop 不符即 `ValueError`（取代 A3-06 的 NotImplementedError，deferred 已兌現）；
- 完整流程：R0 phase → `judge_critic(B0, known_asset_ids)` 一次 → gate →
  - 無 issue：直接輸出 B0，`repair_attempted=False`，b1/guard/verifications 全 None；
  - 有 issue：`_revise_once` 恰一次（repair callable → render → QC）→ verifier → guard → keep B0 or B1 → unconditional stop；
- **repair 失敗不毀 sample**：B0 已存在，寫 versioned `RepairExecutionFailed` ErrorRecord、B1 記為 `status=failed`、guard 直接判 B0；
- revision 消費**同一個 frozen tree condition**（test 以 object identity 鎖定）；Analyst/Planner/Director/R0 Mapper 呼叫數在 L1 全程維持 1/1/1/3；
- artifacts（write-once）：L0 全套之外新增 `judge_critic_result.json`、`repair_decision.json`、`b1_candidate.json`、`issue_verifications.json`、`b0b1_guard.json`、`l1_result.json`；不寫 `l0_result.json`；
- source-level test 鎖定 a3_pipeline_l1.py 無 ACCEPT/REJECT/threshold/consecutive/max_total_rounds/ledger/polish/reroute/while。

### 11.4 Tests

新增：

- `tests/metagpt/ext/agentlayout/test_a3_repair_gate.py`（11 tests）；
- `tests/metagpt/ext/agentlayout/test_a3_l1_pipeline.py`（12 tests）；
- 更新 `test_a3_l0_pipeline.py` 的 deferred-L1 測試為 loop-mismatch 測試。

執行 A3-01～07 全套（10 個測試檔）：

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
  tests/metagpt/ext/agentlayout/test_a3_l1_pipeline.py
```

結果：`102 passed in 3.11s`（A3-06 基準 79 + 新增 23）。

涵蓋：

- routing table 恰覆蓋 12 個 closed types、mapper/director 路由與混合 dominance；
- no-issue outcome 無 route/instruction；decision model outcome consistency；
- unknown target 拒絕；KEEP constraints 與 revision instruction 內容；
- guard：全通過、not-improved、coverage gap、新 hard violation、completeness 下降、
  completeness 訊號缺失各自拒絕；pairwise 只能 demote 不能 rescue；
- L1 happy path 選 B1、unimproved/violation/completeness 各自守回 B0；
- 上游 stage frozen（Analyst/Planner/Director 各 1、Mapper 3、critic 1、repair ≤1）；
- repair 失敗 fallback B0 + ErrorRecord；invalid critic targets 拒絕；
- L1 artifacts write-once、目錄不可重用；L0/L1 loop-config 互斥；
- 兩個 pipeline source 都無 legacy loop constructs。

Ruff：`All checks passed`。`py_compile` 與 `git diff --check` 通過。

### 11.5 成本

- API calls：0；paid tokens：0；全部 fake stage callables + 合成 PNG。

### 11.6 邊界與下一步

- pairwise internal selection 的實際 MLLM prompt/Action 尚未建立（L1 允許 `pairwise_select=None` 跳過）；若 A3-08 要用 pairwise，需另立獨立 request/artifact contract，同 Select/Critic 模式；
- verifier 目前是注入 callable；A3-08 前應把 `feedback_verifier.py` 的幾何 predicates 適配到 `IssueVerification`（issue type → deterministic check 的映射要 versioned）；
- repair callable 尚未綁定真實 `GenerateLayout`/`ComposeConcept` A3 版；`director_then_mapper` 路由的實際兩段呼叫留待 stage binding；
- L1 對品質的因果效果完全未測——那是 A3-09C Gate C 的問題；若 Gate C 未過，最終配置退回 L0。

**A3-07 status: complete。下一階段：A3-08 N=5 smoke——入口是把真實 Actions（AnalyzeA3Brief/PlanAssetsA3/Director/Mapper A3 版/JudgeSelectA3/JudgeCriticA3）綁定到 A3L0Pipeline/A3L1GatedPipeline 的 stage boundary，接上 run_a3.py `run` 子命令與 per-call usage/cost capture，凍結 N=5 sample IDs 與 config 後才允許第一次付費呼叫。**

---

## 12. A3-08a：Director/Mapper A3 Actions 與 deterministic issue verifier

**日期：** 2026-07-10  
**起始 commit：** `24ffb8bea2fa7c7c37c1a3272940082ea8174779`  
**性質：** N=5 smoke 前置的 stage contracts；0 API calls、0 paid tokens。

### 12.1 缺口盤點

A3-07 結束時 pipeline 的 Director/Mapper/verifier 仍是注入 callables；repo 中只有 legacy `ComposeConcept`/`GenerateLayout`（吃 DesignSpec/舊 LayoutTree，audit §4.2 判 partial/conflicting），沒有消費 `A3AnalystOutput`＋`TreeCondition` 的 A3 版。本階段補齊三個 contract。

### 12.2 Tree condition prompt boundary

`layout_tree_v3.py` 新增 `condition_prompt_payload(condition)`：T0 只輸出 asset IDs、T1 加 flat roles、T2/T3 加完整 tree JSON——Director/Mapper 共用同一 serializer，四個 ablation arms 在 prompt 層面**只差 tree 資訊**（test 鎖定 payload key set）。

### 12.3 A3 Composition Director

- `tools/director_contract.py`：`a3.concept-set.v1`（恰 3 個 `CompositionConcept`、名稱必須 distinct）＋`a3.director-request.v1`（prompt hash＋tree arm）；`validate_concepts_against_assets` 強制 focal_element 是已知 asset ID；prompt 要求三個概念空間上明顯不同、禁止輸出座標/bbox/字級/路徑；
- `actions/compose_concept_a3.py`（`ComposeConceptA3`）：vision-required（附 background overview）、exact model match、error-aware retry ×3、request/attempt/concept_set artifacts write-once。

### 12.4 A3 Coordinate Mapper

- `tools/mapper_contract.py`：`a3.mapper-request.v1`（mode=`r0`/`revision`）；輸出沿用 pixel `Candidate` schema；`validate_candidate_coverage` 強制每個 foreground asset 恰一次；
- **R3 leakage 邊界**：prompt 對 bitmap 只給 `bitmap_aspect_ratio`，不給 normalized/original pixel sizes、無 legacy natural-size 指令（test 鎖定）；唯一 pixel 數字是 canvas；
- **revision mode**：同一 contract 承載 L1 單次修復——附 B0 elements 作 editing base＋gate 的 revision instruction，明示「apply ONLY the requested change」；`revision_instruction` 與 `base_elements` 必須成對提供；
- `actions/generate_layout_a3.py`（`GenerateLayoutA3`）：vision-required、exact model、retry ×3、write-once artifacts。

兩個 action 依 repo 慣例拆薄殼：純 contract（schema/prompt/parse/validate）在 tools、`metagpt.actions.Action` 依賴只出現在 action 檔，isolated uv suite 不需拉 tenacity/aiohttp 等重依賴。

### 12.5 Deterministic issue verifier

`tools/issue_verifier.py`（policy `a3.issue-verifier.v1`）把 12 個 closed critic issue types 分兩類（test 鎖定 partition 完整互斥）：

- **STRICT（幾何可嚴格量測）**：overlap（target 交疊面積必須縮小）、clipping/out_of_bounds（出界面積縮小）、text_too_small/illegible_text（最小 target 面積增大）、misalignment（到最近 guide 的距離縮小）；
- **PROXY（需像素/語意，誠實標注 acted-upon proxy）**：spacing/lockup/poor_contrast/text_on_busy_region/hierarchy_error/tree_inconsistency——只驗證所有 target 確實移動/改尺寸；evidence 字串明示 proxy 性質，感知品質由 Gate C 判定，不由 verifier 宣稱；
- target 缺失 fail-closed（improved=False）；輸出 `IssueVerification` 直接餵 A3-07 `check_b1_against_b0`（integration test 驗證）。

### 12.6 Tests

新增 `test_a3_director_mapper.py`（10 tests）與 `test_a3_issue_verifier.py`（10 tests）。全套：

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
  tests/metagpt/ext/agentlayout/test_a3_issue_verifier.py
```

結果：`122 passed in 3.48s`（A3-07 基準 102 + 新增 20）。

Ruff：`All checks passed`。`py_compile` 與 `git diff --check` 通過。

### 12.7 成本

- API calls：0；paid tokens：0。

### 12.8 邊界與下一步（A3-08b）

- Director 的 spatial diversity 仍靠 prompt 要求＋名稱 distinct 驗證，沒有幾何 diversity verifier（audit 既知 partial，非本階段範圍）；
- verifier 的 PROXY 類型只證明「修復有被執行」，不證明感知改善——這個限制已寫進 evidence 字串與本節，論文引用時不得拔高；
- **A3-08b 剩餘工作**：stage binding factory（把 6 個真實 Actions＋renderer/QC 接到 pipeline callables、per-call usage/cost capture）、`run_a3.py run` 子命令、凍結 N=5 sample IDs 與 smoke config；之後的第一次付費呼叫需使用者確認成本後才執行。

**A3-08a status: complete。**

---

## 13. A3-08b：stage binding、run CLI、cost capture 與 smoke freeze

**日期：** 2026-07-11  
**起始 commit：** `39a4e86d46e8dfb657d0105b9e6a1650b2ea978a`  
**性質：** 付費 smoke 前最後一段 zero-API 綁定；0 API calls、0 paid tokens。

### 13.1 Stage binding（a3_stage_binding.py）

`A3StageBinding`（一個 instance 服務一個 sample）把六個真實 Actions＋deterministic renderer/QC 接到 A3L0Pipeline/A3L1GatedPipeline 的 callables：

- analyst → `AnalyzeA3Brief`（R3 manifest＋write-once artifacts dir）；輸出凍結後立刻導出 rendering `DesignSpec`；
- planner → `PlanAssetsA3`；director → `ComposeConceptA3`（canvas 字串＋background overview base64）；
- mapper → `GenerateLayoutA3`，三次 R0 呼叫各自獨立 `mapper_NN` artifacts dir，回傳 `Candidate.model_dump()`；
- renderer → 既有 `render_to_file`（R3 contain path）＋frozen DesignSpec；
- qc → 既有 `check_candidate`，violations 序列化為 `type: detail` 字串、completeness＝非背景元素覆蓋率；Analyst 未跑前 renderer/QC fail-closed；
- judge_select → `JudgeSelectA3`（context 只帶 design_intent）；judge_critic → `JudgeCriticA3`（B0 render only）；
- repair → 同一 `GenerateLayoutA3` 的 revision mode，concept 取 **B0 slot 對應的原 concept**（R0_SLOT_IDS index），帶 gate revision instruction＋B0 elements 作 base；
- verifier → `issue_verifier.verify_issues`（manifest canvas 尺寸）。

**Per-call cost capture**：每個 LLM stage 呼叫記一筆 `a3.stage-call-record.v1`（stage、wall seconds、cost manager 的 token/cost delta；provider 無 cost manager 時誠實記 `usage=null` 而非 0）。`write_call_records` write-once 落盤 `stage_calls.json`。

Binding 對 Action 採 duck-typing，離線測試用 fake actions 驗證 wiring，不需拉 `metagpt.actions` 重依賴鏈。

### 13.2 run CLI（fail-closed 授權）

`run_a3.py` 新增 `run` 子命令：

```bash
python layout_agent/run_a3.py run \
  --run-dir layout_agent/runs/a3/<run_id> \
  --tree-arm T2 \
  [--allow-api-calls]
```

- **無 `--allow-api-calls`：只印 call budget JSON（authorized=false）、exit 2、不建立任何輸出、不 import LLM 機件**；
- 有 flag 才 lazy import 六個 Actions；per-sample 建 binding＋pipeline（L0/L1 依 run config.loop）、失敗寫 versioned ErrorRecord、run level write-once `a3_run_summary.json`；
- call budget（不含 schema retry）：L0+T2＝7 calls/sample；L1-Gated+T2＝9 calls/sample（critic＋至多一次 revision）。

### 13.3 Smoke freeze（config 與 sample IDs）

- `layout_agent/configs/a3_smoke_l0.json`、`a3_smoke_l1_gated.json`：全 stage 固定 `gpt-5.4-mini-2026-03-17`、image_detail=high、seed=42、schema version map 完整；
- `layout_agent/sample_ids/a3_smoke_n5.json`：**選樣規則（凍結）**＝cache `layout_agent/output/crello_*` 依字典序，取前 5 個能離線通過 `prepare_pfull_sample`＋`prepare_r3_sample` 且（≥2 placeable foregrounds、≥1 text bitmap）的 IDs；只讀 input metadata/assets、不看 GT coordinates 或任何 model output。掃描結果：前 5 個 ID 直接全數合格（tried=5, selected=5）：
  `5888a28d95a7a863ddcc1c82, 5888a54d95a7a863ddcc1d0c, 5888b64795a7a863ddcc1d6d, 5888bb2995a7a863ddcc1f74, 5888c54095a7a863ddcc2082`。

### 13.4 Tests

新增 `test_a3_stage_binding.py`（6 tests）：

- binding＋fake actions＋**真 renderer/真 QC** 跑完整 L0：三張 800x600 render 落地、completeness=1.0、Director 收到 canvas/b64、三個 mapper artifacts dir 互異、Judge context 來自凍結 Analyst 輸出；
- call records 恰 7 筆（stage 順序鎖定）、fake 無 cost manager 時 usage=None、寫檔 write-once；
- L1 整鏈：critic 只看 B0、revision 用 B0 的原 concept（slot 02→"Top banner"）＋gate instruction＋base elements、verifier evidence 帶 `a3.issue-verifier.v1`；
- QC 缺元素→passed=False、completeness=0.5；Analyst 未跑前 QC 拒絕；
- **CLI subprocess 測試**：無 `--allow-api-calls` 時 exit 2、budget JSON（L0+T2＝7/sample、N=2＝14 total）、stderr 明示 flag、無任何 run 輸出被建立。

全套（13 個測試檔）：`128 passed in 3.91s`（A3-08a 基準 122＋新增 6）。

Ruff：`All checks passed`。`py_compile` 與 `git diff --check` 通過。

### 13.5 成本

- API calls：0；paid tokens：0（選樣掃描與所有測試皆本地 deterministic）。

### 13.6 N=5 smoke 的 exact commands 與 call budget（待使用者授權）

```bash
python layout_agent/run_a3.py init \
  --config layout_agent/configs/a3_smoke_l0.json \
  --sample-ids layout_agent/sample_ids/a3_smoke_n5.json \
  --run-id a3-smoke-n5-l0-01
python layout_agent/run_a3.py prepare-pfull --run-dir layout_agent/runs/a3/a3-smoke-n5-l0-01 --crello-root layout_agent/output
python layout_agent/run_a3.py normalize-r3 --run-dir layout_agent/runs/a3/a3-smoke-n5-l0-01
python layout_agent/run_a3.py prepare-analyst-vision --run-dir layout_agent/runs/a3/a3-smoke-n5-l0-01
python layout_agent/run_a3.py run --run-dir layout_agent/runs/a3/a3-smoke-n5-l0-01 --tree-arm T2   # 印 budget 後拒絕
python layout_agent/run_a3.py run --run-dir layout_agent/runs/a3/a3-smoke-n5-l0-01 --tree-arm T2 --allow-api-calls
```

Budget：L0 N=5 最多 35 model calls（無 retry 時）；L1-Gated 另一 run 最多 45 calls。實際 dollar 成本取決於 runtime 可用的 exact snapshot 與其牌價，未凍結前不虛報。

### 13.7 邊界與剩餘阻塞

- **模型阻塞**：config 凍結 `gpt-5.4-mini-2026-03-17`（new_plam §3.2），Actions 的 exact-model guard 會在 runtime model 不符時 fail-closed。執行前需確認 `~/.metagpt/config2.yaml` 指向該 snapshot（或由使用者決定改凍其他 snapshot 並重寫兩份 config＋log）；repo 歷史顯示 cost log 從來是 0（step91 觀察），stage-call usage 欄位屆時可能為 null，wall time 仍會記錄；
- pairwise B0/B1 internal selection 仍為 None（未建 MLLM Action）；smoke 不需要；
- `sample_record.json` 是 init 時 write-once 的 v1 契約，run 結果存於 `a3_run_summary.json`＋per-sample pipeline artifacts，不回寫 sample_record（v2 再議）；
- 第一次付費呼叫依規範停在授權門口，等使用者確認。

**A3-08b status: complete（zero-API 部分）。A3-08 剩餘＝實際 N=5 付費 smoke，需使用者授權 `--allow-api-calls` 與 model snapshot 確認。**

---

## 14. A3-08c：N=5 付費 smoke 執行（L0 三輪＋L1-Gated 一輪）

**日期：** 2026-07-11  
**起始 commit：** `ff41bf34ce055639587688887220ce456f440b0e`（dirty worktree）  
**授權：** 使用者 2026-07-11 口頭授權（L0 ≤35 calls＋L1 ≤45 calls）  
**Model snapshot：** `gpt-5.4-mini-2026-03-17`（runtime config 已相符，exact-model guard 全程通過；`MULTI_MODAL_MODELS` 已含 `gpt-5.4-mini` substring，vision 正常）

### 14.1 Run 1（a3-smoke-n5-l0-01）：0/5，三個 contract 缺陷現形

Prep（init/prepare-pfull/normalize-r3/prepare-analyst-vision）5/5 全過；budget gate 驗證正常（未授權 exit 2）。付費執行 **0/5 completed**，機制全部正確（fail-closed、versioned ErrorRecord、單 sample 失敗不中斷）：

| 缺陷 | 樣本數 | 根因 |
| --- | --- | --- |
| Analyst 把 placeable 全畫布材質標成 `background_image`，retry 3 次仍堅持 | 2/5 | P-Full pixel-only 規則把舊背景降級為 placeable，schema 卻仍允許該值，coverage 驗證只在事後拒絕 |
| Planner root child 用非 `root` relation，retry 3 次不收斂 | 1/5 | root child 只有一個合法 relation 值，卻交給 LLM 重試 |
| Planner semantic_role 與 Analyst 逐字不符 | 2/5 | A3-05 prompt 寫「Assign a concise semantic_role」但 T2 fidelity 驗證要求逐字相同——prompt 與 contract 直接矛盾 |

另發現 run CLI 缺陷：stage_calls 只在成功時落盤，失敗 sample 的付費呼叫（本輪 ~18 次）沒有 cost trail。

### 14.2 修正（全部 versioned、131 tests 全綠）

1. `A3AssetUnderstanding` schema 層禁止 `background_image`（指名 asset 的 actionable error 進 retry loop）＋Analyst prompt 明文規則；coverage 驗證保留為第二層防護；
2. `parse_layout_tree` 正規化：root child 的 relation 一律 coerce 為 `root`（唯一合法值、零資訊損失）；反向錯誤（非 root parent 用 `root`）維持硬錯誤；
3. `apply_analyst_semantics`：Planner 輸出的 semantic_type/role 由 Analyst 輸出**確定性覆寫**（T2 fidelity by construction），Planner 只貢獻 grouping/edges/ordering/confidence；tree prompt 同步改為 VERBATIM 指令；
4. run CLI `finally` 落盤 stage_calls（成功失敗都留 cost trail）；
5. （Run 2 後追加）`validate_candidate_coverage` 重複 ID 錯誤指名重複的 IDs＋Mapper prompt 明文「視覺相同的資產仍是不同 asset ID」。

新增 3 個測試鎖定修正 1–3；修正 5 由既有測試覆蓋（錯誤訊息 phrase 不變）。

### 14.3 Run 2（a3-smoke-n5-l0-02）：4/5

- 4 samples completed，各恰 7 stage calls、B0 選定、unconditional stop；
- render 視覺抽查：R3 text bitmap 保留設計師字型/顏色、aspect 正確、無 GT 位置洩漏；
- 1 sample `CandidateShortfall`（fail-closed 正確）：Mapper 對兩個視覺相同的 coupon strip 重複同一 asset ID、retry 3 次不收斂 → 修正 5；
- 失敗 sample 的 stage_calls 正確落盤（修正 4 生效）。

### 14.4 Run 3（a3-smoke-n5-l0-03）：**5/5，L0 smoke 通過**

- 5/5 completed，每 sample 恰 7 calls（總 35，零 retry 燒穿）；
- per-sample wall time 20.0–56.6s，run 總計 176.9s；
- B0 分佈：4× `r0_candidate_03`、1× `r0_candidate_01`——**觀察：Judge-Select 可能有 attachment 順序偏好（末位偏好）**，樣本太小不能下結論，記為 Gate C／正式 run 前值得做 position-shuffle 的候補檢查；
- 視覺抽查（先前失敗樣本）：西裝照、雙 coupon 疊成 -40%/SALE lockup、材質塊全部就位；**白色文字 bitmap 放白底上會隱形（poor_contrast）**——品質問題非 smoke 阻塞，正是 Judge-Critic issue types 的靶子。

### 14.5 Run 4（a3-smoke-n5-l1-01）：**5/5，L1-Gated smoke 通過**

- 5/5 completed，每 sample 恰 9 calls（總 45）；
- 整條 L1 鏈全部開火：Critic 每 sample 輸出 1–2 個 closed-type issues（spacing/misalignment/clipping/lockup/text_on_busy_region/illegible_text——全在 closed enum 內，零模糊意見）→ gate 觸發 → 單次 revision → deterministic verifier → **guard 5/5 守回 B0**（unimproved 或 revision 引入新 hard violation，如 out_of_bounds、text_obscured_by_overlay）→ unconditional stop；
- guard 防退化行為與歷史 refinement-negative 結果（step 20b/step89）方向一致；「L1 是否有淨效益」是 A3-09C Gate C 的問題，smoke 只驗證機制。

### 14.6 Phase 1 smoke checklist（new_plam §8）

| 檢查項 | 結果 |
| --- | --- |
| Analyst 確實看到 background 與所有 foreground | ✓（vision packet＋exact model） |
| Asset IDs 全 pipeline 一致 | ✓（coverage 驗證層層把關） |
| Layout Tree 在座標前產生 | ✓（planner 一次、freeze） |
| 3 concepts spatially distinct | ✓（名稱 distinct 驗證；幾何 diversity 仍靠 prompt） |
| 3 candidates 完整 render | ✓（run-03/L1 全數） |
| R3 字型可讀、alpha crop、無 GT bbox leakage | ✓（視覺抽查＋contract tests） |
| L0 與 L1-Gated 都能停止 | ✓（全部 unconditional stop） |
| trace/cost/model ID/prompt hash 落盤 | ✓（stage_calls、per-stage request＋sha256、exact-model guard；token usage 為 provider 回報 0——已知 repo 級缺口，wall time 有記錄） |

### 14.7 成本

- 付費呼叫：run1 ~18（失敗前）＋run2 ~33＋run3 35＋run4 45 ≈ **131 calls**（gpt-5.4-mini-2026-03-17，含 vision）；
- provider usage 回報全 0（與 step91 歷史觀察一致），實際 dollar 成本無法從 runtime 取得；wall time 全程記錄；
- artifacts 落於 `layout_agent/runs/a3/a3-smoke-n5-{l0-01,l0-02,l0-03,l1-01}/`（write-once，未 commit——依 repo output 慣例留在 worktree，四個 run 目錄互不覆蓋、失敗 run 保留 forensic 證據）。

### 14.8 結論與下一步

**A3-08 status: complete。** Pipeline 資料與管線驗證通過；smoke 期間修正的 5 個缺陷全部 versioned＋測試鎖定。已知觀察（非阻塞）：Judge-Select 疑似末位偏好、白字白底 contrast、concept 幾何 diversity 未驗證、provider token usage=0。

下一階段 **A3-09 N=20 gates**（Phase 2）：Gate A（Analyst vision ablation）、Gate B（Crello-Relation T0/T2/T3，需 human reference tree annotation）、Gate C（L0 vs L1-Gated，同一批 R0）。三個 gate 都是付費實驗，執行前需凍結各自 sample IDs／config／評估協定並取得授權；Gate B 另有 human annotation 前置依賴（new_plam §5.3）。
