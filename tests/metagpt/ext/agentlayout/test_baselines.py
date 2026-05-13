"""Pytest port of layout_agent/output/test_baselines.py — pure-math, offline.

Original script: 7 test groups / 14 assertions covering random_layout (seed
determinism + boundary) and centered_stack (deterministic geometry).

Run via:

    pytest tests/metagpt/ext/agentlayout/test_baselines.py -v --no-cov

Cost: $0. Runtime: <0.1s.
"""
from __future__ import annotations

import math

from metagpt.ext.agentlayout.evaluation import centered_stack, random_layout


# ============================================================
# Helpers
# ============================================================


def _all_inside_canvas(items, w: int, h: int):
    """(ok, violator). Violator is the first item that leaks the canvas."""
    for it in items:
        l, t, ww, hh = it.bbox
        if l < 0 or t < 0 or l + ww > w + 1e-6 or t + hh > h + 1e-6:
            return False, it
    return True, None


# ============================================================
# random_layout — Test 1: determinism (2 assertions)
# ============================================================


def test_random_layout_same_seed_produces_same_output():
    ids = ["title_1", "logo_1", "body_1"]
    a = random_layout(ids, 800, 1200, seed=42)
    b = random_layout(ids, 800, 1200, seed=42)
    assert a == b


def test_random_layout_different_seed_produces_different_output():
    ids = ["title_1", "logo_1", "body_1"]
    a = random_layout(ids, 800, 1200, seed=42)
    c = random_layout(ids, 800, 1200, seed=43)
    assert a != c


# ============================================================
# random_layout — Test 2: boundary + ordering (3 assertions)
# ============================================================


def test_random_layout_fits_inside_canvas_and_preserves_id_order():
    ids = ["title_1", "logo_1", "body_1"]
    items = random_layout(ids, 800, 1200, seed=7)
    ok, bad = _all_inside_canvas(items, 800, 1200)

    assert ok, f"violator={bad}"
    assert len(items) == len(ids)
    assert [i.id for i in items] == ids


# ============================================================
# random_layout — Test 3: empty input (1 assertion)
# ============================================================


def test_random_layout_empty_input_yields_empty_output():
    assert random_layout([], 100, 100, seed=0) == []


# ============================================================
# random_layout — Test 4: fraction bounds (2 assertions)
# ============================================================


def test_random_layout_widths_and_heights_within_fraction_bounds():
    """Default size sampler: w/h fraction in [0.05, 0.50] of canvas."""
    items = random_layout(["a", "b", "c", "d", "e"], 1000, 1000, seed=1)
    widths = [i.bbox[2] for i in items]
    heights = [i.bbox[3] for i in items]

    # 0.05*1000 = 50, 0.50*1000 = 500
    assert all(50 <= w <= 500 + 1e-6 for w in widths), f"widths={widths}"
    assert all(50 <= h <= 500 + 1e-6 for h in heights), f"heights={heights}"


# ============================================================
# centered_stack — Test 5: deterministic geometry (4 assertions)
# ============================================================


def test_centered_stack_uniform_width_left_and_monotonic_tops():
    """All elements share width = w*(1-2*margin) and start at left = w*margin.

    margin=0.10 default -> width=800, left=100 for canvas_w=1000.
    """
    ids = ["a", "b", "c"]
    items = centered_stack(ids, canvas_w=1000, canvas_h=900)

    assert len(items) == 3
    assert all(math.isclose(it.bbox[2], 800.0) for it in items)
    assert all(math.isclose(it.bbox[0], 100.0) for it in items)

    tops = [it.bbox[1] for it in items]
    assert tops == sorted(tops), f"tops not monotonic: {tops}"


# ============================================================
# centered_stack — Test 6: empty input (1 assertion)
# ============================================================


def test_centered_stack_empty_input_yields_empty_output():
    assert centered_stack([], 100, 100) == []


# ============================================================
# centered_stack — Test 7: boundary (1 assertion)
# ============================================================


def test_centered_stack_all_inside_canvas_for_4_elements_on_800x1200():
    items = centered_stack(["a", "b", "c", "d"], 800, 1200)
    ok, bad = _all_inside_canvas(items, 800, 1200)
    assert ok, f"violator={bad}"
