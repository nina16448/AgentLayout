# A3 Crello-General N=100 — Session Handoff

Repository: `/home/hui0705/MetaGPT`

Branch: `feat/step76-89-sega-pipeline`

Updated: 2026-07-12 09:14 CST (Asia/Taipei; General generation, SEGA, and COLE
complete and independently verified; scoped commit/push remain)

## Current objective

The Relation N=100 SEGA and matched COLE lines are complete and pushed through
commit `6b4197f9`. The Crello-General N=100 generation, formal SEGA/PKU
evaluation, and separately authorized General-vs-GT COLE judge are also
complete: generation finished 100/100 with no failures, the deterministic
sidecar evaluated 100/100, and the COLE judge published 200/200 successful
blind scores. Human preference experiments remain skipped by the user's
decision in `A3_EXPERIMENT_LOG.md` §23.7.

The only remaining task for these General N=100 changes is zero-cost scoped
verification followed by a commit and push containing exactly the eight paths
listed in checkpoint 15. No completed generation or evaluation may be rerun;
the COLE authorization has been consumed.

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

## Execution checkpoint 6 — General metric launcher retry

The first formal zero-cost metric command did not reach evaluator startup:

```bash
conda run --no-capture-output -n meta python \
  layout_agent/evaluate_a3_sega.py \
  --run-dir layout_agent/runs/a3/a3-general-n100-t2-l0-01 \
  --evaluation-id a3-general-n100-sega-v1 \
  --output-root layout_agent/evaluations/a3-sega \
  --saliency-mode basnet-isnet
```

Result: exit 1 before model loading because the managed filesystem made
`/home/hui0705/.conda/envs/meta` read-only and `conda run` attempted to
create a temporary wrapper there. API calls 0, paid cost `$0.00`, detector
inference 0, and no final or staging evaluation directory was created.

The direct-interpreter retry then passed the conda boundary but stopped during
`rembg` import because Numba attempted to cache
`pymatting.util.kdtree._make_tree` relative to read-only site-packages
(`RuntimeError: no locator available`). It also performed 0 API calls,
`$0.00`, 0 detector inference, and published no sidecar.

Safe retry uses the exact existing environment interpreter and relocates all
temporary/Numba cache writes to `/tmp`:

```bash
TMPDIR=/tmp NUMBA_CACHE_DIR=/tmp/a3-numba-cache \
/home/hui0705/.conda/envs/meta/bin/python \
  layout_agent/evaluate_a3_sega.py \
  --run-dir layout_agent/runs/a3/a3-general-n100-t2-l0-01 \
  --evaluation-id a3-general-n100-sega-v1 \
  --output-root layout_agent/evaluations/a3-sega \
  --saliency-mode basnet-isnet
```

That retry completed successfully with the full offline/API-key-unset guard
shown in `A3_EXPERIMENT_LOG.md` §24.4. The two failed launch attempts occurred
before detector inference, made 0 API calls, cost `$0.00`, and published no
partial sidecar.

## Execution checkpoint 7 — formal deterministic evaluation complete

- evaluation ID: `a3-general-n100-sega-v1`;
- artifact:
  `layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/a3-general-n100-sega-v1/`;
- 100 selected / 100 source-valid / 100 evaluated; skipped 0;
- Ali 0.0019674123, Ove 0.1001687017, Rea 0.0003544206, Occ
  0.0055605521; Und_l/Und_s N/A because P-Full v1 has no legal underlay;
- independent strict bundle reload and per-sample mean recomputation passed;
- SHA-256: manifest `ee6f4d3284c9…`, aggregate `dc5dfe244693…`,
  per_sample `a72c699ff4ea…`;
- generation completion 100/100, failures 0, 714 persisted attempts;
- generation wall 3,143s (~31.43s/sample); per-call mean latency: Analyst
  8.80s, Planner 4.95s, Director 6.18s, Mapper 2.88s, Judge Select 2.26s;
- selected-B0 QC passed 31/100. All 17 `missing_element` diagnostics point to
  the explicitly excluded background asset, so they are QC false positives,
  not foreground completion failures;
- LLM/API calls 0, paid cost `$0.00`, source artifacts modified 0.

Full interpretation and reproducible command are in
`A3_EXPERIMENT_LOG.md` §24.4.

## Execution checkpoint 8 — commit/push blocked by managed Git permissions

After bundle validation and `git diff --check` passed, the required scoped
commit was attempted. `git add` stopped before changing the index with:

```text
fatal: Unable to create '/home/hui0705/MetaGPT/.git/index.lock': Read-only file system
```

No commit was created and nothing was pushed. This is an execution-environment
permission blocker, not a content or Git conflict. In a session where `.git`
is writable, resume with:

