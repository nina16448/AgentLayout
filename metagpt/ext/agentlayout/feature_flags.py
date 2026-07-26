"""Feature flags for AgentLayout experimental knobs.

Centralized env-var gating so individual modules don't litter
``os.environ.get`` calls. Each flag here documents its rationale + default
+ Step that introduced or last validated it, so future reviewers can
re-enable for ablation studies.

Conventions:
- All flag values default to OFF when the env var is unset.
- Accepted "ON" tokens (case-insensitive): "1", "true", "yes", "on".
- All other values (including unset / empty / "0" / "false") = OFF.
"""
from __future__ import annotations

import os

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def visual_loop_enabled() -> bool:
    """Step 77 (2026-07-02): Judge visual-observation feedback loop.

    When ON, the Aesthetic Judge prompt asks for ``visual_observations`` --
    discrete render-level defects from a closed catalogue (text_off_panel /
    text_illegible / text_too_small / text_too_large / text_overlap /
    text_tilted), each carrying a machine-verifiable target. The pipeline
    computes a per-round COMPLIANCE RATE (tools/feedback_verifier.py) so a
    failure localises to perception vs execution.

    Default OFF: judge->generator feedback measured net-negative three times
    in the old regime (Steps 20b / 59 / 65). Step 77 re-tests it in the
    SEGA-mode text-only regime where the Step 59 execution blockers
    (constraint-saturated generator, messy asset semantics) are gone.

    Enable for the ablation:
        export AGENTLAYOUT_VISUAL_LOOP=1
    """
    return _flag("AGENTLAYOUT_VISUAL_LOOP")


def f2_saliency_enabled() -> bool:
    """F2 (Step 72, 2026-06-16): saliency-aware text placement.

    When ON, the Generator prompt includes a saliency landscape block
    (3x3 grid + top-K low-saliency rectangles + Rule 6b policy) and the
    QC pipeline runs ``_check_text_on_high_saliency`` (tau=0.5).

    Default OFF after Step 72 N=100 validation showed net negative
    aesthetic impact on matched COLE H2H (Smean4 6.598 -> 6.495, gap
    widened 10%) despite the calibration GO decision (designer text p95
    saliency = 0.38, false-positive rate at tau=0.5 only 1.3%) and N=3
    smoke showing 2/3 saliency-overlap improvement.

    Implementation + schema + analyzer paths stay live (saliency_map is
    still populated on BackgroundAnalysis so downstream analyses can
    inspect it); only the *consumers* are gated so production behavior
    reverts to the Step 70 baseline.

    Re-enable for ablation reproducibility:
        export AGENTLAYOUT_F2_SALIENCY=1
    """
    return _flag("AGENTLAYOUT_F2_SALIENCY")
