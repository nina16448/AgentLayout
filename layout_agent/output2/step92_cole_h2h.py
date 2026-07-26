"""Step 92 -- matched COLE H2H under the text-as-image protocol (Step 89 samples).

Why this exists
---------------
The paper's B-axis headline is currently Step 70's "86.6% of designer Smean",
measured under the OLD protocol (old renderer, raw-asset input). The paper's
main result is now Step 89 (text-as-image protocol, N=100). Putting those two
in one table is exactly the cross-version comparison that EXPERIMENT_MATRIX
table 3 forbids. This script re-measures the B axis on the Step 89 samples so
that A axis (metrics.json), B axis (this file) and C axis (blind pairwise in
_summary.json) all come from ONE protocol on ONE set of 100 samples.

Comparability contract
----------------------
The COLE prompt, model (gpt-4o), temperature (0.0) and parser are imported
verbatim from ``step21_phaseb_eval`` -- the same module Step 70 and Step 74
called. Nothing about the judge is re-implemented here, so the numbers are
directly comparable to step70_n100_{agent,designer_gt}_5axis.json. The ONLY
thing that changed is which PNGs get fed in.

Note: designer GT is a .jpg but ``_score_image`` wraps every payload in a
``data:image/png`` header. Step 70/74 did the same and OpenAI sniffs the actual
bytes, so it is kept as-is rather than "fixed" -- changing it would silently
break comparability with the published numbers.

The evaluator stays pinned to gpt-4o via ``step21_phaseb_eval.MODEL`` no matter
what ``~/.metagpt/config2.yaml`` sets as the pipeline model (same isolation
decision as Step 91).

Inputs
------
    agent_a  -- output2/step89_n100/<id>/a/final.png   (baseline arm)
    agent_b  -- output2/step89_n100/<id>/b/final.png   (deep-review arm, optional)
    gt       -- output/crello_<id>/ground_truth_preview.jpg

Outputs
-------
    output2/step92_cole_h2h/per_sample/<id>.json   (resumable unit of work)
    output2/step92_cole_h2h/aggregate.json
    output2/step92_cole_h2h/aggregate.md

Cost
----
~$0.0125 per vision call. Default arms (a, gt) = 2 calls/sample = ~$2.5 for
N=100. Adding arm b makes it 3 calls/sample = ~$3.75.

Run::

    # cost/plan only, ZERO API calls -- always do this first
    conda run -n meta python layout_agent/output2/step92_cole_h2h.py --dry-run

    # 3-sample smoke
    conda run -n meta python layout_agent/output2/step92_cole_h2h.py --max-samples 3

    # full run (resumable; re-run with --skip-existing after an interrupt)
    conda run -n meta python layout_agent/output2/step92_cole_h2h.py --skip-existing

    # include the deep-review arm
    conda run -n meta python layout_agent/output2/step92_cole_h2h.py --arms a,b,gt

    # re-aggregate from existing per_sample/ without touching the API
    conda run -n meta python layout_agent/output2/step92_cole_h2h.py --aggregate-only
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import random
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
OUTPUT1 = HERE.parent / "output"
for _p in (str(REPO_ROOT), str(OUTPUT1), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import step21_phaseb_eval as s21  # noqa: E402  (COLE prompt + judge, verbatim)

from provenance import capture as _prov_capture  # noqa: E402
from provenance import summary_line as _prov_line  # noqa: E402
from provenance import write as _prov_write  # noqa: E402

STEP89_ROOT = HERE / "step89_n100"
OUT_ROOT = HERE / "step92_cole_h2h"
PER_SAMPLE = OUT_ROOT / "per_sample"

REPORT_AXES_4 = ("SDL", "SQL", "STV", "SIO")
ALL_AXES = ("SDL", "SQL", "STV", "SGI", "SIO")
COST_PER_CALL_USD = 0.0125
BOOTSTRAP_N = 10_000
BOOTSTRAP_SEED = 20260709

# Step 70 published baselines (old protocol). Echoed in the report purely as
# context -- they are NOT a valid same-table comparison.
STEP70_REF = {"agent_smean4": 6.598, "gt_smean4": 7.617, "pct": 86.6}


# --------------------------------------------------------------------------
# source resolution
# --------------------------------------------------------------------------
def _arm_png(sample_id: str, arm: str) -> Path:
    """Resolve one arm's image. ``gt`` comes from the Crello cache, not step89."""
    if arm == "gt":
        return OUTPUT1 / f"crello_{sample_id}" / "ground_truth_preview.jpg"
    return STEP89_ROOT / sample_id / arm / "final.png"