```bash
git add -- \
  layout_agent/A3_EXPERIMENT_LOG.md \
  layout_agent/next_step.md \
  layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/a3-general-n100-sega-v1/evaluation_manifest.json \
  layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/a3-general-n100-sega-v1/aggregate.json \
  layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/a3-general-n100-sega-v1/per_sample.jsonl
git diff --cached --check
git commit -m "docs(agentlayout): publish General N100 deterministic evaluation"
git push
```

Do not stage any other dirty or untracked file.

## Execution checkpoint 9 — General-vs-GT paid COLE preflight complete

> **HISTORICAL — SUPERSEDED BY CHECKPOINT 14. DO NOT RUN THE PAID JUDGE.**
> The authorization recorded here was consumed successfully and cannot be
> reused or broadened.

At this checkpoint, the user had given a general authorization to run the paid
evaluation. Per the project cost guardrail, paid execution was stopped until
the exact evaluation/model/call/token/USD ceilings below were explicitly
confirmed.

Zero-cost read-only preflight result:

- evaluation ID: `a3-general-n100-cole-v1`;
- judge snapshot: `gpt-5.4-mini-2026-03-17`;
- inputs: 100 pinned General B0 renders + the same 100 IDs' designer GT
  previews, 200/200 present;
- all B0 SHA-256 pins and ordered sample IDs match the formal SEGA sidecar;
- input snapshot SHA-256: `aa7c5b236bc8655bf182cfe8fc898266fbb8e136b30c3f8ae2e7e89bbcb5fa72`;
- write-once final and staging targets are both absent;
- API/LLM calls 0; paid cost `$0.00`.

Proposed exact authorization:

> 授權執行 a3-general-n100-cole-v1，judge=gpt-5.4-mini-2026-03-17，最多
> 220 calls（含 probe/retry）、3M input tokens、150k output tokens、US$4。

Nominal use is 201 calls (200 image scores + one parameter probe); the
remaining 19 calls are retry headroom. Expected runtime is about 1–3 minutes,
based on the completed Relation judge run. Abort before publication on any
input/hash/write-once mismatch, on more than 5% failures in the first 40
scores, or when any authorized ceiling is reached. This authorization must not
be broadened to another evaluation ID, model, dataset, or follow-up run.

Exact authorization received verbatim:

> 授權執行 a3-general-n100-cole-v1，judge=gpt-5.4-mini-2026-03-17，最多
> 220 calls（含 probe/retry）、3M input tokens、150k output tokens、US$4。

The authorization applied only to the evaluation above. The dedicated runner
is `layout_agent/judge_a3_general_cole.py`; it requires
`--allow-api-calls`, rechecks the frozen input snapshot before and after paid
calls, enforces the 220-call gate, records provider-reported token usage, and
publishes atomically only after the post-run hash check.

## Execution checkpoint 10 — authorized runner verification passed

Before any paid request, the dedicated runner passed all zero-cost checks:

- `py_compile`: passed;
- omission of both CLI modes: correctly refused with exit 2;
- `--preflight`: 100 pinned General B0 + 100 pinned GT, input snapshot exact,
  nominal 201 calls <= hard cap 220;
- `git diff --check`: passed;
- API/LLM calls 0; paid cost `$0.00`.

Historical authorized paid command (**already consumed; DO NOT RUN**):

```bash
TMPDIR=/tmp /home/hui0705/.conda/envs/meta/bin/python \
  layout_agent/judge_a3_general_cole.py --allow-api-calls
```

Post-interruption zero-cost verification passed: final/staging artifacts are
absent, `py_compile` passed, the frozen 200-image preflight passed, resumed
aggregate nominal usage is 202/220 calls including the interrupted probe, and
`git diff --check` passed.

## Execution checkpoint 12 — scoped commit/push blocked again

The scoped add/commit/push attempt for the General deterministic sidecar,
experiment log, handoff, and paid-judge runner stopped at `git add` with:

```text
fatal: Unable to create '/home/hui0705/MetaGPT/.git/index.lock': Read-only file system
```

Nothing was staged, committed, or pushed at this historical checkpoint. The
then-planned paid resume in checkpoint 11 was later completed by checkpoint 14
and is now **superseded; DO NOT RUN IT**. After checkpoint 14's successful
atomic publication and the checkpoint 15 verification, the still-current
scoped staging list is:

```bash
git add -- \
  layout_agent/judge_a3_general_cole.py \
  layout_agent/A3_EXPERIMENT_LOG.md \
  layout_agent/next_step.md \
  layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/a3-general-n100-sega-v1/evaluation_manifest.json \
  layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/a3-general-n100-sega-v1/aggregate.json \
  layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/a3-general-n100-sega-v1/per_sample.jsonl \
  layout_agent/evaluations/a3-cole/a3.cole-judge.v1/a3-general-n100-cole-v1/aggregate.json \
  layout_agent/evaluations/a3-cole/a3.cole-judge.v1/a3-general-n100-cole-v1/per_sample.jsonl
git diff --cached --check
git commit -m "feat(agentlayout): publish General N100 evaluations"
git push
```

