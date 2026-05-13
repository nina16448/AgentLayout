"""Pytest port of layout_agent/output/test_iou.py — pure-math, offline.

Original was a script with 7 test groups / 22 assertions. Same coverage, but in
pytest-friendly function form so CI can collect them via:

    pytest tests/metagpt/ext/agentlayout/test_iou.py -v --no-cov

Cost: $0. Runtime: <0.1s.
"""
from __future__ import annotations

import math

from metagpt.ext.agentlayout.evaluation import (
    LayoutIoUResult,
    bbox_iou,
    layout_iou,
)
from metagpt.ext.agentlayout.evaluation.iou import BBoxItem


# ============================================================
# bbox_iou pure-math edge cases (Test 1, 6 assertions)
# ============================================================


def test_bbox_iou_identical_bboxes_returns_one():
    assert math.isclose(bbox_iou((0, 0, 100, 100), (0, 0, 100, 100)), 1.0)


def test_bbox_iou_disjoint_bboxes_returns_zero():
    assert math.isclose(bbox_iou((0, 0, 50, 50), (200, 200, 50, 50)), 0.0)


def test_bbox_iou_half_horizontal_overlap_is_one_third():
    # A=[0,0,100,100] B=[50,0,100,100]
    # inter=50*100=5000 union=10000+10000-5000=15000 -> 1/3
    assert math.isclose(
        bbox_iou((0, 0, 100, 100), (50, 0, 100, 100)),
        1.0 / 3.0,
    )


def test_bbox_iou_nested_smaller_in_larger_is_quarter():
    # A=100x100, B=50x50 inside -> inter 2500 union 10000 -> 0.25
    assert math.isclose(bbox_iou((0, 0, 100, 100), (25, 25, 50, 50)), 0.25)


def test_bbox_iou_zero_width_returns_zero():
    assert math.isclose(bbox_iou((10, 10, 0, 50), (0, 0, 100, 100)), 0.0)


def test_bbox_iou_edge_touching_only_returns_zero():
    """Bboxes that share only a 1D edge produce zero intersection area."""
    assert math.isclose(bbox_iou((0, 0, 50, 50), (50, 0, 50, 50)), 0.0)


# ============================================================
# layout_iou matching (Test 2 + 3, 6 assertions)
# ============================================================


def test_layout_iou_two_perfect_matches():
    generated = [
        BBoxItem(id="title_1", bbox=(0, 0, 100, 50)),
        BBoxItem(id="logo_1", bbox=(0, 100, 50, 50)),
    ]
    ground_truth = [
        BBoxItem(id="0", bbox=(0, 0, 100, 50)),
        BBoxItem(id="1", bbox=(0, 100, 50, 50)),
    ]
    res = layout_iou(generated, ground_truth, {"title_1": "0", "logo_1": "1"})

    assert res.matched == 2
    assert math.isclose(res.mean, 1.0)
    assert math.isclose(res.per_element["title_1"], 1.0)
    assert res.unmatched_generated == []
    assert res.unmatched_gt == []


def test_layout_iou_mixed_quality_matches():
    """1.0 + (1/3) match averaged -> mean = (1 + 1/3) / 2."""
    generated = [
        BBoxItem(id="title_1", bbox=(0, 0, 100, 100)),
        BBoxItem(id="body_1", bbox=(50, 0, 100, 100)),
    ]
    ground_truth = [
        BBoxItem(id="0", bbox=(0, 0, 100, 100)),
        BBoxItem(id="1", bbox=(0, 0, 100, 100)),
    ]
    res = layout_iou(generated, ground_truth, {"title_1": "0", "body_1": "1"})

    assert res.matched == 2
    assert math.isclose(res.mean, (1.0 + 1.0 / 3.0) / 2.0)


# ============================================================
# Unmatched tracking (Test 4 + 5, 6 assertions)
# ============================================================


def test_layout_iou_unmatched_generated_when_no_id_map_entry():
    generated = [
        BBoxItem(id="title_1", bbox=(0, 0, 100, 100)),
        BBoxItem(id="ghost_1", bbox=(0, 0, 100, 100)),
    ]
    ground_truth = [BBoxItem(id="0", bbox=(0, 0, 100, 100))]
    res = layout_iou(generated, ground_truth, {"title_1": "0"})

    assert res.matched == 1
    assert res.unmatched_generated == ["ghost_1"]
    assert math.isclose(res.mean, 1.0)


def test_layout_iou_unmatched_gt_when_nobody_maps_to_it():
    generated = [BBoxItem(id="title_1", bbox=(0, 0, 100, 100))]
    ground_truth = [
        BBoxItem(id="0", bbox=(0, 0, 100, 100)),
        BBoxItem(id="1", bbox=(0, 0, 100, 100)),
    ]
    res = layout_iou(generated, ground_truth, {"title_1": "0"})

    assert res.matched == 1
    assert res.unmatched_gt == ["1"]


# ============================================================
# Empty inputs (Test 6, 3 assertions)
# ============================================================


def test_layout_iou_empty_generated_yields_zero_mean_and_unmatched_gt():
    res = layout_iou([], [BBoxItem(id="0", bbox=(0, 0, 1, 1))], {})

    assert res.matched == 0
    assert math.isclose(res.mean, 0.0)
    assert res.unmatched_gt == ["0"]


# ============================================================
# Pydantic round-trip (Test 7, 2 assertions)
# ============================================================


def test_layout_iou_result_json_round_trip_preserves_fields():
    res = layout_iou(
        [BBoxItem(id="a", bbox=(0, 0, 10, 10))],
        [BBoxItem(id="x", bbox=(5, 5, 10, 10))],
        {"a": "x"},
    )
    dumped = res.model_dump()
    assert set(dumped.keys()) == {
        "per_element",
        "mean",
        "matched",
        "unmatched_generated",
        "unmatched_gt",
    }

    restored = LayoutIoUResult.model_validate(dumped)
    assert math.isclose(restored.mean, res.mean)
