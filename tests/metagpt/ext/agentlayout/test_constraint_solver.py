"""Step 66 (2026-06-13): constraint-solver placement -- offline tests.

The solver's contract is "QC-compliant by construction": for every template
family the candidate it emits must leave the step41 oracle's QC gate empty
(the six gated violation types), satisfy the text-on-photo riding/underlay
contract numerically, and be bit-deterministic for a given (spec, bg,
variant). All tests are zero-LLM.
"""

from __future__ import annotations

from typing import List, Optional

import numpy as np
import pytest
from PIL import Image

from metagpt.ext.agentlayout.schema import (
    BackgroundAnalysis,
    Canvas,
    CompositionDirective,
    DesignSpec,
    Element,
    HardConstraint,
    HardConstraintRule,
    SafeZone,
    SemanticType,
    VisualType,
)
from metagpt.ext.agentlayout.tools import quality_checker as qc
from metagpt.ext.agentlayout.tools.composition_templates import (
    SIZE_BUCKET_RANGES,
    cell_bounds,
    template_by_id,
)
from metagpt.ext.agentlayout.tools.constraint_solver import solve_placement
from metagpt.ext.agentlayout.tools.quality_checker import ViolationType, check_candidate

GATE_TYPES = {
    ViolationType.PRIMARY_OUTSIDE_SAFE_ZONE,
    ViolationType.CANVAS_COVERAGE_LOW,
    ViolationType.DEAD_BAND_EXCESSIVE,
    ViolationType.TEXT_ON_BUSY_TEXTURE,
    ViolationType.COMPOSITION_MISMATCH,
    ViolationType.TEXT_ON_PHOTO_NO_UNDERLAY,
}


def _gate(result) -> List:
    return [v for v in result.violations if v.type in GATE_TYPES]


def _directive(template_id: str, photo_size: Optional[str] = None) -> CompositionDirective:
    tpl = template_by_id(template_id)
    return CompositionDirective(
        template_id=tpl["id"],
        relation=tpl["relation"],
        photo_cell=tpl["photo_cell"],
        photo_size=photo_size or tpl["photo_size"],
        text_cell=tpl["text_cell"],
        rationale="test",
    )


def _make_photo_asset(tmp_path, name="photo.png", size=(600, 400)):
    p = tmp_path / name
    Image.new("RGB", size, (40, 90, 160)).save(p)
    return str(p)


def _make_spec(
    tmp_path,
    template_id: Optional[str] = "hero-center-overlay",
    with_photo: bool = True,
    with_underlay: bool = True,
    canvas=(1080, 1080),
    bg_asset: Optional[str] = None,
    texts=None,
) -> DesignSpec:
    elements = [
        Element(
            id="bg_1",
            semantic_type=SemanticType.BACKGROUND_IMAGE,
            visual_type=VisualType.IMAGE,
            importance=1,
            semantic_relevance=0.5,
        )
    ]
    if with_photo:
        elements.append(
            Element(
                id="img_1",
                semantic_type=SemanticType.PRODUCT_IMAGE,
                visual_type=VisualType.IMAGE,
                asset_ref=_make_photo_asset(tmp_path),
                importance=4,
                semantic_relevance=0.8,
            )
        )
    if with_underlay:
        elements.append(
            Element(
                id="underlay_1",
                semantic_type=SemanticType.DECORATIVE_IMAGE,
                visual_type=VisualType.IMAGE,
                importance=2,
                semantic_relevance=0.4,
            )
        )
    for i, (stype, content) in enumerate(
        texts
        or [
            (SemanticType.TITLE, "Summer Music Festival"),
            (SemanticType.SUBTITLE, "Live at the riverside park"),
            (SemanticType.CTA, "Get tickets"),
        ],
        start=1,
    ):
        elements.append(
            Element(
                id=f"text_{i}",
                semantic_type=stype,
                visual_type=VisualType.TEXT,
                content=content,
                importance=5,
                semantic_relevance=0.9,
            )
        )
    spec = DesignSpec(
        canvas=Canvas(
            width=canvas[0], height=canvas[1], background_asset_ref=bg_asset
        ),
        elements=elements,
        composition=_directive(template_id) if template_id else None,
        hard_constraints=[
            HardConstraint(
                rule=HardConstraintRule.Z_ORDER,
                targets=["text_1"],
                params={"hint": "above_background"},
            )
        ],
        style_keywords=["modern", "vibrant"],
    )
    return spec


