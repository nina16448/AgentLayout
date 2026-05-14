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
