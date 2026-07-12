# A3 Crello-General N=100 — Session Handoff

Repository: `/home/hui0705/MetaGPT`

Branch: `feat/step76-89-sega-pipeline`

Updated: 2026-07-12 (Asia/Taipei; General N=100 paid generation complete and
postchecked)

## Current objective

The Relation N=100 SEGA and matched COLE lines are complete and pushed through
commit `6b4197f9`. The next `new_plam.md` Phase 3 task is the final
Crello-General N=100 system run. Human preference experiments remain skipped by
the user's decision in `A3_EXPERIMENT_LOG.md` §23.7.

The zero-cost preflight and the explicitly authorized paid generation are
complete. The run finished 100/100 with no failures and passed a zero-cost
full-run validation. No General evaluation judge has been authorized.

## Execution checkpoint 1 — General sample freeze complete

Added a reproducible, model-blind selector:

- `layout_agent/select_a3_general.py`
- `layout_agent/configs/a3_general_n100_l0.json`
- `tests/metagpt/ext/agentlayout/test_a3_general_selection.py`

Formal selection command:

```bash
conda run -n meta python layout_agent/select_a3_general.py \
  --crello-root layout_agent/output \
  --ids-output layout_agent/sample_ids/a3_general_n100.json \
  --provenance-output layout_agent/sample_ids/a3_general_n100.provenance.json \
  --count 100 \
  --seed 42 \
  --documented-raw-test-count 1971
```

Result:

- exit 0;
- locally cached test universe: 1,902 records from the documented 1,971 raw
  split (69 unavailable locally; explicitly recorded availability limitation);
- no semantic, geometry, asset-count, model-output, candidate, or score filter;
- selected 100 with seed 42;
- selected-ID SHA-256:
  `0e5401fb45cb83c573c82be458508e6ace003482b027b667556dfd876aed052c`;
- outputs are write-once and idempotently verify identical reruns;
- API/LLM calls 0; paid cost `$0.00`.

Selector verification:

```text
3 passed, 0 failed
py_compile: passed
git diff --check: passed
```

## Execution checkpoint 2 — text bitmap snapshot complete

This command streamed the public Crello test split and downloaded dataset
bytes. It did not call an LLM or a paid API. It wrote only A3 text-bitmap
sidecars under the selected local sample caches and left `meta.json`
unchanged:

```bash
conda run -n meta python layout_agent/run_a3.py snapshot-text-bitmaps \
  --ids layout_agent/sample_ids/a3_general_n100.json \
  --crello-root layout_agent/output
```

Result:

- exit 0; 91 missing sample sidecars created, 369 text bitmaps saved;
- selected sample readiness: 100/100;
- mismatches 0; missing 0;
- selected `meta.json` aggregate SHA-256 stayed
  `358ac01bea8585cb4cabebebec512a086ff904f5c03f004c469372b3d4943370`;
- LLM/API calls 0; paid cost `$0.00`.

## Execution checkpoint 3 — run init and P-Full complete

- `plan`: exit 0, 100 samples, target did not previously exist;
- immutable run initialized at
  `layout_agent/runs/a3/a3-general-n100-t2-l0-01`;
- `prepare-pfull`: 100 total, 0 failed;
- API/LLM calls 0; paid cost `$0.00`.

## Execution checkpoint 4 — R3, vision, and paid gate complete

The completed commands are retained below for reproducibility:

```bash
conda run -n meta python layout_agent/run_a3.py plan \
  --config layout_agent/configs/a3_general_n100_l0.json \
  --sample-ids layout_agent/sample_ids/a3_general_n100.json \
  --run-id a3-general-n100-t2-l0-01

conda run -n meta python layout_agent/run_a3.py init \
  --config layout_agent/configs/a3_general_n100_l0.json \
  --sample-ids layout_agent/sample_ids/a3_general_n100.json \
  --run-id a3-general-n100-t2-l0-01

conda run -n meta python layout_agent/run_a3.py prepare-pfull \
  --run-dir layout_agent/runs/a3/a3-general-n100-t2-l0-01 \
  --crello-root layout_agent/output

conda run -n meta python layout_agent/run_a3.py normalize-r3 \
  --run-dir layout_agent/runs/a3/a3-general-n100-t2-l0-01

conda run -n meta python layout_agent/run_a3.py prepare-analyst-vision \
  --run-dir layout_agent/runs/a3/a3-general-n100-t2-l0-01
```