def _b64(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    return base64.b64encode(path.read_bytes()).decode()


def _sample_ids(limit: Optional[int], only: Optional[str]) -> List[str]:
    ids = sorted(d.name for d in STEP89_ROOT.iterdir()
                 if d.is_dir() and not d.name.startswith("_"))
    if only:
        ids = [i for i in ids if i == only]
    if limit:
        ids = ids[:limit]
    return ids


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------
def _agg_scores(scores: Dict[str, int]) -> Dict[str, object]:
    return {
        "scores": scores,
        "smean4": float(statistics.mean(scores[k] for k in REPORT_AXES_4)),
        "smean5": float(statistics.mean(scores[k] for k in ALL_AXES)),
    }


def _existing_arms(sample_id: str) -> Dict[str, dict]:
    """Already-scored arms for this sample, so a later run can add an arm
    without paying to re-score the ones already on disk. Only ``ok`` arms are
    reused -- a previous ``parse_failed`` gets another chance."""
    p = PER_SAMPLE / f"{sample_id}.json"
    if not p.exists():
        return {}
    prior = json.loads(p.read_text()).get("arms", {})
    return {k: v for k, v in prior.items() if v.get("status") == "ok"}


async def _score_one_sample(client, sample_id: str, arms: Sequence[str]) -> dict:
    """Score every requested arm for one sample. Fails loud, never scores 0."""
    out: dict = {"id": sample_id, "status": "ok", "arms": {}}
    reusable = _existing_arms(sample_id)

    for arm in arms:
        if arm in reusable:
            out["arms"][arm] = reusable[arm]
            continue
        png = _arm_png(sample_id, arm)
        b64 = _b64(png)
        if b64 is None:
            out["arms"][arm] = {"status": "missing_image", "path": str(png)}
            out["status"] = "partial"
            continue

        scores = await s21._score_image(client, b64)
        if scores is None:
            out["arms"][arm] = {"status": "parse_failed", "path": str(png)}
            out["status"] = "partial"
            continue

        out["arms"][arm] = {"status": "ok", "path": str(png), **_agg_scores(scores)}

    # Paired deltas exist only when both sides of the pair actually scored.
    for arm in arms:
        if arm == "gt":
            continue
        a, g = out["arms"].get(arm, {}), out["arms"].get("gt", {})
        if a.get("status") != "ok" or g.get("status") != "ok":
            continue
        out.setdefault("deltas", {})[arm] = {
            "per_axis": {k: a["scores"][k] - g["scores"][k] for k in ALL_AXES},
            "smean4": a["smean4"] - g["smean4"],
            "smean5": a["smean5"] - g["smean5"],
            "pct_of_gt_smean4": (a["smean4"] / g["smean4"] * 100.0) if g["smean4"] else None,
        }
    return out


# --------------------------------------------------------------------------
# statistics (no scipy -- exact binomial + percentile bootstrap)
# --------------------------------------------------------------------------
def _sign_test_p(wins: int, losses: int) -> Optional[float]:
    """Two-sided exact binomial on non-tied pairs, H0: p=0.5."""
    n = wins + losses
    if n == 0:
        return None
    k = min(wins, losses)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2.0 * tail)


def _bootstrap_ci(deltas: Sequence[float], alpha: float = 0.05) -> Optional[Dict[str, float]]:
    """Percentile bootstrap CI on the mean paired delta. Seeded => reproducible."""
    if len(deltas) < 2:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(deltas)
    means = [statistics.fmean(deltas[rng.randrange(n)] for _ in range(n))
             for _ in range(BOOTSTRAP_N)]
    means.sort()
    lo = means[int((alpha / 2) * BOOTSTRAP_N)]
    hi = means[min(BOOTSTRAP_N - 1, int((1 - alpha / 2) * BOOTSTRAP_N))]
    return {"mean": statistics.fmean(deltas), "ci95_lo": lo, "ci95_hi": hi,
            "n_bootstrap": BOOTSTRAP_N, "seed": BOOTSTRAP_SEED}


