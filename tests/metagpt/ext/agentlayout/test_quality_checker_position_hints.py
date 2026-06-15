"""Pinned tests for ``quality_checker.POSITION_HINT_TO_BANDS``.

2026-05-14 step 9: the first live run on a 5-element Crello brief revealed that
the Analyst can emit ``center_top`` instead of the canonical ``top_center``;
because the QC whitelist only contained ``top_center``, every candidate failed
with an ``UNKNOWN_HINT`` violation and the pipeline crashed before producing a
single Judge verdict. The fix added aliases for reversed word ordering. These
tests pin (a) the canonical hints still map where they used to and (b) the new
aliases map to the same band as their canonical sibling.

Run:
    pytest tests/metagpt/ext/agentlayout/test_quality_checker_position_hints.py -v --no-cov
"""
from __future__ import annotations

import pytest


# ============================================================
# 1. Canonical hints (regression guard against accidental removal)
# ============================================================


@pytest.mark.parametrize(
    "hint,expected",
    [
        ("top_left", (0, 0)),
        ("top_center", (1, 0)),
        ("top_right", (2, 0)),
        ("center", (1, 1)),
        ("bottom_left", (0, 2)),
        ("bottom_center", (1, 2)),
        ("bottom_right", (2, 2)),
    ],
)
def test_canonical_position_hints_map_to_expected_band(hint, expected):
    """The canonical hint names date back to the original Generator prompt and
    are quoted in many doc strings; removing them silently would break every
    live run."""
    from metagpt.ext.agentlayout.tools.quality_checker import (
        POSITION_HINT_TO_BANDS,
    )

    assert POSITION_HINT_TO_BANDS[hint] == expected


# ============================================================
# 2. Reversed-word-order aliases (step 9 fix)
# ============================================================


@pytest.mark.parametrize(
    "alias,canonical",
    [
        ("center_top", "top_center"),
        ("center_bottom", "bottom_center"),
        ("left_top", "top_left"),
        ("right_top", "top_right"),
        ("left_bottom", "bottom_left"),
        ("right_bottom", "bottom_right"),
        ("left_middle", "middle_left"),
        ("right_middle", "middle_right"),
    ],
)
def test_reversed_word_order_alias_maps_to_canonical_band(alias, canonical):
    """Each reversed-word-order alias MUST land on the same band as its
    canonical sibling. Otherwise the live Crello run that surfaced this bug
    silently scores layouts against the wrong region."""
    from metagpt.ext.agentlayout.tools.quality_checker import (
        POSITION_HINT_TO_BANDS,
    )

    assert POSITION_HINT_TO_BANDS[alias] == POSITION_HINT_TO_BANDS[canonical]


def test_position_hint_count_pins_alias_addition():
    """If someone removes an alias, this count catches it. Canonical hints =
    14 (per the original dict), aliases = 8 (step 9 addition) = 22 total."""
    from metagpt.ext.agentlayout.tools.quality_checker import (
        POSITION_HINT_TO_BANDS,
    )

    assert len(POSITION_HINT_TO_BANDS) == 22


# ============================================================
# 3. End-to-end: a candidate using the alias should NOT raise UNKNOWN_HINT
# ============================================================


def test_check_position_preference_accepts_center_top_alias():
    """Replay of the step 9 Crello bug: an Analyst-emitted ``center_top`` hint
    on a candidate whose target element actually sits in the top-center band
    must pass position-preference checking with zero violations."""
    from metagpt.ext.agentlayout.schema import (
        Candidate,
        Canvas,
        DesignSpec,
        Element,
        HardConstraint,
        HardConstraintRule,
        LayoutElement,
        SemanticType,
        VisualType,
    )
    from metagpt.ext.agentlayout.tools.quality_checker import check_candidate

    spec = DesignSpec(
        canvas=Canvas(width=1080, height=1920),
        elements=[
            Element(
                id="text_3",
                semantic_type=SemanticType.TITLE,
                visual_type=VisualType.TEXT,
                content="We are hiring a",
                importance=5,
                semantic_relevance=0.5,
            ),
        ],
        hard_constraints=[
            HardConstraint(
                rule=HardConstraintRule.POSITION_PREFERENCE,
                targets=["text_3"],
                params={"hint": "center_top"},
            ),
        ],
        soft_constraints=[],
        style_keywords=[],
        language="en",
        inferred_fields={},
    )
    # text_3 in top-center band: canvas 1080 wide => third=360, center is 360-720;
    # top third of 1920 is 0-640.
    candidate = Candidate(
        candidate_id="cand_alias_probe",
        elements=[
            LayoutElement(
                id="text_3",
                left=400,
                top=100,
                width=280,
                height=200,
                z_index=2,
                font_family="sans-serif",
                font_size=48,
                font_weight="bold",
                color="#111111",
                text_align="center",
            ),
        ],
    )

    result = check_candidate(candidate, spec)
    unknown_hint = [v for v in result.violations if v.type.value == "unknown_hint"]
    position = [v for v in result.violations if v.type.value == "position_preference"]
    assert unknown_hint == [], f"alias 'center_top' must not raise UNKNOWN_HINT: {unknown_hint}"
    assert position == [], f"text_3 is in (1, 0) band; should pass: {position}"


# ============================================================
# 4. no_overlap tolerance (step 10, 2026-05-15)
# ============================================================


def test_no_overlap_tolerance_constant_is_five_percent():
    """The 5% tolerance is the calibration that makes the live Crello 1200x600
    spec satisfiable; tightening it back to 0% reverts the live #8 crash."""
    from metagpt.ext.agentlayout.tools.quality_checker import NO_OVERLAP_TOLERANCE

    assert NO_OVERLAP_TOLERANCE == 0.05


def _no_overlap_spec():
    """Two-element spec sharing a single no_overlap hard constraint, used as a
    minimal fixture for tolerance-boundary tests below."""
    from metagpt.ext.agentlayout.schema import (
        Canvas,
        DesignSpec,
        Element,
        HardConstraint,
        HardConstraintRule,
        SemanticType,
        VisualType,
    )

    return DesignSpec(
        canvas=Canvas(width=1000, height=1000),
        elements=[
            Element(
                id="a",
                semantic_type=SemanticType.DECORATIVE_IMAGE,
                visual_type=VisualType.IMAGE,
                asset_ref="/tmp/a.png",
                importance=2,
                semantic_relevance=0.5,
            ),
            Element(
                id="b",
                semantic_type=SemanticType.DECORATIVE_IMAGE,
                visual_type=VisualType.IMAGE,
                asset_ref="/tmp/b.png",
                importance=2,
                semantic_relevance=0.5,
            ),
        ],
        hard_constraints=[
            HardConstraint(
                rule=HardConstraintRule.NO_OVERLAP, targets=["a", "b"], params={}
            )
        ],
        soft_constraints=[],
        style_keywords=[],
        language="en",
        inferred_fields={},
    )


