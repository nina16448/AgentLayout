"""Step 59 (2026-06-11) text-on-busy-texture QC rule tests.

Threshold calibrated offline against the 20 step13 designer GT layouts
(layout_agent/output/step59_text_gradient_calibration.py): worst exposed GT
text element is 0.0454 and 8/20 GT layouts shield every text element with an
underlay, so TEXT_GRADIENT_MAX=0.065 passes all designer layouts with margin
while catching 23% (74/327) of replayed live candidates with exposed text.

Run:
    pytest tests/metagpt/ext/agentlayout/test_quality_checker_text_gradient.py -v --no-cov
"""
from __future__ import annotations

import numpy as np
from PIL import Image

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
    TEXT_GRADIENT_MAX,
    ViolationType,
    check_candidate,
)

W, H = 400, 400


def _half_busy_bg(tmp_path):
    """Left half flat gray (gradient ~0), right half 4px checkerboard
    (strong gradients everywhere). Normalisation is by the image's own max,
    so flat-region text scores ~0 and checker-region text scores high."""
    arr = np.full((H, W, 3), 128, dtype=np.uint8)
    yy, xx = np.mgrid[0:H, 0 : W // 2]
    checker = (((yy // 4) + (xx // 4)) % 2 * 255).astype(np.uint8)
    arr[:, W // 2 :, :] = checker[..., None]
    path = tmp_path / "bg_half_busy.png"
    Image.fromarray(arr).save(path)
    return str(path)


def _flat_bg(tmp_path):
    arr = np.full((H, W, 3), 200, dtype=np.uint8)
    path = tmp_path / "bg_flat.png"
    Image.fromarray(arr).save(path)
    return str(path)


def _spec(elements, bg_ref=None):
    return DesignSpec(
        canvas=Canvas(width=W, height=H, background_asset_ref=bg_ref),
        elements=elements,
    )


def _text_el(eid, semantic=SemanticType.BODY_TEXT):
    return Element(id=eid, semantic_type=semantic, visual_type=VisualType.TEXT, content="x")


def _underlay_el(eid):
    return Element(
        id=eid,
        semantic_type=SemanticType.DECORATIVE_IMAGE,
        visual_type=VisualType.IMAGE,
        asset_ref="shape_underlay.png",
    )


def _violations_of(result, vtype=ViolationType.TEXT_ON_BUSY_TEXTURE):
    return [v for v in result.violations if v.type == vtype]


def test_text_on_flat_background_passes(tmp_path):
    spec = _spec([_text_el("t1")], bg_ref=_flat_bg(tmp_path))
    cand = Candidate(
        candidate_id="c",
        elements=[LayoutElement(id="t1", left=50, top=50, width=200, height=80, z_index=1)],
    )
    assert not _violations_of(check_candidate(cand, spec))


def test_text_on_busy_region_flagged_with_underlay_directive(tmp_path):
    spec = _spec([_text_el("t1")], bg_ref=_half_busy_bg(tmp_path))
    cand = Candidate(
        candidate_id="c",
        elements=[LayoutElement(id="t1", left=250, top=50, width=120, height=80, z_index=1)],
    )
    viols = _violations_of(check_candidate(cand, spec))
    assert len(viols) == 1
    assert viols[0].targets == ["t1"]
    # Step 59 user decision: the detail must direct the Generator toward the
    # designer-GT solution (shield with an underlay), not just "move it".
    assert "underlay" in viols[0].detail
    assert "flatter" in viols[0].detail


def test_text_on_flat_region_of_busy_image_passes(tmp_path):
    """The rule is texture-LOCAL: a busy image elsewhere on the canvas must
    not penalise text sitting on its flat region."""
    spec = _spec([_text_el("t1")], bg_ref=_half_busy_bg(tmp_path))
    cand = Candidate(
        candidate_id="c",
        elements=[LayoutElement(id="t1", left=20, top=50, width=120, height=80, z_index=1)],
    )
    assert not _violations_of(check_candidate(cand, spec))


def test_underlay_shield_suppresses_violation(tmp_path):
    """Fully shielding the busy-region text with a decorative_image underlay
    is the designer-GT defense (8/20 GT layouts) and must pass."""
    spec = _spec([_text_el("t1"), _underlay_el("u1")], bg_ref=_half_busy_bg(tmp_path))
    cand = Candidate(
        candidate_id="c",
        elements=[
            LayoutElement(id="u1", left=240, top=40, width=140, height=100, z_index=1),
            LayoutElement(id="t1", left=250, top=50, width=120, height=80, z_index=2),
        ],
    )
    assert not _violations_of(check_candidate(cand, spec))


def test_cta_counts_as_text(tmp_path):
    """Classification mirrors the Rea metric (visual_type == text), NOT
    TEXT_SEMANTIC_TYPES: both live layouts the Step 59 calibration caught
    were CTA buttons (5f4f5e15 cta_1 0.0975, 5f56075f cta_1 0.0684)."""
    spec = _spec([_text_el("cta_1", semantic=SemanticType.CTA)], bg_ref=_half_busy_bg(tmp_path))
    cand = Candidate(
        candidate_id="c",
        elements=[LayoutElement(id="cta_1", left=250, top=50, width=120, height=80, z_index=1)],
    )
    viols = _violations_of(check_candidate(cand, spec))
    assert len(viols) == 1
    assert viols[0].targets == ["cta_1"]


def test_no_background_ref_skips_check(tmp_path):
    spec = _spec([_text_el("t1")], bg_ref=None)
    cand = Candidate(
        candidate_id="c",
        elements=[LayoutElement(id="t1", left=250, top=50, width=120, height=80, z_index=1)],
    )
    assert not _violations_of(check_candidate(cand, spec))


def test_missing_background_file_skips_gracefully(tmp_path):
    """Unloadable bg image must skip the check, never crash QC (step-12
    'never crash' philosophy)."""
    spec = _spec([_text_el("t1")], bg_ref=str(tmp_path / "does_not_exist.png"))
    cand = Candidate(
        candidate_id="c",
        elements=[LayoutElement(id="t1", left=250, top=50, width=120, height=80, z_index=1)],
    )
    assert not _violations_of(check_candidate(cand, spec))


def test_threshold_pinned_to_calibration():
    """TEXT_GRADIENT_MAX is a load-bearing calibration output (GT worst
    exposed element 0.0454 + 0.02 margin). Changing it requires re-running
    layout_agent/output/step59_text_gradient_calibration.py."""
    assert TEXT_GRADIENT_MAX == 0.065