def _paired_block(rows: List[dict], arm: str) -> Optional[dict]:
    """Everything the paper needs for one arm vs designer GT."""
    paired = [r for r in rows if r.get("deltas", {}).get(arm)]
    if not paired:
        return None

    d4 = [r["deltas"][arm]["smean4"] for r in paired]
    d5 = [r["deltas"][arm]["smean5"] for r in paired]

    wins = sum(1 for d in d4 if d > 0)
    ties = sum(1 for d in d4 if d == 0)
    losses = sum(1 for d in d4 if d < 0)

    per_axis = {}
    for ax in ALL_AXES:
        dax = [r["deltas"][arm]["per_axis"][ax] for r in paired]
        per_axis[ax] = {
            "agent_mean": statistics.fmean(r["arms"][arm]["scores"][ax] for r in paired),
            "gt_mean": statistics.fmean(r["arms"]["gt"]["scores"][ax] for r in paired),
            "delta_mean": statistics.fmean(dax),
            "bootstrap": _bootstrap_ci(dax),
            "wins": sum(1 for d in dax if d > 0),
            "ties": sum(1 for d in dax if d == 0),
            "losses": sum(1 for d in dax if d < 0),
            "sign_p": _sign_test_p(sum(1 for d in dax if d > 0),
                                   sum(1 for d in dax if d < 0)),
        }

    agent_m4 = statistics.fmean(r["arms"][arm]["smean4"] for r in paired)
    gt_m4 = statistics.fmean(r["arms"]["gt"]["smean4"] for r in paired)
    agent_m5 = statistics.fmean(r["arms"][arm]["smean5"] for r in paired)
    gt_m5 = statistics.fmean(r["arms"]["gt"]["smean5"] for r in paired)
    return {
        "n_paired": len(paired),
        "agent_smean4": agent_m4,
        "gt_smean4": gt_m4,
        "pct_of_gt_smean4": (agent_m4 / gt_m4 * 100.0) if gt_m4 else None,
        "agent_smean5": agent_m5,
        "gt_smean5": gt_m5,
        "pct_of_gt_smean5": (agent_m5 / gt_m5 * 100.0) if gt_m5 else None,
        "smean4_delta_bootstrap": _bootstrap_ci(d4),
        "smean5_delta_bootstrap": _bootstrap_ci(d5),
        "smean4_wins_ties_losses": [wins, ties, losses],
        "smean4_sign_p": _sign_test_p(wins, losses),
        "per_axis": per_axis,
    }


def _arm_vs_arm(rows: List[dict]) -> Optional[dict]:
    """A vs B on Smean4, paired. Answers 'does the deep-review stack help?'"""
    paired = [r for r in rows
              if r["arms"].get("a", {}).get("status") == "ok"
              and r["arms"].get("b", {}).get("status") == "ok"]
    if not paired:
        return None
    d = [r["arms"]["b"]["smean4"] - r["arms"]["a"]["smean4"] for r in paired]
    wins = sum(1 for x in d if x > 0)
    losses = sum(1 for x in d if x < 0)
    return {
        "n_paired": len(paired),
        "b_minus_a_smean4": _bootstrap_ci(d),
        "b_wins_ties_losses": [wins, sum(1 for x in d if x == 0), losses],
        "sign_p": _sign_test_p(wins, losses),
    }


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------
def _fmt_ci(b: Optional[dict]) -> str:
    if not b:
        return "—"
    return f"{b['mean']:+.3f} [{b['ci95_lo']:+.3f}, {b['ci95_hi']:+.3f}]"


def _fmt_p(p: Optional[float]) -> str:
    return "—" if p is None else (f"{p:.3f}" if p >= 0.001 else "<0.001")


