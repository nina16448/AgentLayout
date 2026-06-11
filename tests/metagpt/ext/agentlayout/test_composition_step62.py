"""Step 62 unit tests: Composition Director template library, ComposeSketch
parsing, and the QC composition contract + text-on-photo conditional exemption.

Step 61 quantified the sketch-level gap (photo large+bleed GT 45% vs candidate
0%; text-on-photo GT 43% vs 4%). Step 62 adds an art-director Action that picks
a GT-calibrated template; QC then enforces the directive numerically and swaps
safe-zone/busy-texture rejection for an underlay requirement when text rides
the focal photo (user decision: conditional exemption).

No LLM calls; ComposeSketch is only exercised through its parse helpers.

Run:
    pytest tests/metagpt/ext/agentlayout/test_composition_step62.py -v --no-cov
"""
from __future__ import annotations

from typing import List

import pytest

from metagpt.ext.agentlayout.actions.compose_sketch import ComposeSketch
from metagpt.ext.agentlayout.schema import (
    BackgroundAnalysis,
    Candidate,
    Canvas,
    CompositionDirective,
    DesignSpec,
    Element,
    LayoutElement,
    SafeZone,
    SemanticType,
    VisualType,
)
from metagpt.ext.agentlayout.tools.composition_templates import (
    PHOTO_TEMPLATES,
    SIZE_BUCKET_RANGES,
    TEXT_ONLY_TEMPLATES,
    aspect_bucket,
    cell_bounds,
    template_by_id,
    template_menu,
)
from metagpt.ext.agentlayout.tools.quality_checker import (
    ViolationType,
    _classify_relation,
    check_candidate,
)


def _el(eid: str, sem: SemanticType, vis: VisualType) -> Element:
    return Element(
        id=eid,
        semantic_type=sem,
        visual_type=vis,
        content="x" if vis == VisualType.TEXT else None,
        inferred=False,
        importance=3,
        semantic_relevance=0.8,
    )


def _le(eid: str, left: float, top: float, w: float, h: float) -> LayoutElement:
    return LayoutElement(id=eid, left=left, top=top, width=w, height=h, z_index=1)


def _spec(elements: List[Element], cw: int = 900, ch: int = 900) -> DesignSpec:
    return DesignSpec(
        canvas=Canvas(width=cw, height=ch),
        elements=elements,
        hard_constraints=[],
        style_keywords=["bold"],
        language="en",
    )


def _photo_spec() -> DesignSpec:
    return _spec(
        [
            _el("title_1", SemanticType.TITLE, VisualType.TEXT),
            _el("photo_1", SemanticType.PRODUCT_IMAGE, VisualType.IMAGE),
            _el("underlay_1", SemanticType.DECORATIVE_IMAGE, VisualType.IMAGE),
        ]
    )


def _hero_directive() -> CompositionDirective:
    return CompositionDirective(
        template_id="hero-center-overlay",
        relation="text-on-photo",
        photo_cell="MC",
        photo_size="large",
        text_cell="MC",
    )


def _compliant_candidate() -> Candidate:
    # Large centered photo (ratio 0.605), title riding it, underlay covering
    # the title -- the GT-dominant 'hero-center-overlay' execution.
    return Candidate(
        candidate_id="c_ok",
        elements=[
            _le("photo_1", 100, 100, 700, 700),
            _le("underlay_1", 280, 380, 360, 160),
            _le("title_1", 300, 400, 300, 120),
        ],
    )


def _step62_viols(result):
    return [
        v
        for v in result.violations
        if v.type
        in (ViolationType.COMPOSITION_MISMATCH, ViolationType.TEXT_ON_PHOTO_NO_UNDERLAY)
    ]


# ---------------------------------------------------------------- templates


def test_aspect_bucket():
    assert aspect_bucket(1080, 1920) == "portrait"
    assert aspect_bucket(1920, 1080) == "landscape"
    assert aspect_bucket(900, 900) == "square"


def test_cell_bounds_grid_math():
    assert cell_bounds("TL", 900, 900) == (0, 0, 300, 300)
    assert cell_bounds("MC", 900, 900) == (300, 300, 600, 600)
    assert cell_bounds("BR", 900, 900) == (600, 600, 900, 900)
    with pytest.raises(ValueError):
        cell_bounds("XX", 900, 900)


def test_template_menu_ordering_and_priors():
    menu = template_menu(has_photo=True, aspect="square")
    # square GT prior: text-on-photo 0.47 dominates -> hero template first.
    assert menu[0]["id"] == "hero-center-overlay"
    assert all("relation_prior" in tpl for tpl in menu)
    text_menu = template_menu(has_photo=False, aspect="square")
    assert text_menu[0]["id"] == "text-centered"


