# A3 SEGA Metrics — Session Handoff

Repository: `/home/hui0705/MetaGPT`

Branch: `feat/step76-89-sega-pipeline`

Updated: 2026-07-12 (Asia/Taipei; independent verification passed, results
documented and committed)

## Current objective

The zero-LLM SEGA/PKU rule-based evaluation requested by
`layout_agent/SEGA_METRICS_REMOTE_AGENT_TASK.md` is COMPLETE. Phase 1
hardening, the real-detector smoke, the formal full Relation T0/T2/T3
evaluation, and the independent read-only verification (50 checks, all green)
all passed. Results are documented in `A3_EXPERIMENT_LOG.md` §23.8 and the
sidecar is committed. The only remaining item is the paid four-axis judge
(S_DL/S_QL/S_TV/S_IO), which stays behind a separate cost and authorization
boundary: before any run, report the exact judge snapshot, matched-pair
protocol, call count, and estimated cost, and obtain explicit approval.

## Phase 1 implementation and hardening — complete

Second-round P1 hardening, production-shape smoke validation, and the
independent Phase 1 gate passed. New contracts in this round:

- BASNet loads from the exact authoritative local snapshot path. The actual
  executed config/model class source paths and hashes must match the snapshot;
- source JSON, render, and background bytes are captured once; parsing/hashing
  and background decoding use those same bytes, with a final source-tree rehash
  after staging and immediately before no-replace publication;
- the sidecar schema now strictly validates every manifest, protocol, runtime,
  detector, source-run, artifact, canvas, element, background, saliency, cost,
  write-policy, per-sample, and aggregate field;
- frozen Occ requires complete static and runtime detector provenance;
- ISNet SHA-256 and producer MD5 are pinned before any runtime import. The exact
  verified ONNX bytes are passed directly to an exact rembg `DisSession` using
  frozen `CPUExecutionProvider`; rembg factory/pooch download paths are bypassed.

`metagpt/ext/agentlayout/evaluation/a3_sega_evaluator.py` now:

- reads only the persisted final B0 selected by `b0_slot_id`;
- validates L0, P-Full v1, and R3 schemas plus sample IDs, canvas/background
  agreement, and R3's exact P-Full manifest hash;
- converts A3 LWH boxes to xyxy, clips them to canvas, and drops intersections
  below 0.1% of canvas area before all six metrics;
- preserves the PKU Ali layout-wide-min quirk, Ove element-count denominator,
  Und_s right-edge quirk, and zero-contribution dataset samples;
- derives underlay eligibility only from the post-filter layout. P-Full v1 has
  no legal underlay field, raster assets are never guessed to be underlays,
  and current A3 Und_l/Und_s therefore remain JSON `null` / N/A;
- uses the actual R3 background asset when present and otherwise reconstructs
  the renderer's opaque white blank canvas;
- reads background bytes once, checks their SHA-256 before decoding, includes
  the background in source-artifact lineage, and rechecks sources after the run;
- validates saliency as an exact 2D canvas-sized, finite float map in `[0,1]`;
  detector errors or malformed maps fail closed;
- aggregates source-failed B0 rows into each metric's `skipped_n` and separately
  reports `metric_skipped_n` and `source_skipped_n`, rather than dropping them;
- records source/render/background/saliency hashes and frozen protocol lineage.

`layout_agent/evaluate_a3_sega.py` now:

- accepts repeated `--run-dir` and requires T0/T2/T3 ordered sample-ID lists to
  match exactly before any detector inference;
- verifies summary total/completed/failed rows, unique ordered sample IDs, the
  manifest's stored sample-ID snapshot/hash/count, and run ID consistency;
- refuses to label a full run formal unless all summary rows are terminal
  `completed` or `failed`; diagnostic `--max-samples` remains non-formal;
- writes only a versioned sidecar outside `layout_agent/runs/a3`;
- validates the complete `a3.sega-evaluation.v1` bundle before publication:
  all six axes, metric status/value invariants, finite values, aggregate count
  conservation, manifest/per-sample order, and strict disk round-trip;