def _by_id(cand):
    return {el.id: el for el in cand.elements}


# ------------------------------------------------------------------
# Core contract: gate-clean by construction
# ------------------------------------------------------------------


def test_completeness_and_boundary(tmp_path):
    spec = _make_spec(tmp_path)
    cand = solve_placement(spec)
    assert {el.id for el in cand.elements} == {e.id for e in spec.elements}
    result = check_candidate(cand, spec)
    kinds = {v.type for v in result.violations}
    assert ViolationType.OUT_OF_BOUNDS not in kinds
    assert ViolationType.MISSING_ELEMENT not in kinds
    assert ViolationType.EXTRA_ELEMENT not in kinds


@pytest.mark.parametrize(
    "template_id",
    [
        "hero-center-overlay",
        "split-photo-right",
        "split-photo-left",
        "stacked-text-top",
        "stacked-text-bottom",
        "photo-bottom-anchor",
        "centered-mix",
    ],
)
def test_photo_templates_gate_clean(tmp_path, template_id):
    spec = _make_spec(tmp_path, template_id=template_id)
    cand = solve_placement(spec)
    assert _gate(check_candidate(cand, spec)) == []


@pytest.mark.parametrize(
    "template_id", ["text-centered", "text-column-right", "text-column-left"]
)
def test_text_only_templates_gate_clean(tmp_path, template_id):
    spec = _make_spec(tmp_path, template_id=template_id, with_photo=False, with_underlay=False)
    cand = solve_placement(spec)
    assert _gate(check_candidate(cand, spec)) == []


def test_no_directive_fallback_gate_clean(tmp_path):
    spec = _make_spec(tmp_path, template_id=None)
    cand = solve_placement(spec)
    assert _gate(check_candidate(cand, spec)) == []


# ------------------------------------------------------------------
# Composition directive: numeric compliance
# ------------------------------------------------------------------


def test_hero_photo_cell_and_bucket(tmp_path):
    spec = _make_spec(tmp_path, template_id="hero-center-overlay")
    cand = solve_placement(spec)
    photo = _by_id(cand)["img_1"]
    cw, ch = spec.canvas.width, spec.canvas.height
    xl, yt, xr, yb = cell_bounds("MC", cw, ch)
    cx = photo.left + photo.width / 2
    cy = photo.top + photo.height / 2
    assert xl <= cx <= xr and yt <= cy <= yb
    lo, hi = SIZE_BUCKET_RANGES["large"]
    ratio = photo.width * photo.height / (cw * ch)
    assert lo <= ratio <= hi


def test_text_on_photo_riding_and_underlay_contract(tmp_path):
    spec = _make_spec(tmp_path, template_id="hero-center-overlay")
    cand = solve_placement(spec)
    els = _by_id(cand)
    photo, underlay = els["img_1"], els["underlay_1"]
    texts = [els["text_1"], els["text_2"], els["text_3"]]
    for t in texts:
        assert qc._overlap_ratio(t, photo) >= qc.TEXT_ON_PHOTO_MIN_OVERLAP
        assert qc._overlap_ratio(t, underlay) >= qc.TEXT_ON_PHOTO_UNDERLAY_MIN_COVER
        assert t.z_index > underlay.z_index > photo.z_index
    # The dedicated QC rule agrees:
    assert qc._check_text_on_photo_underlay(cand, spec) == []