def test_template_ids_unique_and_resolvable():
    ids = [t["id"] for t in PHOTO_TEMPLATES + TEXT_ONLY_TEMPLATES]
    assert len(ids) == len(set(ids))
    for tid in ids:
        assert template_by_id(tid)["id"] == tid
    assert template_by_id("nope") is None


def test_size_buckets_cover_designer_range():
    assert SIZE_BUCKET_RANGES["large"] == (0.45, 0.80)
    assert SIZE_BUCKET_RANGES["small"][0] == 0.05
    assert SIZE_BUCKET_RANGES["bleed"][1] == 0.95


# ------------------------------------------------------------ ComposeSketch


def test_parse_response_valid():
    menu = template_menu(True, "square")
    rsp = '{"template_id": "hero-center-overlay", "photo_size": "bleed", "rationale": "ok"}'
    d = ComposeSketch._parse_response(rsp, menu)
    assert d.template_id == "hero-center-overlay"
    assert d.relation == "text-on-photo"
    assert d.photo_size == "bleed"  # override accepted
    assert d.photo_cell == "MC" and d.text_cell == "MC"


def test_parse_response_defaults_to_template_size():
    menu = template_menu(True, "square")
    d = ComposeSketch._parse_response(
        '{"template_id": "split-photo-right", "photo_size": null}', menu
    )
    assert d.photo_size == "medium"  # template default


def test_parse_response_rejects_off_menu_template():
    menu = template_menu(True, "square")
    with pytest.raises(ValueError):
        ComposeSketch._parse_response('{"template_id": "made-up"}', menu)


def test_parse_response_rejects_unknown_size_bucket():
    menu = template_menu(True, "square")
    with pytest.raises(ValueError):
        ComposeSketch._parse_response(
            '{"template_id": "hero-center-overlay", "photo_size": "gigantic"}', menu
        )


def test_parse_response_text_only_template_nulls_photo_size():
    menu = template_menu(False, "square")
    d = ComposeSketch._parse_response(
        '{"template_id": "text-centered", "photo_size": "large"}', menu
    )
    assert d.photo_size is None and d.photo_cell is None


# ------------------------------------------------- QC: composition contract


def test_qc_noop_without_directive():
    """Pre-62 behavior must stay bit-identical when spec.composition is None."""
    spec = _photo_spec()
    assert spec.composition is None
    result = check_candidate(_compliant_candidate(), spec)
    assert _step62_viols(result) == []


def test_qc_compliant_candidate_passes_directive():
    spec = _photo_spec()
    spec.composition = _hero_directive()
    result = check_candidate(_compliant_candidate(), spec)
    assert _step62_viols(result) == []


def test_qc_flags_photo_cell_size_textcell_and_relation():
    spec = _photo_spec()
    spec.composition = _hero_directive()
    bad = Candidate(
        candidate_id="c_bad",
        elements=[
            _le("photo_1", 0, 0, 250, 250),  # ratio 0.077 small, cell TL
            _le("underlay_1", 700, 700, 100, 50),
            _le("title_1", 300, 700, 300, 120),  # text mass at BC
        ],
    )
    result = check_candidate(bad, spec)
    details = " | ".join(
        v.detail for v in result.violations if v.type == ViolationType.COMPOSITION_MISMATCH
    )
    assert "outside directive cell 'MC'" in details  # photo cell
    assert "size bucket 'large'" in details  # photo size
    assert "text mass center" in details  # text cell
    assert "directive requires 'text-on-photo'" in details  # relation


def test_qc_cell_tolerance_allows_near_boundary_center():
    """Step 58 lesson: gates hugging exact boundaries kill GT-style solutions.
    A photo center 2% of the canvas outside the cell must still pass."""
    spec = _photo_spec()
    spec.composition = _hero_directive()
    cand = Candidate(
        candidate_id="c_edge",
        elements=[
            # center (615, 450): 15px (1.7% of 900) right of MC's x-bound 600.
            _le("photo_1", 265, 100, 700, 700),
            _le("underlay_1", 380, 380, 360, 160),
            _le("title_1", 400, 400, 300, 120),
        ],
    )
    result = check_candidate(cand, spec)
    assert not any(
        "outside directive cell" in v.detail and "photo" in v.detail
        for v in _step62_viols(result)
    )


