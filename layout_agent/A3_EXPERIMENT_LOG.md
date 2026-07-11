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
| A3-09 | N=20 gates（Gate C＝L0；Gate A/B 五臂 20/20 executed、指標已算） | complete |
| A3-09M | Human-tree SGC/TLC/PCA 與 tree prediction metrics | complete |
| A3-09H | 三位標註者 adjudication queue、finalizer 與 T3 oracle preflight | complete |
| A3-09O | Human adjudication 20/20 finalized、T3 oracle trees 發布 | complete |
| A3-10P | Relation-100 剩餘 80：三人標註＋人工裁決＋oracle 發布（reference tree 100/100 齊） | complete |
| A3-10R | Crello-Relation N=100 T0/T2/T3 正式實驗＋human-reference 指標 | complete |
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

---

## 15. A3-09C：Gate C（L0 vs L1-Gated，N=20，同一批 R0）

**日期：** 2026-07-11  
**起始 commit：** `fe4cef54`（infra commit `f6f99805`）  
**授權：** 使用者「繼續下一階段」；Gate A/B 因 human reference tree annotation 前置依賴而未跑，Gate C 是唯一無人工依賴的 gate。

### 15.1 協定與基礎設施

Gate C 核心要求（new_plam §8）：兩臂共用**同一批 R0 candidates 與同一個 B0**，只差 revision tail。實作：

- `A3L1GatedPipeline.run_from_r0(outcome)`：把 L1 拆成 R0 phase＋tail，tail 可吃 persisted L0 run 的 `analyst_output/tree_condition/r0_bundle/judge_select_result`；
- `A3StageBinding.hydrate_from_r0`：從 L0 artifacts 還原 per-sample 狀態（DesignSpec、concept order），Analyst/Planner/Director/Select 四個 stage 零重呼叫；
- CLI `run-l1-tail --reuse-r0-from <l0-run>`：同樣 fail-closed 授權，budget 2 calls/sample；
- 測試 133 全綠（`run_from_r0` 上游零呼叫＋binding hydration 各有測試鎖定）。

### 15.2 Sample 凍結

`layout_agent/sample_ids/a3_gatec_n20.json`：規則＝cache IDs 排序後以 `random.Random(42)` 洗牌、排除 5 個 smoke IDs、依序取前 20 個離線通過 P-Full＋R3 prep 的樣本（tried=473、failed=453）。**資料覆蓋發現**：失敗主因是 step80 text bitmap snapshot 只覆蓋部分 cache（`asset_NNNN has no bitmap; R3 forbids text-only fallback`），可用池約 4%——**N=100 正式實驗前必須擴大 text bitmap snapshot 覆蓋**。選樣只讀 input metadata/assets。

### 15.3 執行

- **L0 臂** `a3-gatec-n20-l0-01`：**20/20 completed**，恰 140 calls（7×20）、wall 10.2 min；B0 分佈 `01:5 / 02:7 / 03:8`——smoke 觀察到的末位偏好在 N=20 未重現，分佈健康；
- **L1 tail 臂** `a3-gatec-n20-l1-tail-01`：**20/20 completed**，39 calls（19×2＋1 sample critic-only）、wall 1.6 min。

### 15.4 結果

| 指標 | 數值 |
| --- | --- |
| Critic 觸發 repair | 19/20（1 sample 無 actionable issue → 直接 B0） |
| Critic issue types（38 issues 全在 closed enum，零模糊意見） | spacing 13、out_of_bounds 5、clipping 4、overlap 3、hierarchy_error 3、text_on_busy_region 3、misalignment 3、illegible_text 2、poor_contrast 2 |
| **B1 存活（guard 判 B1 勝）** | **3/20（15%）** |
| B0 保留 | 16/19（guard reject 原因：issues_not_improved 16、new_hard_violations 3） |
| **Verifier compliance（issue 判定改善比例）** | **mean 34.2%**（per-sample 0–1.0） |
| Completion | 兩臂皆 20/20，L1 零 sample 損失（guard fallback 有效） |
| 幾何退化 | 無：guard by construction 擋掉新 hard violation 與 completeness 下降 |
| 成本 | L1 增量 ~2 calls/sample（+28% vs L0 的 7） |

3 個 B1 存活樣本細節：僅 1 筆是 STRICT 幾何改善（alignment error 26→13），其餘驗證都是 PROXY（targets moved/resized）——嚴格可證的改善其實只有 1/20。

### 15.5 Gate C 判定

升級條件逐項（new_plam §8 Gate C）：

| 條件 | 結果 |
| --- | --- |
| L1 win > loss | 形式上成立（guard 使 loss=0），但實質只有 3/20 樣本輸出改變、其中僅 1 筆 strict 改善 |
| completion 不下降 | ✓（20/20 vs 20/20） |
| alignment/overlap/completeness 無系統性退化 | ✓（guard by construction） |
| **修復問題 compliance 高** | **✗（34.2%）** |
| 每 sample 成本可接受 | ✓（+2 calls） |

**Gate C verdict：未通過（compliance 條件不成立）。依 new_plam §8「若未通過，最終配置改成 L0，不保留 loop 只為符合原始構想」——A3 最終 loop 配置定為 L0。** 此結果與整條歷史 refinement-negative 證據鏈（Step 20b、Step 89 §11.3、A2 negative）方向一致，且這次是在乾淨的 gated 單輪協定下取得：即使把修復限制在 closed-type、element-level、單次、有 deterministic guard 的最有利條件，Mapper 的修復執行力（34%）仍不足以讓 loop 產生淨效益。

**正面資產**：guard 機制證明能以零 completion 損失、零幾何退化的方式安全地嘗試修復——L1-Gated 作為「無害但低效」的機制記錄，論文可誠實引用為 controlled negative。

### 15.6 成本

- 付費呼叫：L0 140＋L1 tail 39＝**179 calls**（gpt-5.4-mini-2026-03-17）；wall 合計 ~11.8 min；provider token usage 仍回報 0，wall time 全記錄；
- artifacts：`layout_agent/runs/a3/a3-gatec-n20-{l0-01,l1-tail-01}/`（write-once、未 commit）。

### 15.7 A3-09 剩餘

- **Gate A**（Analyst vision ablation）：主要指標是 human-tree same-group F1／edge F1／role accuracy——**blocked on human reference tree annotation**；
- **Gate B**（Crello-Relation T0/T2/T3）：同樣 blocked on annotation（T3 oracle＋所有 arms 的 SGC/TLC/PCA 都要用 human reference tree）；Crello-Relation 候選池可從 step97 N=100 subset 出發，但該 subset 是舊 SEGA 協定時代選的，需檢查與 A3 P-Full/R3 離線 prep 的交集；
- annotation 是人工工作（每 sample ≥2 標註者＋adjudication，new_plam §5.3），無法由本 session 代做；可先做的零成本前置＝annotation 工具/格式（human oracle tree 已有 `source="human_oracle"` contract）與 Crello-Relation×bitmap-cache 交集盤點。