def _mk_candidate(a_box, b_box):
    from metagpt.ext.agentlayout.schema import Candidate, LayoutElement

    return Candidate(
        candidate_id="t",
        elements=[
            LayoutElement(id="a", left=a_box[0], top=a_box[1], width=a_box[2], height=a_box[3], z_index=1),
            LayoutElement(id="b", left=b_box[0], top=b_box[1], width=b_box[2], height=b_box[3], z_index=2),
        ],
    )


def test_no_overlap_disjoint_boxes_pass():
    """Disjoint boxes -- the canonical happy path."""
    from metagpt.ext.agentlayout.tools.quality_checker import check_candidate

    cand = _mk_candidate((0, 0, 100, 100), (200, 200, 100, 100))
    out = check_candidate(cand, _no_overlap_spec())
    assert [v for v in out.violations if v.type.value == "no_overlap"] == []


def test_no_overlap_micro_overlap_at_5_percent_passes():
    """20px x 100px overlap on two 100x100 boxes = 2000 / 10000 = 20% -- fails.
    Pick numbers that land at exactly the 5% threshold and confirm it passes."""
    from metagpt.ext.agentlayout.tools.quality_checker import check_candidate

    # a = 0..100, 0..100 (area 10000); b = 95..195, 0..100 (overlap 5x100=500 = 5%)
    cand = _mk_candidate((0, 0, 100, 100), (95, 0, 100, 100))
    out = check_candidate(cand, _no_overlap_spec())
    no_ov = [v for v in out.violations if v.type.value == "no_overlap"]
    assert no_ov == [], f"5% overlap should be within tolerance: {no_ov}"


def test_no_overlap_just_above_tolerance_fails():
    """6% overlap (just above the 5% tolerance) must still fail; otherwise
    real overlap regressions slip through."""
    from metagpt.ext.agentlayout.tools.quality_checker import check_candidate

    # 6x100 overlap on 100x100 = 6% -> fail
    cand = _mk_candidate((0, 0, 100, 100), (94, 0, 100, 100))
    out = check_candidate(cand, _no_overlap_spec())
    no_ov = [v for v in out.violations if v.type.value == "no_overlap"]
    assert len(no_ov) == 1
    assert "6.0%" in no_ov[0].detail
    assert "tolerance: 5%" in no_ov[0].detail


def test_no_overlap_message_format_pins_percentage_detail():
    """The violation detail must carry the overlap percentage so Generator
    feedback can include it; removing the format would silently regress the
    structured-suggestion feedback loop."""
    from metagpt.ext.agentlayout.tools.quality_checker import check_candidate

    cand = _mk_candidate((0, 0, 100, 100), (50, 50, 100, 100))  # 25% overlap
    out = check_candidate(cand, _no_overlap_spec())
    no_ov = [v for v in out.violations if v.type.value == "no_overlap"]
    assert len(no_ov) == 1
    detail = no_ov[0].detail
    assert "overlap by 25.0%" in detail or "overlap by 25%" in detail
    assert "'a'" in detail and "'b'" in detail


def test_aabb_overlap_helper_still_reports_any_overlap():
    """The boolean ``_aabb_overlap`` is kept as a wrapper for any external
    scripts; it must continue to return True for ANY positive overlap area
    (tolerance is applied only by ``_check_no_overlap``)."""
    from metagpt.ext.agentlayout.schema import LayoutElement
    from metagpt.ext.agentlayout.tools.quality_checker import _aabb_overlap

    a = LayoutElement(id="a", left=0, top=0, width=100, height=100, z_index=1)
    b = LayoutElement(id="b", left=99, top=0, width=100, height=100, z_index=2)  # 1px overlap
    c = LayoutElement(id="c", left=200, top=0, width=100, height=100, z_index=3)
    assert _aabb_overlap(a, b) is True
    assert _aabb_overlap(a, c) is False


# ============================================================
# 5. position_preference band tolerance (step 10c, 2026-05-16)
# ============================================================


def test_position_band_tolerance_constants_pinned():
    """Constants drive the calibration; pin them so accidental tightening
    requires deliberate test edits and triggers code review."""
    from metagpt.ext.agentlayout.tools.quality_checker import (
        POSITION_BAND_TOLERANCE,
        POSITION_BAND_TOLERANCE_ABSOLUTE_FLOOR,
    )

    assert POSITION_BAND_TOLERANCE == 0.10
    assert POSITION_BAND_TOLERANCE_ABSOLUTE_FLOOR == 16


def _position_spec(width: int, height: int, hint: str = "center"):
    """Single-element spec with a single position_preference constraint, used
    by the band-tolerance boundary tests below."""
    from metagpt.ext.agentlayout.schema import (
        Canvas,
        DesignSpec,
        Element,
        HardConstraint,
        HardConstraintRule,
        SemanticType,
        VisualType,
    )

    return DesignSpec(
        canvas=Canvas(width=width, height=height),
        elements=[
            Element(
                id="text_1",
                semantic_type=SemanticType.TITLE,
                visual_type=VisualType.TEXT,
                content="hi",
                importance=5,
                semantic_relevance=0.5,
            ),
        ],
        hard_constraints=[
            HardConstraint(
                rule=HardConstraintRule.POSITION_PREFERENCE,
                targets=["text_1"],
                params={"hint": hint},
            )
        ],
        soft_constraints=[],
        style_keywords=[],
        language="en",
        inferred_fields={},
    )


def _mk_text_candidate(left: int, top: int, width: int, height: int):
    from metagpt.ext.agentlayout.schema import Candidate, LayoutElement

    return Candidate(
        candidate_id="t",
        elements=[
            LayoutElement(
                id="text_1",
                left=left,
                top=top,
                width=width,
                height=height,
                z_index=3,
                font_family="sans-serif",
                font_size=36,
                color="#111111",
            )
        ],
    )


def test_position_band_tolerance_unblocks_live8_layout():
    """Live #8 (1200x600, hint=center): the LLM repeatedly placed text_1 at
    center y=450 (50px past the strict 400 boundary). With 10% tolerance the
    band runs y in [140, 460], so y=450 must now PASS. This is the *exact*
    case that motivated step 10c."""
    from metagpt.ext.agentlayout.tools.quality_checker import check_candidate

    spec = _position_spec(1200, 600, hint="center")
    cand = _mk_text_candidate(left=100, top=350, width=1000, height=200)  # cy=450
    result = check_candidate(cand, spec)
    pp = [v for v in result.violations if v.type.value == "position_preference"]
    assert pp == [], f"text_1 cy=450 within 10% tolerance must PASS: {pp}"