- serializes with `allow_nan=False`;
- builds all three outputs in a staging directory and publishes with Linux
  `renameat2(RENAME_NOREPLACE)`; existing directories, broken symlinks, and
  check-to-publish races cannot be replaced, and failures clean staging;
- captures evaluator/metric/CLI/saliency source hashes and Python/numpy/cv2/
  Pillow/torch/torchvision/transformers/rembg/onnxruntime/provider identity
  before evaluation, then rechecks them immediately before publication;
- has no LLM/API path.

Formal Occ remains frozen BASNet + ISNet with pixel-wise maximum and no Sobel
fallback. Detector artifacts must already be cached; offline mode is forced,
weights/revisions plus BASNet `config.json`, `model.safetensors`,
`configuration_basnet.py`, and `modeling_basnet.py` are hashed, and missing
files fail before inference. BASNet loading passes the recorded commit as
`revision`, sets `local_files_only=True`, and uses the hashed remote code.
rembg must return the exact `isnet-general-use` DisSession identity; its silent
U2Net fallback is rejected, and the actual session/provider identity is
recorded. Detector artifacts are rehashed after inference. ISNet
replaces the PFPN branch used by public PKU PosterLayout. Therefore these Occ
values support direct comparison only when every method is re-evaluated by this
same pipeline. Published SEGA values are literature references only, not direct
cross-paper comparisons.

## Direct deterministic test coverage

Two scoped suites now cover both low-level metric formulas and the real
evaluator/CLI paths:

- `tests/metagpt/ext/agentlayout/test_sega_metrics.py`: 28 cases for §7 geometry,
  Ali/Ove/underlay quirks, float Sobel, masks, zero denominators, and synthetic
  BASNet/ISNet max fusion;
- `tests/metagpt/ext/agentlayout/test_a3_sega_evaluator.py`: 56 cases for formal
  clip/filter underlay N/A, blank background reconstruction, failed-row
  aggregation, malformed summary counts/snapshots, nonterminal formal rejection,
  saliency shape/finite/range fail-closed behavior, background TOCTOU hashing,
  rejection of schema-invalid `sega_class_code`, source-final/render/candidate
  invariants, strict sidecar schema/counts, NaN/Inf/status/value failures,
  no-replace broken-symlink/race behavior, matched-ID/order checks, code/model
  rehashing, exact ISNet identity, and exact BASNet revision/remote-code lineage.

Latest verification command:

```bash
PYTHONDONTWRITEBYTECODE=1 conda run -n meta python -m pytest -q \
  --no-cov -p no:cacheprovider \
  tests/metagpt/ext/agentlayout/test_sega_metrics.py \
  tests/metagpt/ext/agentlayout/test_a3_sega_evaluator.py
```

Latest combined metric + evaluator/CLI result: `84 passed`, 0 failed, 0
skipped (final bounded recheck used a 90-second shell timeout and completed in
7.37 seconds). Independent review must still re-run it before unblocking. The 11
warnings are existing Python
3.9/dependency deprecations.

Python 3.9 compilation also passed:

```bash
PYTHONPYCACHEPREFIX=/tmp/phase1-tests-pycache conda run -n meta \
  python -m py_compile \
  metagpt/ext/agentlayout/evaluation/sega_metrics.py \
  metagpt/ext/agentlayout/evaluation/saliency_basnet_isnet.py \
  metagpt/ext/agentlayout/evaluation/a3_sega_evaluator.py \
  layout_agent/evaluate_a3_sega.py \
  tests/metagpt/ext/agentlayout/test_sega_metrics.py \
  tests/metagpt/ext/agentlayout/test_a3_sega_evaluator.py
```

`git diff --check` and the scoped line-length check passed. Ruff was attempted,
but the `meta` environment has no `ruff` executable/module; do not claim a Ruff
pass and do not install anything from the network during the experiment.

A post-hardening matched validate-only smoke used one sample from each of the
three real Relation runs and wrote only under
`/tmp/a3-sega-hardening-smoke/a3.sega-pku-protocol.v1/hardening2-validate-smoke-1`.
It completed successfully, recorded the full matched 100-ID snapshot and
runtime lineage, and used zero detector inference, model download, LLM/API call,
or cost. No formal Relation N=100 metric run has been launched yet.