**A3-09C status: complete（L0 定案）。**

---

## 16. A3-09 前置：text bitmap sidecar 補齊與 human annotation contract

**日期：** 2026-07-11  
**起始 commit：** `5f00a46b`（sidecar infra commit `fbe41f42`）  
**性質：** Gate A/B 資料與標註前置；0 LLM calls（HF dataset 串流下載，非付費 API）。

### 16.1 阻塞盤點

- step97 Crello-Relation N=100 中僅 **6/100** 通過 A3 離線 prep——其餘 94 個樣本的全部 text elements 都沒有 cached bitmap（step80 snapshot 只覆蓋 demo ids）；
- legacy step80 script 有兩個問題不能直接沿用：(a) 會**改寫 cache 的 meta.json**（違反不動舊 cache 的原則）；(b) 把 bitmap **resize 到 GT canvas size**（GT geometry 進入資產本體）。

### 16.2 Text bitmap sidecar（fbe41f42）

- 新 contract：`a3_text_bitmaps.json`（`a3.text-bitmap-sidecar.v1`）＋`a3_text_NNNN.png` 放在 cache 目錄內的 A3 命名空間，**meta.json 一個 byte 都不動**（測試以 read_bytes 比對鎖定）；
- bitmap 存 HF dataset 的 **raw render size**，不做 GT resize；R3 normalizer 的 tight-crop＋512 長邊本來就會消除尺寸訊號；
- `pfull_preprocessor` 解析順序：text element 先查 sidecar、後退 legacy `asset_ref`（sidecar 優先，因 legacy 可能帶 step80 的 GT-size resize）；不支援的 sidecar version fail-closed；
- CLI：`run_a3.py snapshot-text-bitmaps --ids <json> --crello-root <dir>`（lazy import `datasets`、串流 `cyberagent/crello` test split、sidecar per-sample write-once、incremental skip）。

### 16.3 Snapshot 執行結果

```text
targets: 100（relation100_ids.json 全量）
done: 100/100、bitmaps_saved: 542、mismatches: 0（scanned≈1954）
```

重跑審計：**relation-100 現在 100/100 通過 P-Full＋R3 離線 prep**——Gate B 樣本池與（該池內的）N=100 資料阻塞解除。Gate C 曾量到的「cache 可用池 ~4%」對一般池仍成立；一般 N=100 凍結前需對該池再跑一次 snapshot。

### 16.4 Human annotation contract（tools/annotation.py）

依 new_plam §5.3 建立標註工具鏈（Gate A/B 評估的共同依賴）：

- `AnnotationPacket`（`a3.annotation-packet.v1`）：標註者**只看** brief、asset IDs、media type、text content＋contact sheet 檔名——無 GT geometry、無檔案路徑、**連 base background 都不給**（分組判斷純憑素材語意）；packet hash 落盤、write-once，附空白 `annotation_form.json`；
- `HumanAnnotation`（`a3.human-annotation.v1`）：per-asset semantic_type/role、group、parent/relation、uncertain flag；coverage/duplicate 驗證；
- `compute_agreement`：same-group pair Jaccard、edge Jaccard、type agreement、分歧資產清單——驅動 adjudication 佇列；
- `AdjudicationRecord`（`a3.annotation-adjudication.v1`）：**強制 ≥2 標註者**＋adjudicator＋agreement 快照，分歧不可靜默解決；
- `annotation_to_oracle_tree`：合議後標註 → `A3LayoutTree(source="human_oracle")`，直接餵 T3 arm（測試驗證 `make_tree_condition("T3", ...)` 可用）；uncertain 資產 confidence=0.5。

### 16.5 Tests

新增 `test_a3_annotation.py`（7 tests）＋pfull sidecar 3 tests。全套：**143 passed in 4.00s**。Ruff／py_compile／`git diff --check` 通過。

### 16.6 成本與剩餘

- LLM calls：0；HF 串流下載一次 test split（~1954 樣本掃描）；
- **A3-09 剩餘阻塞只剩人工**：Gate A/B 需要真人對 Crello-Relation pilot（N=20）做雙標註＋adjudication。工具鏈（packet/form/agreement/adjudication/oracle-tree 轉換）已就緒，产生 annotation packets 只差一個對已 prep run 的批次命令（可在標註開始前補）；
- Gate A 另需 text-only Analyst ablation 臂（Action 未建，工作量小、zero-cost 可先建）。

---

## 17. A3-09 前置收尾：text-only 臂、annotation 批次命令與 pilot 標註包

**日期：** 2026-07-11  
**起始 commit：** `b155af5d`  
**性質：** Gate A/B 最後零成本前置；0 LLM calls。

### 17.1 Gate A text-only Analyst 臂

- `analyst_vision.build_text_only_analyst_prompt`：與 vision 臂**同一 output schema/coverage/background_image 禁令**，prompt 明示「NO visual access」且要求不得虛構視覺細節（背景描述只能來自 brief）；
- `actions/analyze_a3_text_only.py`（`AnalyzeA3TextOnly`）：不附任何 image、exact-model guard、error-aware retry ×3、request（`a3.analyst-text-only-request.v1`，image_labels=[]）與 attempts write-once；
- `run_a3.py run --analyst-arm vision|text-only`：binding 層換 Action，其餘 stage/budget/protocol 完全一致；`analyst_arm` 記入 run summary。Gate A 兩臂只差 Analyst 可見性一個變因。

### 17.2 prepare-annotation 批次命令

`run_a3.py prepare-annotation --run-dir <prepared-run>`：逐 sample 輸出自足標註包至 `samples/<id>/annotation/`：

```text
annotation_packet.json   （brief＋asset IDs/media/content＋packet hash）
annotation_form.json     （空白表單，標註者填 role/group/parent/uncertain）
asset_contact_sheet_NN.png（只複製 contact sheets——不含 background overview、
                            不含任何 GT 產物）
```

run level `annotation_preparation.json`；失敗寫 versioned ErrorRecord。

### 17.3 Gate A/B pilot N=20 凍結

`layout_agent/sample_ids/a3_gateab_pilot_n20.json`＋`configs/a3_gateab_pilot_l0.json`：