def test_position_band_tolerance_just_inside_boundary_passes():
    """Boundary-just-inside: y=460 = 2*third + tol on 600 canvas (60px). MUST
    pass; otherwise the boundary inclusivity has silently regressed."""
    from metagpt.ext.agentlayout.tools.quality_checker import check_candidate

    spec = _position_spec(1200, 600, hint="center")
    cand = _mk_text_candidate(left=100, top=360, width=1000, height=200)  # cy=460
    result = check_candidate(cand, spec)
    pp = [v for v in result.violations if v.type.value == "position_preference"]
    assert pp == [], f"text_1 cy=460 (= 400 + 10% of 600) must PASS: {pp}"


def test_position_band_tolerance_just_outside_boundary_fails():
    """y=470 is 70px past the strict edge -> beyond the 60px tolerance ->
    must FAIL. Otherwise tolerance would be unbounded."""
    from metagpt.ext.agentlayout.tools.quality_checker import check_candidate

    spec = _position_spec(1200, 600, hint="center")
    cand = _mk_text_candidate(left=100, top=370, width=1000, height=200)  # cy=470
    result = check_candidate(cand, spec)
    pp = [v for v in result.violations if v.type.value == "position_preference"]
    assert len(pp) == 1
    detail = pp[0].detail
    assert "tolerance 10%" in detail
    assert "accepted" in detail and "[140" in detail and "460]" in detail


def test_position_band_tolerance_canonical_center_still_passes():
    """Regression: a candidate that *was* passing under the strict rule
    (text dead-center, cy=300 on 600 canvas) must keep passing. Step 10c is
    a strict relaxation; nothing that was OK before may break."""
    from metagpt.ext.agentlayout.tools.quality_checker import check_candidate

    spec = _position_spec(1200, 600, hint="center")
    cand = _mk_text_candidate(left=400, top=240, width=400, height=120)  # cy=300
    result = check_candidate(cand, spec)
    pp = [v for v in result.violations if v.type.value == "position_preference"]
    assert pp == [], f"dead-center placement must still PASS: {pp}"


def test_position_band_tolerance_top_left_still_rejects_far_misses():
    """Hint=top_left (band (0,0)) on 1200x600: a center at (1000, 500) is
    nowhere near the upper-left third even with tolerance, must FAIL.
    Defends against tolerance accidentally being applied as an additive
    union of all bands."""
    from metagpt.ext.agentlayout.tools.quality_checker import check_candidate

    spec = _position_spec(1200, 600, hint="top_left")
    cand = _mk_text_candidate(left=900, top=400, width=200, height=200)  # c=(1000, 500)
    result = check_candidate(cand, spec)
    pp = [v for v in result.violations if v.type.value == "position_preference"]
    assert len(pp) == 1


def test_position_band_tolerance_floor_protects_tiny_canvas():
    """On a 100x100 fixture the 10% relative tolerance would be only 10px,
    smaller than the 16px absolute floor. The floor must apply: with hint
    'center' (band 1, strict y in [33, 66]) a candidate at cy=80 (= 66 + 14
    < 16) must PASS thanks to the floor."""
    from metagpt.ext.agentlayout.tools.quality_checker import check_candidate

    spec = _position_spec(100, 100, hint="center")
    cand = _mk_text_candidate(left=30, top=70, width=40, height=20)  # cy=80
    result = check_candidate(cand, spec)
    pp = [v for v in result.violations if v.type.value == "position_preference"]
    assert pp == [], f"floor must let cy=80 (within 16px of strict edge 66.7): {pp}"


# ============================================================
# 5. z_order semantic-hint resolution (step 12, 2026-05-19)
#
# The first real content-aware live run (Crello 5efdd2dd) hard-crashed
# "0 candidates passed QC after 3 top-up round(s)": a background element only
# exists in content-aware mode, so only then does the Analyst emit a z_order
# constraint -- as the semantic form params={"hint": "above_background"} --
# while _check_z_order historically required params={"above": <id>} and raised
# UNKNOWN_HINT on every candidate. The fix accepts the semantic hint, resolves
# the reference via SemanticType.BACKGROUND_IMAGE (spec threaded in), and
# skips gracefully when there is no background. These tests pin both the new
# behaviour and strict back-compat with the explicit-param form.
# ============================================================


def _zorder_spec(hint=None, above=None, with_bg=True):
    """Spec with a foreground TITLE 'fg_1' plus (optionally) a BACKGROUND_IMAGE
    'bg_1', sharing one z_order hard constraint. Pass either ``hint`` (semantic
    form) or ``above`` (legacy explicit form)."""
    from metagpt.ext.agentlayout.schema import (
        Canvas,
        DesignSpec,
        Element,
        HardConstraint,
        HardConstraintRule,
        SemanticType,
        VisualType,
    )

    elements = [
        Element(
            id="fg_1",
            semantic_type=SemanticType.TITLE,
            visual_type=VisualType.TEXT,
            content="Hello",
            importance=5,
            semantic_relevance=0.9,
        ),
    ]
    if with_bg:
        elements.append(
            Element(
                id="bg_1",
                semantic_type=SemanticType.BACKGROUND_IMAGE,
                visual_type=VisualType.IMAGE,
                asset_ref="/tmp/bg.png",
                importance=1,
                semantic_relevance=0.5,
            )
        )
    params = {}
    if hint is not None:
        params["hint"] = hint
    if above is not None:
        params["above"] = above
    return DesignSpec(
        canvas=Canvas(width=600, height=600),
        elements=elements,
        hard_constraints=[
            HardConstraint(
                rule=HardConstraintRule.Z_ORDER, targets=["fg_1"], params=params
            )
        ],
        soft_constraints=[],
        style_keywords=[],
        language="en",
        inferred_fields={},
    )


def _mk_zorder_candidate(fg_z, bg_z=None):
    """Candidate with fg_1 (and bg_1 when bg_z is given), all in-bounds so only
    the z_order rule can fire. fg_1 spans most of the canvas so the Step 57
    coverage / dead-band guardrails stay quiet (the old 200x80 sliver was a
    genuinely degenerate layout that the new rules correctly flag)."""
    from metagpt.ext.agentlayout.schema import Candidate, LayoutElement

    els = [LayoutElement(id="fg_1", left=50, top=50, width=500, height=500, z_index=fg_z)]
    if bg_z is not None:
        els.append(LayoutElement(id="bg_1", left=0, top=0, width=600, height=600, z_index=bg_z))
    return Candidate(candidate_id="zt", elements=els)


