"""Step 96 (E2') -- per-round paired gain curve for the LEGACY pipeline (N=178).

Why
---
Paper §5.8 currently cites the Step 89 per-round curve (+0.45 / -0.19 / ...),
which was measured on the text-as-image protocol with the deep-review stack.
E2' recomputes the same curve on the Step 73-74 full-trace run -- the legacy
pipeline, raw-asset input, refinement loop -- so §5.8 can cite a curve from the
pipeline it actually describes.

Zero API. Reads only `layout_agent/full_result/<id>/trace/per_round_judge.json`,
the JudgeAesthetic verdicts persisted during the Step 73-74 run.

Round total
-----------
Each round the judge scores several candidates and names one
``best_candidate_id``. The round's total is that candidate's 5-axis COLE total
(5..50) -- the score the pipeline actually carried forward. Spot-checked across
rounds: best_candidate_id is always the argmax, so this equals "the score of
what the pipeline chose". Rounds whose named id is missing from the evaluation
list fall back to argmax and are counted in ``fallback_to_argmax``; a nonzero
count is reported, never silently absorbed.

Pairing
-------
``mean_delta`` for transition k is the paired mean of total(k) - total(k-1)
over samples that reached BOTH rounds. Samples that accepted (and stopped) at
round k-1 do not contribute to transition k, which is why n_pairs shrinks with
k. That attrition is survivorship, not missing data -- reported per transition
so the reader can see it.

Columns mirror `step93_perround_curve.py` so the two tables line up; the 95%
bootstrap CI is an extra column (step93 reports only the sign test).

Run::

    conda run -n meta python layout_agent/output2/step96_legacy_perround_curve.py
"""
from __future__ import annotations

import json
import math
import random
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from provenance import capture as _prov_capture  # noqa: E402
from provenance import summary_line as _prov_line  # noqa: E402

FULL_RESULT = REPO_ROOT / "layout_agent" / "full_result"
OUT_ROOT = HERE / "step96_legacy_perround"

AXES = ("design_layout", "content_relevance", "typography_color",
        "graphics_images", "innovation_originality")
BOOTSTRAP_N = 10_000
BOOTSTRAP_SEED = 20260709

# Step 89 §11.3 curve, for the side-by-side contrast (different protocol,
# different pipeline -- shown to justify the replacement, never merged).
STEP89_A = {1: +0.45, 2: +0.14}
STEP89_B = {1: +0.45, 2: -0.19, 3: -0.03, 4: -0.12}


# --------------------------------------------------------------------------
# statistics (no scipy)
# --------------------------------------------------------------------------
def _sign_test_p(wins: int, losses: int) -> Optional[float]:
    """Two-sided exact binomial on non-tied pairs, H0: p=0.5."""
    n = wins + losses
    if n == 0:
        return None
    k = min(wins, losses)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2.0 * tail)


def _bootstrap_ci(vals: Sequence[float], alpha: float = 0.05) -> Optional[Dict[str, float]]:
    if len(vals) < 2:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(vals)
    means = [statistics.fmean(vals[rng.randrange(n)] for _ in range(n))
             for _ in range(BOOTSTRAP_N)]
    means.sort()
    return {
        "mean": statistics.fmean(vals),
        "ci95_lo": means[int((alpha / 2) * BOOTSTRAP_N)],
        "ci95_hi": means[min(BOOTSTRAP_N - 1, int((1 - alpha / 2) * BOOTSTRAP_N))],
        "n_bootstrap": BOOTSTRAP_N,
        "seed": BOOTSTRAP_SEED,
    }


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------
def _round_total(judgement: dict) -> Tuple[Optional[int], Optional[dict], bool]:
    """(total, axis_scores, used_fallback) for the candidate the pipeline kept."""
    evals = judgement.get("evaluations") or []
    by_id = {e.get("candidate_id"): e for e in evals if e.get("total") is not None}
    if not by_id:
        return None, None, False

    best_id = judgement.get("best_candidate_id")
    if best_id in by_id:
        e = by_id[best_id]
        return e["total"], e.get("scores"), False

    e = max(by_id.values(), key=lambda x: x["total"])
    return e["total"], e.get("scores"), True