- **選樣規則（凍結）**：依 step97 `relation100_ids.json` 的 stored 順序，取前 10 個 tier=rich＋前 10 個 tier=medium（維持 step97 tier-stratified、pre-generation、model-blind 性質）；
- 註記：其中 2 個 ID（5888bb29…、5888c540…）與 N=5 smoke 重疊——smoke 只驗管線未做任何評估，標註只看 input，無汙染；
- pilot run `a3-gateab-pilot-n20-01` 已完成 init→prepare-pfull→normalize-r3→prepare-analyst-vision→**prepare-annotation 20/20**（全 zero-cost）；標註包落於 `layout_agent/runs/a3/a3-gateab-pilot-n20-01/samples/*/annotation/`。

### 17.4 Tests

新增 text-only prompt/action 測試（無 image、宣告無視覺、同 leakage 規則）。全套：**144 passed in 3.59s**。Ruff／py_compile 通過。

### 17.5 A3-09 待辦與依賴（快照）

| 事項 | 狀態 |
| --- | --- |
| Gate C | ✅ complete（L0 定案） |
| Gate B 資料（bitmap sidecar） | ✅ relation-100 全 ready |
| Annotation 工具鏈＋pilot 標註包 | ✅ 20 份已產出待分發 |
| Gate A text-only 臂 | ✅ 已建 |
| **Human annotation（pilot N=20 雙標註＋adjudication）** | ⏸ **等真人**——分發 `samples/*/annotation/`，每位標註者填回 `annotation_form.json`（annotator_id 各自唯一），回收後以 `compute_agreement`＋`AdjudicationRecord`＋`annotation_to_oracle_tree` 合成 T3 oracle |
| Gate A 兩臂付費 run（vision vs text-only，各 N=20×7 calls） | 標註回收後執行（tree metrics 需 human trees） |
| Gate B T0/T2/T3 付費 runs（L0、各 N=20） | 同上；T3 需 oracle trees |

**A3-09 前置全部完成；關鍵路徑現在完全在人工標註上。**

---

## 18. A3-09M：human-tree evaluation metrics

**日期：** 2026-07-11
**起始 local HEAD：** `91793a30`（upstream `6493e3e1`；本階段開始前已有一筆未 push 的 handoff 文件 commit）
**性質：** deterministic evaluation infrastructure；0 API calls、0 paid tokens；未修改任何既有 run artifact。

### 18.1 A3 human-tree SGC / TLC / PCA

新增 `metagpt/ext/agentlayout/tools/human_tree_metrics.py`。輸入介面明確要求：

- 一棵呼叫端注入的 `A3LayoutTree(source="human_oracle")`；不讀取 arm 自己的 predicted tree 或任何 global tree state；
- legacy `Candidate` 的 pixel `left/top/width/height` 與 canvas width/height；bbox 先分別除以 canvas width/height，再建立一次共用距離矩陣；
- tree 以 stable asset ID 對 Candidate；tree 外元素（含 legacy `bg_*`）忽略，tree element 缺漏或重複則整筆記 `None`＋`layout:*` skip reason，不做猜測式對齊。

共用距離為外框 L1 間隙：

```text
d(i,j) = max(0, gap_x) + max(0, gap_y)
gap_x  = max(x_i,x_j) - min(x_i+w_i,x_j+w_j)
```

重疊或相貼為 0。三個 metric 的實作與邊界：

- **SGC**：每個 non-singleton group 先算組內 pair mean，再對 group mean 取平均（group-level `D_intra`）；`D_inter` 是所有跨 group pair 的 pair-level mean；`SGC = D_inter / (D_intra + D_inter + 1e-6)`。單 group → `sgc:single_group`；全部 groups singleton → `sgc:all_groups_singleton`；均為 `None`。
- **TLC**：列舉所有 anchor-ordered `(i,j,l)`，`i,j` 同 group、`i,l` 異 group；近者得 1、平手得 0.5、否則 0。無 triplet → `None`＋`tlc:no_triplets`。
- **PCA**：每個 non-root `(parent, child)` 檢查 `d(parent,child) <= median_{j!=parent} d(parent,j)`；無 non-root edge → `None`＋`pca:no_edges`。

**與 `Metrics.md` legacy 定義的唯一結構差異（A3 contract 規定）：** group 不再由 legacy tree 的 root 直接子樹推導，而是逐字使用 `A3LayoutTree.groups[*].member_ids` 的 explicit exact partition；PCA edge 直接讀 `A3TreeNode.parent_id`，排除 `parent_id == "root"`。舊 `semantic_group_metrics.py` 完全未修改，保留作 Step-90 predicted-tree 自我一致性歷史實作。

### 18.2 Tree prediction metrics

`evaluate_tree_prediction(predicted, human_oracle)` 強制兩棵 tree stable-ID coverage 完全一致，輸出：

- same-group unordered pair Precision / Recall / F1；
- directed parent-child edge Precision / Recall / F1；
- `semantic_type` accuracy；
- `semantic_role` **case-sensitive exact-string** accuracy（不另報 normalized role score）；
- P/R/F1 同時保存 TP、predicted-set size、reference-set size；雙方 relation set 都空時定義為 exact match `1/1/1`，只有單側為空則依 zero-division rule 記 0。

Human oracle 中 `confidence == 0.5` 的 uncertain node 不強迫算錯：所有涉及該 node 的 pair/edge，以及其 type/role，從 primary metrics 排除；另報 uncertain node IDs、uncertain-only pair/edge P/R/F1、uncertain type/role accuracy，以及排除的 relation counts。

### 18.3 Tests 與驗收

先建立測試並確認因新模組不存在而 collection error，再實作至全綠。新增 `tests/metagpt/ext/agentlayout/test_human_tree_metrics.py`（18 tests），涵蓋：

1. `d` 的重疊／相貼／水平／垂直／對角五種案例；
2. 同組緊貼異組遠離（SGC > 0.999、TLC=1、PCA=1）與同組打散（SGC<0.5、TLC<0.5）；
3. 全部重疊 TLC=0.5；single-group、all-singleton、no-triplet、no-edge 與 missing/duplicate ID skip；
4. 固定 `random.Random(20260711)` 的 1000 次 Monte Carlo，TLC mean 落在 `0.5±0.05`；
5. SGC group-level mean 的數值反例、pixel normalization scale invariance、tree 外元素排除與 PCA median criterion；
6. tree prediction perfect／partial／zero-overlap／uncertain，以及 source/coverage fail-closed。

完整 A3 suite（原 144 tests＋本階段 18 tests）：

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
  tests/metagpt/ext/agentlayout/test_a3_annotation.py \
  tests/metagpt/ext/agentlayout/test_human_tree_metrics.py