def test_z_order_accepted_hint_set_is_pinned():
    """Pin the accepted-hint frozenset so silently dropping the canonical
    'above_background' token (which would re-introduce the crash) is caught."""
    from metagpt.ext.agentlayout.tools.quality_checker import (
        Z_ORDER_ABOVE_BACKGROUND_HINTS,
    )

    assert "above_background" in Z_ORDER_ABOVE_BACKGROUND_HINTS
    assert Z_ORDER_ABOVE_BACKGROUND_HINTS == frozenset(
        {
            "above_background",
            "above_bg",
            "over_background",
            "above_the_background",
            "front_of_background",
        }
    )


def test_z_order_legacy_explicit_above_param_still_passes():
    """Back-compat: the historical params={'above': <id>} form is unchanged."""
    from metagpt.ext.agentlayout.tools.quality_checker import check_candidate

    spec = _zorder_spec(above="bg_1")
    cand = _mk_zorder_candidate(fg_z=2, bg_z=1)  # fg strictly above bg
    out = check_candidate(cand, spec)
    assert [v for v in out.violations if v.type.value in ("z_order", "unknown_hint")] == []


def test_z_order_legacy_explicit_below_still_fails():
    """Back-compat: explicit form still flags fg not strictly above ref."""
    from metagpt.ext.agentlayout.tools.quality_checker import check_candidate

    spec = _zorder_spec(above="bg_1")
    cand = _mk_zorder_candidate(fg_z=1, bg_z=3)
    zo = [v for v in check_candidate(cand, spec).violations if v.type.value == "z_order"]
    assert len(zo) == 1


def test_z_order_legacy_explicit_missing_reference_still_unknown_target():
    """Author-supplied 'above' id absent from candidate is a real authoring
    error and must still surface as UNKNOWN_TARGET (not a graceful skip)."""
    from metagpt.ext.agentlayout.tools.quality_checker import check_candidate

    spec = _zorder_spec(above="ghost", with_bg=False)
    cand = _mk_zorder_candidate(fg_z=2)
    ut = [v for v in check_candidate(cand, spec).violations if v.type.value == "unknown_target"]
    assert len(ut) == 1
    assert "ghost" in ut[0].detail


def test_z_order_hint_above_background_resolves_and_passes():
    """The live failure mode: hint 'above_background' resolves the reference
    via SemanticType.BACKGROUND_IMAGE and passes when z ordering is correct."""
    from metagpt.ext.agentlayout.tools.quality_checker import check_candidate

    spec = _zorder_spec(hint="above_background")
    cand = _mk_zorder_candidate(fg_z=5, bg_z=0)
    out = check_candidate(cand, spec)
    assert [v for v in out.violations if v.type.value in ("z_order", "unknown_hint")] == []


def test_z_order_hint_foreground_below_background_fails():
    """Hint path still enforces the real geometric rule: fg z_index <= bg
    z_index is a Z_ORDER violation referencing the resolved bg id."""
    from metagpt.ext.agentlayout.tools.quality_checker import check_candidate

    spec = _zorder_spec(hint="above_background")
    cand = _mk_zorder_candidate(fg_z=0, bg_z=3)
    zo = [v for v in check_candidate(cand, spec).violations if v.type.value == "z_order"]
    assert len(zo) == 1
    assert "bg_1" in zo[0].detail


def test_z_order_hint_no_background_element_skips_gracefully():
    """'Above the background' is vacuously satisfied with no background element
    -- must NOT emit a violation (that would re-create the 0-pass crash)."""
    from metagpt.ext.agentlayout.tools.quality_checker import check_candidate

    spec = _zorder_spec(hint="above_background", with_bg=False)
    cand = _mk_zorder_candidate(fg_z=1)  # only fg_1, matches spec.elements
    out = check_candidate(cand, spec)
    assert out.passed, f"no-background z_order hint must skip cleanly: {out.violations}"


def test_z_order_garbage_hint_still_unknown_hint():
    """An unrecognised non-empty hint must still raise UNKNOWN_HINT so genuine
    malformed constraints are not silently swallowed."""
    from metagpt.ext.agentlayout.tools.quality_checker import check_candidate

    spec = _zorder_spec(hint="to_the_left_a_bit", with_bg=False)
    cand = _mk_zorder_candidate(fg_z=1)
    uh = [v for v in check_candidate(cand, spec).violations if v.type.value == "unknown_hint"]
    assert len(uh) == 1
    assert "to_the_left_a_bit" in uh[0].detail


def test_z_order_missing_both_above_and_hint_unknown_hint():
    """params={} (neither 'above' nor 'hint') is a malformed constraint."""
    from metagpt.ext.agentlayout.tools.quality_checker import check_candidate

    spec = _zorder_spec(with_bg=False)  # params stays {}
    cand = _mk_zorder_candidate(fg_z=1)
    uh = [v for v in check_candidate(cand, spec).violations if v.type.value == "unknown_hint"]
    assert len(uh) == 1
    assert "missing both 'above' param and 'hint'" in uh[0].detail


@pytest.mark.parametrize(
    "hint",
    [
        "above_background",
        "above_bg",
        "over_background",
        "above_the_background",
        "front_of_background",
        "Above Background",  # case + space normalisation
        "above-bg",  # dash normalisation
    ],
)
def test_z_order_hint_variants_all_resolve(hint):
    """Every accepted token plus dash/space/case variants resolves to the bg
    path (no UNKNOWN_HINT) and validates correct z ordering."""
    from metagpt.ext.agentlayout.tools.quality_checker import check_candidate

    spec = _zorder_spec(hint=hint)
    cand = _mk_zorder_candidate(fg_z=9, bg_z=0)
    out = check_candidate(cand, spec)
    assert [v for v in out.violations if v.type.value in ("z_order", "unknown_hint")] == [], (
        f"hint {hint!r} should resolve to background and pass: {out.violations}"
    )