def load_series() -> Tuple[Dict[str, Dict[int, dict]], dict]:
    """-> {sample_id: {round_idx: {total, scores, label}}}, plus diagnostics."""
    series: Dict[str, Dict[int, dict]] = {}
    diag = {"dirs": 0, "no_trace": 0, "empty": 0, "fallback_to_argmax": 0,
            "rounds_without_total": 0}

    for d in sorted(p for p in FULL_RESULT.iterdir()
                    if p.is_dir() and not p.name.startswith("_")):
        diag["dirs"] += 1
        f = d / "trace" / "per_round_judge.json"
        if not f.exists():
            diag["no_trace"] += 1
            continue
        try:
            rounds_raw = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            diag["no_trace"] += 1
            continue

        rounds: Dict[int, dict] = {}
        for r in rounds_raw:
            total, scores, fb = _round_total(r.get("judgement") or {})
            if total is None:
                diag["rounds_without_total"] += 1
                continue
            if fb:
                diag["fallback_to_argmax"] += 1
            rounds[int(r["round"])] = {"total": total, "scores": scores,
                                       "label": r.get("label")}
        if rounds:
            series[d.name] = rounds
        else:
            diag["empty"] += 1
    return series, diag


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------
def transition_stats(series: Dict[str, Dict[int, dict]], k: int) -> Optional[dict]:
    """Paired total(k) - total(k-1) over samples reaching both rounds."""
    deltas: List[Tuple[str, int]] = []
    for sid, rounds in series.items():
        if k in rounds and (k - 1) in rounds:
            deltas.append((sid, rounds[k]["total"] - rounds[k - 1]["total"]))
    if not deltas:
        return None

    vals = [d for _, d in deltas]
    wins = sum(1 for v in vals if v > 0)
    losses = sum(1 for v in vals if v < 0)
    ties = sum(1 for v in vals if v == 0)
    return {
        "arm": "legacy",
        "round": k,
        "transition": f"R{k-1}->R{k}",
        "n_pairs": len(vals),
        "mean_delta": statistics.fmean(vals),
        "median_delta": statistics.median(vals),
        "bootstrap": _bootstrap_ci(vals),
        "improved": wins,
        "tied": ties,
        "worsened": losses,
        "sign_p_two_sided": _sign_test_p(wins, losses),
        "per_sample": {sid: d for sid, d in deltas},
    }


def per_round_means(series: Dict[str, Dict[int, dict]]) -> List[dict]:
    """Descriptive per-round means. The paper reports the total-level curve;
    axis columns are descriptive only (same discipline as step93)."""
    out = []
    max_k = max((max(r) for r in series.values()), default=-1)
    for k in range(max_k + 1):
        rows = [r[k] for r in series.values() if k in r]
        if not rows:
            continue
        entry = {"round": k, "n": len(rows),
                 "mean_total": statistics.fmean(r["total"] for r in rows)}
        scored = [r for r in rows if r.get("scores")]
        for axis in AXES:
            vals = [r["scores"][axis] for r in scored if axis in r["scores"]]
            if vals:
                entry[f"mean_{axis}"] = statistics.fmean(vals)
        out.append(entry)
    return out


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------
def _fmt_ci(b: Optional[dict]) -> str:
    return "—" if not b else f"[{b['ci95_lo']:+.3f}, {b['ci95_hi']:+.3f}]"


def _fmt_p(p: Optional[float]) -> str:
    return "—" if p is None else (f"{p:.3f}" if p >= 0.001 else "<0.001")


