<INSTRUCTIONS>
# User-visible execution protocol

These rules are persistent project instructions and apply to every future session.

## Explain before acting

- Before starting tools or a long-running command, tell the user in plain language:
  what is being run, why it is needed, whether it uses paid APIs, the expected
  duration, and the exact condition that will stop or unblock the next step.
- Keep the user oriented. Every status update must say what is happening now and
  what will happen next; do not expose only internal agent/reviewer mechanics.
- For a command expected to take more than two minutes, provide one start
  checkpoint and then report only meaningful milestones, completion, failure, or
  when the user asks. Do not spend Codex usage on repetitive heartbeat polling.

## Conserve Codex usage

- Use the minimum number of agents and review rounds needed for the risk. Do not
  launch repeated implementation/review loops after the acceptance criteria pass.
- Prefer one implementation pass plus one focused verification pass. Add another
  reviewer only for a concrete unresolved risk, not as a routine ritual.
- If the user asks to reduce usage, immediately stop nonessential agents, polling,
  and commentary. Never interrupt a useful local process merely to inspect it.

## Durable handoff after every execution

- After every command that materially advances or blocks the task, update
  `layout_agent/next_step.md` with the timestamp, exact command, result or error,
  artifact path, paid/API cost, what remains, and the safest resume command.
- A new session must be able to continue from `layout_agent/next_step.md` without
  reconstructing hidden conversation context.

## Commit and push every completed change

- Before handing control back after changing files, run proportionate checks,
  create a scoped commit containing only the task's files, and push the current
  branch. This applies to code, tests, documentation, and handoff updates.
- Never include unrelated pre-existing dirty files. Never stash, reset, clean, or
  overwrite user work to make a commit possible.
- Always report the branch, commit hash, push result, checks run, and any files
  intentionally left uncommitted. If commit or push is blocked, state the exact
  blocker instead of silently leaving changes local.

## Cost and completion guardrails

- Never run a paid API, LLM judge, or paid generation step without first stating
  the exact call/token budget and receiving explicit paid-run authorization.
- A completion report must clearly list: what changed, what ran, the result and
  artifact, remaining work, `next_step.md` status, and commit/push status.
</INSTRUCTIONS>

<claude-mem-context>
# Memory Context

# [MetaGPT] recent context, 2026-07-12 3:14am GMT+8

Legend: 🎯session 🔴bugfix 🟣feature 🔄refactor ✅change 🔵discovery ⚖️decision
Format: ID TIME TYPE TITLE
Fetch details: get_observations([IDs]) | Search: mem-search skill

Stats: 50 obs (20,187t read) | 1,752,582t work | 99% savings