def test_z_order_live_5efdd2dd_reproduction_unblocks():
    """Crash canary: the exact live constraint
    {'rule':'z_order','targets':['image_1','text_1'],'params':{'hint':'above_background'}}
    on a 3-element content-aware spec must yield a passing candidate (the
    original RuntimeError can never recur for this shape)."""
    from metagpt.ext.agentlayout.schema import (
        Candidate,
        Canvas,
        DesignSpec,
        Element,
        HardConstraint,
        HardConstraintRule,
        LayoutElement,
        SemanticType,
        VisualType,
    )
    from metagpt.ext.agentlayout.tools.quality_checker import check_candidate

    spec = DesignSpec(
        canvas=Canvas(width=1008, height=1296),
        elements=[
            Element(
                id="bg_1",
                semantic_type=SemanticType.BACKGROUND_IMAGE,
                visual_type=VisualType.IMAGE,
                asset_ref="/tmp/asset_00_image.png",
                importance=1,
                semantic_relevance=0.5,
            ),
            Element(
                id="image_1",
                semantic_type=SemanticType.DECORATIVE_IMAGE,
                visual_type=VisualType.IMAGE,
                asset_ref="/tmp/asset_01_image.png",
                importance=3,
                semantic_relevance=0.7,
            ),
            Element(
                id="text_1",
                semantic_type=SemanticType.TITLE,
                visual_type=VisualType.TEXT,
                content="LAUNDRY",
                importance=5,
                semantic_relevance=0.9,
            ),
        ],
        hard_constraints=[
            HardConstraint(
                rule=HardConstraintRule.Z_ORDER,
                targets=["image_1", "text_1"],
                params={"hint": "above_background"},
            ),
        ],
        soft_constraints=[],
        style_keywords=[],
        language="en",
        inferred_fields={},
    )
    candidate = Candidate(
        candidate_id="cand_01",
        elements=[
            LayoutElement(id="bg_1", left=0, top=0, width=1008, height=1296, z_index=1),
            LayoutElement(id="image_1", left=100, top=100, width=808, height=500, z_index=2),
            LayoutElement(
                id="text_1",
                left=336,
                top=520,
                width=336,
                height=260,
                z_index=3,
                font_family="sans-serif",
                font_size=48,
                font_weight="bold",
                # Step 35 (2026-06-09): contrast rule added to QC; the
                # original test used #F4F4F4 (~near-white) against the
                # default #FFFFFF canvas bg, which now correctly fails
                # LOW_TEXT_CONTRAST. Change to a dark colour so this
                # z_order regression test stays focused on the original
                # crash signature.
                color="#111111",
                text_align="center",
            ),
        ],
    )

    result = check_candidate(candidate, spec)
    assert result.passed, f"live 5efdd2dd z_order shape must no longer crash QC: {result.violations}"


# ============================================================
# 9. Graceful degradation -- rank_candidates_by_violations (step 10b)
# ============================================================


def _make_report(cid: str, n_violations: int):
    """Build a CheckResult with ``n_violations`` dummy UNKNOWN_HINT entries."""
    from metagpt.ext.agentlayout.tools.quality_checker import (
        CheckResult,
        Violation,
        ViolationType,
    )

    return CheckResult(
        candidate_id=cid,
        passed=n_violations == 0,
        violations=[
            Violation(type=ViolationType.UNKNOWN_HINT, targets=["x"], detail="d")
            for _ in range(n_violations)
        ],
    )


def test_rank_candidates_by_violations_orders_fewest_first():
    """The degradation fallback must surface the least-broken layouts first
    so the Aesthetic Judge scores the best available, not arbitrary ones."""
    from metagpt.ext.agentlayout.schema import Candidate, LayoutElement
    from metagpt.ext.agentlayout.tools.quality_checker import rank_candidates_by_violations

    def _c(cid):
        return Candidate(
            candidate_id=cid,
            elements=[LayoutElement(id="e", left=0, top=0, width=10, height=10, z_index=1)],
        )

    cands = [_c("a"), _c("b"), _c("c")]
    reports = [_make_report("a", 3), _make_report("b", 1), _make_report("c", 2)]

    ordered = rank_candidates_by_violations(cands, reports)
    assert [c.candidate_id for c in ordered] == ["b", "c", "a"]


def test_rank_candidates_by_violations_is_stable_on_ties():
    """The step 10b crash signature: every candidate fails the *same* single
    out-of-vocabulary hint, so violation counts tie at 1. Ranking must be
    stable (insertion order preserved) and return a non-empty fallback so the
    run continues instead of raising RuntimeError."""
    from metagpt.ext.agentlayout.schema import Candidate, LayoutElement
    from metagpt.ext.agentlayout.tools.quality_checker import rank_candidates_by_violations

    ids = [f"r0_cand_{i}" for i in range(5)]
    cands = [
        Candidate(
            candidate_id=i,
            elements=[LayoutElement(id="e", left=0, top=0, width=10, height=10, z_index=1)],
        )
        for i in ids
    ]
    reports = [_make_report(i, 1) for i in ids]  # all tie at 1 (unknown_hint)

    ordered = rank_candidates_by_violations(cands, reports)
    assert [c.candidate_id for c in ordered] == ids  # stable, non-empty
    assert ordered[:5], "degradation fallback must never be empty when candidates exist"


def test_below_title_hint_crashes_filter_valid_but_degradation_survives():
    """End-to-end step 10b reproduction: a spec carrying the relational
    ``below_title`` hint makes filter_valid drop every candidate (UNKNOWN_HINT),
    which previously raised RuntimeError and aborted the whole run. After the
    fix, rank_candidates_by_violations still returns a usable fallback set."""
    from metagpt.ext.agentlayout.schema import (
        Candidate,
        Canvas,
        DesignSpec,
        Element,
        HardConstraint,
        HardConstraintRule,
        LayoutElement,
        SemanticType,
        VisualType,
    )
    from metagpt.ext.agentlayout.tools.quality_checker import (
        filter_valid,
        rank_candidates_by_violations,
    )

    spec = DesignSpec(
        canvas=Canvas(width=537, height=240),
        elements=[
            Element(
                id="subtitle_1",
                semantic_type=SemanticType.SUBTITLE,
                visual_type=VisualType.TEXT,
                content="winter trips",
                importance=3,
                semantic_relevance=0.5,
            ),
        ],
        hard_constraints=[
            HardConstraint(
                rule=HardConstraintRule.POSITION_PREFERENCE,
                targets=["subtitle_1"],
                params={"hint": "below_title"},  # the exact crash hint
            ),
        ],
        soft_constraints=[],
        style_keywords=[],
        language="ru",
        inferred_fields={},
    )
    cands = [
        Candidate(
            candidate_id=f"r0_cand_{i}",
            elements=[
                LayoutElement(
                    id="subtitle_1", left=160, top=80 + i, width=217, height=40,
                    z_index=2, font_family="sans-serif", font_size=14,
                    font_weight="normal", color="#111111", text_align="center",
                ),
            ],
        )
        for i in range(5)
    ]

    kept, reports = filter_valid(cands, spec)
    assert kept == [], "below_title must still be UNKNOWN_HINT (root-cause unchanged)"

    degraded = rank_candidates_by_violations(cands, reports)
    assert len(degraded) == 5, "degradation must keep the run alive (step 10b fix)"


# ============================================================
# 10. Step 35 visual-quality rules (text obscured + low contrast)
# ============================================================


