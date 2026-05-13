"""Trivial baseline layout generators for IoU comparison.

Why: ``layout_iou`` produces an absolute number in [0, 1] but the number
is meaningless without a reference point. A reviewer reading "AgentLayout
mIoU = 0.105" will rightly ask "is that good?". This module provides
two cheap reference baselines so the comparison is grounded:

  * ``random_layout``  -- each element gets uniformly random
    (left, top, width, height) within the canvas. Models the
    "no learning at all" floor.

  * ``centered_stack`` -- deterministic vertical stack, all elements
    centered horizontally, equal vertical slices. Models a "naive
    designer prior" that anyone can implement without LLMs.

Both functions return a list of ``BBoxItem`` whose ids match the input,
so they plug directly into ``layout_iou(..., id_map={id: id})`` for a
self-mapped run, or any caller-supplied id_map.

Constraint: every emitted bbox satisfies
  0 <= left, 0 <= top, left + width <= canvas_w, top + height <= canvas_h
so subsequent rendering / Quality Checker pass without boundary errors.
"""
from __future__ import annotations

import random
from typing import List

from metagpt.ext.agentlayout.evaluation.iou import BBoxItem


# Element size sampling range (fraction of canvas dim).
# 0.05..0.5 keeps elements neither degenerate nor canvas-spanning.
_RANDOM_SIZE_LO_FRAC = 0.05
_RANDOM_SIZE_HI_FRAC = 0.50


def random_layout(
    element_ids: List[str],
    canvas_w: float,
    canvas_h: float,
    seed: int = 0,
) -> List[BBoxItem]:
    """Uniformly random (left, top, width, height) per element.

    Both position and size are sampled. Bbox is guaranteed to fit on canvas.

    Args:
        element_ids: ordered list of unique element ids; the output list
            preserves this order so caller can build trivial id_maps.
        canvas_w / canvas_h: canvas dimensions in the same pixel space as
            the ground-truth annotations.
        seed: RNG seed so a paper / CI can reproduce the exact baseline run.

    Returns:
        list[BBoxItem] of length ``len(element_ids)``.
    """
    rng = random.Random(seed)
    items: List[BBoxItem] = []
    w_lo = canvas_w * _RANDOM_SIZE_LO_FRAC
    w_hi = canvas_w * _RANDOM_SIZE_HI_FRAC
    h_lo = canvas_h * _RANDOM_SIZE_LO_FRAC
    h_hi = canvas_h * _RANDOM_SIZE_HI_FRAC
    for eid in element_ids:
        w = rng.uniform(w_lo, w_hi)
        h = rng.uniform(h_lo, h_hi)
        # Pick a left/top such that the box stays inside the canvas.
        left = rng.uniform(0.0, max(0.0, canvas_w - w))
        top = rng.uniform(0.0, max(0.0, canvas_h - h))
        items.append(BBoxItem(id=eid, bbox=(left, top, w, h)))
    return items


def centered_stack(
    element_ids: List[str],
    canvas_w: float,
    canvas_h: float,
    horizontal_padding_frac: float = 0.10,
    vertical_gap_frac: float = 0.02,
) -> List[BBoxItem]:
    """Deterministic vertical stack, equal slices, centered horizontally.

    All elements share the same width = ``canvas_w * (1 - 2*pad)`` and
    are stacked top-to-bottom with a small vertical gap between them.
    Models the "naive layout designer prior" baseline.
    """
    n = len(element_ids)
    if n == 0:
        return []

    width = canvas_w * (1.0 - 2.0 * horizontal_padding_frac)
    left = canvas_w * horizontal_padding_frac
    gap = canvas_h * vertical_gap_frac
    available_h = canvas_h - gap * (n - 1)
    each_h = max(1.0, available_h / n)

    items: List[BBoxItem] = []
    cursor_top = 0.0
    for eid in element_ids:
        items.append(
            BBoxItem(id=eid, bbox=(left, cursor_top, width, each_h))
        )
        cursor_top += each_h + gap
    return items
