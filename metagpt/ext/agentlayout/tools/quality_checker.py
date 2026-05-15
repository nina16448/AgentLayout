"""Quality Checker — programmatic validation of Layout Generator candidates.

The checker enforces three classes of rules described in ``layout_agent/README.md``:

    1. Element Completeness  — candidate element ids must equal DesignSpec ids
    2. Boundary Check        — every element fits inside the canvas
    3. Hard Constraints      — position_preference / no_overlap / z_order /
                               size_preference each evaluated against the
                               candidate's actual geometry

Every check produces structured ``Violation`` records (rather than just a bool)
so the pipeline driver can both reject failing candidates and log the failure
mode for downstream error analysis. The single public entry point is
``check_candidate``; ``filter_valid`` is a thin batch wrapper.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, Iterable, List, Tuple

from pydantic import BaseModel, Field

from metagpt.ext.agentlayout.schema import (
    Candidate,
    DesignSpec,
    HardConstraint,
    HardConstraintRule,
    LayoutElement,
)


# ============================================================
# Lookup tables
# ============================================================


POSITION_HINT_TO_BANDS: Dict[str, Tuple[int, int]] = {
    # hint -> (h_band, v_band) on a 3x3 canvas grid.
    # h_band: 0=left, 1=center, 2=right.   v_band: 0=top, 1=middle, 2=bottom.
    "top_left": (0, 0),
    "top": (1, 0),
    "top_center": (1, 0),
    "top_right": (2, 0),
    "left": (0, 1),
    "middle_left": (0, 1),
    "center": (1, 1),
    "middle_center": (1, 1),
    "right": (2, 1),
    "middle_right": (2, 1),
    "bottom_left": (0, 2),
    "bottom": (1, 2),
    "bottom_center": (1, 2),
    "bottom_right": (2, 2),
    # Aliases for reversed word order (2026-05-14 step 9: live Crello run
    # surfaced Analyst emitting "center_top" / "center_bottom" instead of
    # "top_center" / "bottom_center", causing UNKNOWN_HINT on every candidate.
    # Tolerate both orderings so a single LLM word-order quirk does not crash
    # the whole pipeline.)
    "center_top": (1, 0),
    "center_bottom": (1, 2),
    "left_top": (0, 0),
    "right_top": (2, 0),
    "left_bottom": (0, 2),
    "right_bottom": (2, 2),
    "left_middle": (0, 1),
    "right_middle": (2, 1),
}

SIZE_HINT_LOWER_BOUND: Dict[str, float] = {
    # hint -> minimum element_area / canvas_area ratio.
    # Buckets follow the Layout Generator prompt's size reference, plus
    # 'prominent' which Analyst uses as a synonym in the spec example.
    "full-canvas": 0.95,
    "hero": 0.60,
    "large": 0.30,
    # 'prominent' = "should stand out", typical for poster headlines.
    # 0.10 calibrated against vertical posters where headline is one line of bold text;
    # 0.20 was too strict (forced a 240px-tall banner on a 1200px canvas).
    "prominent": 0.10,
    "medium": 0.08,
    "small": 0.08,
    "caption": 0.03,
}


# ============================================================
# Result types
# ============================================================


class ViolationType(str, Enum):
    """One label per kind of failure, makes downstream analytics easy."""

    MISSING_ELEMENT = "missing_element"
    EXTRA_ELEMENT = "extra_element"
    OUT_OF_BOUNDS = "out_of_bounds"
    POSITION_PREFERENCE = "position_preference"
    NO_OVERLAP = "no_overlap"
    Z_ORDER = "z_order"
    SIZE_PREFERENCE = "size_preference"
    UNKNOWN_HINT = "unknown_hint"
    UNKNOWN_TARGET = "unknown_target"


class Violation(BaseModel):
    """A single rule failure with enough context to debug or to log."""

    type: ViolationType
    targets: List[str] = Field(default_factory=list)
    detail: str


class CheckResult(BaseModel):
    """Aggregated outcome for one candidate."""

    candidate_id: str
    passed: bool
    violations: List[Violation] = Field(default_factory=list)


# ============================================================
# Public API
# ============================================================


def check_candidate(candidate: Candidate, spec: DesignSpec) -> CheckResult:
    """Run all three validation phases and aggregate violations.

    Phases run sequentially but every phase always runs — we want a *full*
    violation list for analytics, not a fail-fast bool. ``passed`` is True
    iff no violation was recorded.
    """
    violations: List[Violation] = []
    violations.extend(_check_completeness(candidate, spec))
    violations.extend(_check_boundary(candidate, spec))
    violations.extend(_check_hard_constraints(candidate, spec))
    return CheckResult(
        candidate_id=candidate.candidate_id,
        passed=not violations,
        violations=violations,
    )


def filter_valid(
    candidates: Iterable[Candidate],
    spec: DesignSpec,
) -> Tuple[List[Candidate], List[CheckResult]]:
    """Batch wrapper. Returns (kept_candidates, all_reports)."""
    kept: List[Candidate] = []
    reports: List[CheckResult] = []
    for c in candidates:
        report = check_candidate(c, spec)
        reports.append(report)
        if report.passed:
            kept.append(c)
    return kept, reports


# ============================================================
# Phase 1: Element Completeness
# ============================================================


def _check_completeness(candidate: Candidate, spec: DesignSpec) -> List[Violation]:
    spec_ids = {e.id for e in spec.elements}
    cand_ids = {e.id for e in candidate.elements}
    out: List[Violation] = []
    for missing in sorted(spec_ids - cand_ids):
        out.append(
            Violation(
                type=ViolationType.MISSING_ELEMENT,
                targets=[missing],
                detail=f"Element '{missing}' is in DesignSpec but missing from candidate.",
            )
        )
    for extra in sorted(cand_ids - spec_ids):
        out.append(
            Violation(
                type=ViolationType.EXTRA_ELEMENT,
                targets=[extra],
                detail=f"Element '{extra}' is in candidate but not in DesignSpec.",
            )
        )
    return out


# ============================================================
# Phase 2: Boundary Check
# ============================================================


def _check_boundary(candidate: Candidate, spec: DesignSpec) -> List[Violation]:
    canvas = spec.canvas
    out: List[Violation] = []
    for el in candidate.elements:
        problems: List[str] = []
        if el.left < 0:
            problems.append(f"left={el.left} < 0")
        if el.top < 0:
            problems.append(f"top={el.top} < 0")
        if el.left + el.width > canvas.width:
            problems.append(
                f"left+width={el.left + el.width} > canvas.width={canvas.width}"
            )
        if el.top + el.height > canvas.height:
            problems.append(
                f"top+height={el.top + el.height} > canvas.height={canvas.height}"
            )
        if problems:
            out.append(
                Violation(
                    type=ViolationType.OUT_OF_BOUNDS,
                    targets=[el.id],
                    detail="; ".join(problems),
                )
            )
    return out


# ============================================================
# Phase 3: Hard Constraints
# ============================================================


def _check_hard_constraints(candidate: Candidate, spec: DesignSpec) -> List[Violation]:
    elements_by_id = {e.id: e for e in candidate.elements}
    out: List[Violation] = []
    for constraint in spec.hard_constraints:
        if constraint.rule == HardConstraintRule.POSITION_PREFERENCE:
            out.extend(_check_position_preference(constraint, elements_by_id, spec))
        elif constraint.rule == HardConstraintRule.NO_OVERLAP:
            out.extend(_check_no_overlap(constraint, elements_by_id))
        elif constraint.rule == HardConstraintRule.Z_ORDER:
            out.extend(_check_z_order(constraint, elements_by_id))
        elif constraint.rule == HardConstraintRule.SIZE_PREFERENCE:
            out.extend(_check_size_preference(constraint, elements_by_id, spec))
    return out


def _check_position_preference(
    constraint: HardConstraint,
    elements_by_id: Dict[str, LayoutElement],
    spec: DesignSpec,
) -> List[Violation]:
    hint_raw = constraint.params.get("hint", "")
    hint = str(hint_raw).strip().lower()
    if hint not in POSITION_HINT_TO_BANDS:
        return [
            Violation(
                type=ViolationType.UNKNOWN_HINT,
                targets=list(constraint.targets),
                detail=f"position_preference hint '{hint_raw}' is not a known region.",
            )
        ]
    expected_h, expected_v = POSITION_HINT_TO_BANDS[hint]
    canvas = spec.canvas
    out: List[Violation] = []
    for tid in constraint.targets:
        el = elements_by_id.get(tid)
        if el is None:
            out.append(
                Violation(
                    type=ViolationType.UNKNOWN_TARGET,
                    targets=[tid],
                    detail=f"position_preference target '{tid}' not found in candidate.",
                )
            )
            continue
        cx = el.left + el.width / 2
        cy = el.top + el.height / 2
        h_ok = _in_band_with_tolerance(cx, expected_h, canvas.width)
        v_ok = _in_band_with_tolerance(cy, expected_v, canvas.height)
        if not (h_ok and v_ok):
            h_band = _band_index(cx, canvas.width)
            v_band = _band_index(cy, canvas.height)
            h_lo, h_hi = _band_bounds_with_tolerance(expected_h, canvas.width)
            v_lo, v_hi = _band_bounds_with_tolerance(expected_v, canvas.height)
            out.append(
                Violation(
                    type=ViolationType.POSITION_PREFERENCE,
                    targets=[tid],
                    detail=(
                        f"Element '{tid}' center=({cx:.0f}, {cy:.0f}) is in band "
                        f"({h_band}, {v_band}); '{hint}' requires ({expected_h}, "
                        f"{expected_v}) -- accepted x in [{h_lo:.0f}, {h_hi:.0f}], "
                        f"y in [{v_lo:.0f}, {v_hi:.0f}] (tolerance "
                        f"{POSITION_BAND_TOLERANCE:.0%})."
                    ),
                )
            )
    return out


def _band_index(coord: float, total: int) -> int:
    """Return 0/1/2 third-band index of ``coord`` along a [0, total] axis."""
    third = total / 3
    if coord < third:
        return 0
    if coord < 2 * third:
        return 1
    return 2


POSITION_BAND_TOLERANCE: float = 0.10
"""Per-edge tolerance applied when checking ``position_preference``.