def _step35_spec(text_color: str = "#111111", bg_color: str = "#FFFFFF"):
    """Tiny 2-element spec used by the Step 35 rule tests below."""
    from metagpt.ext.agentlayout.schema import (
        Canvas,
        DesignSpec,
        Element,
        SemanticType,
        VisualType,
    )

    return DesignSpec(
        canvas=Canvas(width=800, height=600, background_color=bg_color),
        elements=[
            Element(
                id="title_1",
                semantic_type=SemanticType.TITLE,
                visual_type=VisualType.TEXT,
                content="HELLO",
                importance=5,
                semantic_relevance=0.9,
            ),
            Element(
                id="decor_1",
                semantic_type=SemanticType.DECORATIVE_IMAGE,
                visual_type=VisualType.IMAGE,
                asset_ref="/tmp/decor.png",
                importance=2,
                semantic_relevance=0.5,
            ),
        ],
        hard_constraints=[],
        soft_constraints=[],
        style_keywords=[],
        language="en",
        inferred_fields={},
    )


def _step35_candidate(
    decor_z: int,
    text_color: str = "#111111",
    decor_overlap: bool = True,
):
    """Build a candidate where decor either overlaps title 50% or sits aside."""
    from metagpt.ext.agentlayout.schema import Candidate, LayoutElement

    title = LayoutElement(
        id="title_1",
        left=200,
        top=200,
        width=400,
        height=100,
        z_index=3,
        font_family="sans-serif",
        font_size=48,
        font_weight="bold",
        color=text_color,
        text_align="center",
    )
    if decor_overlap:
        decor = LayoutElement(
            id="decor_1", left=300, top=200, width=400, height=100, z_index=decor_z
        )
    else:
        decor = LayoutElement(
            id="decor_1", left=10, top=10, width=80, height=80, z_index=decor_z
        )
    return Candidate(candidate_id="cand_step35", elements=[title, decor])


def test_step35_text_obscured_by_overlay_flags_higher_z_overlap():
    """A decorative_image with z_index > text z_index that covers >= 30% of
    the text bbox triggers TEXT_OBSCURED_BY_OVERLAY. Catches the Step 34
    589d7bd9 'F.YD' failure mode where a mountain shape sat over the title."""
    from metagpt.ext.agentlayout.tools.quality_checker import (
        ViolationType,
        check_candidate,
    )

    spec = _step35_spec()
    cand = _step35_candidate(decor_z=5, decor_overlap=True)  # decor above title
    result = check_candidate(cand, spec)
    violations = [v for v in result.violations if v.type == ViolationType.TEXT_OBSCURED_BY_OVERLAY]
    assert len(violations) == 1, f"expected 1 obscured violation, got {result.violations}"
    assert set(violations[0].targets) == {"title_1", "decor_1"}


def test_step35_text_obscured_does_not_flag_lower_z_overlay():
    """A decorative_image with z_index < text z_index is the *intended*
    underlay-anchor pattern (decoration sits BEHIND text). Must NOT trigger
    TEXT_OBSCURED_BY_OVERLAY even with 100% overlap."""
    from metagpt.ext.agentlayout.tools.quality_checker import (
        ViolationType,
        check_candidate,
    )

    spec = _step35_spec()
    cand = _step35_candidate(decor_z=1, decor_overlap=True)  # decor below title
    result = check_candidate(cand, spec)
    obscured = [v for v in result.violations if v.type == ViolationType.TEXT_OBSCURED_BY_OVERLAY]
    assert obscured == [], f"underlay-below-text must NOT flag, got {obscured}"


def test_step35_low_text_contrast_flags_near_white_on_white():
    """Light gray text on white canvas falls under WCAG AA (4.5). Catches the
    Step 34 Generator failure mode where text colour drifts toward the bg."""
    from metagpt.ext.agentlayout.tools.quality_checker import (
        ViolationType,
        check_candidate,
    )

    spec = _step35_spec(bg_color="#FFFFFF")
    cand = _step35_candidate(decor_z=1, text_color="#F4F4F4", decor_overlap=False)
    result = check_candidate(cand, spec)
    contrast = [v for v in result.violations if v.type == ViolationType.LOW_TEXT_CONTRAST]
    assert len(contrast) == 1, f"expected 1 contrast violation, got {result.violations}"
    assert contrast[0].targets == ["title_1"]


def test_step35_high_text_contrast_passes():
    """Dark text on white canvas easily exceeds 4.5 -- must NOT flag."""
    from metagpt.ext.agentlayout.tools.quality_checker import (
        ViolationType,
        check_candidate,
    )

    spec = _step35_spec(bg_color="#FFFFFF")
    cand = _step35_candidate(decor_z=1, text_color="#111111", decor_overlap=False)
    result = check_candidate(cand, spec)
    contrast = [v for v in result.violations if v.type == ViolationType.LOW_TEXT_CONTRAST]
    assert contrast == [], f"high-contrast text must pass, got {contrast}"


# ============================================================
# 11. Step 36 visual-quality rules (oversized decor + tiny title + peripheral title)
# ============================================================


def _step36_spec_with_title_decor():
    """Minimal 2-element spec for Step 36: one title + one decorative_image."""
    from metagpt.ext.agentlayout.schema import (
        Canvas,
        DesignSpec,
        Element,
        SemanticType,
        VisualType,
    )

    return DesignSpec(
        canvas=Canvas(width=1000, height=1000),
        elements=[
            Element(
                id="title_1",
                semantic_type=SemanticType.TITLE,
                visual_type=VisualType.TEXT,
                content="HELLO",
                importance=5,
                semantic_relevance=0.9,
            ),
            Element(
                id="underlay_1",
                semantic_type=SemanticType.DECORATIVE_IMAGE,
                visual_type=VisualType.IMAGE,
                asset_ref="/tmp/underlay.png",
                importance=2,
                semantic_relevance=0.4,
            ),
        ],
        hard_constraints=[],
        soft_constraints=[],
        style_keywords=[],
        language="en",
        inferred_fields={},
    )


def _step36_candidate(
    title_left=200,
    title_top=200,
    title_w=400,
    title_h=200,
    decor_w=300,
    decor_h=300,
    decor_left=600,
    decor_top=600,
    title_color="#111111",
):
    from metagpt.ext.agentlayout.schema import Candidate, LayoutElement

    return Candidate(
        candidate_id="cand_step36",
        elements=[
            LayoutElement(
                id="title_1",
                left=title_left,
                top=title_top,
                width=title_w,
                height=title_h,
                z_index=3,
                font_family="sans-serif",
                font_size=48,
                font_weight="bold",
                color=title_color,
                text_align="center",
            ),
            LayoutElement(
                id="underlay_1",
                left=decor_left,
                top=decor_top,
                width=decor_w,
                height=decor_h,
                z_index=1,
            ),
        ],
    )


