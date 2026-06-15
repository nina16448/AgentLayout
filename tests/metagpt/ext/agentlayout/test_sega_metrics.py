"""Unit tests for SEGA / PKU PosterLayout rule-based metrics.

Sanity inputs only (geometric corner cases on a 1000x1000 canvas);
the file does not exercise readability / occlusion that need pixel
inputs -- those are integration-tested by the step20 driver against
real Crello backgrounds.
"""
from __future__ import annotations

import numpy as np
import pytest

from metagpt.ext.agentlayout.evaluation.sega_metrics import (
    CLS_IMAGE_LOGO,
    CLS_TEXT,
    CLS_UNDERLAY,
    drop_invalid_elements,
    layout_has_underlay,
    metric_alignment,
    metric_occlusion,
    metric_overlay,
    metric_readability,
    metric_underlay_loose,
    metric_underlay_strict,
    to_xyxy,
)


# ============================================================
# Overlay
# ============================================================


def test_overlay_two_identical_boxes_is_0_5():
    """Two identical text bboxes: 1 pair with IoU=1, n=2, score=1/2=0.5."""
    bb = to_xyxy(100, 100, 200, 200)
    layout = [(CLS_TEXT, bb), (CLS_TEXT, bb)]
    assert metric_overlay([layout]) == pytest.approx(0.5)


def test_overlay_no_overlap_returns_zero():
    """Two disjoint boxes -> 0 overlay."""
    a = to_xyxy(0, 0, 100, 100)
    b = to_xyxy(500, 500, 100, 100)
    layout = [(CLS_TEXT, a), (CLS_IMAGE_LOGO, b)]
    assert metric_overlay([layout]) == 0.0


def test_overlay_excludes_underlay_class():
    """Underlay (cls=3) must NOT contribute to overlay -- it is a deco shape
    intentionally overlapping non-underlay elements."""
    bb = to_xyxy(0, 0, 200, 200)
    layout = [
        (CLS_UNDERLAY, bb),
        (CLS_TEXT, bb),
        (CLS_TEXT, bb),
    ]
    # n=2 non-underlay, 1 pair IoU=1 -> 1/2 = 0.5
    assert metric_overlay([layout]) == pytest.approx(0.5)


# ============================================================
# Alignment
# ============================================================


def test_alignment_single_element_skipped():
    """Single-element layouts cannot violate alignment; mean over layouts is 0."""
    layout = [(CLS_TEXT, to_xyxy(100, 100, 200, 200))]
    assert metric_alignment([layout], canvas_w=1000, canvas_h=1000) == 0.0


def test_alignment_two_left_aligned_same_size_boxes_is_zero():
    """Two boxes with identical left+size (only top differs): they share
    left, right and center_x, so delta on left=0, right=0, center_x=0 axes
    -> per-element min(g)=0."""
    a = to_xyxy(100, 100, 200, 100)
    b = to_xyxy(100, 400, 200, 100)
    layout = [(CLS_TEXT, a), (CLS_TEXT, b)]
    assert metric_alignment([layout], canvas_w=1000, canvas_h=1000) == 0.0


def test_alignment_no_shared_axis_scores_positive():
    """Two boxes with ALL 6 axes distinct -> alignment score > 0 and finite."""
    a = to_xyxy(100, 100, 50, 50)
    b = to_xyxy(700, 700, 120, 80)  # different left/top/center/right/bottom
    layout = [(CLS_TEXT, a), (CLS_IMAGE_LOGO, b)]
    score = metric_alignment([layout], canvas_w=1000, canvas_h=1000)
    assert score > 0.0
    assert score < 5.0  # log-clipped, not inf


# ============================================================
# Underlay loose / strict
# ============================================================


def test_underlay_loose_returns_zero_when_no_underlay():
    """No deco element -> metric returns 0 (the honest 'no underlay' reading
    for AgentLayout, which currently cannot emit decoration shapes)."""
    layout = [
        (CLS_TEXT, to_xyxy(0, 0, 100, 100)),
        (CLS_IMAGE_LOGO, to_xyxy(200, 0, 100, 100)),
    ]
    assert metric_underlay_loose([layout]) == 0.0
    assert metric_underlay_strict([layout]) == 0.0


def test_underlay_loose_perfect_cover_scores_one():
    """A deco fully containing the text -> intersection-over-text = 1.0."""
    deco = to_xyxy(0, 0, 500, 500)
    text = to_xyxy(100, 100, 200, 200)
    layout = [(CLS_UNDERLAY, deco), (CLS_TEXT, text)]
    assert metric_underlay_loose([layout]) == pytest.approx(1.0)


def test_underlay_strict_one_contained_is_one():
    """1 deco fully containing 1 text -> strict = 1/1 = 1.0."""
    deco = to_xyxy(0, 0, 500, 500)
    text = to_xyxy(100, 100, 200, 200)
    layout = [(CLS_UNDERLAY, deco), (CLS_TEXT, text)]
    assert metric_underlay_strict([layout]) == pytest.approx(1.0)


def test_underlay_strict_partial_overlap_is_zero():
    """Deco partially overlaps text but does NOT contain it -> strict = 0."""
    deco = to_xyxy(0, 0, 150, 150)
    text = to_xyxy(100, 100, 200, 200)  # extends beyond deco right/bottom
    layout = [(CLS_UNDERLAY, deco), (CLS_TEXT, text)]
    assert metric_underlay_strict([layout]) == 0.0
    # Loose still earns partial credit (intersection-over-text)
    loose = metric_underlay_loose([layout])
    assert 0.0 < loose < 1.0