Do not include any other pre-existing dirty or untracked file.

## Execution checkpoint 11 — paid launch blocked by sandbox network isolation

> **HISTORICAL — SUPERSEDED BY CHECKPOINT 14. DO NOT RUN THE COMMAND BELOW.**
> It is retained only to explain the interrupted attempt and conservative
> cross-session ledger. The authorization is now consumed.

The authorized command passed its in-process hash preflight and entered the
single parameter-compatibility probe. It then received no API response or
error for more than three minutes. A one-time process inspection confirmed
that this managed execution profile uses an isolated/restricted network. The
stalled probe was interrupted with exit 130; the runner did not start the 200
image-scoring tasks and did not create either the final or staging artifact.

Conservative budget accounting:

- successful API responses: 0;
- locally initiated attempts: at most 1 compatibility probe;
- provider-reported usage: unavailable because no response arrived;
- published artifacts: 0;
- actual billing: likely `$0.00`, but must be confirmed in the provider
  dashboard because delivery to the provider cannot be proven locally.

To guarantee the original authorization is never exceeded across sessions,
the runner now carries forward that interrupted probe as 1 call plus a
conservative reserve of 1,000 input and 600 output tokens. Therefore a resumed
run can initiate at most 219 additional calls while staying inside the
original 220-call / 3M-input / 150k-output / `$4` ceilings.

Historical resume command (**DO NOT RUN; checkpoint 14 completed it once**):

```bash
TMPDIR=/tmp /home/hui0705/.conda/envs/meta/bin/python \
  layout_agent/judge_a3_general_cole.py --allow-api-calls
```

## Execution checkpoint 13 — checkpoint 11-12 resume gates passed

> **HISTORICAL — SUPERSEDED BY CHECKPOINT 14. DO NOT RUN THE PAID COMMAND
> BELOW.** The gate evidence remains useful, but it is not an active resume
> instruction.

At `2026-07-12 08:54:32 CST (+0800)`, the new session ran only zero-cost
connectivity and Git-write gates. It did not start the paid judge.

OpenAI network probe (no `Authorization` header, no API key, no model call,
and a hard 15-second timeout):

```bash
http_code=$(curl --silent --show-error --max-time 15 \
  --output /dev/null --write-out '%{http_code}' \
  https://api.openai.com/v1/models)
curl_rc=$?
```

Result: `curl_rc=0`, `http_code=401`. The expected unauthenticated HTTP
response proves DNS, TLS, and routing to the OpenAI API are currently
available. API/model calls 0, token use 0, paid cost `$0.00`.

Git index-write and repository-state gate:

```bash
git_refresh_output=$(git update-index --refresh 2>&1)
git_refresh_rc=$?
test -e .git/index.lock
git branch --show-current
git rev-parse HEAD
git diff --cached --name-only
git status --short --branch
```

Result:

- `git_update_index_refresh_rc=1` only because the existing unstaged tracked
  files reported `needs update`; there was no `index.lock`, read-only, or
  permission failure;
- `.git/index` is writable in this session, `.git/index.lock` was absent after
  the check, and no task or unrelated content was staged;
- branch: `feat/step76-89-sega-pipeline`;
- HEAD: `c6340319b32b13db9bd348cf563d28f8aa188adf`;
- staged paths: none;
- the pre-existing dirty worktree remains preserved: 7 tracked files are
  modified and the existing untracked paths shown by `git status` remain
  untracked. This gate changed no path other than this required handoff update.

Write-once artifact status was checked with:

```bash
rg -n "evaluation-id|evaluation_id|staging|output_dir|a3-general-n100-cole-v1" \
  layout_agent/judge_a3_general_cole.py
find layout_agent/evaluations/a3-cole -maxdepth 4 -mindepth 1 -print \
  2>/dev/null
```

Both paid-run targets remain absent:

- final:
  `layout_agent/evaluations/a3-cole/a3.cole-judge.v1/a3-general-n100-cole-v1`;
- staging:
  `layout_agent/evaluations/a3-cole/a3.cole-judge.v1/.staging-a3-general-n100-cole-v1`.

Only the completed Relation judge artifact exists under that protocol root.
Paid/API cost for all checkpoint 13 commands: `$0.00`.

At that time, what remained was to run the checkpoint 11 command once, enforce
the original cumulative ceilings and stop conditions, verify atomic
publication, update the experiment log and this handoff, then run focused
checks and the checkpoint 12 scoped commit/push. Checkpoint 14 completed that
paid step; it must not be repeated.

Historical paid command (**DO NOT RUN; authorization consumed**):

```bash
TMPDIR=/tmp /home/hui0705/.conda/envs/meta/bin/python \
  layout_agent/judge_a3_general_cole.py --allow-api-calls
```