def test_step36_decorative_image_oversized_flags_huge_underlay():
    """A decorative_image covering > 40% of the canvas triggers
    DECORATIVE_IMAGE_OVERSIZED. Replicates the dominant Step 34 N=20 failure
    mode (7/17 samples) where Generator inflated an underlay to dwarf the
    main visual."""
    from metagpt.ext.agentlayout.tools.quality_checker import (
        ViolationType,
        check_candidate,
    )

    spec = _step36_spec_with_title_decor()
    # 700x700 = 49% canvas area > 40% threshold
    cand = _step36_candidate(decor_w=700, decor_h=700, decor_left=0, decor_top=0)
    result = check_candidate(cand, spec)
    over = [v for v in result.violations if v.type == ViolationType.DECORATIVE_IMAGE_OVERSIZED]
    assert len(over) == 1, f"expected 1 oversized violation, got {result.violations}"
    assert over[0].targets == ["underlay_1"]


def test_step36_decorative_image_modest_size_passes():
    """A decorative_image at ~10% canvas is fine -- intended underlay use."""
    from metagpt.ext.agentlayout.tools.quality_checker import (
        ViolationType,
        check_candidate,
    )

    spec = _step36_spec_with_title_decor()
    # 300x300 = 9% canvas area
    cand = _step36_candidate(decor_w=300, decor_h=300)
    result = check_candidate(cand, spec)
    over = [v for v in result.violations if v.type == ViolationType.DECORATIVE_IMAGE_OVERSIZED]
    assert over == [], f"modest underlay must pass, got {over}"


def test_step36_title_undersized_flags_tiny_title():
    """A title < 2.5% canvas area triggers TITLE_UNDERSIZED. Catches the
    Step 34 5fbf 'ECUTER' / 5dad 'GreenKO' tiny-corner failures."""
    from metagpt.ext.agentlayout.tools.quality_checker import (
        ViolationType,
        check_candidate,
    )

    spec = _step36_spec_with_title_decor()
    # 100x100 = 1% canvas area < 2.5% threshold
    cand = _step36_candidate(title_w=100, title_h=100)
    result = check_candidate(cand, spec)
    tiny = [v for v in result.violations if v.type == ViolationType.TITLE_UNDERSIZED]
    assert len(tiny) == 1, f"expected 1 undersized violation, got {result.violations}"
    assert tiny[0].targets == ["title_1"]


def test_step36_title_reasonable_size_passes():
    """A title at ~8% canvas easily clears 2.5%."""
    from metagpt.ext.agentlayout.tools.quality_checker import (
        ViolationType,
        check_candidate,
    )

    spec = _step36_spec_with_title_decor()
    # 400x200 = 8% canvas area
    cand = _step36_candidate(title_w=400, title_h=200)
    result = check_candidate(cand, spec)
    tiny = [v for v in result.violations if v.type == ViolationType.TITLE_UNDERSIZED]
    assert tiny == [], f"reasonable-size title must pass, got {tiny}"


def test_step36_title_peripheral_flags_edge_placement():
    """A title whose center sits in the right-edge band triggers
    TITLE_PERIPHERAL. Catches Step 34 placements like '5f4f Limited time
    offer' at the top-left corner."""
    from metagpt.ext.agentlayout.tools.quality_checker import (
        ViolationType,
        check_candidate,
    )

    spec = _step36_spec_with_title_decor()
    # center_x = (920 + 60/2) / 1000 = 0.95 > 0.90 → peripheral
    cand = _step36_candidate(
        title_left=920, title_top=400, title_w=60, title_h=200
    )
    result = check_candidate(cand, spec)
    peri = [v for v in result.violations if v.type == ViolationType.TITLE_PERIPHERAL]
    assert len(peri) == 1, f"expected 1 peripheral violation, got {result.violations}"
    assert peri[0].targets == ["title_1"]


def test_step36_title_central_band_passes():
    """A title centred horizontally and in the upper-middle vertical band is
    the canonical hero placement -- must NOT flag."""
    from metagpt.ext.agentlayout.tools.quality_checker import (
        ViolationType,
        check_candidate,
    )

    spec = _step36_spec_with_title_decor()
    # center = (500, 400) → x=0.5 (in band), y=0.4 (<0.85)
    cand = _step36_candidate(title_left=300, title_top=300, title_w=400, title_h=200)
    result = check_candidate(cand, spec)
    peri = [v for v in result.violations if v.type == ViolationType.TITLE_PERIPHERAL]
    assert peri == [], f"central-band title must pass, got {peri}"


def test_step36c_title_pinned_to_top_edge_flags():
    """Step 36c (2026-06-09): titles pinned to the absolute top edge
    (center_y < 0.05) trigger TITLE_PERIPHERAL. Catches the 5dad GreenKO
    failure mode under Step 36 where the leaf underlay correctly shrank
    but the title still landed in the upper-right corner (top edge)."""
    from metagpt.ext.agentlayout.tools.quality_checker import (
        ViolationType,
        check_candidate,
    )

    spec = _step36_spec_with_title_decor()
    # center = (500, 25) → x=0.5 (in band), y=0.025 (<0.05)
    cand = _step36_candidate(title_left=300, title_top=0, title_w=400, title_h=50)
    result = check_candidate(cand, spec)
    peri = [v for v in result.violations if v.type == ViolationType.TITLE_PERIPHERAL]
    assert len(peri) == 1, f"top-edge title must flag peripheral, got {result.violations}"
    assert peri[0].targets == ["title_1"]


# ============================================================
# 12. Step 43 PRIMARY_OUTSIDE_SAFE_ZONE
# ============================================================


def _step43_spec_and_candidate(
    title_left: int = 0,
    title_top: int = 100,
    title_w: int = 400,
    title_h: int = 200,
):
    from metagpt.ext.agentlayout.schema import (
        BackgroundAnalysis,
        Candidate,
        Canvas,
        DesignSpec,
        Element,
        LayoutElement,
        SafeZone,
        SemanticType,
        VisualType,
    )

    spec = DesignSpec(
        canvas=Canvas(width=1000, height=1000),
        elements=[
            Element(
                id="title_1",
                semantic_type=SemanticType.TITLE,
                visual_type=VisualType.TEXT,
                content="HELLO",
                importance=5,
                semantic_relevance=0.9,
            ),
        ],
        hard_constraints=[],
        soft_constraints=[],
        style_keywords=[],
        language="en",
        inferred_fields={},
    )
    cand = Candidate(
        candidate_id="cand_step43",
        elements=[
            LayoutElement(
                id="title_1",
                left=title_left,
                top=title_top,
                width=title_w,
                height=title_h,
                z_index=3,
                font_family="sans-serif",
                font_size=48,
                font_weight="bold",
                color="#111111",
                text_align="center",
            ),
        ],
    )
    # One safe zone occupying the left half of the canvas. SafeZone.bbox
    # is LTRB (left, top, right, bottom), not LTWH; for the left-half
    # zone on a 1000x1000 canvas that means right=500, bottom=1000.
    bg = BackgroundAnalysis(
        safe_zones=[SafeZone(region="r1c0", bbox=[0, 0, 500, 1000], confidence=0.9)],
        dominant_palette=["#FFFFFF"],
        recommended_text_color="#111111",
    )
    return spec, cand, bg


