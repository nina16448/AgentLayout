# A3 Full-Crello Expansion — Session Handoff

Repository: `/home/hui0705/MetaGPT` — branch `feat/step76-89-sega-pipeline`,
remote `nina` (`github.com:nina16448/AgentLayout.git`).

Updated: 2026-07-12 (batch 001 paid generation paused mid-run by the user;
49/100 samples completed; continuation recovery verified, waiting at the paid
boundary).

Persistence protocol (root `AGENTS.md`, user-mandated): update this handoff
**once** immediately before returning control, create one scoped commit (task
files + handoff), push once. No per-command checkpointing, no receipt-only
commits. Read-only answers need no persistence.

**Session start (user-mandated, 2026-07-12): read this file, then act.** Do
NOT re-verify artifact hashes, re-run readiness checks or session-catchup
scripts, re-read the full experiment log or old checkpoints, or run
disk/Git/network/process gates at session start. Verify something only when
the action you are about to take directly depends on it (e.g. check free disk
and no concurrent same-batch process immediately before launching a paid
run — nothing else).

## Completed and immutable — never rerun, overwrite, or reuse authorizations

- **General N=100 generation** (`layout_agent/runs/a3/a3-general-n100-t2-l0-01`,
  100/100, 714 attempts) with formal SEGA sidecar
  `layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/a3-general-n100-sega-v1/`
  and matched COLE judge artifact
  `layout_agent/evaluations/a3-cole/a3.cole-judge.v1/a3-general-n100-cole-v1/`
  (200/200 `ok`; General S_mean4 5.4675 vs GT 6.6725 = 81.94%; 10W/85L/5T,
  sign p=5.76e-16). Both paid authorizations are **consumed**. Details and
  reproduction: `A3_EXPERIMENT_LOG.md` §24.
- Artifact SHA-256 values are recorded in each bundle's
  `evaluation_manifest.json` and `A3_EXPERIMENT_LOG.md` §24. They have been
  verified repeatedly; do **not** re-verify them again unless corruption is
  actually suspected.
- COLE runner `layout_agent/judge_a3_general_cole.py` is hardened (paid lock,
  four-cap reservation/settlement; 19 offline tests in
  `tests/metagpt/ext/agentlayout/test_judge_a3_general_cole_hardening.py`).
- The once-exposed OpenAI credential has been **rotated** (user-confirmed).
  Never print or commit credentials. The ~$87 dashboard balance is an
  account-level observation, not a budget or authorization.
- Everything above plus the Crello readiness tooling is committed and pushed
  through `f8ef25aa`. Later commit `81909ed0` adds only the separate Relation
  N=100 tree-accuracy/statistics work (`A3_EXPERIMENT_LOG.md` §25) and does
  not touch batch-001 state.

## Full-Crello scope and frozen inputs

Scope (user decision): **official Crello test split N=1,971 only** — reuse the
completed 100, generate 18 new batches of 100 plus one final batch of 71
(1,871 new). Deterministic six-axis SEGA/PKU after each batch. Paid COLE,
train, and validation splits are out of scope. Plan:
`layout_agent/FULL_CRELLO_BATCH_PLAN.md`; scoped ledgers:
`.planning/crello-full-test/{task_plan,findings,progress}.md`.

Frozen facts:

- Dataset source revision `7997e2f434ee4aa73cf4cdf22c5954cb175872e1`
  (`cyberagent/crello`, test=1,971). Formal loads must pin this revision.
- Pinned ID snapshot `layout_agent/sample_ids/a3_crello_test_n1971_v1/`
  (file sha `c3578fa5…`, ordered-ID sha `b082ec96…`).
- Cache membership: 1,897 pinned overlap + 74 caches materialized + 1,706
  text sidecars added → all 1,971 official caches verified present. Five
  local split-drift extras (`5954bda995a7a863ddce14a1`,
  `5c6c0cba85ea3c16f964a15d`, `5d972ca9abc8ea6d1c54e002`,
  `5efdd2dd499b85dcc75ba0bc`, `5f885a9ba637ee11e3498683`) are preserved
  byte-for-byte but excluded from the official manifest.
- Batch bundle `layout_agent/sample_ids/a3_crello_test_batches_v1/`
  (manifest sha `3b334f24…`; 19 batches; `paid_generation_authorized=false`).
- Tooling: `layout_agent/prepare_full_crello.py` (+ config
  `layout_agent/configs/a3_crello_test_l0_v1.json`, 9 offline tests). Network
  is double-gated (`--allow-network` / `--allow-dataset-download`); it has no
  paid-API path.
- Batch 001 zero-cost readiness is complete: run
  `layout_agent/runs/a3/a3-crello-test-batch-001-n100-t2-l0-v1/` with init,
  P-Full, R3, Analyst-vision all 100/100, failed 0. Do **not** pre-prepare
  batches 002–019.

## Batch 001 paid generation — PAUSED at the paid boundary

