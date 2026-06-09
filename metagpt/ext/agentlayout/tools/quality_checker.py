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
from typing import Dict, Iterable, List, Optional, Tuple

from pydantic import BaseModel, Field

from metagpt.ext.agentlayout.schema import (
    Candidate,
    DesignSpec,
    HardConstraint,
    HardConstraintRule,
    LayoutElement,
    SemanticType,
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
    # Step 35 (2026-06-09): visual-quality rules targeting the "super ugly"
    # failure modes observed in Step 34 N=20 (text broken under decoration,
    # white-on-white invisible body copy).
    TEXT_OBSCURED_BY_OVERLAY = "text_obscured_by_overlay"
    LOW_TEXT_CONTRAST = "low_text_contrast"
    # Step 36 (2026-06-09): additional visual-quality rules driven by Step 34
    # N=20 failure audit. Underlay/decoration scale was 7/17 failures (41%);
    # title under-size + title-in-corner together were ~3/17 (18%).
    DECORATIVE_IMAGE_OVERSIZED = "decorative_image_oversized"
    TITLE_UNDERSIZED = "title_undersized"
    TITLE_PERIPHERAL = "title_peripheral"


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
    # Step 35 (2026-06-09): visual-quality checks driven by Step 34 N=20
    # failure-mode review.
    violations.extend(_check_text_obscured_by_overlay(candidate, spec))
    violations.extend(_check_text_contrast(candidate, spec))
    # Step 36 (2026-06-09): additional rules from Step 34 N=20 visual audit.
    violations.extend(_check_decorative_image_oversized(candidate, spec))
    violations.extend(_check_title_undersized(candidate, spec))
    violations.extend(_check_title_peripheral(candidate, spec))
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


def rank_candidates_by_violations(
    candidates: Iterable[Candidate],
    reports: Iterable[CheckResult],
) -> List[Candidate]:
    """Order candidates fewest-violations-first (stable on ties).

    Used for graceful degradation. When *no* candidate passes QC -- e.g. an
    out-of-vocabulary ``position_preference`` hint that fails every candidate
    identically (the step 10b post-RetryAnalyst crash), or a genuinely
    over-constrained spec -- the pipeline must still hand the Aesthetic Judge
    the least-broken layouts instead of hard-crashing the whole run. Crashing
    silently shrinks the evaluable sample size; degrading keeps the reject
    loop alive so feedback can still route the spec back to the Analyst.

    Candidates with no matching report keep violation count 0 (they were never
    checked) and sort first; ``sorted`` is stable so insertion order breaks
    ties deterministically.
    """
    by_id = {r.candidate_id: len(r.violations) for r in reports}
    return sorted(candidates, key=lambda c: by_id.get(c.candidate_id, 0))


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
            out.extend(_check_z_order(constraint, elements_by_id, spec))
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


# ============================================================
# Step 35 visual-quality rules (2026-06-09)
# ============================================================

TEXT_SEMANTIC_TYPES = frozenset(
    {
        SemanticType.TITLE,
        SemanticType.SUBTITLE,
        SemanticType.BODY_TEXT,
        SemanticType.CAPTION,
    }
)

TEXT_OBSCURED_RATIO_THRESHOLD: float = 0.30
"""Step 35: text element is flagged TEXT_OBSCURED_BY_OVERLAY when an above-z
non-text element covers >= 30% of its area. Tuned conservatively so that
intentional small decorations (corner badges, accent shapes) do not trigger
the rule. Tighten to 0.20 if false-negatives dominate in eval."""

MIN_TEXT_CONTRAST_RATIO: float = 4.5
"""Step 35: WCAG 2.1 AA threshold for normal text. Hex color luminance is
computed with the sRGB linearization formula; the ratio is (L_light + 0.05)
/ (L_dark + 0.05). v1 compares text color against the canvas background
only; a future v2 should resolve the *effective* background by walking
under-z elements that overlap the text bbox."""


def _hex_to_rgb01(hex_color: str) -> Optional[Tuple[float, float, float]]:
    """Parse '#RRGGBB' / '#RGB' / '#RRGGBBAA' into floats in [0,1]. None on bad input."""
    h = hex_color.lstrip("#") if hex_color else ""
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    elif len(h) == 8:
        h = h[:6]
    if len(h) != 6 or not all(c in "0123456789abcdefABCDEF" for c in h):
        return None
    try:
        return int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0
    except ValueError:
        return None


def _relative_luminance(hex_color: str) -> Optional[float]:
    """WCAG 2.1 relative luminance for an sRGB hex color. None on bad input."""
    rgb = _hex_to_rgb01(hex_color)
    if rgb is None:
        return None

    def _to_linear(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (_to_linear(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(fg_hex: str, bg_hex: str) -> Optional[float]:
    l1 = _relative_luminance(fg_hex)
    l2 = _relative_luminance(bg_hex)
    if l1 is None or l2 is None:
        return None
    lighter, darker = (l1, l2) if l1 >= l2 else (l2, l1)
    return (lighter + 0.05) / (darker + 0.05)


def _spec_semantic_map(spec: DesignSpec) -> Dict[str, SemanticType]:
    return {e.id: e.semantic_type for e in spec.elements}


def _check_text_obscured_by_overlay(
    candidate: Candidate, spec: DesignSpec
) -> List[Violation]:
    """Flag text elements that an above-z non-text element covers >= 30% of.

    Catches the Step 34 N=20 failure mode where a decorative_image with high
    z_index lands directly on top of a title and breaks it visually (e.g.
    sample 589d7bd9 'FIND' rendered as 'F.YD' because the mountain shape sat
    over the middle letters).
    """
    types = _spec_semantic_map(spec)
    out: List[Violation] = []
    for text_el in candidate.elements:
        if types.get(text_el.id) not in TEXT_SEMANTIC_TYPES:
            continue
        for other in candidate.elements:
            if other.id == text_el.id:
                continue
            if types.get(other.id) in TEXT_SEMANTIC_TYPES:
                continue
            if other.z_index <= text_el.z_index:
                continue
            ratio = _aabb_overlap_ratio(text_el, other)
            if ratio >= TEXT_OBSCURED_RATIO_THRESHOLD:
                out.append(
                    Violation(
                        type=ViolationType.TEXT_OBSCURED_BY_OVERLAY,
                        targets=[text_el.id, other.id],
                        detail=(
                            f"text '{text_el.id}' (z={text_el.z_index}) covered by "
                            f"'{other.id}' (z={other.z_index}) at "
                            f"overlap_ratio={ratio:.2f} >= "
                            f"{TEXT_OBSCURED_RATIO_THRESHOLD}"
                        ),
                    )
                )
    return out


# ----- Step 36 rules -----------------------------------------------------

DECORATIVE_IMAGE_MAX_AREA_RATIO: float = 0.40
"""Step 36: a single decorative_image element occupying > 40% of the canvas
area is flagged DECORATIVE_IMAGE_OVERSIZED. Catches the dominant failure
mode in Step 34 N=20 (7/17 failures): Generator inflated an underlay shape
to canvas-sized so that the main photo/text was buried (e.g. 5dad leaf
covering 60% of canvas, 5f4e gray triangle ~50%, 5e7a yellow cross ~50%).
True full-canvas plates use semantic_type=background_image, not
decorative_image, so 0.40 is safely above legitimate underlay sizes."""

TITLE_MIN_AREA_RATIO: float = 0.025
"""Step 36: a TITLE element whose bbox is smaller than 2.5% of the canvas
area is flagged TITLE_UNDERSIZED. Catches Step 34 N=20 cases like 5fbf
('ECUTER' rendered tiny in a corner) and 5dad ('GreenKO' as a small
caption at the bottom)."""

TITLE_EDGE_X_BAND: Tuple[float, float] = (0.10, 0.90)
TITLE_EDGE_Y_MAX: float = 0.85
"""Step 36: a TITLE element whose bbox CENTER falls outside the horizontal
band [0.10, 0.90] OR sits below 85% of the canvas height is flagged
TITLE_PERIPHERAL. Bottom-edge titles still fail because the user-facing
complaint was 'title in corner / at edge'; top-band placement is left
fully permissive because titles legitimately anchor the top of designs."""


def _check_decorative_image_oversized(
    candidate: Candidate, spec: DesignSpec
) -> List[Violation]:
    canvas_area = max(1.0, spec.canvas.width * spec.canvas.height)
    types = _spec_semantic_map(spec)
    out: List[Violation] = []
    for el in candidate.elements:
        if types.get(el.id) != SemanticType.DECORATIVE_IMAGE:
            continue
        ratio = (el.width * el.height) / canvas_area
        if ratio > DECORATIVE_IMAGE_MAX_AREA_RATIO:
            out.append(
                Violation(
                    type=ViolationType.DECORATIVE_IMAGE_OVERSIZED,
                    targets=[el.id],
                    detail=(
                        f"decorative_image '{el.id}' covers "
                        f"area_ratio={ratio:.2f} > "
                        f"{DECORATIVE_IMAGE_MAX_AREA_RATIO} of canvas; "
                        "shrink so the main image/text stays dominant"
                    ),
                )
            )
    return out


def _check_title_undersized(candidate: Candidate, spec: DesignSpec) -> List[Violation]:
    canvas_area = max(1.0, spec.canvas.width * spec.canvas.height)
    types = _spec_semantic_map(spec)
    out: List[Violation] = []
    for el in candidate.elements:
        if types.get(el.id) != SemanticType.TITLE:
            continue
        ratio = (el.width * el.height) / canvas_area
        if ratio < TITLE_MIN_AREA_RATIO:
            out.append(
                Violation(
                    type=ViolationType.TITLE_UNDERSIZED,
                    targets=[el.id],
                    detail=(
                        f"title '{el.id}' bbox area_ratio={ratio:.3f} < "
                        f"{TITLE_MIN_AREA_RATIO} of canvas; titles must be "
                        "large enough to anchor the design"
                    ),
                )
            )
    return out


def _check_title_peripheral(candidate: Candidate, spec: DesignSpec) -> List[Violation]:
    cw = max(1.0, float(spec.canvas.width))
    ch = max(1.0, float(spec.canvas.height))
    types = _spec_semantic_map(spec)
    x_lo, x_hi = TITLE_EDGE_X_BAND
    out: List[Violation] = []
    for el in candidate.elements:
        if types.get(el.id) != SemanticType.TITLE:
            continue
        cx = (el.left + el.width / 2.0) / cw
        cy = (el.top + el.height / 2.0) / ch
        peripheral = False
        why = ""
        if cx < x_lo or cx > x_hi:
            peripheral = True
            why = f"center_x={cx:.2f} outside [{x_lo}, {x_hi}]"
        elif cy > TITLE_EDGE_Y_MAX:
            peripheral = True
            why = f"center_y={cy:.2f} > {TITLE_EDGE_Y_MAX} (too close to bottom)"
        if peripheral:
            out.append(
                Violation(
                    type=ViolationType.TITLE_PERIPHERAL,
                    targets=[el.id],
                    detail=(
                        f"title '{el.id}' bbox center peripheral: {why}; "
                        "move the title into the central hero zone"
                    ),
                )
            )
    return out


# ----- end Step 36 rules -------------------------------------------------


def _check_text_contrast(candidate: Candidate, spec: DesignSpec) -> List[Violation]:
    """Flag text whose color has < WCAG AA contrast (4.5) against canvas bg.

    Catches the Step 34 N=20 failure mode where the Generator emits text
    colored close to the canvas plate (e.g. light-gray title on white
    background that the COLE judge then marks unreadable). v1 only compares
    against canvas.background_color; v2 should resolve effective bg through
    z-layered elements covering the text bbox.
    """
    bg = spec.canvas.background_color or "#FFFFFF"
    types = _spec_semantic_map(spec)
    out: List[Violation] = []
    for text_el in candidate.elements:
        if types.get(text_el.id) not in TEXT_SEMANTIC_TYPES:
            continue
        if not text_el.color:
            continue
        ratio = _contrast_ratio(text_el.color, bg)
        if ratio is None:
            continue
        if ratio < MIN_TEXT_CONTRAST_RATIO:
            out.append(
                Violation(
                    type=ViolationType.LOW_TEXT_CONTRAST,
                    targets=[text_el.id],
                    detail=(
                        f"text '{text_el.id}' color={text_el.color} vs "
                        f"canvas_bg={bg} contrast={ratio:.2f} < "
                        f"{MIN_TEXT_CONTRAST_RATIO} (WCAG AA)"
                    ),
                )
            )
    return out


Z_ORDER_ABOVE_BACKGROUND_HINTS = frozenset(
    {
        "above_background",
        "above_bg",
        "over_background",
        "above_the_background",
        "front_of_background",
    }
)
"""Accepted semantic ``hint`` values meaning "stack above the background plate".

Normalised 2026-05-19 step 12: the first real content-aware live run (Crello
5efdd2dd, layout_agent/output/live_step12_5efdd2dd.log) hard-crashed
``RuntimeError: 0 candidates passed QC after 3 top-up round(s)``. A background
element only exists in content-aware mode, so only then does the Analyst emit a
z_order constraint -- and it emits the *semantic* form
params={"hint": "above_background"} (analyze_brief lists z_order as a rule but
gives no params example and tells the LLM params must be semantic hints), while
``_check_z_order`` historically required the *explicit*
params={"above": <element_id>} form and raised UNKNOWN_HINT on every candidate
when it was absent. We accept the observed token plus the natural LLM
paraphrases and resolve the reference element via
``SemanticType.BACKGROUND_IMAGE`` (the codebase's only background marker --
LayoutElement carries no semantic_type, so spec is threaded in, mirroring
``_check_position_preference``). Trade-off: a brand-new hint outside this set
still yields UNKNOWN_HINT by design, so genuinely malformed constraints are not
silently swallowed; absence of a background element is a graceful skip
(vacuously satisfied), consistent with the step-12 ``resolve_background``
"never crash" philosophy.
"""


def _check_z_order(
    constraint: HardConstraint,
    elements_by_id: Dict[str, LayoutElement],
    spec: DesignSpec,
) -> List[Violation]:
    above_id = constraint.params.get("above")
    explicit = bool(above_id)
    if not above_id:
        # No explicit reference id -- accept the semantic ``hint`` form the
        # Analyst emits in content-aware mode (see frozenset docstring above).
        hint_raw = constraint.params.get("hint", "")
        hint = str(hint_raw).strip().lower().replace("-", "_").replace(" ", "_")
        if not hint:
            return [
                Violation(
                    type=ViolationType.UNKNOWN_HINT,
                    targets=list(constraint.targets),
                    detail="z_order constraint missing both 'above' param and 'hint'.",
                )
            ]
        if hint not in Z_ORDER_ABOVE_BACKGROUND_HINTS:
            return [
                Violation(
                    type=ViolationType.UNKNOWN_HINT,
                    targets=list(constraint.targets),
                    detail=f"z_order hint '{hint_raw}' is not a known z-order relation.",
                )
            ]
        bg = next(
            (e for e in spec.elements if e.semantic_type == SemanticType.BACKGROUND_IMAGE),
            None,
        )
        if bg is None:
            # "Above the background" is vacuously satisfied when the spec has
            # no background element -- skip rather than re-introduce the
            # 0-pass crash on the no-background path.
            return []
        above_id = bg.id
    ref = elements_by_id.get(above_id)
    if ref is None:
        if explicit:
            # Author-supplied reference id -- a real authoring error, surface it.
            return [
                Violation(
                    type=ViolationType.UNKNOWN_TARGET,
                    targets=[above_id],
                    detail=f"z_order reference '{above_id}' not found in candidate.",
                )
            ]
        # Spec-derived background id absent from the candidate:
        # _check_completeness independently reports MISSING_ELEMENT, so do not
        # double-report; skip gracefully rather than crash QC.
        return []
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
