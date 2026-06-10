"""Step 57 (2026-06-11) coverage / dead-space QC guardrail tests.

Thresholds were calibrated offline against the 20 step13 designer GT layouts
(layout_agent/output/step57_coverage_calibration.py): designer coverage
minimum is 0.129 and dead-band maxima are v=0.503 / h=0.548, so
CANVAS_COVERAGE_MIN=0.10 and DEAD_BAND_MAX=0.60 pass every designer layout
with margin while catching the degenerate step56 candidates (coverage down
to 0.048, dead bands up to 0.794).

Run:
    pytest tests/metagpt/ext/agentlayout/test_quality_checker_coverage.py -v --no-cov
"""
from __future__ import annotations

from metagpt.ext.agentlayout.schema import (
    Candidate,
    Canvas,
    DesignSpec,
    Element,
    LayoutElement,
    SemanticType,
    VisualType,
)
from metagpt.ext.agentlayout.tools.quality_checker import (
    CANVAS_COVERAGE_MIN,
    DEAD_BAND_MAX,
    ViolationType,
    check_candidate,
)


def _spec(elements):
    return DesignSpec(canvas=Canvas(width=1000, height=1000), elements=elements)


def _text_el(eid):
    return Element(
        id=eid,
        semantic_type=SemanticType.BODY_TEXT,
        visual_type=VisualType.TEXT,
        content="x",
    )


def _bg_el(eid):
    return Element(
        id=eid,
        semantic_type=SemanticType.BACKGROUND_IMAGE,
        visual_type=VisualType.IMAGE,
        asset_ref="bg.png",
    )


def _violations_of(result, vtype):
    return [v for v in result.violations if v.type == vtype]


def test_crammed_corner_flags_low_coverage_and_both_dead_bands():
    """All content shrunk into a 100x100 corner sliver (the 5f56075f step56
    failure mode, coverage 0.050) must trip the coverage guardrail and both
    axis dead-band guardrails."""
    spec = _spec([_text_el("t1"), _text_el("t2")])
    cand = Candidate(
        candidate_id="c",
        elements=[
            LayoutElement(id="t1", left=0, top=0, width=100, height=50, z_index=1),
            LayoutElement(id="t2", left=0, top=50, width=100, height=50, z_index=2),
        ],
    )
    result = check_candidate(cand, spec)
    assert len(_violations_of(result, ViolationType.CANVAS_COVERAGE_LOW)) == 1
    assert len(_violations_of(result, ViolationType.DEAD_BAND_EXCESSIVE)) == 2


def test_designer_style_minimalist_layout_passes():
    """Spread-out minimalist composition (coverage ~0.135, dead bands ~0.2)
    mirrors the legitimate designer layouts the thresholds were calibrated
    to keep -- it must NOT be flagged."""
    spec = _spec([_text_el("t1"), _text_el("t2"), _text_el("t3")])
    cand = Candidate(
        candidate_id="c",
        elements=[
            LayoutElement(id="t1", left=100, top=100, width=300, height=150, z_index=1),
            LayoutElement(id="t2", left=600, top=450, width=300, height=150, z_index=2),
            LayoutElement(id="t3", left=100, top=800, width=300, height=150, z_index=3),
        ],
    )
    result = check_candidate(cand, spec)
    assert not _violations_of(result, ViolationType.CANVAS_COVERAGE_LOW)
    assert not _violations_of(result, ViolationType.DEAD_BAND_EXCESSIVE)


def test_background_image_excluded_from_coverage():
    """A full-canvas background plate must not mask foreground emptiness:
    bg covers 100% but the only foreground element is a sliver, so the
    guardrails still fire."""
    spec = _spec([_bg_el("bg_1"), _text_el("t1")])
    cand = Candidate(
        candidate_id="c",
        elements=[
            LayoutElement(id="bg_1", left=0, top=0, width=1000, height=1000, z_index=0),
            LayoutElement(id="t1", left=0, top=0, width=100, height=50, z_index=5),
        ],
    )
    result = check_candidate(cand, spec)
    assert len(_violations_of(result, ViolationType.CANVAS_COVERAGE_LOW)) == 1
    assert len(_violations_of(result, ViolationType.DEAD_BAND_EXCESSIVE)) == 2


def test_vertical_dead_band_flagged_alone():
    """Wide banner in the top 35% leaves a 65% bottom band: coverage passes
    (0.35) but the vertical dead band exceeds 0.60 -- the '589d7bd9 stacked
    everything in one band' step56 failure mode."""
    spec = _spec([_text_el("t1")])
    cand = Candidate(
        candidate_id="c",
        elements=[
            LayoutElement(id="t1", left=0, top=0, width=1000, height=350, z_index=1),
        ],
    )
    result = check_candidate(cand, spec)
    assert not _violations_of(result, ViolationType.CANVAS_COVERAGE_LOW)
    dead = _violations_of(result, ViolationType.DEAD_BAND_EXCESSIVE)
    assert len(dead) == 1
    assert "vertical" in dead[0].detail


def test_horizontal_dead_band_flagged_alone():
    spec = _spec([_text_el("t1")])
    cand = Candidate(
        candidate_id="c",
        elements=[
            LayoutElement(id="t1", left=0, top=0, width=350, height=1000, z_index=1),
        ],
    )
    result = check_candidate(cand, spec)
    assert not _violations_of(result, ViolationType.CANVAS_COVERAGE_LOW)
    dead = _violations_of(result, ViolationType.DEAD_BAND_EXCESSIVE)
    assert len(dead) == 1
    assert "horizontal" in dead[0].detail


def test_band_at_designer_envelope_passes():
    """Dead band just below the threshold (0.55, between the designer max
    0.548 and the 0.60 threshold) must pass -- tall posters legitimately
    leave half the canvas to the background subject."""
    spec = _spec([_text_el("t1")])
    cand = Candidate(
        candidate_id="c",
        elements=[
            LayoutElement(id="t1", left=0, top=0, width=1000, height=450, z_index=1),
        ],
    )
    result = check_candidate(cand, spec)
    assert not _violations_of(result, ViolationType.DEAD_BAND_EXCESSIVE)


def test_background_only_candidate_flags_everything():
    """Candidate whose only element is the background plate has zero
    foreground: coverage 0 and both axes fully dead."""
    spec = _spec([_bg_el("bg_1")])
    cand = Candidate(
        candidate_id="c",
        elements=[
            LayoutElement(id="bg_1", left=0, top=0, width=1000, height=1000, z_index=0),
        ],
    )
    result = check_candidate(cand, spec)
    assert len(_violations_of(result, ViolationType.CANVAS_COVERAGE_LOW)) == 1
    assert len(_violations_of(result, ViolationType.DEAD_BAND_EXCESSIVE)) == 2


def test_thresholds_pinned_to_calibration():
    """Thresholds are load-bearing calibration outputs (designer GT envelope:
    coverage min 0.129, dead band max 0.548). Changing them requires re-running
    layout_agent/output/step57_coverage_calibration.py."""
    assert CANVAS_COVERAGE_MIN == 0.10
    assert DEAD_BAND_MAX == 0.60