## Execution checkpoint 14 — authorized General-vs-GT COLE judge complete

At `2026-07-12 08:58:40 CST (+0800)`, the launch gates were rechecked in the
current session before spending any authorized budget:

- unauthenticated `https://api.openai.com/v1/models` returned HTTP 401 with
  `curl_rc=0`, proving DNS/TLS/routing without a model call or token use;
- `.git` and `.git/index` were writable, `.git/index.lock` was absent, the
  branch remained `feat/step76-89-sega-pipeline`, HEAD remained
  `c6340319b32b13db9bd348cf563d28f8aa188adf`, and nothing was staged;
- no `judge_a3_general_cole.py` process was already running;
- both the final and staging write-once targets were absent;
- the runner's existing project configuration could supply its API client
  without printing the credential.

These gate checks made 0 API/model calls, used 0 tokens, and cost `$0.00`.
The single authorized paid command was then run exactly once:

```bash
TMPDIR=/tmp /home/hui0705/.conda/envs/meta/bin/python \
  layout_agent/judge_a3_general_cole.py --allow-api-calls
```

Result:

- start/end: approximately `2026-07-12 09:00:13–09:01:14 CST (+0800)`;
  runner wall time `61.59s`; exit 0;
- pre- and post-call frozen input snapshot:
  `aa7c5b236bc8655bf182cfe8fc898266fbb8e136b30c3f8ae2e7e89bbcb5fa72`;
- compatibility probe selected `max_completion_tokens=600`;
- 200/200 blind image scores returned `ok`; no scoring retry, early-failure
  abort, hash mismatch, write-once mismatch, or authorization guard fired;
- cumulative ledger: 202 calls = 1 prior interrupted probe reserve + 1 resumed
  compatibility probe + 200 successful scores, below the 220-call ceiling;
- cumulative token ledger: 312,795 input and 41,653 output, including the
  prior conservative reserves of 1,000 input and 600 output; 200 calls
  reported usage, so the provider-reported scoring portion was 311,795 input
  and 41,053 output tokens;
- conservative ledger cost estimate: `$0.422035`, below `$4`; actual billing
  still must be checked in the provider dashboard;
- atomic publication:
  `layout_agent/evaluations/a3-cole/a3.cole-judge.v1/a3-general-n100-cole-v1/`;
  staging is absent;
- artifact SHA-256: aggregate
  `f4ea72902a598996687240074be255d13c188304342169470084b56dda42fcb8`,
  per-sample
  `56671d43916762c85c7ae30aa11dd91ed1151a12741a0f2e2fa376edb45706b7`;
- General `S_mean4=5.4675`, designer GT `S_mean4=6.6725`, General/GT
  `81.9408%`; paired General-vs-GT: 100 pairs, 10 wins / 85 losses / 5 ties,
  mean delta `-1.205`, bootstrap 95% CI `[-1.42, -0.9925]`, two-sided sign
  `p=5.7623e-16`.

Immediate zero-cost postcheck confirmed 200 JSONL rows, valid aggregate JSON,
the hashes above, absent staging, and `git diff --check` exit 0. API/model
calls 0 and cost `$0.00` for that postcheck.

At `2026-07-12 09:04:26 CST (+0800)`, a focused read-only verification used
the following command family (no API/LLM path):

```bash
jq -e '.evaluation_id == "a3-general-n100-cole-v1" and \
  .judge_model == "gpt-5.4-mini-2026-03-17" and \
  .status_counts == {"ok": 200} and .usage.calls == 202 and \
  .usage.input_tokens == 312795 and .usage.output_tokens == 41653 and \
  .paired.general_vs_gt.n_pairs == 100 and \
  .paired.general_vs_gt.smean4.wins == 10 and \
  .paired.general_vs_gt.smean4.losses == 85 and \
  .paired.general_vs_gt.smean4.ties == 5' \
  layout_agent/evaluations/a3-cole/a3.cole-judge.v1/a3-general-n100-cole-v1/aggregate.json
jq -s -e 'length == 200 and \
  (map(select(.status == "ok")) | length == 200) and \
  (map(select(.arm == "general")) | length == 100) and \
  (map(select(.arm == "gt")) | length == 100) and \
  (map(.sample_id) | unique | length == 100)' \
  layout_agent/evaluations/a3-cole/a3.cole-judge.v1/a3-general-n100-cole-v1/per_sample.jsonl
sha256sum \
  layout_agent/evaluations/a3-cole/a3.cole-judge.v1/a3-general-n100-cole-v1/aggregate.json \
  layout_agent/evaluations/a3-cole/a3.cole-judge.v1/a3-general-n100-cole-v1/per_sample.jsonl
test ! -e layout_agent/evaluations/a3-cole/a3.cole-judge.v1/.staging-a3-general-n100-cole-v1
rg -n '24\.5 General-vs-designer-GT|202 calls|f4ea7290|56671d43|checkpoint 14|paid judge' \
  layout_agent/A3_EXPERIMENT_LOG.md layout_agent/next_step.md
git diff --check
```