def test_relation_classifier_mirrors_step61_signature():
    texts = [_le("title_1", 300, 400, 300, 120)]
    on_photo = _le("photo_1", 100, 100, 700, 700)
    assert _classify_relation(on_photo, texts, 900, 900) == "text-on-photo"
    stacked = _classify_relation(
        _le("photo_1", 100, 500, 700, 380), [_le("t", 300, 50, 300, 120)], 900, 900
    )
    assert stacked == "stacked"
    sbs = _classify_relation(
        _le("photo_1", 500, 200, 380, 500), [_le("t", 30, 300, 300, 120)], 900, 900
    )
    assert sbs == "side-by-side"
    # centered-mix: text centroid offset (140, 120) px < 150 (= 900/6) on both
    # axes, corner overlap 1,600 / 19,200 px^2 = 8% < 30%.
    mixed = _classify_relation(
        _le("photo_1", 350, 350, 200, 200), [_le("t", 470, 530, 240, 80)], 900, 900
    )
    assert mixed == "centered-mix"


# --------------------------------------- QC: text-on-photo underlay contract


def test_qc_requires_underlay_for_text_riding_photo():
    spec = _photo_spec()
    spec.composition = _hero_directive()
    naked = Candidate(
        candidate_id="c_naked",
        elements=[
            _le("photo_1", 100, 100, 700, 700),
            _le("underlay_1", 0, 850, 100, 40),  # parked far from the title
            _le("title_1", 300, 400, 300, 120),
        ],
    )
    result = check_candidate(naked, spec)
    viols = [v for v in result.violations if v.type == ViolationType.TEXT_ON_PHOTO_NO_UNDERLAY]
    assert [v.targets for v in viols] == [["title_1"]]
    assert "underlay" in viols[0].detail


def test_qc_underlay_rule_skipped_when_spec_has_no_decorative_image():
    """Completeness forbids inventing elements: a spec without any
    decorative_image cannot be asked to add one (unsatisfiable gate)."""
    spec = _spec(
        [
            _el("title_1", SemanticType.TITLE, VisualType.TEXT),
            _el("photo_1", SemanticType.PRODUCT_IMAGE, VisualType.IMAGE),
        ]
    )
    spec.composition = _hero_directive()
    cand = Candidate(
        candidate_id="c_no_underlay_asset",
        elements=[
            _le("photo_1", 100, 100, 700, 700),
            _le("title_1", 300, 400, 300, 120),
        ],
    )
    result = check_candidate(cand, spec)
    assert not any(
        v.type == ViolationType.TEXT_ON_PHOTO_NO_UNDERLAY for v in result.violations
    )


# ------------------------------------------------ QC: safe-zone deference (Step 63)


def test_safe_zone_defers_when_directive_present():
    """Step 63 (user decision, option a): the Composition Director picked the
    template while looking at the background, so a directive supersedes the
    saliency-based safe-zone rule entirely; without a directive the Step 43
    rule keeps firing (zero regression)."""
    spec = _photo_spec()
    bg = BackgroundAnalysis(
        safe_zones=[SafeZone(region="top-band", bbox=[0, 0, 900, 90], confidence=0.9)]
    )
    cand = _compliant_candidate()  # photo + title both far outside the tiny zone

    # Without a directive the old rule fires on both primaries.
    spec.composition = None
    before = check_candidate(cand, spec, bg=bg)
    flagged = {
        t
        for v in before.violations
        if v.type == ViolationType.PRIMARY_OUTSIDE_SAFE_ZONE
        for t in v.targets
    }
    assert {"photo_1", "title_1"} <= flagged

    # With the directive the safe-zone rule defers entirely.
    spec.composition = _hero_directive()
    after = check_candidate(cand, spec, bg=bg)
    assert not any(
        v.type == ViolationType.PRIMARY_OUTSIDE_SAFE_ZONE for v in after.violations
    )


def test_safe_zone_defers_for_text_only_directive():
    """Direct repro of the step62 double-bind, now resolved: a text-only
    directive demands the text mass at MC while the only safe zone is a thin
    top band -- under Step 63 the directive wins and the centered text is no
    longer rejected."""
    spec = _spec(
        [
            _el("title_1", SemanticType.TITLE, VisualType.TEXT),
            _el("subtitle_1", SemanticType.SUBTITLE, VisualType.TEXT),
        ]
    )
    spec.composition = CompositionDirective(
        template_id="text-centered",
        relation=None,
        photo_cell=None,
        photo_size=None,
        text_cell="MC",
    )
    bg = BackgroundAnalysis(
        safe_zones=[SafeZone(region="top-band", bbox=[0, 0, 900, 90], confidence=0.9)]
    )
    cand = Candidate(
        candidate_id="c_text_mc",
        elements=[
            _le("title_1", 300, 360, 300, 120),  # centered, outside the band
            _le("subtitle_1", 300, 500, 300, 60),
        ],
    )
    result = check_candidate(cand, spec, bg=bg)
    assert not any(
        v.type == ViolationType.PRIMARY_OUTSIDE_SAFE_ZONE for v in result.violations
    )
