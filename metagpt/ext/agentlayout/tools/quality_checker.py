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
    BackgroundAnalysis,
    Candidate,
    DesignSpec,
    HardConstraint,
    HardConstraintRule,
    LayoutElement,
    SemanticType,
    VisualType,
)
from metagpt.ext.agentlayout.tools.composition_templates import (
    SIZE_BUCKET_RANGES,
    cell_bounds,
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
    # Step 60 (2026-06-11): GT-calibrated PHOTO bucket. Designer GT photos
    # have area_ratio p50 = 0.213 (N=2,374 elements over 1,902 layouts, see
    # layout_agent/output/step60_area_ratio_calibration.py); candidates
    # cluster at 0.083-0.111 ("size timidity", Step 58). 0.20 sits just
    # under the GT median so median-style designer solutions stay legal.
    # This bucket is injected programmatically for product_image elements
    # (analyze_brief.inject_photo_size_prior); the Analyst never emits it.
    "photo-prominent": 0.20,
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
    # Step 43 (2026-06-10): primary content (title/subtitle/body_text/
    # product_image/logo) must overlap with at least one of the safe_zones
    # provided by BackgroundAnalyzer. The saliency-low safe_zones are where
    # the background subject is NOT; primary content placed elsewhere
    # obscures both background and content. Active only when the caller
    # passes a BackgroundAnalysis to check_candidate.
    PRIMARY_OUTSIDE_SAFE_ZONE = "primary_outside_safe_zone"
    # Step 57 (2026-06-11): coverage / dead-space guardrails. Step 56 blind
    # re-measure isolated a ~41-pt pure Generator composition gap whose
    # visible failure modes are degenerate layouts (all elements crammed in
    # one region, entire canvas bands left blank).
    CANVAS_COVERAGE_LOW = "canvas_coverage_low"
    DEAD_BAND_EXCESSIVE = "dead_band_excessive"
    # Step 59 (2026-06-11): Rea (Sobel gradient under text) is the only
    # experiment.md geometric axis where AL trails designer GT (0.0141 vs
    # 0.0066, ~2x). Root cause: safe zones are saliency-driven, not
    # texture-driven. GT-calibrated like Step 57 (degradation guard, not an
    # aesthetic rule): all 20 GT layouts pass at T=0.065 (GT worst exposed
    # element 0.0454 + 0.02 margin; 8/20 GT layouts shield every text with
    # an underlay).
    TEXT_ON_BUSY_TEXTURE = "text_on_busy_texture"
    # Step 62 (2026-06-12): Composition Director contract. Step 61 quantified
    # the sketch-level gap (photo large+bleed GT 45% vs candidate 0%;
    # text-on-photo GT 43% vs 4%); the directive on spec.composition is the
    # designer-GT template the Generator must execute.
    COMPOSITION_MISMATCH = "composition_mismatch"
    # Under a text-on-photo directive the old safe-zone / busy-texture
    # rejections are waived for text riding the focal photo, but readability
    # must then be protected the designer way: an underlay beneath the text.
    TEXT_ON_PHOTO_NO_UNDERLAY = "text_on_photo_no_underlay"


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


def check_candidate(
    candidate: Candidate,
    spec: DesignSpec,
    bg: "Optional[BackgroundAnalysis]" = None,
) -> CheckResult:
    """Run all three validation phases and aggregate violations.

    Phases run sequentially but every phase always runs — we want a *full*
    violation list for analytics, not a fail-fast bool. ``passed`` is True
    iff no violation was recorded.

    Step 43 (2026-06-10): an optional ``bg`` BackgroundAnalysis enables the
    PRIMARY_OUTSIDE_SAFE_ZONE check. Callers that already resolve a
    BackgroundAnalysis (Generator's QC pipeline, Step 41/43 oracle driver)
    should pass it; callers that do not (legacy tests, scripts) keep the
    default and silently skip the new check.
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
    # Step 43 (2026-06-10): primary content must overlap a safe_zone.
    violations.extend(_check_primary_in_safe_zone(candidate, spec, bg))
    # Step 57 (2026-06-11): coverage / dead-space degenerate-layout guardrails.
    violations.extend(_check_canvas_coverage(candidate, spec))
    # Step 59 (2026-06-11): text on busy background texture (Rea deficit).
    violations.extend(_check_text_on_busy_texture(candidate, spec))
    # Step 62 (2026-06-12): Composition Director contract + the conditional
    # underlay requirement that replaces safe-zone/busy-texture rejection
    # for text riding the focal photo. Both no-op when spec.composition is
    # None, keeping pre-62 callers bit-identical.
    violations.extend(_check_composition(candidate, spec))
    violations.extend(_check_text_on_photo_underlay(candidate, spec))
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

TEXT_OBSCURED_RATIO_THRESHOLD: float = 0.20
"""Step 35: text element is flagged TEXT_OBSCURED_BY_OVERLAY when an above-z
(or same-z) non-text element covers >= 20% of its area.

Step 37 tightening (2026-06-09, P3 of Tier-1 N=100 audit): threshold lowered
0.30 -> 0.20 and the same-z case was added (was strictly above-z) after
samples 5dc93 (Statco logo) and 589d7bd9 (Find yourself) showed text
overlapping decorations the rule should have caught but did not. Risk: a
few legitimate "text floating over a soft illustration backdrop" patterns
(e.g. 5e8d nurse with white text over the white-shield foreground) may now
false-positive; tolerated because pipeline retries on QC fail and the
downstream pairwise judge filters surviving candidates anyway."""

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
            # Step 37 P3 (2026-06-09): same-z trigger added (was strictly >).
            # When text and an image element share the same z_index the
            # render order is undefined; we now flag any image >= text's z
            # that meaningfully covers the text bbox.
            if other.z_index < text_el.z_index:
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
TITLE_EDGE_Y_MIN: float = 0.05
"""Step 36 / 36c: a TITLE element whose bbox CENTER falls outside the
horizontal band [0.10, 0.90] OR sits below 85% of the canvas height OR
sits above 5% of the canvas height is flagged TITLE_PERIPHERAL. The
top-band cutoff was added in Step 36c (2026-06-09) after sample 5dad
'GreenKO' landed at the very top-right corner under Step 36 (the leaf
underlay correctly shrank but the title peripheral rule allowed any
top placement). Titles legitimately sit in the upper third of designs
(y ~ 0.10-0.30) but pinning them to the absolute top edge (y < 0.05)
is the crop / catalog-badge failure mode we want to catch."""


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
        elif cy < TITLE_EDGE_Y_MIN:
            peripheral = True
            why = (
                f"center_y={cy:.2f} < {TITLE_EDGE_Y_MIN} "
                "(pinned to canvas top edge, likely cropped)"
            )
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


# ----- Step 43 rule ------------------------------------------------------

PRIMARY_SEMANTIC_TYPES = frozenset(
    {
        SemanticType.TITLE,
        SemanticType.SUBTITLE,
        SemanticType.BODY_TEXT,
        SemanticType.PRODUCT_IMAGE,
        SemanticType.LOGO,
    }
)

PRIMARY_SAFE_ZONE_MIN_OVERLAP: float = 0.50
"""Step 43 (2026-06-10): a PRIMARY element (title/subtitle/body_text/
product_image/logo) MUST overlap by at least 50% of its own area with at
least one of the safe_zones returned by BackgroundAnalyzer. Less than
50% means the primary content is mostly sitting on top of the
background saliency subject, obscuring both the background and the
content itself."""


def _check_primary_in_safe_zone(
    candidate: Candidate,
    spec: DesignSpec,
    bg: Optional[BackgroundAnalysis],
) -> List[Violation]:
    if bg is None or not bg.safe_zones:
        return []
    types = _spec_semantic_map(spec)
    # Step 62 (2026-06-12): under a text-on-photo directive the focal photo
    # is the safe surface -- the photo itself and primaries riding it are
    # exempt (the old rule rejected the GT-dominant composition, Step 58/61).
    exempt = _text_on_photo_exempt_ids(candidate, spec)
    out: List[Violation] = []
    for el in candidate.elements:
        if types.get(el.id) not in PRIMARY_SEMANTIC_TYPES:
            continue
        if el.id in exempt:
            continue
        elem_area = float(el.width) * float(el.height)
        if elem_area <= 0:
            continue
        # Step 43 bug-fix (2026-06-10): SafeZone.bbox is [left, top, RIGHT,
        # BOTTOM] (absolute coords) per schema docstring, NOT
        # [left, top, width, height]. The first cut of this rule treated
        # the last two entries as width/height, which extended the zone's
        # right/bottom by `left + right` (e.g. r0c2 at [1000, 0, 1500, 166]
        # was treated as covering x in [1000, 2500] instead of [1000,
        # 1500]). Decoding as LTRB fixes the overlap math.
        el_left = float(el.left)
        el_top = float(el.top)
        el_right = el_left + float(el.width)
        el_bottom = el_top + float(el.height)
        best_overlap = 0.0
        best_zone = None
        for sz in bg.safe_zones:
            sl, st, sr, sb = sz.bbox  # LTRB
            ix = max(0.0, min(el_right, float(sr)) - max(el_left, float(sl)))
            iy = max(0.0, min(el_bottom, float(sb)) - max(el_top, float(st)))
            overlap = (ix * iy) / elem_area
            if overlap > best_overlap:
                best_overlap = overlap
                best_zone = sz.region
        if best_overlap < PRIMARY_SAFE_ZONE_MIN_OVERLAP:
            out.append(
                Violation(
                    type=ViolationType.PRIMARY_OUTSIDE_SAFE_ZONE,
                    targets=[el.id],
                    detail=(
                        f"primary '{el.id}' ({types[el.id].value}) overlaps "
                        f"best safe_zone '{best_zone or '<none>'}' at "
                        f"ratio={best_overlap:.2f} < "
                        f"{PRIMARY_SAFE_ZONE_MIN_OVERLAP}; place primary "
                        "content inside background-saliency-low safe_zones"
                    ),
                )
            )
    return out


# ----- end Step 43 rule --------------------------------------------------


# ----- Step 57 rules -------------------------------------------------------

CANVAS_COVERAGE_MIN: float = 0.10
"""Step 57 (2026-06-11): minimum union-area fraction of the canvas that the
foreground elements (everything except background_image) must cover.

Calibrated offline against the 20 step13 designer GT layouts
(layout_agent/output/step57_coverage_calibration.py): designer minimum is
0.129 (minimalist layouts where the background photo carries the design are
legitimate), so 0.10 passes every designer layout with margin. This is a
DEGENERATE-LAYOUT GUARDRAIL, not an aesthetic rule: in the step56 live run,
10/70 generated candidates fell below it (e.g. 5f56075f at 0.050 -- all
content shrunk into a sliver) while zero designer layouts do."""

DEAD_BAND_MAX: float = 0.60
"""Step 57: maximum allowed contiguous blank band (no foreground element)
along either canvas axis, as a fraction of that axis, leading/trailing
margins included.

Designer GT maxima are v=0.503 / h=0.548 (a tall poster legitimately leaves
half the height to the background subject), so 0.60 passes all 20 designer
layouts. Step56 candidates regularly hit 0.66-0.79 (e.g. 589d7bd9 left 2/3
of the canvas empty in all 5 candidates) -- those are the 'bottom half is
blank' degenerate compositions the blind judge punishes on design_layout."""

_COVERAGE_GRID: int = 100
"""Raster resolution for the bbox-union coverage estimate. 1% cell size is
well below the 3-point gap between threshold (0.10) and designer minimum
(0.129); exact polygon union would be needless complexity."""


def _foreground_elements(
    candidate: Candidate, spec: DesignSpec
) -> List[LayoutElement]:
    types = _spec_semantic_map(spec)
    return [
        el
        for el in candidate.elements
        if types.get(el.id) != SemanticType.BACKGROUND_IMAGE
    ]


def _union_coverage_ratio(elements: List[LayoutElement], cw: float, ch: float) -> float:
    grid = [[False] * _COVERAGE_GRID for _ in range(_COVERAGE_GRID)]
    for el in elements:
        x0 = max(0, int(el.left / cw * _COVERAGE_GRID))
        x1 = min(_COVERAGE_GRID, int((el.left + el.width) / cw * _COVERAGE_GRID + 0.9999))
        y0 = max(0, int(el.top / ch * _COVERAGE_GRID))
        y1 = min(_COVERAGE_GRID, int((el.top + el.height) / ch * _COVERAGE_GRID + 0.9999))
        for y in range(y0, y1):
            row = grid[y]
            for x in range(x0, x1):
                row[x] = True
    covered = sum(row.count(True) for row in grid)
    return covered / (_COVERAGE_GRID * _COVERAGE_GRID)


def _max_dead_band(intervals: List[Tuple[float, float]], total: float) -> float:
    """Largest fraction of [0, total] not covered by any (start, end) interval."""
    clipped = sorted(
        (max(0.0, s), min(total, e)) for s, e in intervals if e > 0 and s < total
    )
    if not clipped:
        return 1.0
    best = clipped[0][0]  # leading margin
    cur_end = clipped[0][1]
    for s, e in clipped[1:]:
        if s > cur_end:
            best = max(best, s - cur_end)
        cur_end = max(cur_end, e)
    best = max(best, total - cur_end)  # trailing margin
    return best / total


def _check_canvas_coverage(candidate: Candidate, spec: DesignSpec) -> List[Violation]:
    cw = max(1.0, float(spec.canvas.width))
    ch = max(1.0, float(spec.canvas.height))
    fg = _foreground_elements(candidate, spec)
    out: List[Violation] = []
    coverage = _union_coverage_ratio(fg, cw, ch)
    if coverage < CANVAS_COVERAGE_MIN:
        out.append(
            Violation(
                type=ViolationType.CANVAS_COVERAGE_LOW,
                targets=[el.id for el in fg],
                detail=(
                    f"foreground union covers {coverage:.1%} of the canvas < "
                    f"{CANVAS_COVERAGE_MIN:.0%}; spread/enlarge elements so the "
                    "composition fills the canvas (designer layouts never go "
                    "below 13%)"
                ),
            )
        )
    v_dead = _max_dead_band([(el.top, el.top + el.height) for el in fg], ch)
    h_dead = _max_dead_band([(el.left, el.left + el.width) for el in fg], cw)
    if v_dead > DEAD_BAND_MAX:
        out.append(
            Violation(
                type=ViolationType.DEAD_BAND_EXCESSIVE,
                targets=[el.id for el in fg],
                detail=(
                    f"vertical blank band spans {v_dead:.1%} of canvas height > "
                    f"{DEAD_BAND_MAX:.0%}; distribute elements along the full "
                    "height instead of stacking them in one band"
                ),
            )
        )
    if h_dead > DEAD_BAND_MAX:
        out.append(
            Violation(
                type=ViolationType.DEAD_BAND_EXCESSIVE,
                targets=[el.id for el in fg],
                detail=(
                    f"horizontal blank band spans {h_dead:.1%} of canvas width > "
                    f"{DEAD_BAND_MAX:.0%}; distribute elements along the full "
                    "width instead of stacking them in one band"
                ),
            )
        )
    return out


# ----- end Step 57 rules ----------------------------------------------------


# ----- Step 59 rule (2026-06-11) ---------------------------------------------

TEXT_GRADIENT_MAX: float = 0.065
"""Step 59: max mean normalised Sobel background gradient under a text
element's *exposed* pixels (pixels not shielded by a decorative_image
underlay). GT-calibrated degradation guard (Step 57 SOP, not an aesthetic
rule): all 20 designer GT layouts pass — worst exposed GT element is
0.0454, threshold = GT max + 0.02 margin (Step 58 lesson: gates hugging
the GT max kill GT-style solutions). At 0.065 the rule catches 23%
(74/327) of replayed live candidates with exposed text, including exactly
the samples driving the Rea gap. Convention matches
``evaluation.sega_metrics.metric_readability``: the gradient map is
normalised by the image's own max and underlay bboxes zero out text
pixels (an underlay shields the text from texture, which is the designer
GT's dominant defense — 8/20 GT layouts shield every text element)."""

_BG_GRADIENT_CACHE: Dict[Tuple[str, int, int], object] = {}
"""(background_asset_ref, canvas_w, canvas_h) -> normalised gradient ndarray,
or None when the image could not be loaded. One Sobel per sample, shared
across every candidate/batch QC call for that spec."""


def _bg_gradient_map(bg_ref: str, cw: int, ch: int):
    key = (bg_ref, cw, ch)
    if key not in _BG_GRADIENT_CACHE:
        try:
            import cv2
            import numpy as np
            from PIL import Image

            from metagpt.ext.agentlayout.evaluation.sega_metrics import (
                _sobel_gradient_normalised,
            )

            arr = np.array(Image.open(bg_ref).convert("RGB"))
            if arr.shape[1] != cw or arr.shape[0] != ch:
                arr = cv2.resize(arr, (cw, ch))
            _BG_GRADIENT_CACHE[key] = _sobel_gradient_normalised(arr)
        except Exception:
            # Missing/corrupt bg image or optional deps (cv2) unavailable:
            # skip the check rather than crash the QC pipeline (step-12
            # "never crash" philosophy, same as resolve_background).
            _BG_GRADIENT_CACHE[key] = None
    return _BG_GRADIENT_CACHE[key]


def _clip_box_px(el: LayoutElement, cw: int, ch: int) -> Optional[Tuple[int, int, int, int]]:
    xl = max(0, int(round(float(el.left))))
    yl = max(0, int(round(float(el.top))))
    xr = min(cw, int(round(float(el.left) + float(el.width))))
    yr = min(ch, int(round(float(el.top) + float(el.height))))
    if xr <= xl or yr <= yl:
        return None
    return xl, yl, xr, yr


def _check_text_on_busy_texture(candidate: Candidate, spec: DesignSpec) -> List[Violation]:
    """Flag text sitting on busy background texture without an underlay shield.

    Element classification mirrors the Rea metric / Step 59 calibration (NOT
    ``TEXT_SEMANTIC_TYPES``): every ``visual_type == text`` element counts as
    text — both live layouts the calibration caught were CTA buttons, which
    ``TEXT_SEMANTIC_TYPES`` would miss. ``decorative_image`` elements are
    underlays whose bbox shields any text pixels beneath; full-canvas
    (>95% area) elements act as background and are ignored.
    """
    bg_ref = spec.canvas.background_asset_ref
    if not bg_ref:
        return []
    cw = max(1, int(round(float(spec.canvas.width))))
    ch = max(1, int(round(float(spec.canvas.height))))
    spec_by_id = {e.id: e for e in spec.elements}

    text_boxes: List[Tuple[str, Tuple[int, int, int, int]]] = []
    underlay_boxes: List[Tuple[int, int, int, int]] = []
    for el in candidate.elements:
        spec_el = spec_by_id.get(el.id)
        if spec_el is None:
            continue
        if spec_el.semantic_type == SemanticType.BACKGROUND_IMAGE:
            continue
        if float(el.width) * float(el.height) > 0.95 * cw * ch:
            continue
        box = _clip_box_px(el, cw, ch)
        if box is None:
            continue
        if spec_el.semantic_type == SemanticType.DECORATIVE_IMAGE:
            underlay_boxes.append(box)
        elif spec_el.visual_type == VisualType.TEXT:
            text_boxes.append((el.id, box))
    if not text_boxes:
        return []

    # Step 62 (2026-06-12): under a text-on-photo directive the focal photo
    # covers the background wherever text rides it -- the background gradient
    # under the photo bbox is invisible, so subtract it like an underlay.
    comp = spec.composition
    if comp is not None and comp.relation == "text-on-photo":
        focal = _focal_photo(candidate, spec)
        if focal is not None:
            fbox = _clip_box_px(focal, cw, ch)
            if fbox is not None:
                underlay_boxes.append(fbox)

    grad = _bg_gradient_map(bg_ref, cw, ch)
    if grad is None:
        return []
    import numpy as np

    out: List[Violation] = []
    for el_id, (xl, yl, xr, yr) in text_boxes:
        mask = np.zeros((ch, cw), dtype=bool)
        mask[yl:yr, xl:xr] = True
        for uxl, uyl, uxr, uyr in underlay_boxes:
            mask[uyl:uyr, uxl:uxr] = False
        if not mask.any():
            # Fully shielded by an underlay: trivially readable, skip.
            continue
        g = float(grad[mask].mean())
        if g > TEXT_GRADIENT_MAX:
            out.append(
                Violation(
                    type=ViolationType.TEXT_ON_BUSY_TEXTURE,
                    targets=[el_id],
                    detail=(
                        f"text '{el_id}' sits on busy background texture "
                        f"(mean gradient {g:.4f} > {TEXT_GRADIENT_MAX}); "
                        "place an underlay shape beneath this text to shield "
                        "it (designers fully shield text with underlays in "
                        "8/20 reference layouts) or move it to a flatter "
                        "background region"
                    ),
                )
            )
    return out


# ----- end Step 59 rule -------------------------------------------------------


# ----- Step 62 rules (2026-06-12) ---------------------------------------------

COMPOSITION_CELL_TOLERANCE: float = 0.05
"""Slack, as a fraction of each canvas axis, added around the directive's 3x3
grid cell when checking element centers. A center sitting 2% outside the cell
boundary is the same sketch; Step 58 taught us that gates hugging exact
boundaries kill GT-style solutions."""

COMPOSITION_SIZE_MARGIN: float = 0.02
"""Slack on the photo area-ratio bucket bounds, same rationale."""

TEXT_ON_PHOTO_MIN_OVERLAP: float = 0.30
"""A text element 'rides' the focal photo when >= 30% of its own area overlaps
the photo bbox. Identical to the Step 61 calibration signature
(layout_agent/output/step61_composition_calibration.py::signature) so QC
classifies candidates with exactly the rule that produced the GT priors."""

RELATION_AXIS_FRACTION: float = 1.0 / 6.0
"""Centroid-offset threshold (fraction of the canvas axis) separating
stacked / side-by-side from centered-mix. Mirrors the Step 61 signature."""

TEXT_ON_PHOTO_UNDERLAY_MIN_COVER: float = 0.80
"""Under a text-on-photo directive, each text element riding the focal photo
must have a decorative_image underlay covering >= 80% of its own bbox. This is
the designer defense (8/20 GT layouts shield every text element with an
underlay); 100% is not demanded because GT underlays often inset slightly."""


def _focal_photo(candidate: Candidate, spec: DesignSpec) -> Optional[LayoutElement]:
    """Largest candidate element whose spec semantic_type is product_image."""
    types = _spec_semantic_map(spec)
    photos = [
        el
        for el in candidate.elements
        if types.get(el.id) == SemanticType.PRODUCT_IMAGE
    ]
    if not photos:
        return None
    return max(photos, key=lambda el: float(el.width) * float(el.height))


def _overlap_ratio(el: LayoutElement, other: LayoutElement) -> float:
    """Intersection area as a fraction of ``el``'s own area."""
    area = float(el.width) * float(el.height)
    if area <= 0:
        return 0.0
    ix = max(
        0.0,
        min(float(el.left) + float(el.width), float(other.left) + float(other.width))
        - max(float(el.left), float(other.left)),
    )
    iy = max(
        0.0,
        min(float(el.top) + float(el.height), float(other.top) + float(other.height))
        - max(float(el.top), float(other.top)),
    )
    return (ix * iy) / area


def _candidate_text_elements(candidate: Candidate, spec: DesignSpec) -> List[LayoutElement]:
    spec_by_id = {e.id: e for e in spec.elements}
    out: List[LayoutElement] = []
    for el in candidate.elements:
        spec_el = spec_by_id.get(el.id)
        if spec_el is not None and spec_el.visual_type == VisualType.TEXT:
            out.append(el)
    return out


def _text_mass_center(texts: List[LayoutElement]) -> Optional[Tuple[float, float]]:
    """Area-weighted centroid of the text elements (Step 61 convention)."""
    total = 0.0
    tx = ty = 0.0
    for el in texts:
        a = float(el.width) * float(el.height)
        total += a
        tx += a * (float(el.left) + float(el.width) / 2.0)
        ty += a * (float(el.top) + float(el.height) / 2.0)
    if total <= 0:
        return None
    return tx / total, ty / total


def _classify_relation(
    focal: LayoutElement, texts: List[LayoutElement], cw: float, ch: float
) -> Optional[str]:
    """Photo-text relation of a candidate, replicating the Step 61 signature."""
    center = _text_mass_center(texts)
    if center is None:
        return None
    total = sum(float(el.width) * float(el.height) for el in texts)
    olap = sum(
        _overlap_ratio(el, focal) * float(el.width) * float(el.height) for el in texts
    )
    if olap / total >= TEXT_ON_PHOTO_MIN_OVERLAP:
        return "text-on-photo"
    px = float(focal.left) + float(focal.width) / 2.0
    py = float(focal.top) + float(focal.height) / 2.0
    dx = abs(center[0] - px) / cw
    dy = abs(center[1] - py) / ch
    if max(dx, dy) < RELATION_AXIS_FRACTION:
        return "centered-mix"
    return "stacked" if dy >= dx else "side-by-side"


def _check_composition(candidate: Candidate, spec: DesignSpec) -> List[Violation]:
    """Verify the candidate executes the Composition Director's directive.

    Inactive (returns []) when ``spec.composition`` is None, keeping pre-62
    behavior bit-identical for every existing caller. Detail strings carry
    concrete numbers because they feed the Generator's retry feedback.
    """
    comp = spec.composition
    if comp is None:
        return []
    cw = max(1.0, float(spec.canvas.width))
    ch = max(1.0, float(spec.canvas.height))
    tol_x = COMPOSITION_CELL_TOLERANCE * cw
    tol_y = COMPOSITION_CELL_TOLERANCE * ch
    focal = _focal_photo(candidate, spec)
    texts = _candidate_text_elements(candidate, spec)
    out: List[Violation] = []

    if comp.photo_cell and focal is not None:
        xl, yt, xr, yb = cell_bounds(comp.photo_cell, cw, ch)
        cx = float(focal.left) + float(focal.width) / 2.0
        cy = float(focal.top) + float(focal.height) / 2.0
        if not (xl - tol_x <= cx <= xr + tol_x and yt - tol_y <= cy <= yb + tol_y):
            out.append(
                Violation(
                    type=ViolationType.COMPOSITION_MISMATCH,
                    targets=[focal.id],
                    detail=(
                        f"photo '{focal.id}' center ({cx:.0f}, {cy:.0f}) outside "
                        f"directive cell '{comp.photo_cell}' (x in [{xl:.0f}, "
                        f"{xr:.0f}], y in [{yt:.0f}, {yb:.0f}]); move the photo "
                        "so its center falls inside the directive cell"
                    ),
                )
            )

    bucket = SIZE_BUCKET_RANGES.get(comp.photo_size) if comp.photo_size else None
    if bucket is not None and focal is not None:
        lo, hi = bucket
        ratio = (float(focal.width) * float(focal.height)) / (cw * ch)
        if not (lo - COMPOSITION_SIZE_MARGIN <= ratio <= hi + COMPOSITION_SIZE_MARGIN):
            out.append(
                Violation(
                    type=ViolationType.COMPOSITION_MISMATCH,
                    targets=[focal.id],
                    detail=(
                        f"photo '{focal.id}' area_ratio={ratio:.3f} outside "
                        f"directive size bucket '{comp.photo_size}' "
                        f"[{lo}, {hi}]; resize the photo to "
                        f"[{lo * cw * ch:,.0f} .. {hi * cw * ch:,.0f}] px^2"
                    ),
                )
            )

    if comp.text_cell and texts:
        center = _text_mass_center(texts)
        if center is not None:
            xl, yt, xr, yb = cell_bounds(comp.text_cell, cw, ch)
            tx, ty = center
            if not (xl - tol_x <= tx <= xr + tol_x and yt - tol_y <= ty <= yb + tol_y):
                out.append(
                    Violation(
                        type=ViolationType.COMPOSITION_MISMATCH,
                        targets=[el.id for el in texts],
                        detail=(
                            f"text mass center ({tx:.0f}, {ty:.0f}) outside "
                            f"directive cell '{comp.text_cell}' (x in [{xl:.0f}, "
                            f"{xr:.0f}], y in [{yt:.0f}, {yb:.0f}]); shift the "
                            "text block so its area-weighted center falls "
                            "inside the directive cell"
                        ),
                    )
                )

    if comp.relation and focal is not None and texts:
        actual = _classify_relation(focal, texts, cw, ch)
        if actual is not None and actual != comp.relation:
            out.append(
                Violation(
                    type=ViolationType.COMPOSITION_MISMATCH,
                    targets=[focal.id] + [el.id for el in texts],
                    detail=(
                        f"candidate photo-text relation classifies as "
                        f"'{actual}' but the directive requires "
                        f"'{comp.relation}'"
                        + (
                            "; overlap >= 30% of the text area with the photo "
                            "bbox is required -- place the text ON the photo"
                            if comp.relation == "text-on-photo"
                            else "; adjust the photo-vs-text-mass offset to "
                            "match the directive"
                        )
                    ),
                )
            )
    return out


def _check_text_on_photo_underlay(candidate: Candidate, spec: DesignSpec) -> List[Violation]:
    """Designer-style readability protection under a text-on-photo directive.

    The conditional exemption deal (user decision, Step 62): text riding the
    focal photo is no longer rejected by safe-zone / busy-texture rules, but
    each such text must sit on a decorative_image underlay. Skipped when the
    spec has no decorative_image at all -- completeness forbids inventing
    elements, so the demand would be unsatisfiable; _check_text_contrast still
    guards readability there.
    """
    comp = spec.composition
    if comp is None or comp.relation != "text-on-photo":
        return []
    focal = _focal_photo(candidate, spec)
    if focal is None:
        return []
    types = _spec_semantic_map(spec)
    underlays = [
        el
        for el in candidate.elements
        if types.get(el.id) == SemanticType.DECORATIVE_IMAGE
    ]
    if not underlays:
        return []
    out: List[Violation] = []
    for el in _candidate_text_elements(candidate, spec):
        if _overlap_ratio(el, focal) < TEXT_ON_PHOTO_MIN_OVERLAP:
            continue
        best = max(_overlap_ratio(el, u) for u in underlays)
        if best < TEXT_ON_PHOTO_UNDERLAY_MIN_COVER:
            out.append(
                Violation(
                    type=ViolationType.TEXT_ON_PHOTO_NO_UNDERLAY,
                    targets=[el.id],
                    detail=(
                        f"text '{el.id}' rides the focal photo but its best "
                        f"underlay covers only {best:.0%} < "
                        f"{TEXT_ON_PHOTO_UNDERLAY_MIN_COVER:.0%} of its bbox; "
                        "place a decorative_image underlay beneath this text "
                        "(the designer norm for text-on-photo compositions)"
                    ),
                )
            )
    return out


def _text_on_photo_exempt_ids(candidate: Candidate, spec: DesignSpec) -> frozenset:
    """Element ids exempt from safe-zone rejection under text-on-photo.

    The focal photo IS the directive (it must cover the canvas center, which
    safe zones rarely include), and elements riding it sit on the photo, not
    on the background subject -- rejecting either kills the GT-dominant
    composition (Step 58/61 finding)."""
    comp = spec.composition
    if comp is None or comp.relation != "text-on-photo":
        return frozenset()
    focal = _focal_photo(candidate, spec)
    if focal is None:
        return frozenset()
    ids = {focal.id}
    for el in candidate.elements:
        if el.id != focal.id and _overlap_ratio(el, focal) >= TEXT_ON_PHOTO_MIN_OVERLAP:
            ids.add(el.id)
    return frozenset(ids)


# ----- end Step 62 rules --------------------------------------------------------


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