```

結果：**162 passed in 3.94s**。新檔 Ruff `All checks passed`；conda `meta` 的 `py_compile` 與三個 staged phase files 的 scoped `git diff --cached --check` 均通過。

### 18.4 Pilot annotation 條件驗收（read-only）

`a3-gateab-pilot-n20-01` 已出現三位 annotator（`T`、`hui`、`neiji`）各 20 份 annotation。本階段唯讀執行指定驗收：

- `HumanAnnotation.model_validate`＋`validate_annotation_coverage`：**60/60 valid，0 invalid**；sample ID、filename annotator suffix 亦一致；
- 額外結構防線 `annotation_to_oracle_tree`：**60/60 可形成合法 A3 oracle tree**；
- 每 sample 三組 annotator pair 均跑 `compute_agreement`：共 60 reports，**58/60 至少一項不完全一致**；
- `sample_uncertain` 三位均為 0；uncertain nodes：T=18、hui=26、neiji=0。

Pairwise mean（現有 `compute_agreement.role_type_agreement` 實際只比較 `semantic_type`；不等同本階段新增的 exact free-text role accuracy）：

| Pair | N | same-group Jaccard | edge Jaccard | semantic-type agreement | 三項完全一致 |
| --- | ---: | ---: | ---: | ---: | --- |
| T / hui | 20 | 0.3282 | 0.1911 | 0.9095 | 0 |
| T / neiji | 20 | 0.7853 | 0.5083 | 0.9209 | 1（`58ab189395a7a863ddcc7847`） |
| hui / neiji | 20 | 0.3507 | 0.2927 | 0.8862 | 1（`5952934395a7a863ddcdff21`） |

分歧清單如下；asset 欄是三組 `compute_agreement.disagreeing_assets` 的聯集（只代表 semantic-type 分歧），pair 欄另涵蓋 grouping/edge/type 任一分歧。即使其中一組 pair 完全一致，只要第三位不同仍需 adjudication。

| sample_id | semantic-type 分歧 assets（union） | 有分歧 pairs |
| --- | --- | --- |
| `5888bb2995a7a863ddcc1f74` | asset_0001,asset_0003,asset_0005,asset_0006,asset_0007,asset_0008,asset_0009,asset_0010,asset_0011,asset_0013,asset_0016 | T/hui, T/neiji, hui/neiji |
| `5888c54095a7a863ddcc2082` | asset_0005 | T/hui, T/neiji, hui/neiji |
| `5888dd7995a7a863ddcc2e86` | asset_0001,asset_0003,asset_0004,asset_0011,asset_0012,asset_0013,asset_0015,asset_0018 | T/hui, T/neiji, hui/neiji |
| `5889bc5995a7a863ddcc3b97` | — | T/hui, T/neiji, hui/neiji |
| `589b457b95a7a863ddcc5331` | asset_0004,asset_0005,asset_0010,asset_0011 | T/hui, T/neiji, hui/neiji |
| `58ab17ba95a7a863ddcc77bf` | asset_0009 | T/hui, T/neiji, hui/neiji |
| `58ab189395a7a863ddcc7847` | — | T/hui, hui/neiji |
| `58cbc44595a7a863ddccc20b` | asset_0013,asset_0015 | T/hui, T/neiji, hui/neiji |
| `5909cfb695a7a863ddcd37cb` | asset_0009 | T/hui, T/neiji, hui/neiji |
| `5914233f95a7a863ddcd777c` | asset_0000,asset_0002,asset_0009 | T/hui, T/neiji, hui/neiji |
| `592d1a2b95a7a863ddcd97aa` | asset_0004,asset_0008 | T/hui, T/neiji, hui/neiji |
| `592fdd7e95a7a863ddcdbe67` | — | T/hui, T/neiji, hui/neiji |
| `5930177f95a7a863ddcdc313` | asset_0004,asset_0009 | T/hui, T/neiji, hui/neiji |
| `5931132c95a7a863ddcdc5d3` | — | T/hui, T/neiji, hui/neiji |
| `59313e5495a7a863ddcdc9ac` | asset_0006,asset_0007,asset_0010,asset_0011,asset_0012,asset_0014 | T/hui, T/neiji, hui/neiji |
| `5952704d95a7a863ddcdecb5` | asset_0005,asset_0006 | T/hui, T/neiji, hui/neiji |
| `5952934395a7a863ddcdff21` | — | T/hui, T/neiji |
| `59b2809c1350e8329300dbe4` | asset_0000 | T/hui, T/neiji, hui/neiji |
| `59bb96701350e8329301120a` | — | T/hui, T/neiji, hui/neiji |
| `5a21848dd8141396fe9a33eb` | asset_0016 | T/hui, T/neiji, hui/neiji |

**結論：20/20 samples 都需使用者 adjudication。** 本階段沒有自行裁決、沒有產生 final oracle、沒有寫回 write-once run 目錄。

### 18.5 成本與下一步

- API calls：**0**；paid tokens / dollar cost：**0**；所有 metric、tests、annotation validation/agreement 都是 local deterministic Python。
- A3-09M status：**complete**。
- 下一入口：使用者 adjudication → `AdjudicationRecord`＋final `annotation_to_oracle_tree` → Gate A（vision vs text-only，各約 140 calls）與 Gate B（T0/T2/T3；L0）付費 runs。任何 paid run 仍須先逐 arm 報 budget 並取得明確授權；Gate C 的 L0 決策不重啟。

---

## 19. A3-09H：human adjudication queue 與 T3 oracle handoff

**日期：** 2026-07-11
**起始 commit：** `1b40fa569cd759434cf39a0e0f17aa5e103523ea`
**性質：** zero-cost human-work handoff infrastructure；0 API calls、0 paid tokens；未做任何自動 adjudication。

### 19.1 發現的 contract 缺口

Pilot 現有 `T`、`hui`、`neiji` 三份獨立 annotation／sample，但原本只有：

- pairwise `compute_agreement(a,b)`；
- 只容納單一 `AgreementReport` 的 `AdjudicationRecord`；
- `annotation_to_oracle_tree`，沒有 queue、比較 packet、完成表單、批次 validator 或 T3 CLI oracle input。

直接選其中一位當 base/winner 會構成未授權的自動裁決；三位 annotations 也不能只保留任意一組 pairwise report。因此本階段建立 evidence-only packet：保留所有來源與 pairwise 結果，最終選擇仍完全由使用者作成。

### 19.2 Adjudication packet contract

新增 `metagpt/ext/agentlayout/tools/adjudication.py`：

- `a3.adjudication-packet.v1`；每份 source annotation 保存 filename＋raw SHA-256；
- load 時逐份執行 `HumanAnnotation.model_validate`、manifest coverage、filename/annotator identity，以及 `annotation_to_oracle_tree` 的 parent/cycle/group structural validation；
- N 位 annotator 產生全部 `N choose 2` pairwise `AgreementReport`；三位即 3 份；
- `aggregate_agreement` 明確定義為各 pair 的 defined Jaccard／semantic-type agreement arithmetic mean，`disagreeing_assets` 取 union；原欄位 `role_type_agreement` 仍只代表既有 contract 的 semantic-type agreement；
- per-asset comparison 同時呈現每位 annotator 的 semantic type、exact free-text role、same-group member set、group ID/label、parent/relation、uncertain；
- 不存在 selected/winner 欄位；`requires_adjudication` 只標示是否有差異，不作決策；
- `annotation_adjudicated_form.json` 與 `adjudication_record_form.json` 只預填**所有 annotator 逐字一致**的欄位；任何分歧保持空字串／`null`，故在使用者填完前刻意無法通過 schema；
- `validate_adjudication_submission` 強制 packet sample/provenance、annotator IDs、frozen aggregate、adjudicator identity、asset coverage 及完整 A3 tree validity一致，才回傳 `source="human_oracle"` tree。

### 19.3 CLI 與 real pilot materialization

`layout_agent/run_a3.py` 新增：

```bash
python layout_agent/run_a3.py prepare-adjudication \
  --run-dir layout_agent/runs/a3/a3-gateab-pilot-n20-01