def _render_md(agg: dict) -> str:
    L: List[str] = []
    L.append("# Step 92 — matched COLE H2H under the text-as-image protocol\n")
    L.append(f"- samples scored: **{agg['n_scored']}** / {agg['n_total']}")
    L.append(f"- arms: `{', '.join(agg['arms'])}`  |  judge: `{agg['judge_model']}` "
             f"single-call COLE, temperature 0.0 (verbatim from `step21_phaseb_eval`)")
    L.append(f"- vision calls: {agg['n_calls']}  |  est. cost: ${agg['est_cost_usd']:.2f}")
    L.append("\n> Same 100 samples as `output2/step89_n100/` — A axis (`metrics.json`), "
             "B axis (this file) and C axis (`_summary.json` blind pairwise) are now "
             "**one protocol, one sample set**.\n")

    for arm, name in (("a", "A 基線"), ("b", "B 深審")):
        blk = agg["vs_gt"].get(arm)
        if not blk:
            continue
        L.append(f"\n## {name} vs designer GT (paired, n={blk['n_paired']})\n")
        L.append(f"- **Smean4 {blk['agent_smean4']:.3f} vs GT {blk['gt_smean4']:.3f}** "
                 f"→ **{blk['pct_of_gt_smean4']:.1f}% of designer**")
        L.append(f"- Smean5 {blk['agent_smean5']:.3f} vs GT {blk['gt_smean5']:.3f} "
                 f"→ {blk['pct_of_gt_smean5']:.1f}%")
        L.append(f"- Δ Smean4 (95% bootstrap CI): {_fmt_ci(blk['smean4_delta_bootstrap'])}")
        w, t, lo = blk["smean4_wins_ties_losses"]
        L.append(f"- per-sample win/tie/loss: {w}/{t}/{lo}  "
                 f"(sign test p={_fmt_p(blk['smean4_sign_p'])})\n")
        L.append("| axis | agent | GT | Δ mean [95% CI] | W/T/L | sign p |")
        L.append("|---|---|---|---|---|---|")
        for ax in ALL_AXES:
            a = blk["per_axis"][ax]
            L.append(f"| {ax} | {a['agent_mean']:.2f} | {a['gt_mean']:.2f} | "
                     f"{_fmt_ci(a['bootstrap'])} | {a['wins']}/{a['ties']}/{a['losses']} | "
                     f"{_fmt_p(a['sign_p'])} |")

    if agg.get("b_vs_a"):
        b = agg["b_vs_a"]
        L.append(f"\n## B 深審 − A 基線 (paired, n={b['n_paired']})\n")
        L.append(f"- Δ Smean4: {_fmt_ci(b['b_minus_a_smean4'])}")
        w, t, lo = b["b_wins_ties_losses"]
        L.append(f"- B better / tie / A better: {w}/{t}/{lo}  (sign p={_fmt_p(b['sign_p'])})")

    a_blk = agg["vs_gt"].get("a")
    if a_blk and a_blk.get("pct_of_gt_smean4"):
        L.append("\n## Context — do NOT put in the same table\n")
        L.append(f"Step 70 (old protocol, old renderer, raw-asset input) reported agent "
                 f"Smean4 {STEP70_REF['agent_smean4']} vs GT {STEP70_REF['gt_smean4']} "
                 f"= {STEP70_REF['pct']}% of designer. This run reports "
                 f"**{a_blk['pct_of_gt_smean4']:.1f}%** under text-as-image. The two differ "
                 f"in renderer version AND input protocol; per `EXPERIMENT_MATRIX.md` table 3 "
                 f"they are not directly comparable. Report this run; cite Step 70 as the "
                 f"pre-text-as-image measurement only.")

    if agg["failures"]:
        L.append("\n## Failures\n")
        for f in agg["failures"]:
            L.append(f"- `{f['id']}` — {f['arm']}: {f['status']}")
    return "\n".join(L) + "\n"


def _aggregate(rows: List[dict], arms: Sequence[str], n_total: int) -> dict:
    failures = [
        {"id": r["id"], "arm": arm, "status": info["status"]}
        for r in rows for arm, info in r["arms"].items()
        if info.get("status") != "ok"
    ]
    n_calls = sum(1 for r in rows for info in r["arms"].values()
                  if info.get("status") == "ok")
    vs_gt = {arm: _paired_block(rows, arm) for arm in arms if arm != "gt"}
    return {
        "step": 92,
        "protocol": "text-as-image (Step 89 samples)",
        "judge_model": s21.MODEL,
        # Read back what the SCORING run recorded. Capturing fresh here would
        # stamp aggregate.json with the re-aggregation environment (possibly a
        # different commit / model) and silently misattribute the numbers.
        "provenance": _read_run_provenance(),
        "arms": list(arms),
        "n_total": n_total,
        "n_scored": len(rows),
        "n_calls": n_calls,
        "est_cost_usd": n_calls * COST_PER_CALL_USD,
        "vs_gt": {k: v for k, v in vs_gt.items() if v},
        "b_vs_a": _arm_vs_arm(rows) if {"a", "b"} <= set(arms) else None,
        "failures": failures,
    }


def _read_run_provenance() -> dict:
    """Provenance of the run that produced per_sample/, not of this process.

    Absent for scores written before provenance capture existed -- say so
    rather than substituting today's environment.
    """
    p = OUT_ROOT / "provenance.json"
    if p.exists():
        return json.loads(p.read_text())
    return {"captured": False,
            "note": "scored before provenance capture was wired in; "
                    "see IMPLEMENTATION_LOG Step 92 for the reconstructed environment"}


def _load_per_sample() -> List[dict]:
    if not PER_SAMPLE.exists():
        return []
    return [json.loads(p.read_text()) for p in sorted(PER_SAMPLE.glob("*.json"))]