def test_text_mass_centroid_in_directive_cell(tmp_path):
    spec = _make_spec(
        tmp_path, template_id="text-column-right", with_photo=False, with_underlay=False
    )
    cand = solve_placement(spec)
    texts = qc._candidate_text_elements(cand, spec)
    tx, ty = qc._text_mass_center(texts)
    cw, ch = spec.canvas.width, spec.canvas.height
    xl, yt, xr, yb = cell_bounds("MR", cw, ch)
    tol_x, tol_y = qc.COMPOSITION_CELL_TOLERANCE * cw, qc.COMPOSITION_CELL_TOLERANCE * ch
    assert xl - tol_x <= tx <= xr + tol_x
    assert yt - tol_y <= ty <= yb + tol_y


def test_side_by_side_relation_classifies_correctly(tmp_path):
    spec = _make_spec(tmp_path, template_id="split-photo-right")
    cand = solve_placement(spec)
    focal = qc._focal_photo(cand, spec)
    texts = qc._candidate_text_elements(cand, spec)
    cw, ch = float(spec.canvas.width), float(spec.canvas.height)
    assert qc._classify_relation(focal, texts, cw, ch) == "side-by-side"


# ------------------------------------------------------------------
# Determinism and variants
# ------------------------------------------------------------------


def test_deterministic_same_inputs(tmp_path):
    spec = _make_spec(tmp_path)
    a = solve_placement(spec, variant=0)
    b = solve_placement(spec, variant=0)
    assert a.model_dump() == b.model_dump()


def test_variants_differ(tmp_path):
    spec = _make_spec(tmp_path)
    a = solve_placement(spec, variant=0)
    b = solve_placement(spec, variant=1)
    assert a.model_dump() != b.model_dump()
    # Variant 1 is the bolder type scale.
    fa = {el.id: el.font_size for el in a.elements if el.font_size}
    fb = {el.id: el.font_size for el in b.elements if el.font_size}
    assert all(fb[k] > fa[k] for k in fa)


def test_variants_all_gate_clean(tmp_path):
    spec = _make_spec(tmp_path)
    for variant in range(3):
        cand = solve_placement(spec, variant=variant)
        assert _gate(check_candidate(cand, spec)) == [], f"variant {variant}"


# ------------------------------------------------------------------
# Background-aware anchor search
# ------------------------------------------------------------------