Calibrated 2026-05-16 step 10c: live-#8 (1200x600) re-run with the step 10
no_overlap fix in place still hard-failed 5/5 candidates because the strict
[H/3, 2H/3] center band is only 200 px tall on a 600 px canvas, leaving the
LLM no room for ``text_1`` once 3 images sat in the upper third. We widen
each band edge by ``POSITION_BAND_TOLERANCE * canvas_dim`` (with a 16 px
absolute floor) so a centre-of-mass that misses by less than ~10% of the
canvas dimension still counts as "in band". A 60-px slack on the 600-px
canvas turns the live-#8 LLM placement (cy=450, 50 px past 400) from FAIL
into PASS without making the rule meaningless on tall canvases.
"""

POSITION_BAND_TOLERANCE_ABSOLUTE_FLOOR: int = 16
"""Minimum tolerance in pixels regardless of canvas size, so that on tiny
fixtures (e.g. 100x100 unit-test canvases) the rule still has any slack."""


def _band_bounds_with_tolerance(band: int, total: int) -> Tuple[float, float]:
    """Return inclusive [low, high] coordinate range that counts as ``band``.

    Edges are widened by ``max(POSITION_BAND_TOLERANCE * total, FLOOR)``. The
    range is clamped to [0, total] so callers do not need to clip.
    """
    third = total / 3.0
    tol = max(POSITION_BAND_TOLERANCE * total, POSITION_BAND_TOLERANCE_ABSOLUTE_FLOOR)
    if band == 0:
        return 0.0, min(total, third + tol)
    if band == 1:
        return max(0.0, third - tol), min(total, 2 * third + tol)
    return max(0.0, 2 * third - tol), float(total)


def _in_band_with_tolerance(coord: float, band: int, total: int) -> bool:
    low, high = _band_bounds_with_tolerance(band, total)
    return low <= coord <= high


NO_OVERLAP_TOLERANCE: float = 0.05
"""Maximum allowed bbox-overlap area as a fraction of the smaller element's area.

