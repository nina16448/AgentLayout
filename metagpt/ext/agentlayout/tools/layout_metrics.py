"""Step 83 -- NEUTRAL machine measurements of a candidate layout.

The internal judge cannot see 20-200px geometry on a downscaled render, and
LLM absolute scoring without evidence collapses into "minor adjustments
needed" boilerplate (Step 82 trace: six near-identical verdicts, 34-39).
This module computes the facts and hands them to the judge as DESCRIPTORS,
never as judgements: full centering, for example, is legitimate in ~22% of
designer layouts, so whether a measurement is a flaw depends on the design --
that call stays with the judge.

GT reference values below were calibrated over the 1,746 cached Crello
designer layouts with >= 2 text elements (2026-07-03).
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from metagpt.ext.agentlayout.schema import (
    Candidate,
    DesignSpec,
    LayoutElement,
    UnderlayRegion,
    VisualType,
)

GT_METRICS = {
    "lockup_gap_p50": 0.014,   # nearest-text gap to the dominant text, / canvas h
    "lockup_gap_p75": 0.051,
    "lockup_gap_p90": 0.122,
    "left_groups_p50": 3,      # distinct left-edge alignment groups
    "left_groups_p75": 4,
    "center_groups_p50": 2,    # distinct center-x alignment groups
    "fully_centered_rate": 0.223,  # designer layouts with EVERY text centered
    "mean_centered_fraction": 0.342,
}

# Two x-positions belong to the same alignment group when closer than this
# fraction of canvas width (same tolerance used in the GT calibration).
ALIGN_TOL_FRAC: float = 0.02

# A text element counts as horizontally centered within this tolerance.
CENTER_TOL_FRAC: float = 0.02

# Text pair counts as overlapping above this fraction of the smaller area.
OVERLAP_MIN_FRACTION: float = 0.05


class LayoutMetrics(BaseModel):
    """Neutral geometry descriptors for one candidate."""

    n_texts: int
    centered_count: int
    centered_fraction: Optional[float] = None
    n_left_groups: Optional[int] = None
    n_center_groups: Optional[int] = None
    lockup_gap_frac: Optional[float] = Field(
        default=None,
        description="Edge-to-edge vertical gap between the dominant text and its "
        "nearest text, as a fraction of canvas height. None with < 2 texts.",
    )
    n_overlap_pairs: int = 0
    panel_utilization: Optional[float] = Field(
        default=None,
        description="Approximate share of total panel area covered by text "
        "(per-text intersections summed, capped at 1.0). None without panels.",
    )


def _text_ids(spec: DesignSpec) -> set:
    """Text-like elements: plain text OR pre-rendered text bitmaps (Step 80)."""
    return {
        el.id
        for el in spec.elements
        if el.visual_type == VisualType.TEXT
        or (el.asset_ref or "").endswith("_text.png")
    }


def _cluster_count(values: List[float], tol: float) -> int:
    values = sorted(values)
    groups = 0
    last = None
    for v in values:
        if last is None or v - last > tol:
            groups += 1
        last = v
    return groups


def _overlap_fraction(a: LayoutElement, b: LayoutElement) -> float:
    ix = max(0, min(a.left + a.width, b.left + b.width) - max(a.left, b.left))
    iy = max(0, min(a.top + a.height, b.top + b.height) - max(a.top, b.top))
    smaller = max(1, min(a.width * a.height, b.width * b.height))
    return ix * iy / smaller


def measure_layout(
    candidate: Candidate,
    spec: DesignSpec,
    regions: Optional[List[UnderlayRegion]] = None,
) -> LayoutMetrics:
    """Compute the neutral descriptors for ``candidate``."""
    cw = max(1, spec.canvas.width)
    ch = max(1, spec.canvas.height)
    ids = _text_ids(spec)
    texts = [el for el in candidate.elements if el.id in ids]

    metrics = LayoutMetrics(n_texts=len(texts), centered_count=0)
    if not texts:
        return metrics

    mid = cw / 2
    centered = [t for t in texts if abs((t.left + t.width / 2) - mid) <= CENTER_TOL_FRAC * cw]
    metrics.centered_count = len(centered)
    metrics.centered_fraction = round(len(centered) / len(texts), 3)
    metrics.n_left_groups = _cluster_count([float(t.left) for t in texts], ALIGN_TOL_FRAC * cw)
    metrics.n_center_groups = _cluster_count(
        [t.left + t.width / 2 for t in texts], ALIGN_TOL_FRAC * cw
    )

    if len(texts) >= 2:
        title = max(texts, key=lambda t: t.width * t.height)
        gaps = []
        for o in texts:
            if o is title:
                continue
            gap = max(0.0, max(title.top, o.top)
                      - min(title.top + title.height, o.top + o.height))
            gaps.append(gap / ch)
        metrics.lockup_gap_frac = round(min(gaps), 4)

    metrics.n_overlap_pairs = sum(
        1
        for i, a in enumerate(texts)
        for b in texts[i + 1:]
        if _overlap_fraction(a, b) > OVERLAP_MIN_FRACTION
    )

    if regions:
        panel_area = sum(
            max(0, (r.bbox[2] - r.bbox[0])) * max(0, (r.bbox[3] - r.bbox[1]))
            for r in regions
        )
        if panel_area > 0:
            inter = 0.0
            for t in texts:
                for r in regions:
                    ix = max(0, min(t.left + t.width, r.bbox[2]) - max(t.left, r.bbox[0]))
                    iy = max(0, min(t.top + t.height, r.bbox[3]) - max(t.top, r.bbox[1]))
                    inter += ix * iy
            metrics.panel_utilization = round(min(1.0, inter / panel_area), 3)

    return metrics


def format_metrics_block(metrics: LayoutMetrics) -> str:
    """Render the descriptors + GT reference values as NEUTRAL prompt text."""
    if metrics.n_texts == 0:
        return "(no text elements to measure)"
    g = GT_METRICS
    lines = [
        f"- {metrics.centered_count}/{metrics.n_texts} text elements horizontally "
        f"centered (reference: {g['fully_centered_rate']:.0%} of designer layouts "
        f"centre EVERY text -- full centering is a legitimate style)",
        f"- alignment: {metrics.n_left_groups} left-edge group(s), "
        f"{metrics.n_center_groups} center-x group(s) "
        f"(designer medians: {g['left_groups_p50']} left / {g['center_groups_p50']} center)",
    ]
    if metrics.lockup_gap_frac is not None:
        lines.append(
            f"- dominant-text lockup: nearest text is {metrics.lockup_gap_frac:.3f} "
            f"of canvas height away (designer p50={g['lockup_gap_p50']:.3f}, "
            f"p90={g['lockup_gap_p90']:.3f})"
        )
    lines.append(f"- overlapping text pairs: {metrics.n_overlap_pairs}")
    if metrics.panel_utilization is not None:
        lines.append(
            f"- baked-panel utilization: {metrics.panel_utilization:.0%} of panel "
            f"area covered by text"
        )
    lines.append(
        "These are NEUTRAL measurements, not verdicts: decide per measurement "
        "whether it fits THIS design's style and background."
    )
    return "\n".join(lines)