def _busy_left_flat_right_bg(tmp_path, size=(1080, 1080)):
    """Left half = high-frequency noise (max Sobel), right half flat."""
    rng = np.random.RandomState(7)
    arr = np.full((size[1], size[0], 3), 235, dtype=np.uint8)
    noise = rng.randint(0, 255, (size[1], size[0] // 2, 3), dtype=np.uint8)
    arr[:, : size[0] // 2] = noise
    p = tmp_path / "bg_busy_left.png"
    Image.fromarray(arr).save(p)
    return str(p)


def test_anchor_search_avoids_busy_texture(tmp_path):
    bg_asset = _busy_left_flat_right_bg(tmp_path)
    spec = _make_spec(
        tmp_path,
        template_id="text-centered",
        with_photo=False,
        with_underlay=False,
        bg_asset=bg_asset,
        texts=[(SemanticType.TITLE, "Sale"), (SemanticType.CTA, "Shop now")],
    )
    bg = BackgroundAnalysis(
        safe_zones=[SafeZone(region="right", bbox=[540, 0, 1080, 1080], confidence=0.9)],
        recommended_text_color="#111111",
    )
    cand = solve_placement(spec, bg=bg)
    assert qc._check_text_on_busy_texture(cand, spec) == []
    # The stack should have moved toward the flat right half of the MC cell.
    texts = qc._candidate_text_elements(cand, spec)
    tx, _ = qc._text_mass_center(texts)
    assert tx > spec.canvas.width / 2


def test_underlay_shields_busy_background_when_available(tmp_path):
    """When the whole canvas is busy, the solver deploys the underlay."""
    rng = np.random.RandomState(11)
    arr = rng.randint(0, 255, (1080, 1080, 3), dtype=np.uint8)
    p = tmp_path / "bg_all_busy.png"
    Image.fromarray(arr).save(p)
    spec = _make_spec(
        tmp_path,
        template_id="text-centered",
        with_photo=False,
        with_underlay=True,
        bg_asset=str(p),
    )
    cand = solve_placement(spec)
    els = _by_id(cand)
    underlay = els["underlay_1"]
    for t in qc._candidate_text_elements(cand, spec):
        assert qc._overlap_ratio(t, underlay) >= 0.99
        assert t.z_index > underlay.z_index
    assert qc._check_text_on_busy_texture(cand, spec) == []


# ------------------------------------------------------------------
# Luminance-aware text color (step66 smoke fix)
# ------------------------------------------------------------------


def test_white_text_on_dark_photo(tmp_path):
    dark = tmp_path / "dark_photo.png"
    Image.new("RGB", (600, 400), (15, 20, 35)).save(dark)
    spec = _make_spec(tmp_path, template_id="hero-center-overlay")
    spec.get_element("img_1").asset_ref = str(dark)
    bg = BackgroundAnalysis(recommended_text_color="#111111")
    cand = solve_placement(spec, bg=bg)
    for el in cand.elements:
        if el.color:
            assert el.color == "#FFFFFF"


def test_dark_text_on_light_background_region(tmp_path):
    light = tmp_path / "light_bg.png"
    Image.new("RGB", (1080, 1080), (245, 240, 230)).save(light)
    spec = _make_spec(
        tmp_path,
        template_id="text-centered",
        with_photo=False,
        with_underlay=False,
        bg_asset=str(light),
    )
    bg = BackgroundAnalysis(recommended_text_color="#FFFFFF")
    cand = solve_placement(spec, bg=bg)
    for el in cand.elements:
        if el.color:
            assert el.color == "#111111"


# ------------------------------------------------------------------
# Robustness corners
# ------------------------------------------------------------------


def test_small_landscape_canvas(tmp_path):
    spec = _make_spec(tmp_path, template_id="split-photo-left", canvas=(537, 240))
    cand = solve_placement(spec)
    assert _gate(check_candidate(cand, spec)) == []


def test_portrait_canvas_stacked(tmp_path):
    spec = _make_spec(tmp_path, template_id="stacked-text-top", canvas=(1080, 1920))
    cand = solve_placement(spec)
    assert _gate(check_candidate(cand, spec)) == []


def test_sparse_single_text_coverage(tmp_path):
    spec = _make_spec(
        tmp_path,
        template_id="text-centered",
        with_photo=False,
        with_underlay=False,
        texts=[(SemanticType.TITLE, "Hi")],
    )
    cand = solve_placement(spec)
    result = check_candidate(cand, spec)
    assert ViolationType.CANVAS_COVERAGE_LOW not in {v.type for v in _gate(result)}


def test_z_order_hard_constraint_satisfied(tmp_path):
    spec = _make_spec(tmp_path)
    cand = solve_placement(spec)
    kinds = {v.type for v in check_candidate(cand, spec).violations}
    assert ViolationType.Z_ORDER not in kinds
    assert ViolationType.UNKNOWN_HINT not in kinds


def test_secondary_photos_and_accents_placed(tmp_path):
    spec = _make_spec(tmp_path)
    spec.elements.append(
        Element(
            id="img_2",
            semantic_type=SemanticType.PRODUCT_IMAGE,
            visual_type=VisualType.IMAGE,
            asset_ref=_make_photo_asset(tmp_path, "photo2.png", (300, 300)),
            importance=3,
            semantic_relevance=0.6,
        )
    )
    spec.elements.append(
        Element(
            id="logo_1",
            semantic_type=SemanticType.LOGO,
            visual_type=VisualType.IMAGE,
            importance=3,
            semantic_relevance=0.5,
        )
    )
    cand = solve_placement(spec)
    els = _by_id(cand)
    assert "img_2" in els and "logo_1" in els
    cw, ch = spec.canvas.width, spec.canvas.height
    for eid in ("img_2", "logo_1"):
        el = els[eid]
        assert el.left >= 0 and el.top >= 0
        assert el.left + el.width <= cw and el.top + el.height <= ch
