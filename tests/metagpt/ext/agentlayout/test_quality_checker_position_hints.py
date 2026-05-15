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