def _render_md(res: dict) -> str:
    L: List[str] = []
    L.append("# Step 96 (E2') — legacy pipeline per-round paired gain curve\n")
    L.append("- source: `layout_agent/full_result/*/trace/per_round_judge.json` "
             "(Step 73–74 full-trace run)")
    L.append(f"- samples with usable trace: **{res['n_samples']}** / "
             f"{res['diagnostics']['dirs']} sample dirs")
    L.append("- round total = 5-axis COLE total (5..50) of the round's "
             "`best_candidate_id`; zero API calls\n")

    L.append("## Per-transition paired Δ (judge total)\n")
    L.append("| transition | n pairs | mean Δ | median Δ | 95% CI | improved/tied/worsened | sign p |")
    L.append("|---|---|---|---|---|---|---|")
    for t in res["transitions"]:
        L.append(f"| {t['transition']} | {t['n_pairs']} | **{t['mean_delta']:+.3f}** | "
                 f"{t['median_delta']:+.1f} | {_fmt_ci(t['bootstrap'])} | "
                 f"{t['improved']}/{t['tied']}/{t['worsened']} | "
                 f"{_fmt_p(t['sign_p_two_sided'])} |")

    L.append("\n## Descriptive per-round means\n")
    L.append("| round | n | mean total | " + " | ".join(a[:4].upper() for a in AXES) + " |")
    L.append("|---|---|---|" + "---|" * len(AXES))
    for m in res["per_round_means"]:
        cells = " | ".join(
            (f"{m['mean_' + a]:.2f}" if f"mean_{a}" in m else "—") for a in AXES)
        L.append(f"| R{m['round']} | {m['n']} | {m['mean_total']:.2f} | {cells} |")

    L.append("\n## Contrast with the Step 89 curve (§11.3) — different pipeline, do not merge\n")
    L.append("| transition | legacy (this run) | Step 89 arm A | Step 89 arm B |")
    L.append("|---|---|---|---|")
    for t in res["transitions"]:
        k = t["round"]
        a = f"{STEP89_A[k]:+.2f}" if k in STEP89_A else "—"
        b = f"{STEP89_B[k]:+.2f}" if k in STEP89_B else "—"
        L.append(f"| {t['transition']} | {t['mean_delta']:+.3f} | {a} | {b} |")
    L.append("\n> Legacy = raw-asset input + refinement loop. Step 89 = text-as-image "
             "input, arm A best-of-3 / arm B deep-review. §5.8 cites the legacy curve "
             "because that is the pipeline the section describes.")

    d = res["diagnostics"]
    L.append("\n## Load diagnostics\n")
    L.append(f"- sample dirs scanned: {d['dirs']}")
    L.append(f"- no/unreadable `per_round_judge.json`: {d['no_trace']}")
    L.append(f"- trace present but no scored round: {d['empty']}")
    L.append(f"- rounds with no scorable candidate: {d['rounds_without_total']}")
    L.append(f"- rounds where `best_candidate_id` was absent → argmax fallback: "
             f"{d['fallback_to_argmax']}")
    L.append("\n> n_pairs shrinks with k because samples that accepted at round k−1 "
             "stopped there. This is survivorship, not missing data.")
    return "\n".join(L) + "\n"


def main() -> int:
    if not FULL_RESULT.exists():
        print(f"[error] missing {FULL_RESULT}")
        return 1

    prov = _prov_capture()
    print(_prov_line(prov))

    series, diag = load_series()
    if not series:
        print("[error] no usable per_round_judge.json found")
        return 1

    transitions = []
    k = 1
    while (t := transition_stats(series, k)) is not None:
        transitions.append(t)
        k += 1

    res = {
        "step": 96,
        "label": "E2' legacy per-round paired gain curve",
        "source": "layout_agent/full_result/*/trace/per_round_judge.json",
        "provenance": prov,
        "n_samples": len(series),
        "diagnostics": diag,
        "transitions": transitions,
        "per_round_means": per_round_means(series),
    }

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    (OUT_ROOT / "curve.json").write_text(json.dumps(res, indent=2, ensure_ascii=False))
    (OUT_ROOT / "curve.md").write_text(_render_md(res))

    print(f"[loaded] {len(series)} samples with trace "
          f"({diag['no_trace']} missing, {diag['fallback_to_argmax']} argmax fallbacks)")
    for t in transitions:
        print(f"  {t['transition']}  n={t['n_pairs']:3d}  "
              f"mean={t['mean_delta']:+.3f}  {_fmt_ci(t['bootstrap'])}  "
              f"W/T/L={t['improved']}/{t['tied']}/{t['worsened']}  "
              f"p={_fmt_p(t['sign_p_two_sided'])}")
    print(f"[done] -> {OUT_ROOT / 'curve.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