R3 normalization and analyst-vision preparation both completed 100/100 with
zero failures. The paid gate was then run without authorization and behaved as
required:

```bash
conda run -n meta python layout_agent/run_a3.py run \
  --run-dir layout_agent/runs/a3/a3-general-n100-t2-l0-01 \
  --tree-arm T2 \
  --analyst-arm vision
```

Result:

- exit 2 before importing or calling the LLM path;
- `authorized=false`;
- nominal budget: 7 calls/sample × 100 = 700 calls;
- each stage permits at most three reliability attempts, so the code-bound
  theoretical maximum is 21 calls/sample × 100 = 2,100 calls;
- run manifest remains `initialized`, completion 0/0/100, cost empty, errors
  empty;
- all three preparation summaries are 100 total / 0 failed;
- total preflight API/LLM calls 0; paid cost `$0.00`.

## Paid proposal — explicitly authorized

- Frozen model: `gpt-5.4-mini-2026-03-17`.
- Protocol: A3-MLLM, P-Full, R3, predicted tree (T2), L0, vision analyst,
  three spatial concepts/candidates, internal blind candidate selection.
- Nominal calls: 700. Code-bound retry maximum: 2,100.
- Prior matched Relation T2 artifacts contain approximately 2.38M text-input
  tokens and 0.56M output tokens at similar N; image input is additional.
- Conservative authorization budget: 10M input tokens plus 2.25M output
  tokens. At the current official GPT-5.4 mini rates ($0.75/M input,
  $4.50/M output), that is $17.625; proposed billing ceiling: **US$20**.
- Runtime estimate: 60–75 minutes, based on the prior Relation T2 N=100 run.
- Provider usage fields have historically reported zero, so the real bill must
  be checked in the provider dashboard; token totals above are a conservative
  authorization budget, not reliable runtime telemetry.
- Official pricing snapshot:
  `https://developers.openai.com/api/docs/models/gpt-5.4-mini`.

Authorization received verbatim:

> 授權執行 a3-general-n100-t2-l0-01，最多 2100 calls、10M input tokens、2.25M
> output tokens、US$20。

Authorized command:

```bash
conda run -n meta python layout_agent/run_a3.py run \
  --run-dir layout_agent/runs/a3/a3-general-n100-t2-l0-01 \
  --tree-arm T2 \
  --analyst-arm vision \
  --allow-api-calls
```

Do not broaden this authorization to another run ID, model, dataset, loop,
evaluation judge, or follow-up paid task.

## Execution checkpoint 5 — paid generation complete

- command exit 0;
- wall time: 07:10:10–08:02:33 CST, about 3,143 seconds (52m23s);
- summary: 100 total, 100 completed, 0 failed;
- successful stage records: 700; persisted model attempts: 714, below the
  authorized 2,100-call limit;
- request JSON bytes: 8,512,166; raw responses plus Analyst outputs:
  1,845,357 bytes;
- rough text-only estimate: about 2.13M input and 0.46M output tokens, with
  image tokens additional; snapshot token telemetry remains unsupported, so
  the real charge must be checked in the provider dashboard;
- two non-fatal warning classes were observed: unsupported snapshot token
  counting and HTTPX cleanup after repeated `asyncio.run`
  (`RuntimeError: Event loop is closed`); neither caused a sample failure;
- no evaluation judge or other paid task was run.

Zero-cost postcheck:

```bash
conda run -n meta python layout_agent/evaluate_a3_sega.py \
  --run-dir layout_agent/runs/a3/a3-general-n100-t2-l0-01 \
  --evaluation-id a3-general-n100-postrun-validate-v1 \
  --output-root /tmp/a3-general-postrun-20260712 \
  --validate-only
```

Result: exit 0; 100 validated-only records, 0 source skipped, 0 API calls,
`$0.00`, and 0 source artifacts modified.

Results are documented in `A3_EXPERIMENT_LOG.md` §24.

## Next task and stop conditions

- The General N=100 generation is complete; do not rerun or overwrite it.
- Next: zero-cost deterministic geometry, completion, failure, and latency
  evaluation for this General run.
- Treat the HTTPX event-loop cleanup warning as a separate zero-cost runtime
  hygiene fix; it does not invalidate the completed artifacts.
- Do not run General-vs-GT COLE or any other paid evaluation without a new,
  exact budget and explicit authorization.
- Preserve all unrelated dirty files and existing write-once runs.
- Commit only §24 and this handoff, then push before switching sessions.