Result: exit 0. Aggregate identity/model/usage/paired assertions passed;
per-sample has 200 rows, all `ok`, split 100 General + 100 GT over the same
100 unique IDs; both hashes matched; staging was absent; documentation anchors
were present; diff whitespace validation passed. API/model calls 0, token use
0, paid cost `$0.00`. Artifacts were read only and no source run was modified.

What remains: perform the already documented checkpoint 12 scoped commit/push
without staging unrelated dirty paths. The focused read-only verification is
complete. Do **not** rerun the paid
judge: its write-once final artifact now exists. Safest resume verification:

```bash
jq -e '.evaluation_id == "a3-general-n100-cole-v1" and \
  .judge_model == "gpt-5.4-mini-2026-03-17" and \
  .status_counts.ok == 200 and .usage.calls == 202' \
  layout_agent/evaluations/a3-cole/a3.cole-judge.v1/a3-general-n100-cole-v1/aggregate.json
test "$(wc -l < layout_agent/evaluations/a3-cole/a3.cole-judge.v1/a3-general-n100-cole-v1/per_sample.jsonl)" -eq 200
test ! -e layout_agent/evaluations/a3-cole/a3.cole-judge.v1/.staging-a3-general-n100-cole-v1
git diff --check
```

## Execution checkpoint 15 — independent zero-cost final audit complete

At `2026-07-12 09:14:46 CST (+0800)`, an independent read-only verification
and code/documentation audit rechecked the published General SEGA and COLE
artifacts. It did not import or call an API client, did not run either
evaluation, did not modify source runs or artifacts, and made 0 API/model calls
with token use 0 and paid cost `$0.00`.

The artifact identity, strict parsing, independent statistics, every recorded
input-image pin, input snapshot, formatting, runner syntax, documentation, and
Git whitespace checks were covered by this zero-cost command set:

```bash
sha256sum \
  layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/a3-general-n100-sega-v1/evaluation_manifest.json \
  layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/a3-general-n100-sega-v1/aggregate.json \
  layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/a3-general-n100-sega-v1/per_sample.jsonl \
  layout_agent/evaluations/a3-cole/a3.cole-judge.v1/a3-general-n100-cole-v1/aggregate.json \
  layout_agent/evaluations/a3-cole/a3.cole-judge.v1/a3-general-n100-cole-v1/per_sample.jsonl
wc -l \
  layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/a3-general-n100-sega-v1/per_sample.jsonl \
  layout_agent/evaluations/a3-cole/a3.cole-judge.v1/a3-general-n100-cole-v1/per_sample.jsonl

/home/hui0705/.conda/envs/meta/bin/python - <<'PY'
from collections import Counter
from pathlib import Path
import hashlib
import json
import math
import random
import statistics

root = Path("/home/hui0705/MetaGPT")
artifact = root / (
    "layout_agent/evaluations/a3-cole/a3.cole-judge.v1/"
    "a3-general-n100-cole-v1"
)
sidecar = root / (
    "layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/"
    "a3-general-n100-sega-v1"
)

def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def close(left, right):
    return math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-12)

aggregate = json.loads((artifact / "aggregate.json").read_text())
lines = (artifact / "per_sample.jsonl").read_text().splitlines()
rows = [json.loads(line) for line in lines]
assert len(lines) == 200 and all(lines)
assert Counter(row["arm"] for row in rows) == {"general": 100, "gt": 100}
assert Counter(row["status"] for row in rows) == {"ok": 200}
assert len({(row["arm"], row["sample_id"]) for row in rows}) == 200

ids = sorted({row["sample_id"] for row in rows})
by_arm = {
    arm: {row["sample_id"]: row for row in rows if row["arm"] == arm}
    for arm in ("general", "gt")
}
assert len(ids) == 100 and set(by_arm["general"]) == set(by_arm["gt"])
report_axes = ("SDL", "SQL", "STV", "SIO")
all_axes = ("SDL", "SQL", "STV", "SGI", "SIO")
for row in rows:
    assert close(
        row["smean4"], statistics.mean(row["scores"][axis] for axis in report_axes)
    )
    path = root / row["path"]
    assert path.is_file() and sha256(path) == row["render_sha256"]

means = {}
for arm in ("general", "gt"):
    arm_rows = list(by_arm[arm].values())
    means[arm] = {
        "smean4": statistics.mean(row["smean4"] for row in arm_rows),
        **{
            axis: statistics.mean(row["scores"][axis] for row in arm_rows)
            for axis in all_axes
        },
    }
    for key, value in means[arm].items():
        assert close(value, aggregate["arm_means"][arm][key])
ratio = 100 * means["general"]["smean4"] / means["gt"]["smean4"]
assert close(ratio, aggregate["arm_means"]["general"]["pct_of_gt_smean4"])

deltas = [
    by_arm["general"][sample_id]["smean4"]
    - by_arm["gt"][sample_id]["smean4"]
    for sample_id in ids
]
wins = sum(delta > 0 for delta in deltas)
losses = sum(delta < 0 for delta in deltas)
ties = sum(delta == 0 for delta in deltas)
sign_p = min(
    1.0,
    2
    * sum(math.comb(wins + losses, index) for index in range(min(wins, losses) + 1))
    / (2 ** (wins + losses)),
)
rng = random.Random(20260712)
boot = sorted(
    statistics.mean(deltas[rng.randrange(len(deltas))] for _ in deltas)
    for _ in range(10_000)
)
paired = aggregate["paired"]["general_vs_gt"]["smean4"]
assert (wins, losses, ties) == (paired["wins"], paired["losses"], paired["ties"])
assert close(statistics.mean(deltas), paired["bootstrap"]["mean_delta"])
assert close(boot[250], paired["bootstrap"]["ci95_low"])
assert close(boot[9750], paired["bootstrap"]["ci95_high"])
assert close(sign_p, paired["sign_p"])

manifest = json.loads((sidecar / "evaluation_manifest.json").read_text())
side_rows = [
    json.loads(line)
    for line in (sidecar / "per_sample.jsonl").read_text().splitlines()
    if line
]
evaluated = [row for row in side_rows if row["status"] == "evaluated"]
ordered_ids = manifest["matched_samples"]["ordered_sample_ids"]
assert [row["sample_id"] for row in evaluated] == ordered_ids
gt_by_id = by_arm["gt"]
snapshot_payload = "\n".join(
    ordered_ids
    + [row["b0_render_sha256"] for row in evaluated]
    + [gt_by_id[sample_id]["render_sha256"] for sample_id in ordered_ids]
).encode()
snapshot = hashlib.sha256(snapshot_payload).hexdigest()
assert snapshot == aggregate["input_snapshot_sha256"]
print(len(rows), means, ratio, wins, losses, ties, sign_p, snapshot)
PY

/home/hui0705/.conda/envs/meta/bin/python - <<'PY'
from pathlib import Path
import json

root = Path("/home/hui0705/MetaGPT")
json_paths = [
    root / "layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/a3-general-n100-sega-v1/evaluation_manifest.json",
    root / "layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/a3-general-n100-sega-v1/aggregate.json",
    root / "layout_agent/evaluations/a3-cole/a3.cole-judge.v1/a3-general-n100-cole-v1/aggregate.json",
]
jsonl_paths = [
    root / "layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/a3-general-n100-sega-v1/per_sample.jsonl",
    root / "layout_agent/evaluations/a3-cole/a3.cole-judge.v1/a3-general-n100-cole-v1/per_sample.jsonl",
]
for path in json_paths:
    raw = path.read_text()
    value = json.loads(raw, parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)))
    schema_order = json.dumps(value, indent=2, allow_nan=False) + "\n"
    sorted_order = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    assert raw in (schema_order, sorted_order)
for path in jsonl_paths:
    raw = path.read_text()
    assert raw.endswith("\n") and all(raw.splitlines())
    for line in raw.splitlines():
        value = json.loads(line)
        assert line == json.dumps(value, sort_keys=True, allow_nan=False)
runner = root / "layout_agent/judge_a3_general_cole.py"
compile(runner.read_text(), str(runner), "exec")
print("strict JSON/JSONL and runner syntax: PASS")
PY

test ! -e \
  layout_agent/evaluations/a3-cole/a3.cole-judge.v1/.staging-a3-general-n100-cole-v1
git diff --check
git diff --cached --check
```

All substantive commands exited 0. The SEGA JSONL has 100 rows and the COLE
JSONL has 200 rows. The audit independently confirmed 100 General plus 100 GT
rows, all `ok`, over 100 unique paired IDs; all 200 recorded input paths and
SHA-256 pins matched disk, and the rebuilt input snapshot was
`aa7c5b236bc8655bf182cfe8fc898266fbb8e136b30c3f8ae2e7e89bbcb5fa72`.

Recomputed General/GT `S_mean4` values were `5.4675`/`6.6725`, or
`81.94080179842638%`. Paired General-vs-GT was 10W/85L/5T with mean delta
`-1.205`, deterministic 10k bootstrap 95% CI `[-1.42, -0.9925]`, and exact
two-sided sign-test `p=5.762323641301719e-16`. Per-axis means, W/L/T counts,
and p-values also matched the aggregate. The 200 scoring rows summed to
311,795 input and 41,053 output tokens; after the pre-existing conservative
reserves the artifact ledger is 312,795 input, 41,653 output, 202 calls, and an
estimated `$0.422035`, all below the authorization ceilings.