def test_step43_primary_inside_safe_zone_passes():
    """Step 43: a title that sits entirely inside a safe_zone must NOT trigger
    PRIMARY_OUTSIDE_SAFE_ZONE."""
    from metagpt.ext.agentlayout.tools.quality_checker import (
        ViolationType,
        check_candidate,
    )

    # title at (100, 100, 300x300) fully inside r1c0 = (0, 0, 500x1000)
    spec, cand, bg = _step43_spec_and_candidate(
        title_left=100, title_top=100, title_w=300, title_h=300
    )
    result = check_candidate(cand, spec, bg=bg)
    out = [v for v in result.violations if v.type == ViolationType.PRIMARY_OUTSIDE_SAFE_ZONE]
    assert out == [], f"in-zone title must pass; got {out}"


def test_step43_primary_outside_safe_zone_flags():
    """Step 43: a title centred in the saliency-high region (no safe-zone
    overlap) MUST trigger PRIMARY_OUTSIDE_SAFE_ZONE. Replicates the 5928
    'title centred at x=500-950 outside any safe_zone' failure observed
    under Step 42 before this rule was wired in."""
    from metagpt.ext.agentlayout.tools.quality_checker import (
        ViolationType,
        check_candidate,
    )

    # title at (600, 400, 300x200): entirely outside r1c0 (x in [0,500])
    spec, cand, bg = _step43_spec_and_candidate(
        title_left=600, title_top=400, title_w=300, title_h=200
    )
    result = check_candidate(cand, spec, bg=bg)
    out = [v for v in result.violations if v.type == ViolationType.PRIMARY_OUTSIDE_SAFE_ZONE]
    assert len(out) == 1, f"outside-zone title must flag; got {result.violations}"
    assert out[0].targets == ["title_1"]


def test_step43_skipped_when_bg_is_none():
    """Step 43: callers that do not resolve a BackgroundAnalysis (legacy
    tests, scripts) keep the default bg=None and the new rule is
    silently inert -- preserving backward compatibility."""
    from metagpt.ext.agentlayout.tools.quality_checker import (
        ViolationType,
        check_candidate,
    )

    spec, cand, _bg = _step43_spec_and_candidate(
        title_left=600, title_top=400, title_w=300, title_h=200
    )
    result = check_candidate(cand, spec)  # no bg passed
    out = [v for v in result.violations if v.type == ViolationType.PRIMARY_OUTSIDE_SAFE_ZONE]
    assert out == [], f"bg=None must skip the rule; got {out}"


def test_step67_filter_valid_forwards_bg_to_safe_zone_rule():
    """Step 67 (2026-06-14): pre-fix, ``filter_valid`` silently dropped its
    background-analysis argument because the signature was
    ``filter_valid(candidates, spec)``. Both production call sites
    (``pipeline._generate_with_topup`` and
    ``LayoutGeneratorRole._generate_with_topup``) had a resolved ``bg`` in
    scope but had no way to forward it -- so the Step 43
    PRIMARY_OUTSIDE_SAFE_ZONE rule never fired in the live pipeline on
    non-composition specs.

    This regression test pins the post-fix contract: a candidate that
    sits outside every safe_zone must be dropped from the kept list
    *when bg is passed*. ``spec.composition`` is left as None so the
    Step 63 deference short-circuit does not mask the regression.
    """
    from metagpt.ext.agentlayout.tools.quality_checker import filter_valid

    # Title sized to satisfy other QC rules (Step 57 coverage >=10%,
    # Step 36 title size >=8% etc.) and positioned ENTIRELY outside the
    # left-half safe_zone r1c0=[0,0,500,1000]. 500x300 at (500,350) =
    # 150_000 px^2 = 15% of a 1000x1000 canvas.
    spec, cand, bg = _step43_spec_and_candidate(
        title_left=500, title_top=350, title_w=500, title_h=300
    )

    # Without bg: safe-zone rule is inert, candidate is kept.
    kept_no_bg, reports_no_bg = filter_valid([cand], spec)
    assert kept_no_bg == [cand], (
        f"bg=None must keep the candidate (rule inert); "
        f"got violations={reports_no_bg[0].violations}"
    )
    assert reports_no_bg[0].passed, "bg=None must report passed"

    # With bg: safe-zone rule fires, candidate is dropped.
    kept_with_bg, reports_with_bg = filter_valid([cand], spec, bg=bg)
    assert kept_with_bg == [], (
        "filter_valid must forward bg so the Step 43 safe-zone rule fires; "
        f"unexpectedly kept {kept_with_bg}"
    )
    assert not reports_with_bg[0].passed, "candidate outside every safe_zone must fail QC"


def test_step67_filter_valid_bg_defers_when_composition_present():
    """Step 67 + Step 63: when a CompositionDirective is on the spec, the
    safe-zone rule defers (Composition Director already picked the
    placement template under vision). ``filter_valid`` forwarding ``bg``
    must NOT regress this -- the candidate that would have been dropped
    above must now survive QC.
    """
    from metagpt.ext.agentlayout.schema import CompositionDirective
    from metagpt.ext.agentlayout.tools.quality_checker import filter_valid

    # Same outside-zone geometry as the previous test: would be dropped
    # by safe-zone QC when bg is forwarded and composition is None.
    spec, cand, bg = _step43_spec_and_candidate(
        title_left=500, title_top=350, title_w=500, title_h=300
    )
    # Minimal directive: presence (not contents) triggers the Step 63
    # deference branch in _check_primary_in_safe_zone.
    spec.composition = CompositionDirective(
        template_id="text_only_centered",
        rationale="test fixture: deference contract.",
    )

    kept, reports = filter_valid([cand], spec, bg=bg)
    assert kept == [cand], (
        "Step 63: safe-zone rule must defer when spec.composition is set; "
        f"got kept={kept}, violations={reports[0].violations}"
    )
