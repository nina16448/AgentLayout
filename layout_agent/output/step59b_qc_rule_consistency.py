"""Step 59b -- consistency check: QC rule vs calibration script.

Replays the same candidates as step59_text_gradient_calibration.py (all
generator batches from the oracle live logs) through the REAL production
rule (quality_checker.check_candidate -> TEXT_ON_BUSY_TEXTURE) and compares
hit counts against the calibration sweep (T=0.0654 caught 74/590 replayed
candidates). The production threshold is 0.065 (slightly below 0.0654), so
the rule may catch a few extra candidates whose worst element falls in
(0.065, 0.0654]; anything beyond that delta means the rule and the
calibration disagree on geometry/classification and must be debugged.

Usage (layout_agent/output, conda env meta):
    python step59b_qc_rule_consistency.py
"""
from __future__ import annotations

import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT))

from step59_text_gradient_calibration import parse_log_samples  # noqa: E402

from metagpt.ext.agentlayout.schema import Candidate, DesignSpec  # noqa: E402
from metagpt.ext.agentlayout.tools.quality_checker import (  # noqa: E402
    TEXT_GRADIENT_MAX,
    ViolationType,
    check_candidate,
)


def main():
    logs = [
        OUT / "step56_live_N20.log",
        OUT / "step58_live_N20.log",
        OUT / "step58b_live_N20.log",
    ]
    n_total = 0
    n_hit = 0
    hits = []
    for lp in logs:
        for sid, spec_dict, cands in parse_log_samples(lp):
            spec = DesignSpec.model_validate(spec_dict)
            for cand_dict in cands:
                try:
                    cand = Candidate.model_validate(cand_dict)
                except Exception:
                    continue
                n_total += 1
                viols = [
                    v
                    for v in check_candidate(cand, spec).violations
                    if v.type == ViolationType.TEXT_ON_BUSY_TEXTURE
                ]
                if viols:
                    n_hit += 1
                    hits.append((lp.stem, sid[:8], cand.candidate_id, viols[0].detail))

    print(f"threshold={TEXT_GRADIENT_MAX}")
    print(f"replayed candidates: {n_total}")
    print(f"TEXT_ON_BUSY_TEXTURE hits: {n_hit} ({100.0 * n_hit / n_total:.0f}%)")
    print("calibration reference: 74/590 candidates (T=0.0654), 95/590 (T=0.0454)")
    print("\nfirst 10 hits:")
    for log, sid, cid, detail in hits[:10]:
        print(f"  {log}:{sid}:{cid}  {detail[:90]}")


if __name__ == "__main__":
    main()
