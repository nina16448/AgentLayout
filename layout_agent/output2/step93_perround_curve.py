"""Step 93 -- formalize the per-round gain curve from the Step 89 N=100 run (E2).

Context
-------
Paper section 5.8 currently cites +0.45 / -0.19 / -0.03 / -0.12 from the
result.md section 11.4 prose. Those numbers came from the Step 89 run but were
never dumped as a standalone, per-transition paired table with sample counts
and significance. After Step 92 moved the B-axis main table onto the same
Step 89 batch, this curve is now SAME-protocol with the headline numbers, so
the only remaining gap is provenance: a formal table with n / mean / median /
W-T-L / exact sign p per round transition, written to disk.

Zero new generation: reads only `step89_n100/<id>/{a,b}/rounds/round*.json`
(the in-pipeline JudgeAesthetic verdicts persisted by the Step 89 driver).
The `total` field is the 5-axis COLE total (5..50) emitted by the pipeline's
own judge during the run -- NOT the Step 92 post-hoc single-call scores.

Outputs
-------
    output2/step93_perround/perround.json   full per-sample series + stats
    output2/step93_perround/perround.md     paper-ready tables

Cross-check: the script prints the published section 11.4 values next to the
recomputed ones; any mismatch means the definition drifted and must be
resolved before the paper cites this table.
"""
from __future__ import annotations

import json
import math
import re
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "step89_n100"
OUT_DIR = Path(__file__).resolve().parent / "step93_perround"

# Published in result.md section 11.4 (2026-07-03). Keyed by (arm, round_k):
# paired mean of total(k) - total(k-1).
PUBLISHED = {
    ("a", 1): 0.45,
    ("a", 2): 0.14,
    ("b", 1): 0.45,
    ("b", 2): -0.19,
    ("b", 3): -0.03,
    ("b", 4): -0.12,
}

_ROUND_RE = re.compile(r"round(\d+)\.json$")


def exact_sign_test_two_sided(wins: int, losses: int) -> float | None:
    """Exact two-sided binomial sign test on non-tied pairs, p0=0.5.

    Same convention as step92_cole_h2h.py: ties excluded, symmetric null so
    two-sided p = 2 * P(X >= max(wins, losses)), capped at 1.0.
    """
    n = wins + losses
    if n == 0:
        return None
    k = max(wins, losses)
    tail = sum(math.comb(n, i) for i in range(k, n + 1)) / 2.0**n
    return min(1.0, 2.0 * tail)


def load_series() -> dict[str, dict[str, dict[int, dict]]]:
    """-> {sample_id: {arm: {round_idx: verdict_dict}}} (arms with >=1 round)."""
    series: dict[str, dict[str, dict[int, dict]]] = {}
    for sample_dir in sorted(p for p in ROOT.iterdir() if p.is_dir()):
        arms: dict[str, dict[int, dict]] = {}
        for arm in ("a", "b"):
            rounds_dir = sample_dir / arm / "rounds"
            if not rounds_dir.is_dir():
                continue
            rounds: dict[int, dict] = {}
            for f in rounds_dir.iterdir():
                m = _ROUND_RE.search(f.name)
                if not m:
                    continue
                data = json.loads(f.read_text())
                idx = int(m.group(1))
                if data.get("total") is not None:
                    rounds[idx] = data
            if rounds:
                arms[arm] = rounds
        if arms:
            series[sample_dir.name] = arms
    return series


def transition_stats(series, arm: str, k: int) -> dict | None:
    """Paired stats for total(k) - total(k-1) over samples having both rounds."""
    deltas = []
    for sid, arms in series.items():
        rounds = arms.get(arm, {})
        if k in rounds and (k - 1) in rounds:
            deltas.append((sid, rounds[k]["total"] - rounds[k - 1]["total"]))
    if not deltas:
        return None
    vals = [d for _, d in deltas]
    wins = sum(1 for v in vals if v > 0)
    losses = sum(1 for v in vals if v < 0)
    ties = sum(1 for v in vals if v == 0)
    return {
        "arm": arm,
        "round": k,
        "n_pairs": len(vals),
        "mean_delta": statistics.fmean(vals),
        "median_delta": statistics.median(vals),
        "improved": wins,
        "tied": ties,
        "worsened": losses,
        "sign_p_two_sided": exact_sign_test_two_sided(wins, losses),
        "published_11_4": PUBLISHED.get((arm, k)),
        "per_sample": {sid: d for sid, d in deltas},
    }


def round0_cross_arm(series) -> dict:
    """Paired A.R0.total - B.R0.total (the best-of-3 vs single-candidate gap)."""
    deltas = []
    for sid, arms in series.items():
        a0 = arms.get("a", {}).get(0)
        b0 = arms.get("b", {}).get(0)
        if a0 and b0:
            deltas.append((sid, a0["total"] - b0["total"]))
    vals = [d for _, d in deltas]
    wins = sum(1 for v in vals if v > 0)
    losses = sum(1 for v in vals if v < 0)
    return {
        "n_pairs": len(vals),
        "mean_delta_a_minus_b": statistics.fmean(vals),
        "median_delta": statistics.median(vals),
        "a_better": wins,
        "tied": sum(1 for v in vals if v == 0),
        "b_better": losses,
        "sign_p_two_sided": exact_sign_test_two_sided(wins, losses),
        "per_sample": {sid: d for sid, d in deltas},
    }