Calibrated 2026-05-15 step 10: live runs on tight Crello canvases (1200x600,
537x240) revealed the Generator regularly emits adjacent boxes that miss by
1-20 pixels due to LLM rounding, which under a strict zero-tolerance check
caused 15/15 candidates to fail QC on every retry round. 5% lets the LLM round
edges without breaking, while genuine overlaps (>=10% area share) still fail.
"""


def _check_no_overlap(
    constraint: HardConstraint,
    elements_by_id: Dict[str, LayoutElement],
) -> List[Violation]:
    elems: List[LayoutElement] = []
    out: List[Violation] = []
    for tid in constraint.targets:
        el = elements_by_id.get(tid)
        if el is None:
            out.append(
                Violation(
                    type=ViolationType.UNKNOWN_TARGET,
                    targets=[tid],
                    detail=f"no_overlap target '{tid}' not found in candidate.",
                )
            )
        else:
            elems.append(el)
    for i in range(len(elems)):
        for j in range(i + 1, len(elems)):
            a, b = elems[i], elems[j]
            ratio = _aabb_overlap_ratio(a, b)
            if ratio > NO_OVERLAP_TOLERANCE:
                out.append(
                    Violation(
                        type=ViolationType.NO_OVERLAP,
                        targets=[a.id, b.id],
                        detail=(
                            f"'{a.id}' and '{b.id}' bounding boxes overlap by "
                            f"{ratio:.1%} of the smaller box (tolerance: "
                            f"{NO_OVERLAP_TOLERANCE:.0%})."
                        ),
                    )
                )
    return out


def _aabb_overlap_ratio(a: LayoutElement, b: LayoutElement) -> float:
    """Overlap area / min(area_a, area_b). 0.0 = disjoint, 1.0 = full containment.

    Ignores rotation (angle != 0). Returns 0.0 if either box is degenerate.
    """
    ix = max(0, min(a.left + a.width, b.left + b.width) - max(a.left, b.left))
    iy = max(0, min(a.top + a.height, b.top + b.height) - max(a.top, b.top))
    overlap = ix * iy
    if overlap == 0:
        return 0.0
    area_a = a.width * a.height
    area_b = b.width * b.height
    smaller = min(area_a, area_b)
    if smaller <= 0:
        return 0.0
    return overlap / smaller


def _aabb_overlap(a: LayoutElement, b: LayoutElement) -> bool:
    """Boolean wrapper for backward-compat callers (none in-tree, kept for
    external scripts that may import the helper)."""
    return _aabb_overlap_ratio(a, b) > 0.0


def _check_z_order(
    constraint: HardConstraint,
    elements_by_id: Dict[str, LayoutElement],
) -> List[Violation]:
    above_id = constraint.params.get("above")
    if not above_id:
        return [
            Violation(
                type=ViolationType.UNKNOWN_HINT,
                targets=list(constraint.targets),
                detail="z_order constraint missing 'above' param.",
            )
        ]
    ref = elements_by_id.get(above_id)
    if ref is None:
        return [
            Violation(
                type=ViolationType.UNKNOWN_TARGET,
                targets=[above_id],
                detail=f"z_order reference '{above_id}' not found in candidate.",
            )
        ]
    out: List[Violation] = []
    for tid in constraint.targets:
        el = elements_by_id.get(tid)
        if el is None:
            out.append(
                Violation(
                    type=ViolationType.UNKNOWN_TARGET,
                    targets=[tid],
                    detail=f"z_order target '{tid}' not found in candidate.",
                )
            )
            continue
        if el.z_index <= ref.z_index:
            out.append(
                Violation(
                    type=ViolationType.Z_ORDER,
                    targets=[tid, above_id],
                    detail=(
                        f"Element '{tid}' z_index={el.z_index} is not strictly above "
                        f"'{above_id}' z_index={ref.z_index}."
                    ),
                )
            )
    return out


def _check_size_preference(
    constraint: HardConstraint,
    elements_by_id: Dict[str, LayoutElement],
    spec: DesignSpec,
) -> List[Violation]:
    hint_raw = constraint.params.get("hint", "")
    hint = str(hint_raw).strip().lower()
    if hint not in SIZE_HINT_LOWER_BOUND:
        return [
            Violation(
                type=ViolationType.UNKNOWN_HINT,
                targets=list(constraint.targets),
                detail=f"size_preference hint '{hint_raw}' is not a known size bucket.",
            )
        ]
    lower = SIZE_HINT_LOWER_BOUND[hint]
    canvas_area = spec.canvas.width * spec.canvas.height
    out: List[Violation] = []
    for tid in constraint.targets:
        el = elements_by_id.get(tid)
        if el is None:
            out.append(
                Violation(
                    type=ViolationType.UNKNOWN_TARGET,
                    targets=[tid],
                    detail=f"size_preference target '{tid}' not found in candidate.",
                )
            )
            continue
        ratio = (el.width * el.height) / canvas_area
        if ratio < lower:
            out.append(
                Violation(
                    type=ViolationType.SIZE_PREFERENCE,
                    targets=[tid],
                    detail=(
                        f"Element '{tid}' area_ratio={ratio:.3f} is below '{hint}' "
                        f"lower bound {lower:.2f}."
                    ),
                )
            )
    return out