def test_underlay_strict_replicates_pku_right_edge_bug():
    """A5: PKU's is_contain never checks the right edge (bug `xr_2>=xr_2`).
    A text box that exceeds the deco's RIGHT edge only -- contained on
    left/top/bottom -- still counts as 'contained' (strict=1), matching the
    PKU/SEGA evaluator we are comparing against. (A mathematically-correct
    4-sided check would score 0 here.)"""
    deco = to_xyxy(0, 0, 200, 400)  # xyxy (0,0,200,400)
    text = to_xyxy(50, 50, 250, 50)  # xyxy (50,50,300,100): right 300 > deco 200
    layout = [(CLS_UNDERLAY, deco), (CLS_TEXT, text)]
    assert metric_underlay_strict([layout]) == pytest.approx(1.0)


# ============================================================
# Empty / edge inputs
# ============================================================


def test_empty_layout_list_returns_zero():
    assert metric_overlay([]) == 0.0
    assert metric_alignment([], canvas_w=1000, canvas_h=1000) == 0.0
    assert metric_underlay_loose([]) == 0.0
    assert metric_underlay_strict([]) == 0.0


def test_layout_with_only_invisible_elements_skipped():
    """cls <= 0 means 'invalid / empty slot'; such elements must be ignored."""
    layout = [(0, to_xyxy(0, 0, 100, 100)), (-1, to_xyxy(200, 200, 100, 100))]
    assert metric_overlay([layout]) == 0.0
    assert metric_alignment([layout], canvas_w=1000, canvas_h=1000) == 0.0


# ============================================================
# A2 -- drop_invalid_elements (PKU getRidOfInvalid)
# ============================================================


def test_drop_invalid_removes_sub_threshold_boxes():
    """A box below 0.1% of canvas area is dropped; a large one is kept."""
    canvas_w = canvas_h = 1000  # 0.1% area threshold = 1000 px
    big = (CLS_TEXT, to_xyxy(0, 0, 200, 200))  # 40000 px -> kept
    tiny = (CLS_TEXT, to_xyxy(500, 500, 10, 10))  # 100 px -> dropped
    kept = drop_invalid_elements([big, tiny], canvas_w, canvas_h)
    assert kept == [big]


def test_drop_invalid_clamps_to_canvas_before_area_test():
    """Off-canvas overhang doesn't inflate the area: the on-canvas part is
    what counts (matches PKU's clamp-then-measure)."""
    canvas_w = canvas_h = 1000  # threshold 1000 px
    # Box mostly off the right edge: on-canvas part is 5 wide x 300 tall = 1500 px (kept)
    partly_off = (CLS_TEXT, to_xyxy(995, 0, 300, 300))
    assert drop_invalid_elements([partly_off], canvas_w, canvas_h) == [partly_off]
    # Now a box whose on-canvas sliver is < threshold gets dropped
    sliver = (CLS_TEXT, to_xyxy(998, 0, 300, 300))  # on-canvas 2 x 300 = 600 px < 1000
    assert drop_invalid_elements([sliver], canvas_w, canvas_h) == []


# ============================================================
# A7 -- layout_has_underlay
# ============================================================


def test_layout_has_underlay_true_false():
    no_u = [(CLS_TEXT, to_xyxy(0, 0, 100, 100)), (CLS_IMAGE_LOGO, to_xyxy(0, 0, 50, 50))]
    with_u = no_u + [(CLS_UNDERLAY, to_xyxy(0, 0, 200, 200))]
    assert layout_has_underlay(no_u) is False
    assert layout_has_underlay(with_u) is True


# ============================================================
# A4 -- Readability / Occlusion denominator counts every evaluable sample
# ============================================================


def test_occlusion_denominator_counts_zero_contribution_sample():
    """A second layout that covers nothing contributes 0 but is still counted
    in the denominator (PKU len(img_names)); so mean halves, not stays."""
    sal = np.full((10, 10), 0.5, dtype=np.float32)
    covering = [(CLS_TEXT, to_xyxy(0, 0, 10, 10))]  # covers all -> mean sal 0.5
    empty = []  # covers nothing -> contributes 0
    one = metric_occlusion([covering], [sal], 10, 10)
    two = metric_occlusion([covering, empty], [sal, sal], 10, 10)
    assert one == pytest.approx(0.5)
    assert two == pytest.approx(0.25)  # (0.5 + 0) / 2, not 0.5 / 1


def test_readability_denominator_counts_textless_sample():
    """A layout with no TEXT element contributes 0 yet counts in the
    denominator: two-sample mean == one-sample value / 2 (A4)."""
    bg = np.zeros((10, 10, 3), dtype=np.uint8)
    bg[:, 5:, :] = 255  # vertical edge -> nonzero Sobel gradient
    text = [(CLS_TEXT, to_xyxy(0, 0, 10, 10))]
    no_text = [(CLS_IMAGE_LOGO, to_xyxy(0, 0, 10, 10))]  # not CLS_TEXT -> empty text mask
    one = metric_readability([text], [bg], 10, 10)
    two = metric_readability([text, no_text], [bg, bg], 10, 10)
    assert one > 0.0
    assert two == pytest.approx(one / 2.0)