def per_round_means(series, arm: str) -> list[dict]:
    """Descriptive per-round means (total + 5 axes). Finding-2 discipline:
    the paper reports the total-level curve; axis columns are descriptive."""
    out = []
    max_k = max((max(r) for _, a in series.items() if (r := a.get(arm))), default=-1)
    for k in range(0, max_k + 1):
        rows = [a[arm][k] for a in series.values() if arm in a and k in a[arm]]
        if not rows:
            continue
        entry = {
            "round": k,
            "n": len(rows),
            "mean_total": statistics.fmean(r["total"] for r in rows),
        }
        for axis in ("design_layout", "content_relevance", "typography_color",
                     "graphics_images", "innovation_originality"):
            entry[f"mean_{axis}"] = statistics.fmean(r["scores"][axis] for r in rows)
        out.append(entry)
    return out


def main() -> None:
    series = load_series()
    n_samples = len(series)

    transitions = []
    for arm in ("a", "b"):
        k = 1
        while (t := transition_stats(series, arm, k)) is not None:
            transitions.append(t)
            k += 1

    r0 = round0_cross_arm(series)
    means = {arm: per_round_means(series, arm) for arm in ("a", "b")}

    OUT_DIR.mkdir(exist_ok=True)
    payload = {
        "step": 93,
        "source": "output2/step89_n100/<id>/{a,b}/rounds/round*.json",
        "protocol_note": (
            "In-pipeline JudgeAesthetic 5-axis totals (5..50) persisted during "
            "the Step 89 text-as-image N=100 run; same batch as the Step 92 "
            "B-axis main table. Judge = pipeline LLM (gpt-4o), multi-candidate "
            "prompt -- distinct from Step 92's post-hoc single-call protocol."
        ),
        "n_samples_with_rounds": n_samples,
        "transitions": transitions,
        "round0_cross_arm_a_minus_b": r0,
        "per_round_descriptive_means": means,
    }
    (OUT_DIR / "perround.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False)
    )

    lines = [
        "# Step 93 -- Per-round paired gain curve (Step 89 N=100 batch)",
        "",
        f"Samples with round traces: {n_samples}. "
        "Delta(k) = total(round k) - total(round k-1), paired per sample; "
        "totals are the in-pipeline 5-axis judge totals (5-50) from the "
        "Step 89 run itself (same batch + protocol as the Step 92 main table).",
        "",
        "| arm | round k | n pairs | mean Δ | median Δ | improved/tied/worsened |"
        " sign p (two-sided) | §11.4 published |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for t in transitions:
        p = t["sign_p_two_sided"]
        lines.append(
            f"| {t['arm'].upper()} | R{t['round'] - 1}→R{t['round']} | {t['n_pairs']} "
            f"| {t['mean_delta']:+.3f} | {t['median_delta']:+.1f} "
            f"| {t['improved']}/{t['tied']}/{t['worsened']} "
            f"| {('%.4f' % p) if p is not None else '—'} "
            f"| {t['published_11_4']:+.2f} |"
        )
    lines += [
        "",
        "## Round-0 cross-arm gap (A best-of-3 concepts vs B single candidate)",
        "",
        f"n={r0['n_pairs']} paired; mean Δ(A−B) = {r0['mean_delta_a_minus_b']:+.3f}, "
        f"median {r0['median_delta']:+.1f}; A better {r0['a_better']} / tie {r0['tied']} / "
        f"B better {r0['b_better']}; sign p = {r0['sign_p_two_sided']:.2e}",
        "",
        "## Descriptive per-round means (totals; axis means in perround.json)",
        "",
        "| arm | round | n | mean total |",
        "|---|---|---|---|",
    ]
    for arm in ("a", "b"):
        for e in means[arm]:
            lines.append(f"| {arm.upper()} | R{e['round']} | {e['n']} | {e['mean_total']:.2f} |")
    lines += [
        "",
        "Provenance: recomputed 2026-07-09 by step93_perround_curve.py; the "
        "'§11.4 published' column must match the recomputed mean Δ -- any "
        "mismatch blocks citing this table.",
    ]
    (OUT_DIR / "perround.md").write_text("\n".join(lines) + "\n")

    print(f"samples={n_samples}")
    for t in transitions:
        pub = t["published_11_4"]
        flag = ""
        if pub is not None and abs(t["mean_delta"] - pub) > 0.005:
            flag = "  <-- MISMATCH vs §11.4"
        print(
            f"{t['arm'].upper()} R{t['round']-1}->R{t['round']}: n={t['n_pairs']} "
            f"mean={t['mean_delta']:+.3f} (published {pub}){flag}"
        )
    print(
        f"R0 A-B: n={r0['n_pairs']} mean={r0['mean_delta_a_minus_b']:+.3f} "
        f"(§11.4 prose: best-of-3 R0 +1.6)"
    )
    print(f"wrote {OUT_DIR}/perround.json and perround.md")


if __name__ == "__main__":
    main()