def _write_aggregate(rows: List[dict], arms: Sequence[str], n_total: int) -> dict:
    agg = _aggregate(rows, arms, n_total)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    (OUT_ROOT / "aggregate.json").write_text(json.dumps(agg, indent=2, ensure_ascii=False))
    (OUT_ROOT / "aggregate.md").write_text(_render_md(agg))
    return agg


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arms", default="a,gt",
                    help="Comma list from {a,b,gt}. 'gt' is required. Default: a,gt")
    ap.add_argument("--only", default=None, help="Single sample id (smoke).")
    ap.add_argument("--max-samples", type=int, default=None, help="Limit (smoke).")
    ap.add_argument("--skip-existing", action="store_true",
                    help="Skip samples that already have per_sample/<id>.json.")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the plan and cost estimate. Makes ZERO API calls.")
    ap.add_argument("--aggregate-only", action="store_true",
                    help="Rebuild aggregate.{json,md} from per_sample/. ZERO API calls.")
    args = ap.parse_args()

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    bad = set(arms) - {"a", "b", "gt"}
    if bad:
        print(f"[error] unknown arms: {sorted(bad)}")
        return 2
    if "gt" not in arms:
        print("[error] 'gt' arm is required -- this is a head-to-head against designer GT.")
        return 2

    if not STEP89_ROOT.exists():
        print(f"[error] step89 root missing: {STEP89_ROOT}")
        return 1

    if args.aggregate_only:
        rows = _load_per_sample()
        if not rows:
            print(f"[error] no per-sample results under {PER_SAMPLE}")
            return 1
        agg = _write_aggregate(rows, arms, len(rows))
        print(f"[done] re-aggregated {agg['n_scored']} samples -> {OUT_ROOT}")
        return 0

    ids = _sample_ids(args.max_samples, args.only)
    if args.skip_existing:
        # Arm-level: a sample is done only when EVERY requested arm is scored.
        ids = [i for i in ids if not set(arms) <= set(_existing_arms(i))]

    # Pre-flight: check every image exists before spending a cent. Arms already
    # on disk are reused, so they cost nothing.
    todo = [(i, arm) for i in ids for arm in arms if arm not in _existing_arms(i)]
    missing = [(i, arm) for i, arm in todo if not _arm_png(i, arm).exists()]
    n_reused = len(ids) * len(arms) - len(todo)
    n_calls = len(todo) - len(missing)
    if n_reused:
        print(f"[plan] reusing {n_reused} already-scored arm(s) from per_sample/")

    prov = _prov_capture(judge_model=s21.MODEL)
    print(_prov_line(prov))
    print(f"[plan] samples={len(ids)}  arms={arms}  judge={s21.MODEL}")
    print(f"[plan] vision calls={n_calls}  est. cost=${n_calls * COST_PER_CALL_USD:.2f}")
    if missing:
        print(f"[plan] WARNING {len(missing)} missing images (recorded, not scored):")
        for i, arm in missing[:10]:
            print(f"        {i} [{arm}] -> {_arm_png(i, arm)}")
    if args.dry_run:
        print("[dry-run] no API calls made.")
        return 0
    if not ids:
        print("[plan] nothing to do (all skipped).")
        return 0

    PER_SAMPLE.mkdir(parents=True, exist_ok=True)
    _prov_write(OUT_ROOT / "provenance.json", prov)
    client = s21._load_openai_client()
    sem = asyncio.Semaphore(args.concurrency)
    done = {"n": 0}

    async def _worker(sid: str) -> dict:
        async with sem:
            row = await _score_one_sample(client, sid, arms)
            (PER_SAMPLE / f"{sid}.json").write_text(
                json.dumps(row, indent=2, ensure_ascii=False))
            done["n"] += 1
            d = row.get("deltas", {}).get("a")
            tail = f"Δsmean4={d['smean4']:+.2f}" if d else row["status"]
            print(f"[{done['n']:3d}/{len(ids)}] {sid}  {tail}")
            return row

    await asyncio.gather(*(_worker(i) for i in ids))

    rows = _load_per_sample()  # includes previously-completed samples on resume
    agg = _write_aggregate(rows, arms, len(rows))
    a = agg["vs_gt"].get("a")
    if a:
        print(f"\n[result] A vs GT  Smean4 {a['agent_smean4']:.3f} / {a['gt_smean4']:.3f} "
              f"= {a['pct_of_gt_smean4']:.1f}% of designer  "
              f"(Δ CI {_fmt_ci(a['smean4_delta_bootstrap'])})")
    print(f"[done] -> {OUT_ROOT / 'aggregate.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