### Jul 12, 2026
4988 2:26a 🟣 evaluate_a3_sega.py — Atomic RENAME_NOREPLACE Publication and Full Bundle Validation Before Write
4989 " 🟣 validate_evaluation_bundle() — Full v1 Sidecar Contract Enforcement in a3_sega_evaluator.py
4990 " 🟣 Matched Sample ID Enforcement and Code/Detector Rehash in evaluate_a3_sega.py
4996 2:28a 🔵 phase1_hardening Callback Pattern — Third Brief Signal at 18:26:25, Then Silent Again
4997 2:30a 🔴 Orchestrator Pre-Stages Re-Verification Round While phase1_hardening Still Running
4998 2:31a 🔄 saliency_basnet_isnet.py — Full Hardening: Snapshot Resolution, Identity Recording, Output Validation, Strict ISNet Identity
4999 " 🟣 evaluate_a3_sega.py — Pre-Publish Lineage Check Callback Added to _write_results
5000 " 🟣 test_sega_metrics.py Expanded — PKU Quirk Tests and Overlay Denominator Tests Added
5001 " ✅ Phase 1 Hardening Final State — 64 Tests Passing, All Checks Clean, Smoke-2 Confirmed at 02:28 CST
5002 2:32a 🔵 phase1_hardening Entered Another Silent Period — Completion Estimate Was Premature
5003 2:33a 🔵 new_plam.md A3 Task Audit — Phase 3 Remaining Items After Phase 1+2 Completion
5004 2:34a 🔵 Pipeline Enters Final Re-Verification Phase — Three Rapid Consecutive Completions at 18:33–18:34
5005 2:36a 🟣 A3 SEGA Evaluator Phase 1 Hardening — Major Expansion to 1,157 Lines
5006 " 🟣 A3 SEGA Test Suite Expanded — 64 Tests Passing (Up from 42)
5007 " 🔵 BASNet + ISNet Detector Artifact Verification — Hashes and Session Identity Confirmed
5008 " ✅ next_step.md Updated — Formal N=100 Run Blocked Pending Independent Review
5009 2:37a 🔵 Orchestrator Retrying Identical followup_task to phase1_hardening — Subtask Not Yet Acknowledged
5010 " 🔵 new_plam.md A3 Task Audit — Phase 3 Remaining Items After Phase 1+2 Completion
5011 2:39a 🔵 phase1_hardening Sixth Callback at 18:38:28 — Still Active at 23-Minute Mark
5012 2:40a 🔵 phase1_hardening Silent Period Continues Past 25-Minute Total Runtime
5014 2:43a 🔵 phase1_hardening Seventh Callback at 18:41:55 — Orchestrator on ~100s Retry Loop
5015 " 🔵 A3 SEGA Phase 1 Pipeline Wall-Clock Cost Profile — 28+ Minutes for Hardening Alone
5016 2:44a 🔵 phase1_hardening Past 29-Minute Mark — Longest Agent Execution in A3 SEGA Pipeline
5018 2:46a 🔵 phase1_hardening Eighth Callback at 18:45:22 — Orchestrator Cycling Through Multiple Nudge Messages
5019 2:48a 🔵 Orchestrator Sent Fourth Distinct Escalation Payload to phase1_hardening at 18:48:33
5020 2:52a 🔵 phase1_hardening Ninth Callback at 18:48:47 — 33+ Minutes Runtime, Still No Completion
5021 2:54a 🔵 Orchestrator Shortened Timeout Windows to 30s and Sent Fifth Escalation Payload at 38-Minute Mark
5023 " 🔵 phase1_hardening Silent for 6+ Minutes — Longest Quiet Period in Session
5024 2:55a 🔵 phase1_antipattern Final Report — No Blockers, 3 Non-Blocking Edge Cases, renameat2 and ISNet Fixes Confirmed
5025 " 🔵 phase1_final_quality Report — 4 P1 Blockers Prevent Phase 2 Unlock; 64/64 Tests Pass
5030 " 🔵 phase1_hardening Force-Interrupted After 40 Minutes — Orchestrator Issued interrupt_agent
5031 2:58a 🔵 Post-Interrupt Pivot — Orchestrator Re-Engaged All Three Audit Agents with New P1 Blocker Tasks
5032 " 🔵 new_plam.md A3 Task Audit — Phase 3 Has Three Remaining Items; Phase1_final_quality Reports 4 P1 Blockers
5033 3:00a 🔵 Post-Interrupt Re-Verification Phase Active — Split Wait Results Confirm Multiple Agents Running
5034 " 🔵 Phase 1 Final Gate Verification — 84 Tests Pass, Real Runtime Contract Valid, ISNet Artifact Hashes Confirmed On-Disk
5035 " 🔵 Post-Interrupt Audit Round In Progress — Awaiting list_agents for P1 Blocker Resolution Results
5036 " 🔵 Frozen Detector Artifact Hashes Confirmed — ISNet 178MB ONNX + BASNet Revision Pinned and Schema-Valid
5037 3:02a ⚖️ Phase 1 Declared Complete — Full 5-Phase A3 SEGA Evaluation Roadmap Established
5038 " 🟣 phase1_commit and phase1_sync Agents Spawned — Phase 1 Work Being Persisted to Git
5039 3:04a 🟣 Phase 2 Formal Evaluation Started — phase2_run Agent Spawned for T0/T2/T3 Zero-LLM Six-Axis Run
5040 " 🔵 new_plam.md A3 Task Audit — Phase 3 Has Three Remaining Items
5041 3:06a 🔵 phase2_run Agent Active and Responding — Formal Evaluation Execution Confirmed Started
5043 3:07a 🟣 A3 SEGA Phase 2 Real-Detector Smoke — Passed with Metric Values
5044 " 🟣 Formal A3 SEGA Relation N=100 Evaluation Launched — evaluation-id a3-relation-n100-t0-t2-t3-sega-v1
5045 " 🔵 BASNet Safetensors Load — Non-Meta Parameter Warnings Are Cosmetic Only
5042 " 🔵 phase2_run In Silent Execution — Formal Evaluation Processing T0/T2/T3 Samples
5046 3:09a 🔵 phase2_run Callback Cadence Established — ~2-Minute Inter-Callback Intervals During Evaluation
5047 3:10a 🔵 Formal A3 SEGA N=100 Evaluation — Runtime Resource Profile and Write-at-End Architecture Confirmed
5048 3:11a 🔵 New wait_agent Outcome Observed — "Wait interrupted by new input" Indicates Concurrent Agent Activity
5049 3:12a 🔵 A3 SEGA Evaluator Process Profile — 56 Threads, Per-Sample File Open/Close, Stable Memory Growth

Access 1753k tokens of past work via get_observations([IDs]) or mem-search skill.
</claude-mem-context>