Artifact SHA-256 values matched exactly:

- SEGA manifest:
  `ee6f4d3284c91a0d8c5346b42d7e74f8640a63ddf77a97800931212a5d56086e`;
- SEGA aggregate:
  `dc5dfe2446933df717b21987258beb933d702add6a5416c4e3819f72c66bf5ae`;
- SEGA per-sample:
  `a72c699ff4eac61022c8cb12d4705afb845a80699c76fbe4923465827e663f25`;
- COLE aggregate:
  `f4ea72902a598996687240074be255d13c188304342169470084b56dda42fcb8`;
- COLE per-sample:
  `56671d43916762c85c7ae30aa11dd91ed1151a12741a0f2e2fa376edb45706b7`.

The published artifacts are valid and commit-ready. The following runner
hardening items are future zero-cost engineering work and do not invalidate or
block committing this already successful artifact:

1. hold an exclusive process lock from preflight through atomic publication;
2. reserve worst-case in-flight input/output tokens and USD before dispatch;
3. define and enforce a final completeness/publication policy after all rows;
4. close remaining check/use (TOCTOU) windows and sanitize provider exception
   text before any error is logged or persisted.

Security note: during this read-only workflow, an internal tool output exposed
the configured API credential. The credential is deliberately not reproduced
here and must not be printed, pasted, logged, or committed. Treat it as exposed
and revoke/rotate it after this commit/push workflow, update the appropriate
secret store, and confirm the old credential is disabled.

What remains is zero-cost only: rerun the final diff/status checks, stage
exactly the eight intended paths below, inspect the staged name list and diff,
then commit and push the current branch. The safest scoped staging command is:

```bash
git add -- \
  layout_agent/judge_a3_general_cole.py \
  layout_agent/A3_EXPERIMENT_LOG.md \
  layout_agent/next_step.md \
  layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/a3-general-n100-sega-v1/evaluation_manifest.json \
  layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/a3-general-n100-sega-v1/aggregate.json \
  layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/a3-general-n100-sega-v1/per_sample.jsonl \
  layout_agent/evaluations/a3-cole/a3.cole-judge.v1/a3-general-n100-cole-v1/aggregate.json \
  layout_agent/evaluations/a3-cole/a3.cole-judge.v1/a3-general-n100-cole-v1/per_sample.jsonl
git diff --cached --name-only
git diff --cached --check
git diff --cached --stat
```

## Next task and stop conditions

- General N=100 generation, deterministic SEGA/PKU, and matched COLE are
  complete. Never rerun or overwrite any of these write-once artifacts.
- The checkpoint 9 authorization for `a3-general-n100-cole-v1` has been
  consumed successfully. **Never rerun that command or reuse/broaden its
  authorization** for any evaluation ID, model, dataset, loop, or follow-up.
- The only remaining task for this completed workflow is the zero-cost scoped
  staging, commit, and push described in checkpoint 15.
- After that push, any runner/process-lock, token-reservation, completeness,
  TOCTOU/error-sanitization, HTTPX cleanup, Numba cache/runtime, or credential
  rotation work is a separate zero-cost hardening/security task; it must not
  mutate or rerun the completed evaluations.
- Preserve all unrelated dirty files and existing write-once runs.
- Commit only §24, this handoff, the General COLE runner, the three formal SEGA
  sidecar files, and the two formal COLE files, then push before switching
  sessions.

## Execution checkpoint 16 — exact scoped stage and pre-commit gates passed

At `2026-07-12 09:20:11 CST (+0800)`, the branch and index preflight passed at
`feat/step76-89-sega-pipeline` / parent
`c6340319b32b13db9bd348cf563d28f8aa188adf`: `.git` and `.git/index` were
writable, `.git/index.lock` was absent, and the pre-existing cached path set was
empty. No API/model call was made; token use was 0 and paid cost was `$0.00`.

The exact staging command was:

```bash
git add -- \
  layout_agent/judge_a3_general_cole.py \
  layout_agent/A3_EXPERIMENT_LOG.md \
  layout_agent/next_step.md \
  layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/a3-general-n100-sega-v1/evaluation_manifest.json \
  layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/a3-general-n100-sega-v1/aggregate.json \
  layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/a3-general-n100-sega-v1/per_sample.jsonl \
  layout_agent/evaluations/a3-cole/a3.cole-judge.v1/a3-general-n100-cole-v1/aggregate.json \
  layout_agent/evaluations/a3-cole/a3.cole-judge.v1/a3-general-n100-cole-v1/per_sample.jsonl
```

`git diff --cached --name-only` plus an exact sorted-set comparison reported
only those eight paths (count 8), exit 0. The focused check commands and results
were:

