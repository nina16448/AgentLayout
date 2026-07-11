# A3 SEGA Metrics — Session Handoff

Repository: `/home/hui0705/MetaGPT`

Branch: `feat/step76-89-sega-pipeline`

Updated: 2026-07-12 02:56 (Asia/Taipei; Phase 1 hardening round 2 checkpoint)

## Current objective

Finish the zero-LLM SEGA/PKU rule-based evaluation requested by
`layout_agent/SEGA_METRICS_REMOTE_AGENT_TASK.md`. Phase 1 hardening is
implemented and locally green, but the formal Relation T0/T2/T3 run is
**blocked pending independent final verification/anti-pattern/code-quality
review**. Do not run N=100 until those reviews clear this checkpoint. The paid
four-axis judge remains behind a separate cost and authorization boundary.

## Phase 1 implementation and hardening — verification pending

Second-round P1 hardening is now implemented and local production-shape smoke
validation passes, but independent review is still pending and the formal N=100
run remains blocked. New contracts in this round:

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

## Exact next action: independent Phase 1 review (formal run blocked)

Re-run the complete combined command, Python compilation, `git diff --check`, and a fresh
one-sample-per-arm `/tmp` validate-only smoke. Independently audit all P1
contracts above. Only after all reviews pass may the following formal command
evaluate the existing Relation T0/T2/T3 runs with cached, offline BASNet and
ISNet:

```bash
conda run -n meta python layout_agent/evaluate_a3_sega.py \
  --run-dir layout_agent/runs/a3/a3-rel100-t0-01 \
  --run-dir layout_agent/runs/a3/a3-rel100-t2-01 \
  --run-dir layout_agent/runs/a3/a3-rel100-t3-01 \
  --evaluation-id a3-relation-n100-t0-t2-t3-sega-v1
```

Do not add `--max-samples` to the formal run. Do not use
`--saliency-mode skip` for a complete six-axis result. The command must not call
an LLM/API or download weights.

Authoritative source counts expected before metric evaluation:

- T0: 100 completed, 0 source-failed;
- T2: 98 completed, 2 source-failed;
- T3: 99 completed, 1 source-failed.

The sidecar should contain 300 `per_sample.jsonl` rows, including all three
explicit `source_skipped` rows. For applicable metrics, T2/T3 `skipped_n` must
include 2/1 source failures. For Und_l/Und_s, `value` and `applicable_n` must
remain `null` and `0`; successful rows are `not_applicable`, while failed source
rows remain separately visible through `source_skipped_n`. Never rewrite N/A as
zero.

After completion, verify:

```bash
jq '.runs | with_entries(.value = {sample_counts: .value.sample_counts, metrics: .value.metrics})' \
  layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/\
a3-relation-n100-t0-t2-t3-sega-v1/aggregate.json

wc -l layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/\
a3-relation-n100-t0-t2-t3-sega-v1/per_sample.jsonl
```

## Phase 2 completion checklist

1. Confirm the source run trees remain byte-identical and outputs exist only in
   the versioned sidecar.
2. Report per arm: six aggregate axes, `valid_n`, `skipped_n`,
   `source_skipped_n`, `applicable_n`, `not_applicable_n`, and every failed
   sample ID/reason.
3. Confirm detector revision/hash lineage and retain the ISNet-for-PFPN,
   matched-evaluator-only, cross-paper-literature-only caveat.
4. Append the exact command, results, and provenance to `A3_EXPERIMENT_LOG.md`.
5. Update this `next_step.md` immediately before any session switch.
6. Do not start S_DL/S_QL/S_TV/S_IO. First report the exact judge snapshot,
   matched-pair protocol, call count, and estimated cost for explicit approval.

## Dirty-worktree boundary

Do not reset, checkout, clean, or overwrite unrelated user work. Phase 1-owned
files are limited to:

- `metagpt/ext/agentlayout/evaluation/a3_sega_evaluator.py`
- `metagpt/ext/agentlayout/evaluation/saliency_basnet_isnet.py`
- `layout_agent/evaluate_a3_sega.py`
- `tests/metagpt/ext/agentlayout/test_sega_metrics.py`
- `tests/metagpt/ext/agentlayout/test_a3_sega_evaluator.py`
- `layout_agent/next_step.md`

No commit or push has been performed. No formal Relation N=100 evaluation,
detector inference, model download, LLM/API call, or paid judge was performed
during Phase 1.
