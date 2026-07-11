"""Formal paid COLE judge for the A3 Relation N=100 arms (authorized 2026-07-12).

Scores every image once (absolute, blind by construction) with the pinned
gpt-5.4-mini snapshot, then computes matched-pair statistics against the
designer ground truth and between arms.

Inputs are resolved from the verified formal SEGA sidecar
(a3-relation-n100-t0-t2-t3-sega-v1): each evaluated row's ``b0_slot_id`` and
``b0_render_sha256`` pin the exact render; GT comes from the Crello cache.
The COLE prompt and parser are imported verbatim from
``layout_agent/output/step21_phaseb_eval.py``; only the model is overridden.

Budget contract (from the approved proposal): 397 scoring calls nominal,
a hard global cap of 420 API calls including retries and the one
parameter-compat probe, and an abort if failures exceed 5% of the first 40
completed calls.

Usage:
    python layout_agent/judge_a3_cole.py --preflight   # zero-cost checks only
    python layout_agent/judge_a3_cole.py               # paid run
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import math
import os
import random
import statistics
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "layout_agent" / "output"))
import step21_phaseb_eval as s21  # noqa: E402  (COLE prompt + parser, verbatim)

MODEL = "gpt-5.4-mini-2026-03-17"
SIDECAR = REPO / (
    "layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/"
    "a3-relation-n100-t0-t2-t3-sega-v1"
)
RUNS_ROOT = REPO / "layout_agent/runs/a3"
GT_ROOT = REPO / "layout_agent/output"
OUT_ROOT = REPO / "layout_agent/evaluations/a3-cole/a3.cole-judge.v1"
EVALUATION_ID = "a3-relation-n100-cole-v1"

HARD_CALL_CAP = 420
CONCURRENCY = 8
PARSE_FAIL_WINDOW = 40
PARSE_FAIL_MAX_RATE = 0.05
ARM_SHORT = {"a3-rel100-t0-01": "t0", "a3-rel100-t2-01": "t2", "a3-rel100-t3-01": "t3"}
COMPARISONS = (
    ("t0", "gt"), ("t2", "gt"), ("t3", "gt"),
    ("t2", "t0"), ("t3", "t0"), ("t3", "t2"),
)
BOOTSTRAP_N = 10_000
BOOTSTRAP_SEED = 20260712


# --------------------------------------------------------------------------
# task resolution from the verified sidecar
# --------------------------------------------------------------------------
def _load_tasks() -> Tuple[List[dict], List[str]]:
    """Return (tasks, matched_ids). Each task pins one image by path+sha."""
    manifest = json.loads((SIDECAR / "evaluation_manifest.json").read_text())
    matched_ids = manifest["matched_samples"]["ordered_sample_ids"]
    tasks: List[dict] = []
    for line in (SIDECAR / "per_sample.jsonl").read_text().splitlines():
        rec = json.loads(line)
        if rec["status"] != "evaluated":
            continue
        path = (
            RUNS_ROOT / rec["run_id"] / "samples" / rec["sample_id"]
            / "renders" / f"{rec['b0_slot_id']}.png"
        )
        tasks.append({
            "arm": ARM_SHORT[rec["run_id"]],
            "sample_id": rec["sample_id"],
            "path": path,
            "sha256": rec["b0_render_sha256"],
            "mime": "image/png",
        })
    for sid in matched_ids:
        tasks.append({
            "arm": "gt",
            "sample_id": sid,
            "path": GT_ROOT / f"crello_{sid}" / "ground_truth_preview.jpg",
            "sha256": None,
            "mime": "image/jpeg",
        })
    return tasks, matched_ids


def _preflight(tasks: Sequence[dict]) -> None:
    """Zero-cost: every image must exist; pinned renders must match their sha."""
    problems = []
    for t in tasks:
        if not t["path"].exists():
            problems.append(f"missing: {t['path']}")
            continue
        if t["sha256"]:
            actual = hashlib.sha256(t["path"].read_bytes()).hexdigest()
            if actual != t["sha256"]:
                problems.append(f"sha mismatch: {t['path']}")
    if problems:
        for p in problems:
            print(f"[preflight FAIL] {p}")
        sys.exit(1)
    n_arm = sum(1 for t in tasks if t["arm"] != "gt")
    n_gt = sum(1 for t in tasks if t["arm"] == "gt")
    print(f"[preflight OK] {n_arm} pinned arm renders + {n_gt} GT previews; "
          f"nominal calls={len(tasks)} <= cap {HARD_CALL_CAP}")


# --------------------------------------------------------------------------
# paid calls
# --------------------------------------------------------------------------
class Budget:
    """Global hard cap over every API attempt, probe included."""

    def __init__(self, cap: int):
        self.cap = cap
        self.used = 0

    def take(self) -> bool:
        if self.used >= self.cap:
            return False
        self.used += 1
        return True


class ParamVariant:
    """gpt-5.x snapshots may reject temperature/max_tokens; probe once."""

    def __init__(self):
        self.kwargs: Optional[dict] = None

    async def resolve(self, client, budget: Budget) -> dict:
        if self.kwargs is not None:
            return self.kwargs
        legacy = {"temperature": 0.0, "max_tokens": 600}
        modern = {"max_completion_tokens": 600}
        if not budget.take():
            raise RuntimeError("call cap exhausted before probe")
        try:
            await client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": "Reply with the word ok."}],
                **legacy,
            )
            self.kwargs = legacy
        except Exception as err:
            msg = str(err).lower()
            if "temperature" in msg or "max_tokens" in msg or "unsupported" in msg:
                self.kwargs = modern
            else:
                raise
        print(f"[probe] param variant = {self.kwargs}")
        return self.kwargs


def _row(task: dict) -> dict:
    return {"arm": task["arm"], "sample_id": task["sample_id"],
            "path": str(task["path"].relative_to(REPO)),
            "render_sha256": task["sha256"]}


async def _score_task(client, task: dict, kwargs: dict, budget: Budget) -> dict:
    b64 = None
    last = "no attempt"
    for attempt in range(2):
        if not budget.take():
            return {**_row(task), "status": "call_cap_reached"}
        if b64 is None:
            b64 = base64.b64encode(task["path"].read_bytes()).decode()
        try:
            resp = await client.chat.completions.create(
                model=MODEL,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": s21.COLE_PROMPT},
                        {"type": "image_url", "image_url": {
                            "url": f"data:{task['mime']};base64,{b64}"}},
                    ],
                }],
                **kwargs,
            )
            text = (resp.choices[0].message.content or "").strip()
            parsed = s21._parse_cole_json(text)
            if parsed is not None:
                smean4 = statistics.mean(parsed[k] for k in s21.REPORT_AXES)
                return {**_row(task), "status": "ok", "scores": parsed,
                        "smean4": smean4}
            last = f"unparseable: {text[:160]!r}"
        except Exception as err:  # noqa: BLE001
            last = f"{type(err).__name__}: {err}"
        if attempt == 0:
            await asyncio.sleep(1.5)
    return {**_row(task), "status": "failed", "error": last}


# --------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------
def _sign_test_p(wins: int, losses: int) -> Optional[float]:
    n = wins + losses
    if n == 0:
        return None
    k = min(wins, losses)
    total = sum(math.comb(n, i) for i in range(k + 1)) * 2
    return min(1.0, total / (2 ** n))


def _bootstrap_ci(deltas: Sequence[float]) -> Optional[dict]:
    if not deltas:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(deltas)
    means = sorted(
        statistics.mean(deltas[rng.randrange(n)] for _ in range(n))
        for _ in range(BOOTSTRAP_N)
    )
    return {
        "mean_delta": statistics.mean(deltas),
        "ci95_low": means[int(0.025 * BOOTSTRAP_N)],
        "ci95_high": means[int(0.975 * BOOTSTRAP_N)],
    }


def _paired_stats(rows: List[dict]) -> dict:
    by_arm: Dict[str, Dict[str, dict]] = {}
    for r in rows:
        if r["status"] == "ok":
            by_arm.setdefault(r["arm"], {})[r["sample_id"]] = r
    out = {}
    for a, b in COMPARISONS:
        common = sorted(set(by_arm.get(a, {})) & set(by_arm.get(b, {})))
        deltas = [by_arm[a][s]["smean4"] - by_arm[b][s]["smean4"] for s in common]
        wins = sum(1 for d in deltas if d > 0)
        losses = sum(1 for d in deltas if d < 0)
        per_axis = {}
        for axis in s21.REPORT_AXES:
            aw = sum(1 for s in common
                     if by_arm[a][s]["scores"][axis] > by_arm[b][s]["scores"][axis])
            al = sum(1 for s in common
                     if by_arm[a][s]["scores"][axis] < by_arm[b][s]["scores"][axis])
            per_axis[axis] = {"wins": aw, "losses": al,
                              "ties": len(common) - aw - al,
                              "sign_p": _sign_test_p(aw, al)}
        out[f"{a}_vs_{b}"] = {
            "n_pairs": len(common),
            "smean4": {"wins": wins, "losses": losses,
                       "ties": len(common) - wins - losses,
                       "sign_p": _sign_test_p(wins, losses),
                       "bootstrap": _bootstrap_ci(deltas)},
            "per_axis": per_axis,
        }
    return out


def _arm_means(rows: List[dict]) -> dict:
    out = {}
    for arm in ("t0", "t2", "t3", "gt"):
        ok = [r for r in rows if r["arm"] == arm and r["status"] == "ok"]
        if not ok:
            out[arm] = {"n": 0}
            continue
        out[arm] = {
            "n": len(ok),
            "smean4": statistics.mean(r["smean4"] for r in ok),
            **{axis: statistics.mean(r["scores"][axis] for r in ok)
               for axis in ("SDL", "SQL", "STV", "SGI", "SIO")},
        }
    gt = out.get("gt", {})
    for arm in ("t0", "t2", "t3"):
        if out[arm].get("n") and gt.get("n"):
            out[arm]["pct_of_gt_smean4"] = 100.0 * out[arm]["smean4"] / gt["smean4"]
    return out


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
async def _run_paid(tasks: List[dict]) -> Tuple[List[dict], Budget]:
    client = s21._load_openai_client()
    budget = Budget(HARD_CALL_CAP)
    variant = ParamVariant()
    kwargs = await variant.resolve(client, budget)
    sem = asyncio.Semaphore(CONCURRENCY)
    done: List[dict] = []
    abort = asyncio.Event()

    async def worker(task: dict) -> dict:
        async with sem:
            if abort.is_set():
                return {**_row(task), "status": "aborted"}
            row = await _score_task(client, task, kwargs, budget)
            done.append(row)
            n = len(done)
            if n % 25 == 0 or n == len(tasks):
                ok = sum(1 for r in done if r["status"] == "ok")
                print(f"[progress] {n}/{len(tasks)} scored, ok={ok}, "
                      f"calls={budget.used}/{HARD_CALL_CAP}", flush=True)
            if n == PARSE_FAIL_WINDOW:
                fails = sum(1 for r in done if r["status"] != "ok")
                if fails / n > PARSE_FAIL_MAX_RATE:
                    print(f"[ABORT] {fails}/{n} failures in first window")
                    abort.set()
            return row

    rows = await asyncio.gather(*(worker(t) for t in tasks))
    return list(rows), budget


def _publish(rows: List[dict], budget: Budget, wall_s: float,
             matched_ids: List[str]) -> Path:
    final_dir = OUT_ROOT / EVALUATION_ID
    if final_dir.exists():
        sys.exit(f"refusing to overwrite existing {final_dir}")
    staging = OUT_ROOT / f".staging-{EVALUATION_ID}"
    staging.mkdir(parents=True, exist_ok=False)
    per_sample = "\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n"
    (staging / "per_sample.jsonl").write_text(per_sample)
    aggregate = {
        "schema_version": "a3.cole-judge.v1",
        "evaluation_id": EVALUATION_ID,
        "judge_model": MODEL,
        "prompt_sha256": hashlib.sha256(s21.COLE_PROMPT.encode()).hexdigest(),
        "source_sidecar": "a3-relation-n100-t0-t2-t3-sega-v1",
        "matched_ids_count": len(matched_ids),
        "api_calls_used": budget.used,
        "call_cap": HARD_CALL_CAP,
        "wall_seconds": round(wall_s, 2),
        "status_counts": {
            s: sum(1 for r in rows if r["status"] == s)
            for s in sorted({r["status"] for r in rows})
        },
        "arm_means": _arm_means(rows),
        "paired": _paired_stats(rows),
    }
    (staging / "aggregate.json").write_text(
        json.dumps(aggregate, indent=2, sort_keys=True, allow_nan=False) + "\n")
    os.rename(staging, final_dir)
    return final_dir


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preflight", action="store_true",
                    help="zero-cost checks only; no API call")
    args = ap.parse_args()
    tasks, matched_ids = _load_tasks()
    _preflight(tasks)
    if args.preflight:
        return 0
    t0 = time.time()
    rows, budget = asyncio.run(_run_paid(tasks))
    wall = time.time() - t0
    final_dir = _publish(rows, budget, wall, matched_ids)
    agg = json.loads((final_dir / "aggregate.json").read_text())
    print(json.dumps({"published": str(final_dir.relative_to(REPO)),
                      "calls": budget.used, "wall_s": round(wall, 1),
                      "status_counts": agg["status_counts"],
                      "arm_means": agg["arm_means"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
