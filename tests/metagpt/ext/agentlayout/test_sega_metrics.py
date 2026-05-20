"""Unit tests for SEGA / PKU PosterLayout rule-based metrics.

Sanity inputs only (geometric corner cases on a 1000x1000 canvas);
the file does not exercise readability / occlusion that need pixel
inputs -- those are integration-tested by the step20 driver against
real Crello backgrounds.
"""
from __future__ import annotations

import pytest

from metagpt.ext.agentlayout.evaluation.sega_metrics import (
    CLS_IMAGE_LOGO,
    CLS_TEXT,
    CLS_UNDERLAY,
    metric_alignment,
    metric_overlay,
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
    """Two boxes with identical left+width+height (only top differs):
    delta on left=0, width=0, height=0 axes -> per-element min(g)=0."""
    a = to_xyxy(100, 100, 200, 100)
    b = to_xyxy(100, 400, 200, 100)
    layout = [(CLS_TEXT, a), (CLS_TEXT, b)]
    assert metric_alignment([layout], canvas_w=1000, canvas_h=1000) == 0.0


def test_alignment_no_shared_axis_scores_positive():
    """Two boxes with ALL 6 axes distinct -> alignment score > 0 and finite."""
    a = to_xyxy(100, 100, 50, 50)
    b = to_xyxy(700, 700, 120, 80)  # different left/top/center/width/height
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