The user authorized run `a3-crello-test-batch-001-n100-t2-l0-v1`, frozen model
`gpt-5.4-mini-2026-03-17`, cumulative hard caps **850 actual HTTP calls /
4,500,000 input tokens / 800,000 output tokens / US$7.00**. Enforcement was
implemented before launch: `metagpt/ext/agentlayout/a3_paid_budget.py`,
receipt `layout_agent/authorizations/a3-crello-test-batch-001-n100-t2-l0-v1.json`,
budget integration in `layout_agent/run_a3.py`, tests in
`tests/metagpt/ext/agentlayout/test_a3_paid_budget.py` (re-verified after the
pause: 4 passed).

At 2026-07-12 17:38 CST the user paused the run (Ctrl-C, exit 1,
`KeyboardInterrupt`) to change the implementation. Nothing is in flight.
Authoritative pause snapshot — append-only ledger
`layout_agent/runs/a3/a3-crello-test-batch-001-n100-t2-l0-v1/a3_paid_budget_ledger.jsonl`:

- 369 reservations = 369 settlements; zero in-flight.
- Charged: 1,341,756 input / 251,389 output tokens; US$2.1375675 at Standard
  rates. The interrupted Judge call had no usage report and was settled
  conservatively at its full 7,988-in/512-out reservation.
- Remaining envelope: ≤481 HTTP calls, 3,158,244 input tokens, 548,611 output
  tokens, US$4.8624325.

Sample status: **49 durable successes** (`pipeline/l0_result.json`), 51
remaining = 48 never attempted + 2 validation-exhausted + 1 interrupted:

- `5d0cf30b8cba87f94359542b` — mapper exhausted 3 attempts (duplicate asset
  placement) after the director stage;
- `5e7c71244b3890eb071e6e40` — planner exhausted 3 attempts (layout-tree
  cycles through `asset_0005`/`asset_0010`/`asset_0016`) after the analyst;
- `592d211c95a7a863ddcd9e61` — interrupted during the Judge call (six stages
  done; conservative settlement above).

Resume conditions (do not resume automatically):

1. Wait for the user's edits and a **new explicit authorization** that names
   the run ID, frozen model, and the remaining envelope (≤481 calls /
   3,158,244 input / 548,611 output / US$4.8624325), and explicitly decides
   whether the two validation-exhausted samples may be retried. Do not infer
   either permission from a general request to continue.
2. Only if the user changed code: run
   `tests/metagpt/ext/agentlayout/test_a3_paid_budget.py` (~10 s). If nothing
   changed, skip all checks and resume directly.
3. Resume with the exact original launch command (unchanged, cumulative
   ledger):

   ```bash
   PYTHONDONTWRITEBYTECODE=1 timeout 4500s \
     /home/hui0705/.conda/envs/meta/bin/python \
     layout_agent/run_a3.py run \
     --run-dir layout_agent/runs/a3/a3-crello-test-batch-001-n100-t2-l0-v1 \
     --tree-arm T2 \
     --analyst-arm vision \
     --authorization-receipt layout_agent/authorizations/a3-crello-test-batch-001-n100-t2-l0-v1.json \
     --allow-api-calls
   ```

   The runtime gate must reuse the existing ledger, skip the 49 completed L0
   samples, and enforce the remaining envelope — never reset any cap.
4. Do not rerun General N=100, batch-001 readiness, or any completed sample.
   Six-axis evaluation only after generation reaches the agreed terminal
   state.

## Operational constraints

- Run evaluators with the direct meta interpreter and relocated caches
  (`TMPDIR=/tmp NUMBA_CACHE_DIR=/tmp/... /home/hui0705/.conda/envs/meta/bin/python …`);
  `conda run` previously failed on read-only cache paths.
- Never call `run_iou_eval.save_sample` from the importer (it mutates
  caches); never bulk-add `layout_agent/output/` or `layout_agent/runs/`.
- Disk hard floor: abort materialization/generation below **80 GiB** free.
- The repeating git auto-GC warning (`.git/gc.log`, unreachable loose
  objects) is benign here; do not run destructive `git prune` as part of a
  task.

## Next task and stop conditions

- Never rerun/overwrite completed write-once artifacts or reuse consumed paid
  authorizations.
- Batch 001 is partially generated and paused. No paid continuation may start
  before the exact remaining call/token/USD envelope and retry policy receive
  new explicit approval.
- Each new batch must finish generation → immediate six-axis evaluation →
  validation → cost recording → handoff → scoped persistence before the next.
- Update `.planning/crello-full-test/{task_plan,findings,progress}.md` only
  at batch completion or when a phase/authorization state changes — not per
  command.
- Preserve every unrelated dirty/untracked path, currently including:
  `AGENTS.md`, `layout_agent/CODEX_HANDOFF.md`,
  `layout_agent/IMPLEMENTATION_LOG.md`, `layout_agent/output2/…`,
  `metagpt/provider/constant.py`, `CLAUDE-FABLE-5.md`,
  `layout_agent/REFACTOR_PLAN.md`,
  `layout_agent/SEGA_METRICS_REMOTE_AGENT_TASK.md`, `layout_agent/demo*/`,
  `layout_agent/demo_ids.json`, `layout_agent/output.md`,
  `layout_agent/run_demo.py`, `layout_agent/runs/`, and `.claude/`.
