"""Step 94 -- formalize the Step 71 Ali 65/100 result as an exact sign test (E3).

Input: `output/b1_root_cause_n100.json` (Step 71 per-sample root-cause dump,
per_axis_summary counts: alignment 65W/3L/32T, readability 21W/41L/38T,
occlusion 45W/55L/0T; win = agent better on that geometric axis vs designer GT).

Two p-value conventions, both exact binomial vs p0=0.5, two-sided:
  * decisive-only  -- ties excluded (standard sign test): Ali 65 wins / 68.
  * conservative   -- ties kept in n (treated as if they could all have gone
    against the winner): Ali 65 wins / 100. This is the number the paper
    cites, per the user's instruction; decisive-only goes in a footnote.

Purpose: one-line defense answer to "is N=100 enough?" -- even under the
conservative convention the Ali win imbalance is far beyond chance.

Outputs: output2/step94_signtest/{signtest.json, signtest.md}
Pure stdlib (math.comb), same convention as step92/step93.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "output" / "b1_root_cause_n100.json"
OUT_DIR = Path(__file__).resolve().parent / "step94_signtest"


def binom_two_sided(k_max_side: int, n: int) -> float:
    """Exact two-sided binomial p vs p0=0.5: 2 * P(X >= max-side count)."""
    tail = sum(math.comb(n, i) for i in range(k_max_side, n + 1)) / 2.0**n
    return min(1.0, 2.0 * tail)


def main() -> None:
    summary = json.loads(SRC.read_text())["per_axis_summary"]
    results = []
    for axis, row in summary.items():
        w, l, t = row["n_agent_win"], row["n_agent_lose"], row["n_tie"]
        n_total = row["n_total"]
        assert w + l + t == n_total, f"{axis}: counts do not sum to n_total"
        decisive = w + l
        results.append({
            "axis": axis,
            "wins": w,
            "losses": l,
            "ties": t,
            "n_decisive": decisive,
            "p_decisive_two_sided": binom_two_sided(max(w, l), decisive),
            "p_conservative_ties_in_n": binom_two_sided(max(w, l), n_total),
            "direction": "agent" if w > l else ("gt" if l > w else "even"),
        })

    OUT_DIR.mkdir(exist_ok=True)
    payload = {
        "step": 94,
        "source": "output/b1_root_cause_n100.json (Step 71, N=100 fresh, geometric axes)",
        "method": (
            "Exact binomial sign test vs p0=0.5, two-sided = 2*P(X >= max-side "
            "count) capped at 1. 'decisive' excludes ties (standard); "
            "'conservative' keeps ties in n. Paper cites the conservative "
            "value; decisive-only in a footnote."
        ),
        "results": results,
    }
    (OUT_DIR / "signtest.json").write_text(json.dumps(payload, indent=2))

    lines = [
        "# Step 94 -- Exact sign tests for Step 71 per-sample geometric axes (N=100)",
        "",
        "Win = agent strictly better than designer GT on that axis (Step 71 per-sample table).",
        "",
        "| axis | W/L/T | direction | p (decisive-only, two-sided) | p (conservative, ties in n=100) |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['axis']} | {r['wins']}/{r['losses']}/{r['ties']} | {r['direction']} "
            f"| {r['p_decisive_two_sided']:.3e} | {r['p_conservative_ties_in_n']:.3e} |"
        )
    ali = next(r for r in results if r["axis"] == "alignment")
    lines += [
        "",
        "## One-line defense answer (\"is N=100 enough?\")",
        "",
        f"Alignment: 65 wins vs 3 losses (32 ties). Even under the conservative "
        f"convention that counts all 32 ties against the result "
        f"(p = {ali['p_conservative_ties_in_n']:.1e}), the imbalance is far beyond "
        f"chance; excluding ties, the exact two-sided p is "
        f"{ali['p_decisive_two_sided']:.1e}. N=100 is ample for this claim.",
        "",
        "Caveats carried over from Step 71: the Ali aggregate mean is pulled by 3 "
        "banner outliers -- the sign test speaks to the per-sample win *count*, "
        "not to the mean margin; readability remains a genuine systematic loss "
        "(21/41/38) and occlusion a mild loss (45/55/0), both shown above for "
        "completeness.",
    ]
    (OUT_DIR / "signtest.md").write_text("\n".join(lines) + "\n")

    for r in results:
        print(
            f"{r['axis']}: {r['wins']}W/{r['losses']}L/{r['ties']}T -> "
            f"decisive p={r['p_decisive_two_sided']:.3e}, "
            f"conservative p={r['p_conservative_ties_in_n']:.3e}"
        )
    print(f"wrote {OUT_DIR}/signtest.json and signtest.md")


if __name__ == "__main__":
    main()