```

輸出使用全新的 write-once namespace，沒有修改任何既有 `annotation_*.json`：

```text
adjudication/
  ADJUDICATION_GUIDE.md
  FINALIZATION_GUIDE.md
  adjudication_queue.json
  samples/<sample_id>/
    adjudication_packet.json
    annotation_adjudicated_form.json
    adjudication_record_form.json
```

Real execution：**20/20 prepared、0 failed**。目前 namespace 共 63 files（CLI 62＋finalization supplement 1）；20 packets 全部 schema-valid，60 個 source hashes 全吻合。每 sample 有 8–24 個需決定 assets，總計 **298 assets** 至少一欄有分歧；因此沒有產生任何 completed form、record 或 oracle tree。

使用者完成每 sample 的兩份 copy-before-edit 表單後，執行：

```bash
python layout_agent/run_a3.py finalize-adjudication \
  --run-dir layout_agent/runs/a3/a3-gateab-pilot-n20-01
```

Finalizer 採 two-phase all-or-nothing：先驗 20/20 completed forms、record、來源 hash與 tree contract，任一錯誤即 exit 1 且不寫任何 oracle。全數通過才 write-once 發布：

```text
adjudication/oracle_trees/<sample_id>.json
adjudication/adjudication_finalization.json
```

缺少 human completed forms 的實際 preflight 結果：0 valid／20 `FileNotFoundError`、exit 1；確認 `oracle_trees/` 與 finalization summary 均未建立。

### 19.4 Gate B T3 fail-closed input

`run_a3.py run` 新增 `--oracle-trees-from <directory>`：

- `--tree-arm T3` 缺此參數立即 exit 1；
- 非 T3 禁止帶此參數；
- 在任何 Action/paid call 前，一次載入全部 sample tree，驗證 `source="human_oracle"`，並和該 Gate run 的 R3 foreground stable IDs 做 exact coverage preflight；
- 只有全部 oracle 合法時才進既有 `--allow-api-calls` budget gate；無授權仍 exit 2，不產生 run output；
- pipeline 明確收到對應 sample 的 `oracle_tree`，不再以 `None` 進 T3 後才失敗。

### 19.5 Tests 與成本

新增 `test_a3_adjudication.py`（8 tests）並擴充 `test_a3_stage_binding.py` 1 test，涵蓋：三位 annotator 全 pairwise＋aggregate、逐 asset disagreement、unanimous-only forms、schema/coverage/source hash、duplicate/sample mismatch、write-once、human-only guide、completed submission validation、oracle source check、T3 complete-set/coverage preflight 與 paid gate refusal。

完整 A3 suite（A3-09M 162＋本階段 9）：**171 passed in 4.77s**。Ruff、conda `meta` py_compile 與 scoped diff check 另行通過。

- API calls：**0**；paid tokens／dollar cost：**0**。
- A3-09H infrastructure：**complete**。
- 人工作業：**blocked on 使用者 adjudication 20/20**；此狀態不授權 Gate A/B 付費 calls。
- 下一入口：使用者依 `adjudication/ADJUDICATION_GUIDE.md` 填完 20 組 completed forms → zero-cost `finalize-adjudication` → 報 Gate A/B 各 arm budget並等待明確授權。

---

## 20. A3-09O：human adjudication 完成與 T3 oracle finalization

**日期：** 2026-07-11

### 20.1 裁決形式：adjudicator 全量重新標註

使用者（adjudicator `nina`）未逐欄調停三位標註者，而是對 20/20 pilot samples 做完整獨立重新標註，寫入各 `samples/<sample_id>/annotation/annotation_nina.json`（HumanAnnotation v1，全欄位含中文 semantic_role）。此為 ADJUDICATION_GUIDE 允許的合法裁決：每一項 per-asset 決定皆由人類做出，且不以 T/hui/neiji 任一位為自動 winner。三份原始標註（frozen SHA-256）全程未動。

### 20.2 唯讀預驗證與人工修正

finalize 前先以唯讀腳本驗證 nina 20 份標註（HumanAnnotation schema＋packet coverage＋`annotation_to_oracle_tree` 結構檢查）：

- 首輪 **10/20 失敗**：9 個 sample 用了非 enum 的 `semantic_type`（`text`／`heading`／`footer`／`footnote`）；`5914233f95a7a863ddcd777c` assets 4/5 四欄全空。錯誤逐欄交回使用者，由使用者本人修正（無代填）。
- 次輪 19/20：`5889bc5995a7a863ddcc3b97` 檔內 `sample_id` 為 `5899bc59…`（單字元 typo，asset coverage 與 packet 完全吻合、內容屬於該目錄）；由本 session 修正該單一欄位。
- 終輪 **20/20 valid**：298 assets、`uncertain` assets **4**、`sample_uncertain` **0**。

### 20.3 機械轉錄與 finalizer

每個 sample 的兩份 completed 檔以零決策方式產生：`annotation_adjudicated.json` = `annotation_nina.json` 逐位元組複製；`adjudication_record.json` = 預填 record form ＋ `adjudicator_id: "nina"` ＋ 事實性 provenance note。template forms、packets、queue、raw annotations 均未修改。

```bash
python layout_agent/run_a3.py finalize-adjudication \
  --run-dir layout_agent/runs/a3/a3-gateab-pilot-n20-01