```bash
git diff --cached --check
PYTHONPYCACHEPREFIX=/tmp/codex-general-n100-commit-pycache \
  /home/hui0705/.conda/envs/meta/bin/python -m py_compile \
  layout_agent/judge_a3_general_cole.py
PYTHONDONTWRITEBYTECODE=1 /home/hui0705/.conda/envs/meta/bin/python - <<'PY'
# Strict json.loads(..., parse_constant=reject_constant) validation of all
# staged JSON/JSONL artifacts; assert 100 ordered unique SEGA evaluated rows,
# 200 unique-arm COLE rows all ok (100 General + 100 GT), and scan all eight
# cached blobs for credential shapes without printing matching content.
PY
```

All three checks exited 0. The strict validator reported 100 SEGA rows, all
`evaluated`; 200 COLE rows, all `ok`, split 100 General/100 GT; and zero
credential-shape matches across the eight cached files. After adding this
checkpoint, `layout_agent/next_step.md` must be restaged and the exact cached
name set plus `git diff --cached --check` revalidated before committing.

What remains is zero-cost only: create the scoped commit and then push it. The
safest resume commands are:

```bash
git commit -m "feat(agentlayout): publish General N100 evaluations"
git push
```

Do not rerun any General generation, SEGA, or COLE evaluation, and do not stage
any unrelated dirty or untracked path.

## Execution checkpoint 17 — General N=100 workflow pushed and handed off

At `2026-07-12 09:24:09 CST (+0800)`, branch
`feat/step76-89-sega-pipeline` was at task commit
`76ee9870b1c96dd6b85f0ef8829c81c2cf2d43f3`, with an empty index and no
changes in any of the eight task paths. The configured remote/upstream was
`nina` / `nina/feat/step76-89-sega-pipeline`.

The exact first sync command and result were:

```bash
git push
# exit 0; c6340319..76ee9870, feat/step76-89-sega-pipeline ->
# feat/step76-89-sega-pipeline
```

This pushed the completed General N=100 task commit. It contains the frozen
SEGA sidecar (`a3-general-n100-sega-v1`, 100 evaluated rows), the matched COLE
artifact (`a3-general-n100-cole-v1`, 200/200 rows `ok`), the dedicated runner,
experiment log, and durable handoff. No generation, evaluator, judge, paid API,
or LLM call was rerun during commit/sync; token use was 0 and paid cost was
`$0.00`.

The following unrelated pre-existing paths remained deliberately uncommitted:

- `AGENTS.md`
- `layout_agent/CODEX_HANDOFF.md`
- `layout_agent/IMPLEMENTATION_LOG.md`
- `layout_agent/output2/step91_o4mini_ab.py`
- `metagpt/provider/constant.py`
- `CLAUDE-FABLE-5.md`
- `layout_agent/REFACTOR_PLAN.md`
- `layout_agent/SEGA_METRICS_REMOTE_AGENT_TASK.md`
- `layout_agent/demo/`
- `layout_agent/demo_ids.json`
- `layout_agent/demo_v2/`
- `layout_agent/output.md`
- `layout_agent/output2/step97_relation_subset.py`
- `layout_agent/output2/step97_relation_subset/`
- `layout_agent/run_demo.py`
- `layout_agent/runs/`

Because an earlier internal tool output exposed the configured credential,
rotate/revoke it, update the appropriate secret store, and confirm the old
credential is disabled. Never print or commit either credential. Optional
future zero-cost hardening remains separate from these immutable artifacts:
add a paid-run process lock, reserve in-flight token/USD budget, enforce final
row completeness, close input TOCTOU windows, sanitize provider errors, and
address the documented HTTPX/Numba runtime cleanup items.

After committing this checkpoint alone with
`docs(agentlayout): record General N100 handoff` and running the second exact
`git push`, safe read-only verification commands are:

```bash
git status -sb
git rev-parse HEAD
git rev-parse '@{u}'
git ls-remote --heads nina refs/heads/feat/step76-89-sega-pipeline
jq -e '.evaluation_id == "a3-general-n100-sega-v1"' \
  layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/a3-general-n100-sega-v1/aggregate.json
jq -e '.evaluation_id == "a3-general-n100-cole-v1" and \
  .status_counts == {"ok": 200}' \
  layout_agent/evaluations/a3-cole/a3.cole-judge.v1/a3-general-n100-cole-v1/aggregate.json
```

## Next task and stop conditions

- The General N=100 generation, SEGA evaluation, COLE evaluation, scoped task
  commit, handoff commit, and both pushes are complete.
- Never rerun, overwrite, or broaden the completed paid evaluation or its
  consumed authorization.
- No required work remains for this workflow. Only optional zero-cost
  hardening and credential rotation remain, each as a separate scoped task.
- Preserve all unrelated dirty/untracked work listed above.
