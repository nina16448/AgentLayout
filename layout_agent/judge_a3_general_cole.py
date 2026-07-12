"""Paid blind COLE judge for A3 Crello-General N=100 versus designer GT.

The runner consumes only the pinned B0 renders from the formal General SEGA
sidecar and the same sample IDs' Crello ground-truth previews. It is
write-once, performs a full hash preflight both before and after API calls, and
requires an explicit ``--allow-api-calls`` flag.

Authorized 2026-07-12 contract: evaluation ``a3-general-n100-cole-v1``;
judge ``gpt-5.4-mini-2026-03-17``; 220 calls including probe/retries;
3,000,000 input tokens; 150,000 output tokens; US$4.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "layout_agent"))
import judge_a3_cole as relation_judge  # noqa: E402

s21 = relation_judge.s21

MODEL = "gpt-5.4-mini-2026-03-17"
RUN_ID = "a3-general-n100-t2-l0-01"
SOURCE_SIDECAR_ID = "a3-general-n100-sega-v1"
SIDECAR = REPO / (
    "layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/"
    + SOURCE_SIDECAR_ID
)
RUNS_ROOT = REPO / "layout_agent/runs/a3"
GT_ROOT = REPO / "layout_agent/output"
OUT_ROOT = REPO / "layout_agent/evaluations/a3-cole/a3.cole-judge.v1"
EVALUATION_ID = "a3-general-n100-cole-v1"

HARD_CALL_CAP = 220
INPUT_TOKEN_CAP = 3_000_000
OUTPUT_TOKEN_CAP = 150_000
USD_CAP = 4.0
# The first authorized launch was interrupted while its compatibility probe
# was waiting inside a network-isolated sandbox. Count it conservatively even
# though no response/usage telemetry was received, so a resumed run cannot
# exceed the user's aggregate authorization.
PRIOR_INTERRUPTED_CALLS = 1
PRIOR_INPUT_TOKEN_RESERVE = 1_000
PRIOR_OUTPUT_TOKEN_RESERVE = 600
INPUT_USD_PER_M = 0.75
OUTPUT_USD_PER_M = 4.50
MAX_COMPLETION_TOKENS = 600
CONCURRENCY = 8
PARSE_FAIL_WINDOW = 40
PARSE_FAIL_MAX_RATE = 0.05
EXPECTED_INPUT_SNAPSHOT_SHA256 = (
    "aa7c5b236bc8655bf182cfe8fc898266fbb8e136b30c3f8ae2e7e89bbcb5fa72"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_tasks() -> Tuple[List[dict], List[str], str]:
    manifest = json.loads((SIDECAR / "evaluation_manifest.json").read_text())
    matched_ids = manifest["matched_samples"]["ordered_sample_ids"]
    if len(matched_ids) != 100 or len(set(matched_ids)) != 100:
        raise RuntimeError("formal sidecar must contain exactly 100 unique IDs")

    records = [
        json.loads(line)
        for line in (SIDECAR / "per_sample.jsonl").read_text().splitlines()
        if line.strip()
    ]
    evaluated = [record for record in records if record["status"] == "evaluated"]
    if len(evaluated) != 100 or any(r["run_id"] != RUN_ID for r in evaluated):
        raise RuntimeError("formal sidecar must contain 100 evaluated General rows")
    if [r["sample_id"] for r in evaluated] != matched_ids:
        raise RuntimeError("formal sidecar row order differs from matched ID snapshot")

    tasks: List[dict] = []
    b0_hashes: List[str] = []
    for record in evaluated:
        path = (
            RUNS_ROOT
            / RUN_ID
            / "samples"
            / record["sample_id"]
            / "renders"
            / f"{record['b0_slot_id']}.png"
        )
        b0_hashes.append(record["b0_render_sha256"])
        tasks.append(
            {
                "arm": "general",
                "sample_id": record["sample_id"],
                "path": path,
                "sha256": record["b0_render_sha256"],
                "mime": "image/png",
            }
        )

    gt_hashes: List[str] = []
    for sample_id in matched_ids:
        path = GT_ROOT / f"crello_{sample_id}" / "ground_truth_preview.jpg"
        if not path.is_file():
            raise RuntimeError(f"missing GT preview: {path}")
        digest = _sha256(path)
        gt_hashes.append(digest)
        tasks.append(
            {
                "arm": "gt",
                "sample_id": sample_id,
                "path": path,
                "sha256": digest,
                "mime": "image/jpeg",
            }
        )

    payload = "\n".join(matched_ids + b0_hashes + gt_hashes).encode()
    return tasks, matched_ids, hashlib.sha256(payload).hexdigest()


def _target_paths() -> Tuple[Path, Path]:
    final = OUT_ROOT / EVALUATION_ID
    return final, OUT_ROOT / f".staging-{EVALUATION_ID}"


def _preflight(tasks: Sequence[dict], snapshot_sha256: str) -> None:
    if len(tasks) != 200:
        raise RuntimeError(f"expected 200 blind scoring tasks, got {len(tasks)}")
    final, staging = _target_paths()
    if final.exists() or staging.exists():
        raise RuntimeError(f"write-once target already exists: {final} or {staging}")
    for task in tasks:
        path = task["path"]
        if not path.is_file():
            raise RuntimeError(f"missing judge input: {path}")
        if _sha256(path) != task["sha256"]:
            raise RuntimeError(f"judge input SHA-256 mismatch: {path}")
    if snapshot_sha256 != EXPECTED_INPUT_SNAPSHOT_SHA256:
        raise RuntimeError(
            f"input snapshot changed: {snapshot_sha256} != "
            f"{EXPECTED_INPUT_SNAPSHOT_SHA256}"
        )
    print(
        "[preflight OK] 100 pinned General B0 + 100 pinned GT; "
        f"snapshot={snapshot_sha256}; resumed nominal new calls=201, "
        f"aggregate including prior interrupted probe=202 <= cap {HARD_CALL_CAP}",
        flush=True,
    )


class Budget:
    """Track every API attempt and all provider-reported token usage."""

    def __init__(self) -> None:
        self.calls = PRIOR_INTERRUPTED_CALLS
        self.input_tokens = PRIOR_INPUT_TOKEN_RESERVE
        self.output_tokens = PRIOR_OUTPUT_TOKEN_RESERVE
        self.usage_reported_calls = 0

    @property
    def estimated_usd(self) -> float:
        return (
            self.input_tokens * INPUT_USD_PER_M
            + self.output_tokens * OUTPUT_USD_PER_M
        ) / 1_000_000

    def take_call(self) -> bool:
        if self.calls >= HARD_CALL_CAP or self.exceeded():
            return False
        self.calls += 1
        return True

    def record(self, response) -> dict:
        usage = getattr(response, "usage", None)
        if usage is None:
            return {"input_tokens": None, "output_tokens": None}
        input_tokens = int(
            getattr(usage, "prompt_tokens", None)
            or getattr(usage, "input_tokens", 0)
            or 0
        )
        output_tokens = int(
            getattr(usage, "completion_tokens", None)
            or getattr(usage, "output_tokens", 0)
            or 0
        )
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.usage_reported_calls += 1
        return {"input_tokens": input_tokens, "output_tokens": output_tokens}

    def exceeded(self) -> bool:
        return (
            self.input_tokens > INPUT_TOKEN_CAP
            or self.output_tokens > OUTPUT_TOKEN_CAP
            or self.estimated_usd > USD_CAP
        )

    def as_dict(self) -> dict:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "usage_reported_calls": self.usage_reported_calls,
            "estimated_usd": round(self.estimated_usd, 6),
            "prior_interrupted_calls": PRIOR_INTERRUPTED_CALLS,
            "prior_input_token_reserve": PRIOR_INPUT_TOKEN_RESERVE,
            "prior_output_token_reserve": PRIOR_OUTPUT_TOKEN_RESERVE,
        }


async def _resolve_params(client, budget: Budget) -> dict:
    legacy = {"temperature": 0.0, "max_tokens": MAX_COMPLETION_TOKENS}
    modern = {"max_completion_tokens": MAX_COMPLETION_TOKENS}
    if not budget.take_call():
        raise RuntimeError("authorization cap exhausted before parameter probe")
    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": "Reply with the word ok."}],
            **legacy,
        )
        budget.record(response)
        print(f"[probe] param variant={legacy}", flush=True)
        return legacy
    except Exception as error:
        message = str(error).lower()
        supported_error = any(
            marker in message
            for marker in ("temperature", "max_tokens", "unsupported")
        )
        if not supported_error:
            raise
        print(f"[probe] param variant={modern}", flush=True)
        return modern


def _base_row(task: dict) -> dict:
    return {
        "arm": task["arm"],
        "sample_id": task["sample_id"],
        "path": str(task["path"].relative_to(REPO)),
        "render_sha256": task["sha256"],
    }


async def _score_task(client, task: dict, kwargs: dict, budget: Budget) -> dict:
    encoded: Optional[str] = None
    last_error = "no attempt"
    for attempt in range(2):
        if not budget.take_call():
            return {**_base_row(task), "status": "authorization_cap_reached"}
        if encoded is None:
            encoded = base64.b64encode(task["path"].read_bytes()).decode()
        try:
            response = await client.chat.completions.create(
                model=MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": s21.COLE_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{task['mime']};base64,{encoded}"
                                },
                            },
                        ],
                    }
                ],
                **kwargs,
            )
            usage = budget.record(response)
            if budget.exceeded():
                return {
                    **_base_row(task),
                    "status": "authorization_cap_exceeded",
                    "usage": usage,
                }
            text = (response.choices[0].message.content or "").strip()
            parsed = s21._parse_cole_json(text)
            if parsed is not None:
                return {
                    **_base_row(task),
                    "status": "ok",
                    "scores": parsed,
                    "smean4": statistics.mean(parsed[k] for k in s21.REPORT_AXES),
                    "usage": usage,
                }
            last_error = f"unparseable: {text[:160]!r}"
        except Exception as error:  # noqa: BLE001
            last_error = f"{type(error).__name__}: {error}"
        if attempt == 0:
            await asyncio.sleep(1.5)
    return {**_base_row(task), "status": "failed", "error": last_error}


async def _run_paid(tasks: List[dict]) -> Tuple[List[dict], Budget, bool]:
    client = s21._load_openai_client()
    budget = Budget()
    kwargs = await _resolve_params(client, budget)
    semaphore = asyncio.Semaphore(CONCURRENCY)
    completed: List[dict] = []
    abort = asyncio.Event()

    async def worker(task: dict) -> dict:
        async with semaphore:
            if abort.is_set():
                return {**_base_row(task), "status": "aborted"}
            row = await _score_task(client, task, kwargs, budget)
            completed.append(row)
            done_n = len(completed)
            if row["status"].startswith("authorization_cap"):
                abort.set()
            if done_n == PARSE_FAIL_WINDOW:
                failures = sum(r["status"] != "ok" for r in completed)
                if failures / done_n > PARSE_FAIL_MAX_RATE:
                    print(
                        f"[ABORT] first-window failures={failures}/{done_n}",
                        flush=True,
                    )
                    abort.set()
            if done_n % 25 == 0 or done_n == len(tasks):
                ok_n = sum(r["status"] == "ok" for r in completed)
                print(
                    f"[progress] {done_n}/{len(tasks)} scored, ok={ok_n}, "
                    f"budget={budget.as_dict()}",
                    flush=True,
                )
            return row

    rows = list(await asyncio.gather(*(worker(task) for task in tasks)))
    return rows, budget, abort.is_set()


def _arm_means(rows: Sequence[dict]) -> dict:
    output: Dict[str, dict] = {}
    for arm in ("general", "gt"):
        ok = [row for row in rows if row["arm"] == arm and row["status"] == "ok"]
        if not ok:
            output[arm] = {"n": 0}
            continue
        output[arm] = {
            "n": len(ok),
            "smean4": statistics.mean(row["smean4"] for row in ok),
            **{
                axis: statistics.mean(row["scores"][axis] for row in ok)
                for axis in ("SDL", "SQL", "STV", "SGI", "SIO")
            },
        }
    if output["general"].get("n") and output["gt"].get("n"):
        output["general"]["pct_of_gt_smean4"] = (
            100.0 * output["general"]["smean4"] / output["gt"]["smean4"]
        )
    return output


def _paired(rows: Sequence[dict]) -> dict:
    by_arm: Dict[str, Dict[str, dict]] = {}
    for row in rows:
        if row["status"] == "ok":
            by_arm.setdefault(row["arm"], {})[row["sample_id"]] = row
    common = sorted(set(by_arm.get("general", {})) & set(by_arm.get("gt", {})))
    deltas = [
        by_arm["general"][sample_id]["smean4"]
        - by_arm["gt"][sample_id]["smean4"]
        for sample_id in common
    ]
    wins = sum(delta > 0 for delta in deltas)
    losses = sum(delta < 0 for delta in deltas)
    per_axis = {}
    for axis in s21.REPORT_AXES:
        axis_wins = sum(
            by_arm["general"][sid]["scores"][axis]
            > by_arm["gt"][sid]["scores"][axis]
            for sid in common
        )
        axis_losses = sum(
            by_arm["general"][sid]["scores"][axis]
            < by_arm["gt"][sid]["scores"][axis]
            for sid in common
        )
        per_axis[axis] = {
            "wins": axis_wins,
            "losses": axis_losses,
            "ties": len(common) - axis_wins - axis_losses,
            "sign_p": relation_judge._sign_test_p(axis_wins, axis_losses),
        }
    return {
        "general_vs_gt": {
            "n_pairs": len(common),
            "smean4": {
                "wins": wins,
                "losses": losses,
                "ties": len(common) - wins - losses,
                "sign_p": relation_judge._sign_test_p(wins, losses),
                "bootstrap": relation_judge._bootstrap_ci(deltas),
            },
            "per_axis": per_axis,
        }
    }


def _publish(
    rows: List[dict],
    budget: Budget,
    wall_seconds: float,
    matched_ids: List[str],
    snapshot_sha256: str,
) -> Path:
    final, staging = _target_paths()
    if final.exists() or staging.exists():
        raise RuntimeError("write-once target appeared before publication")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    staging.mkdir(parents=False, exist_ok=False)
    try:
        (staging / "per_sample.jsonl").write_text(
            "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n"
        )
        aggregate = {
            "schema_version": "a3.cole-judge.v1",
            "evaluation_id": EVALUATION_ID,
            "judge_model": MODEL,
            "prompt_sha256": hashlib.sha256(s21.COLE_PROMPT.encode()).hexdigest(),
            "source_sidecar": SOURCE_SIDECAR_ID,
            "input_snapshot_sha256": snapshot_sha256,
            "matched_ids_count": len(matched_ids),
            "authorization": {
                "call_cap": HARD_CALL_CAP,
                "input_token_cap": INPUT_TOKEN_CAP,
                "output_token_cap": OUTPUT_TOKEN_CAP,
                "usd_cap": USD_CAP,
            },
            "usage": budget.as_dict(),
            "wall_seconds": round(wall_seconds, 2),
            "status_counts": {
                status: sum(row["status"] == status for row in rows)
                for status in sorted({row["status"] for row in rows})
            },
            "arm_means": _arm_means(rows),
            "paired": _paired(rows),
        }
        (staging / "aggregate.json").write_text(
            json.dumps(aggregate, indent=2, sort_keys=True, allow_nan=False) + "\n"
        )
        os.rename(staging, final)
    except Exception:
        if staging.exists():
            for child in staging.iterdir():
                child.unlink()
            staging.rmdir()
        raise
    return final


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true", help="zero-cost only")
    mode.add_argument("--allow-api-calls", action="store_true")
    args = parser.parse_args()

    tasks, matched_ids, snapshot_sha256 = _load_tasks()
    _preflight(tasks, snapshot_sha256)
    if args.preflight:
        return 0

    started = time.time()
    rows, budget, aborted = asyncio.run(_run_paid(tasks))
    wall_seconds = time.time() - started
    if aborted or budget.exceeded():
        raise RuntimeError(
            f"paid judge aborted without publication; budget={budget.as_dict()}"
        )
    tasks_after, ids_after, snapshot_after = _load_tasks()
    _preflight(tasks_after, snapshot_after)
    if ids_after != matched_ids or snapshot_after != snapshot_sha256:
        raise RuntimeError("judge inputs changed during paid execution")
    final = _publish(rows, budget, wall_seconds, matched_ids, snapshot_sha256)
    aggregate = json.loads((final / "aggregate.json").read_text())
    print(
        json.dumps(
            {
                "published": str(final.relative_to(REPO)),
                "wall_seconds": round(wall_seconds, 1),
                "status_counts": aggregate["status_counts"],
                "usage": aggregate["usage"],
                "arm_means": aggregate["arm_means"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