```

結果：**20/20 valid、failed 0**。`adjudication/oracle_trees/*.json` = **20**，每棵 `source == "human_oracle"`；`adjudication/adjudication_finalization.json` 存在且 `failed == 0`（含每 sample annotation/record/oracle-tree SHA-256）。

### 20.4 成本與狀態

- API calls：**0**；paid tokens／dollar cost：**0**（全程 schema 驗證與檔案操作）。
- A3-09O status：**complete**。Gate B T3 的 `--oracle-trees-from` 輸入已就緒。
- 下一入口：Gate A/B 各 arm budget 已回報（nominal 合計 660 calls，不含 reliability retries），等待使用者逐 arm 付費授權；未授權前不帶 `--allow-api-calls`。

---

## 21. A3-09AB：Gate A/B 五臂付費執行與 human-oracle 指標

**日期：** 2026-07-11

### 21.1 授權與執行協定

使用者逐字授權「授權全部五個 arm 帶 --allow-api-calls 執行」。五臂各建獨立 run-id，共用 `a3_gateab_pilot_l0.json`（L0、gpt-5.4-mini-2026-03-17）與 `a3_gateab_pilot_n20.json`（N=20，與 human oracle 同一批）。每臂先各自 `init → prepare-pfull → normalize-r3 → prepare-analyst-vision`（zero-cost、failed 0），再跑無 flag preflight（五臂 budget 與提案完全一致、exit 2），最後依序帶 `--allow-api-calls` 執行。

| Arm | Run-id | Calls | Wall | 完成 |
| --- | --- | ---: | ---: | --- |
| Gate A vision T2 | `a3-gatea-t2-vision-n20-01` | 140 | 771s | 20/20、failed 0 |
| Gate A text-only T2 | `a3-gatea-t2-textonly-n20-01` | 140 | 652s | 20/20、failed 0 |
| Gate B T0 | `a3-gateb-t0-n20-01` | 120 | 628s | 20/20、failed 0 |
| Gate B T2 | `a3-gateb-t2-n20-01` | 140 | 786s | 20/20、failed 0 |
| Gate B T3 | `a3-gateb-t3-n20-01` | 120 | 656s | 20/20、failed 0 |

實際 model calls 合計 **660**＝nominal（零 schema-retry）。T3 oracle preflight 通過並記錄 `oracle_trees_from`。provider 回報 cost 仍為 0（已知現象），實際美元成本無法自 runtime 取得。

### 21.2 Gate A：predicted tree vs human oracle（A3-09M 指標，certain nodes）

| Arm | same-group F1 | edge F1 | type acc | exact-role acc |
| --- | ---: | ---: | ---: | ---: |
| vision | 0.4684 | 0.3088 | 0.6785 | 0.0 |
| text-only | 0.5142 | 0.1818 | 0.5743 | 0.0 |

Paired sign test（vision − text-only）：**edge F1 13W/4L/3T p=0.049、type acc 12W/2L/6T p=0.0129，vision 顯著較好**；same-group F1 8W/12L p=0.50（text-only 均值略高但不顯著）。exact-role accuracy 兩臂皆 0——模型自由文字 role 與人類中文 role 逐字比對本質上不可能命中，此軸只能當 lower bound，不具鑑別力。**Gate A 判讀：保留 vision Analyst**（層級結構與語意型別顯著優於 text-only；分組軸無差異證據）。

### 21.3 Gate B：B0 layout vs 同一棵 human reference tree（SGC/TLC/PCA）

三臂 20/20 全部 defined、零 skip；不做 predicted-tree 自評。

| Arm | SGC | TLC | PCA |
| --- | ---: | ---: | ---: |
| T0 | 0.6375 | 0.6075 | 0.6817 |
| T2 | 0.6373 | 0.6385 | 0.7188 |
| T3（human tree 注入） | **0.7528** | 0.6467 | **0.8534** |

Paired sign tests：**T3−T0 PCA 12W/0L/8T p=0.0005 顯著**；T3−T2 SGC 14W/6L p=0.115（方向有利未顯著）；其餘（含 T2−T0 全軸）皆不顯著。**Gate B 判讀：human tree 注入（T3）在 parent-child adjacency 上有顯著、乾淨的提升，SGC 方向有利；T2 predicted tree 相對 T0 無可辨識增益**——tree 條件的價值目前主要來自 tree 的品質（human > predicted），而非 tree 通道本身。

### 21.4 成本與狀態

- 付費 model calls：**660**（授權範圍內、零 retry）；provider 回報 token/cost 0。
- 分析全程 zero-cost（`human_tree_metrics.py` 唯讀聚合）；run artifacts 不 commit。
- A3-09 全部 gates：**complete**。下一決策點：A3-10 N=100 設計（text bitmap 池已備 100/100，但 human tree annotation 只覆蓋 pilot N=20，T3 臂與 Gate 指標在 N=100 的 reference tree 來源需先定案）。

### 21.5 A3-10 前置：relation-100 剩餘 80 sample 標註包

同日使用者要求準備 N=100 剩餘標註。relation-100（`output2/step97_relation_subset/relation100_ids.json`）扣除 pilot 20（真子集、oracle 直接沿用）＝**80 個**，凍結為 `layout_agent/sample_ids/a3_relation_annot_n80.json`。新 run `a3-relation-annot-n80-01` 走 `init → prepare-pfull → normalize-r3 → prepare-analyst-vision → prepare-annotation`：**80/80 prepared、failed 0**（packet＋空白 form＋contact sheets，部分 sample 兩張），全程 **0 API calls**。80 個的標註／裁決協定（幾位標註者、是否沿用單人 oracle 模式）為使用者待決事項。

---

## 22. A3-10P：relation-100 剩餘 80 的三人標註、人工裁決與 oracle 發布

**日期：** 2026-07-11

### 22.1 三人標註與唯讀驗證

三位標註者（hui、neiji、nina）各完成 80 份，**240/240** 通過 HumanAnnotation schema、annotator/sample id 一致性、packet coverage（每位 1,045 assets）與樹結構檢查。`uncertain` 分布極不均：nina 0、hui 23、**neiji 527（50.4%）**——使用者確認 neiji 屬誠實不確定、維持現狀（uncertain 依協定排除於 primary agreement 之外另計）。

### 22.2 Adjudication queue 與人工裁決

`prepare-adjudication`（write-once）：**80/80 packets、failed 0**；80/80 sample 皆有分歧、合計 **485 個 disagreeing assets**；三人 aggregate agreement 平均 same-group Jaccard **0.571**、edge Jaccard **0.357**、role-type agreement **0.658**。使用者選擇**逐分歧裁決**（非單人 oracle 模式）：逐 sample 對照三人 decisions 完成 `annotation_adjudicated.json`＋`adjudication_record.json`（adjudicator=`nina`，80/80）。

### 22.3 Finalization

`finalize-adjudication` all-or-nothing：**80/80 valid、failed 0**。`adjudication/oracle_trees/` 80 棵、全部 `source="human_oracle"`；`adjudication_finalization.json` 存在且 `failed == 0`。裁決後最終 uncertain assets **23**（neiji 的 527 個不確定絕大多數已由裁決者定案）。

### 22.4 成本與狀態

- API calls：**0**（標註、裁決、驗證、finalization 全程零付費）。
- **Human reference tree 覆蓋達 relation-100 全數 100/100**（pilot 20＋本批 80），Phase 3 的 T3 臂與 human-reference SGC/TLC/PCA 指標來源已就緒。
- 剩餘 Phase 3 前置：Gate B 升級條件 2（T2 vs T0 human semantic-grouping preference）仍未驗證；N=100 各臂付費 budget 需另行提案與授權。

---

## 23. A3-10R：Crello-Relation N=100 T0/T2/T3 正式實驗

**日期：** 2026-07-11

### 23.1 升級決定與協定

使用者明文決定**豁免 Gate B 升級條件 2（human semantic-grouping preference）**，直接進 Phase 3（「這可以先跳過先讓你跑N100」）；條件 1（幾何方向）與 3（T3 upper bound）已於 §21 滿足。T1 依 new_plam §8「預算不足可省」未跑。前置：ids 凍結 `sample_ids/a3_relation_n100.json`（=relation100_ids）；兩批 oracle 逐位元組合併至 `runs/a3/relation100_oracle_trees/`（100 棵、含 `MERGE_PROVENANCE.json` 記錄來源與 SHA-256）。三臂獨立 run-id、四步 zero-cost prep（3×100 failed 0）、無 flag preflight（T0 600／T2 700／T3 600）皆通過後，使用者逐字授權執行。

### 23.2 執行結果

| Arm | Run-id | 完成 | Calls | Wall |
| --- | --- | --- | ---: | ---: |
| T0 | `a3-rel100-t0-01` | 100/100 | 600 | 3153s |
| T2 | `a3-rel100-t2-01` | **98/100** | 692 | 3782s |
| T3 | `a3-rel100-t3-01` | **99/100** | 598 | 3214s |

失敗 3 例（皆 per-sample、error record 落盤、non-retryable）：T2 `5d67ed46`＋T3 `5da04604` 為 CandidateShortfall（3 render 只完成 2）；T2 `5f644f40` 為 Planner 連 3 attempt 輸出重複 asset ID。實際 calls 合計 **1,890**（nominal 1,900，失敗提早中止）；provider 回報 cost 0（已知現象）。

### 23.3 Human-reference SGC/TLC/PCA（全臂同一棵 human tree；配對檢定取兩臂皆完成之交集）

| Arm | SGC | TLC | PCA |
| --- | ---: | ---: | ---: |
| T0（n=100） | 0.6465 | 0.6277 | 0.6930 |
| T2（n=98） | 0.7037 | 0.6711 | 0.7614 |
| T3（n=99） | **0.7779** | **0.7271** | **0.8215** |

Paired sign tests（two-sided）：

- **T2 vs T0：三軸全顯著**——SGC 64W/34L p=0.0032、TLC 63W/32L p=0.0019、PCA 47W/25L p=0.0128。
- **T3 vs T0：三軸全極顯著**——SGC 74W/25L p≈3e-6、TLC 70W/29L p=5e-5、PCA 55W/18L p=2e-5。
- **T3 vs T2**：SGC 64W/33L p=0.0022、TLC 60W/35L p=0.0134 顯著；PCA 46W/29L p=0.064 邊緣。

### 23.4 判讀

- **N=20 的「T2 無增益」結論在 N=100 被推翻**：predicted tree 相對無 tree 在三軸全部顯著改善；N=20 只見方向與本結果一致（統計功效不足）。引用 tree ablation 一律以本節為準、不可再引 §21.3 的 T2−T0 null。
- 三臂呈乾淨的 tree 品質梯度 **T0 < T2 < T3**：tree 通道有效（T2>T0），tree 品質進一步加分（T3>T2，SGC/TLC 顯著）。T3 同時是可解釋的 upper bound。
- Caveat：Gate B 升級條件 2 之 human preference 未驗證即升級（使用者決定），論文引用時幾何指標主張成立、human-perceived 分組偏好主張仍需補人測。
- A3-10R status：**complete**。Phase 3 剩餘項目（Crello-General N=100 final system、matched judge evaluation、human preference study、failure analysis 等）另行規劃。

### 23.5 SEGA/PKU 幾何六軸（Phase 3 必跑項 4；zero-cost、`sega_metrics.py` PKU 忠實移植）

cls 對映：text/text_bitmap→1、image→2；Und_l/Und_s **0 by design**（A3 不產 cls=3 underlay）。Rea/Occ 只有 14/100 sample 有像素底圖可算；該 14 張底圖全為平坦填色，Sobel 梯度為 0 → **Rea 三臂全 0、全平手（無訊號）**。

| Arm | Ali↓ | Ove↓ | Rea↓(n=14) | Occ↓(n=14) |
| --- | ---: | ---: | ---: | ---: |
| T0 | 0.00029 | 0.1142 | 0.0 | 0.0156 |
| T2 | 0.00106 | 0.1151 | 0.0 | 0.0157 |
| T3 | 0.00090 | 0.1414 | 0.0 | 0.0167 |

Paired sign tests：全部不顯著。僅兩個邊緣趨勢（tree 臂略差）：T2−T0 Ali p=0.057（Ali 大多平手：87–79% ties）、T3−T2 Ove 58W/39L p=0.067。**判讀：tree 條件在幾何整潔軸持平——§23.3 的語意組織增益（SGC/TLC/PCA 全顯著）不是用幾何品質換來的**；T3 Ove 邊緣上升與「相關元素放近」一致，屬可解釋的 trade-off 方向但未達顯著。

**協定警告**：本表在 A3 P-Full 協定（text＋image 全部為 placeable、pixel-only background）下計算，**不可**與 Step 89/92 的 text-as-image 協定表或 SEGA Table 3 同表比較；僅供三臂內部對照。S_DL/S_QL/S_TV/S_IO/S_mean4（matched COLE judge，Phase 3 必跑項 7）為付費項目，budget 另行提案。

### 23.6 Failure、cost 與 latency 分析（Phase 3 必跑項 9；zero-cost）

失敗 3/300（1.0%）：CandidateShortfall ×2（T2 `5d67ed46`、T3 `5da04604`——3 個 mapper candidate 只有 2 個完整 render）、Planner 重複 asset ID ×1（T2 `5f644f40`——3 attempts 全數輸出重複 ID，屬 T2 特有失敗模式：T0/T3 無 Planner call）。全部 error record 落盤、fail-loud、無靜默跳過。

Latency（per-call mean）：analyst ~11s（最重，含 vision）、director ~6.6–6.9s、planner 6.6s（僅 T2）、mapper ~3.6–3.8s（×3/sample）、judge_select ~2.8s。Per-sample 端到端約 31–38s；T2 較 T0 多一個 planner call（+6.6s、+100 calls/100 samples）。三臂 calls 600/692/598；provider 回報 token/cost 0（已知現象）、wall time 3153/3782/3214s。

### 23.7 執行決策記錄（2026-07-12，使用者裁示）

1. **所有需要人力的實驗永久跳過**（含 human semantic-grouping preference study）——時程考量；論文相應主張列 limitation，不補人測。
2. **指標修訂由使用者本人接手**——本 session 不再改動任何 metric 定義。
3. **舊架構（pre-A3 pipeline，Step 1–97 線）視為不存在**——論文不引舊表、不維護舊協定可比性；A3 為唯一架構。COLE judge 換用 gpt-5.4-mini 的「與 Step 70/92 不可比」顧慮隨之失效（舊表不進論文）。

### 23.8 Formal SEGA/PKU 六軸重評（hardened evaluator、BASNet+ISNet Occ、zero-cost；2026-07-12）

以強化後的可重現評測器（`layout_agent/evaluate_a3_sega.py` + `metagpt/ext/agentlayout/evaluation/a3_sega_evaluator.py`，Phase 1 hardening 於 commit `7bc92845`）對 Relation N=100 三臂做正式評測。評測 ID `a3-relation-n100-t0-t2-t3-sega-v1`，原子發布於 `layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/a3-relation-n100-t0-t2-t3-sega-v1/`。執行 2617.50s（43m37s）、exit 0、**0 LLM/API call、0 下載、$0.00**。執行指令（含 API-key unset、offline flags、loopback proxy、單執行緒）：

```bash
env -u OPENAI_API_KEY -u ANTHROPIC_API_KEY -u GEMINI_API_KEY \
  -u GOOGLE_API_KEY -u AZURE_OPENAI_API_KEY \
  HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 \
  WANDB_MODE=offline http_proxy=http://127.0.0.1:9 \
  https_proxy=http://127.0.0.1:9 ALL_PROXY=socks5://127.0.0.1:9 \
  NO_PROXY=localhost,127.0.0.1 OMP_NUM_THREADS=1 \
  /usr/bin/time -p conda run --no-capture-output -n meta \
  python layout_agent/evaluate_a3_sega.py \
  --run-dir layout_agent/runs/a3/a3-rel100-t0-01 \
  --run-dir layout_agent/runs/a3/a3-rel100-t2-01 \
  --run-dir layout_agent/runs/a3/a3-rel100-t3-01 \
  --evaluation-id a3-relation-n100-t0-t2-t3-sega-v1 \
  --saliency-mode basnet-isnet
```

結果（cell = `value; applicable_n/valid_n/skipped_n/source_skipped_n/not_applicable_n`；所有軸 `metric_skipped_n=0`）：

| Arm | Ali↓ | Ove↓ | Und_l | Und_s | Rea↓ | Occ↓ |
| --- | --- | --- | --- | --- | --- | --- |
| T0 | `0.00039344577614799106; 100/100/0/0/0` | `0.11029703455008535; 100/100/0/0/0` | `N/A; 0/0/0/0/100` | `N/A; 0/0/0/0/100` | `0; 100/100/0/0/0` | `0.005604150408513137; 100/100/0/0/0` |
| T2 | `0.0010906792273481; 98/98/2/2/0` | `0.11855469211026877; 98/98/2/2/0` | `N/A; 0/0/2/2/98` | `N/A; 0/0/2/2/98` | `0; 98/98/2/2/0` | `0.005628733500349222; 98/98/2/2/0` |
| T3 | `0.0006464536794323366; 99/99/1/1/0` | `0.15041344770396506; 99/99/1/1/0` | `N/A; 0/0/1/1/99` | `N/A; 0/0/1/1/99` | `0; 99/99/1/1/0` | `0.005972501210145759; 99/99/1/1/0` |

Source failures 保留為顯式 `source_skipped` rows（與 §23.2 的 3 例一致）：T2 `5d67ed46cf657b21ef7bdad9`（CandidateShortfall 2/3）、T2 `5f644f40a637ee11e3669a1c`（Planner 重複 asset ID）、T3 `5da04604abc8ea6d1cbe2935`（CandidateShortfall 2/3）。

**與 §23.5 舊表的差異（引用一律以本節為準）**：

1. Occ 改為 frozen **BASNet（revision `c04f6d78a10d2d558260629c3b00a9ed0568dbc6`、本地 snapshot）+ ISNet（`rembg.sessions.dis_general_use.DisSession`、`CPUExecutionProvider`）pixel-wise max**，對全部樣本計算（背景無 raster asset 時重建 R3 的不透明白畫布），非 §23.5 的 n=14 子集；
2. Und_l/Und_s 由「0 by design」更正為 **N/A**——P-Full v1 無合法 underlay 欄位、raster asset 不猜測為 underlay，故 applicable_n=0；
3. Rea 三臂全 0 與 §23.5 一致（平坦填色底圖 Sobel 梯度為 0，無訊號）。

**協定警告**：ISNet 取代 PKU PosterLayout 的 PFPN branch，Occ 只能在「同一 matched pipeline 重評的方法之間」直接比較；published SEGA 數值僅為文獻參考。本表亦不可與 Step 89/92 text-as-image 協定表同表比較。

**獨立驗證（read-only、50 項檢查全過）**：三 artifact SHA-256 與發布記錄一致（manifest `c96937a6…`、aggregate `5eeed54f…`、per_sample `a70121e4…`）；`validate_evaluation_bundle()` 重載通過（records=300, runs=3）；四個適用軸的聚合平均值由 per-sample rows 獨立重算並逐格吻合（rel_tol 1e-12）、zero-contribution/skipped 計數吻合；三臂 per-sample 順序與 manifest 的 100-ID 快照（sha256 `840347c0…`）逐位一致；來源 run trees 前後 hash 不變、無 staging 殘留。

**Status**：Phase 3 必跑項 4（幾何六軸）以本節為 final。付費 matched COLE judge 四軸（S_DL/S_QL/S_TV/S_IO）仍在授權邊界外，須先提 judge snapshot、matched-pair 協定、call 數與預算。