## Phase 2 real-detector smoke — complete

At 2026-07-12 03:05 CST, a non-formal `--max-samples 1` run executed actual
BASNet + ISNet inference for the same matched sample in all three Relation arms.
It exited 0 in 44.73 seconds and atomically wrote:

`/tmp/a3-sega-phase2-real-smoke-20260712/a3.sega-pku-protocol.v1/phase2-real-inference-smoke-1`

Exact command (the API-key unsets, offline flags, loopback-only proxy, and
single-thread setting were part of the execution environment):

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
  --evaluation-id phase2-real-inference-smoke-1 \
  --output-root /tmp/a3-sega-phase2-real-smoke-20260712 \
  --saliency-mode basnet-isnet --max-samples 1
```

Smoke aggregates (Ali, Ove, Und_l, Und_s, Rea, Occ):

- T0: `0`, `0.09605119628000214`, `N/A`, `N/A`, `0`,
  `0.002626500702801232`;
- T2: `0`, `0.12941374203268638`, `N/A`, `N/A`, `0`,
  `0.008867530419493225`;
- T3: `0`, `0.12208100618597029`, `N/A`, `N/A`, `0`,
  `0.0021821770869935786`.

Each arm has `selected_n=1`, `evaluated_n=1`, and `skipped_n=0` for the four
applicable axes. Both underlay axes correctly have `value=null`,
`applicable_n=0`, and `not_applicable_n=1`. The sidecar has three JSONL rows
and no staging residue. Runtime identity confirms BASNet revision
`c04f6d78a10d2d558260629c3b00a9ed0568dbc6` loaded from its exact local
snapshot and exact `rembg.sessions.dis_general_use.DisSession` ISNet using
`CPUExecutionProvider`. The run made 0 LLM/API calls, 0 downloads, and cost
$0.00. Source run trees, evaluator code, BASNet artifacts, and ISNet artifact
hashes are identical before and after the smoke.

## Formal Phase 2 Relation evaluation — complete, verification pending

The formal run started at 2026-07-12 03:06:19 CST and exited 0 after
`2617.50` seconds (43m37.50s). It atomically published evaluation ID
`a3-relation-n100-t0-t2-t3-sega-v1` at:

- relative: `layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/a3-relation-n100-t0-t2-t3-sega-v1`;
- absolute: `/home/hui0705/MetaGPT/layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/a3-relation-n100-t0-t2-t3-sega-v1`.

Exact execution command and environment:

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

The manifest records 0 LLM/API calls, 0 model downloads, and `$0.00` LLM
cost. The result has exactly 300 `per_sample.jsonl` rows: T0 evaluated 100/100,
T2 evaluated 98/100 with 2 source failures, and T3 evaluated 99/100 with 1
source failure.

Metric cells below are `value; applicable_n/valid_n/skipped_n/source_skipped_n/not_applicable_n`.
Every metric has `metric_skipped_n=0`.

| Arm | Ali | Ove | Und_l | Und_s | Rea | Occ |
| --- | --- | --- | --- | --- | --- | --- |
| T0 | `0.00039344577614799106; 100/100/0/0/0` | `0.11029703455008535; 100/100/0/0/0` | `N/A; 0/0/0/0/100` | `N/A; 0/0/0/0/100` | `0; 100/100/0/0/0` | `0.005604150408513137; 100/100/0/0/0` |
| T2 | `0.0010906792273481; 98/98/2/2/0` | `0.11855469211026877; 98/98/2/2/0` | `N/A; 0/0/2/2/98` | `N/A; 0/0/2/2/98` | `0; 98/98/2/2/0` | `0.005628733500349222; 98/98/2/2/0` |
| T3 | `0.0006464536794323366; 99/99/1/1/0` | `0.15041344770396506; 99/99/1/1/0` | `N/A; 0/0/1/1/99` | `N/A; 0/0/1/1/99` | `0; 99/99/1/1/0` | `0.005972501210145759; 99/99/1/1/0` |

Source failures retained as explicit `source_skipped` rows:

- T2 `5d67ed46cf657b21ef7bdad9`: `A3L0PipelineError`, candidate shortfall
  (2/3 R0 candidates completed);
- T2 `5f644f40a637ee11e3669a1c`: `ValueError`, PlanAssetsA3 exhausted three
  attempts because the A3 layout tree contained duplicate asset IDs;
- T3 `5da04604abc8ea6d1cbe2935`: `A3L0PipelineError`, candidate shortfall
  (2/3 R0 candidates completed).

The immediate read-only postcheck reloaded the full sidecar through
`validate_evaluation_bundle()` and passed with `records=300`, `runs=3`. There
is no staging residue. Artifact SHA-256 values are:

- `evaluation_manifest.json`: `c96937a6d9b19caf8a87980e0f5bb4a49df346b19df79c4a57eb4ce1fddf4ff9`;
- `aggregate.json`: `5eeed54fc4b9e9e688b2a87300a48477e5ec240b1fdb0a4126b42959db21377d`;
- `per_sample.jsonl`: `a70121e4edd1ebc7f6dbea16435218e9daca1595257aed434a843294afdfd55b`.

Pre/post source tree hashes are identical: T0
`7c3931ee4164705c0c848d05ec68e7ecf5a0b14a9a5beaf3bc4431cf0e4044b8`,
T2 `c460178325ef2d5f017f5bfe9498c0f1126d838bf75bff9a8812173f35c639d6`,
and T3 `0aa7e075fa8b5873950075f3e55fb6e6cdd26c92dc7ed0b6ea8b40e1ddda30e3`.
Evaluator code and all detector artifact hashes and mtimes are also unchanged.
Runtime lineage records BASNet revision
`c04f6d78a10d2d558260629c3b00a9ed0568dbc6` and exact
`rembg.sessions.dis_general_use.DisSession` ISNet with
`CPUExecutionProvider`. ISNet replaces PKU's PFPN branch, so Occ is directly
comparable only across methods re-evaluated with this matched pipeline;
published SEGA results remain literature references only.

## Independent verification — passed (2026-07-12)

A read-only verification script re-ran 50 checks against the published
sidecar, all green:

- SHA-256 of all three artifacts matches the recorded publication hashes;
- `validate_evaluation_bundle()` reloads the full bundle (records=300, runs=3);
- all four applicable-axis aggregate means were independently recomputed from
  `per_sample.jsonl` and match `aggregate.json` cell-by-cell (rel_tol 1e-12),
  including zero-contribution/skipped/applicable counts;
- Und_l/Und_s are `not_applicable` on every evaluated row with null aggregates;
- source-skipped sample IDs match the three recorded failures exactly;
- per-sample ordering in every arm matches the manifest's 100-ID snapshot
  (count and sha256 `840347c0…` self-consistent);
- no staging residue; the sidecar contains exactly the three artifacts.

Results and provenance are documented in `A3_EXPERIMENT_LOG.md` §23.8
(supersedes §23.5 for citation). Do not rerun or overwrite this evaluation ID.

## Phase 2 verification/handoff checklist

1. Done: source run trees are byte-identical and output exists only in the
   versioned sidecar.
2. Done: this handoff records all six aggregate axes, count denominators, and
   every source-failed sample ID/reason.
3. Done: detector revision/hash lineage and the ISNet-for-PFPN,
   matched-evaluator-only caveat are recorded.
4. Done: independent read-only verification of the formal bundle and
   interpretation passed (50/50 checks).
5. Done: results appended to `A3_EXPERIMENT_LOG.md` §23.8; scoped work
   (log, this handoff, evaluation sidecar) committed and pushed.
6. Done: the paid judge proposal below was submitted on 2026-07-12, the user
   authorized it verbatim ("授權"), and the run completed the same day.

## Paid judge run — complete (2026-07-12)

Executed exactly as proposed below. Outcome: 398 API calls (1 param probe +
397 scoring, cap 420 untouched), 397/397 parsed OK, zero retry burn, wall
113.3s. Results published atomically at
`layout_agent/evaluations/a3-cole/a3.cole-judge.v1/a3-relation-n100-cole-v1/`
and documented in `A3_EXPERIMENT_LOG.md` §23.9. Headline: all three arms lose
to designer GT on S_mean4 (77.6–79.5% of GT, p ≤ 2e-18); between-arm
differences are all non-significant — the tree channel moves semantic
organization (§23.3) but neither geometry (§23.8) nor COLE aesthetics.
The SEGA/PKU and COLE evaluation lines are now BOTH CLOSED. No further paid
work is pending in this task file.

## Paid judge proposal (submitted 2026-07-12, authorized and executed)

- Judge snapshot: `gpt-5.4-mini-2026-03-17` (pinned in `~/.metagpt/config2.yaml`;
  already in `MULTI_MODAL_MODELS`, so vision input works — Step 91 lesson
  applied). Temperature 0.0, max_tokens 600.
- Protocol: COLE absolute scoring, one call per image, prompt verbatim from
  `layout_agent/output/step21_phaseb_eval.py::COLE_PROMPT` (5 axes, single
  JSON response). Absolute per-image scoring is blind by construction (no arm
  label reaches the judge). Aggregation: S_mean4 plus per-axis; matched-pair
  stats (two-sided sign test + bootstrap CI on per-sample deltas) for
  T0/T2/T3 vs designer GT and T2−T0, T3−T0, T3−T2, restricted to samples
  where both images exist.
- Inputs (all verified present, zero missing): per-arm selected B0 render
  resolved from the formal sidecar's `b0_slot_id`/`b0_render_sha256`
  (T0 100, T2 98, T3 99) and designer GT
  `layout_agent/output/crello_<id>/ground_truth_preview.jpg` (100).
- Call count: 397 scoring calls, retry margin ≤ ~420. No other paid calls.
- Token estimate: ~700 prompt-text tokens + ~1.1k–2.3k image tokens per call
  (600×1200 canvas), ≤600 output. Totals ≈ 0.7–1.2M input, 0.16–0.24M output.
- Cost estimate: at gpt-5-mini-tier public pricing ($0.25/M input, $2/M
  output) ≈ **$0.6–0.8, hard upper bound < $2**. Caveat: repo cost logs
  historically report $0; the real bill is on the provider dashboard.
- Runtime estimate: ~15–25 min at concurrency 8 (~2.8s/call observed for the
  same judge shape in Step 92).
- Output: new results section in `A3_EXPERIMENT_LOG.md` (§23.9) and a
  versioned result file under `layout_agent/evaluations/`; the SEGA sidecar
  `a3-relation-n100-t0-t2-t3-sega-v1` is never rerun or overwritten.
- Protocol warning: this is an A3-only, GT-referenced table. Old-architecture
  COLE tables (Step 70/92) are not citable next to it (user ruling
  2026-07-12: pre-A3 line treated as nonexistent).
- Stop conditions: pre-flight existence/hash check before the first paid
  call; abort and report if JSON parse-failure rate exceeds 5% in the first
  40 calls; never exceed 420 calls.

## Dirty-worktree boundary

Do not reset, checkout, clean, or overwrite unrelated user work. Phase 1-owned
files are limited to:

- `metagpt/ext/agentlayout/evaluation/a3_sega_evaluator.py`
- `metagpt/ext/agentlayout/evaluation/saliency_basnet_isnet.py`
- `layout_agent/evaluate_a3_sega.py`
- `tests/metagpt/ext/agentlayout/test_sega_metrics.py`
- `tests/metagpt/ext/agentlayout/test_a3_sega_evaluator.py`
- `layout_agent/next_step.md`

Phase 1 code and tests were committed in `7bc92845`. The formal evaluation
sidecar, the §23.8 log entry, and this handoff are committed in the follow-up
scoped commit on `feat/step76-89-sega-pipeline`. No detector download, LLM/API
call, or paid judge has been performed at any point in this line of work.
