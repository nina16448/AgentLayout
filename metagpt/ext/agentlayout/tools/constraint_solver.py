"""Constraint-solver placement (Step 66, 2026-06-13).

Steps 30-65 established that the pipeline is Generator-bounded: the LLM
Layout Generator systematically fails to execute the Composition Director's
GT-calibrated sketch at the pixel level, and neither textual QC feedback
(Step 59) nor visual self-correction (Step 65) moves it. This module removes
the LLM from geometry entirely: given the same inputs the Generator receives
(DesignSpec + CompositionDirective + BackgroundAnalysis), it CONSTRUCTS a
candidate from the exact same math the Quality Checker verifies --
``cell_bounds`` / ``SIZE_BUCKET_RANGES`` for composition, the 30% riding /
80% underlay-cover contract for text-on-photo, the coverage / dead-band
guardrails, and the Step 59 Sobel gradient map for busy-texture avoidance.

What stays with the LLM: brief analysis (Analyst), template choice
(Composition Director) and judging. What becomes deterministic: every bbox,
z-index and the typography defaults.

The solver is a small search, not a single formula: for templates where text
sits directly on the background it scores a 3x3 grid of anchor points inside
the directive cell by (mean background gradient under the text, safe-zone
containment) and picks the argmin -- the numeric hill-descent the Generator
never performed.

``variant`` (0/1/2) gives the oracle's retry loop distinct deterministic
outputs: 0 = calibrated defaults, 1 = bolder type scale, 2 = conservative
type scale with a photo-size nudge. Same (spec, bg, variant) always yields
the same candidate.
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image

from metagpt.ext.agentlayout.schema import (
    BackgroundAnalysis,
    Candidate,
    DesignSpec,
    Element,
    LayoutElement,
    SemanticType,
    VisualType,
)
from metagpt.ext.agentlayout.tools.composition_templates import (
    SIZE_BUCKET_RANGES,
    cell_bounds,
)
from metagpt.ext.agentlayout.tools import quality_checker as qc
from metagpt.ext.agentlayout.tools.renderer import (
    _MEASURE_DRAW,
    _resolve_font,
    _wrap_to_width,
)
from metagpt.logs import logger

# ============================================================
# Calibration constants
# ============================================================

SIZE_BUCKET_TARGET: Dict[str, float] = {
    "small": 0.125,
    "medium": 0.325,
    "large": 0.625,
    "bleed": 0.875,
}
"""Target photo area ratio per directive bucket = midpoint of the QC's
``SIZE_BUCKET_RANGES`` interval, so the constructed photo sits maximally far
from both bucket edges (COMPOSITION_SIZE_MARGIN never comes into play)."""

TEXT_PRIORITY: Dict[SemanticType, int] = {
    SemanticType.TITLE: 0,
    SemanticType.SUBTITLE: 1,
    SemanticType.BODY_TEXT: 2,
    SemanticType.CAPTION: 3,
    SemanticType.CTA: 4,
    SemanticType.PRICETAG: 5,
    SemanticType.OTHER: 6,
}
"""Top-to-bottom stacking order of the text column (designer reading order)."""

FONT_SCALE: Dict[SemanticType, float] = {
    SemanticType.TITLE: 0.095,
    SemanticType.SUBTITLE: 0.052,
    SemanticType.BODY_TEXT: 0.038,
    SemanticType.CAPTION: 0.028,
    SemanticType.CTA: 0.042,
    SemanticType.PRICETAG: 0.048,
    SemanticType.OTHER: 0.036,
}
"""Font size = scale * min(canvas_w, canvas_h). Ratios eyeballed from the
Step 61 GT gallery; the dead-band/coverage repair loop below adjusts them
when a sparse spec leaves the canvas under-filled."""

MIN_SOLVER_FONT: int = 11
"""Lower bound for solver-chosen font sizes (renderer's own floor is 8)."""

STACK_GAP_SCALE: float = 0.022
"""Vertical gap between stacked text elements, as a fraction of min(cw, ch)."""

TEXT_PAD_X: int = 6
TEXT_PAD_Y_SCALE: float = 0.18
"""Bbox padding added around the measured text so the renderer's
shrink-to-fit never fires (it only shrinks when the text overflows)."""

EDGE_MARGIN_SCALE: float = 0.02
"""Minimum gap kept between any element and the canvas edge."""

UNDERLAY_PAD_SCALE: float = 0.06
"""Underlay bbox expansion per side relative to the text-stack bbox: the
Step 64 contract demands the underlay cover >= 80% of each riding text; full
containment plus this designer-style breathing room satisfies it trivially."""

ANCHOR_GRID: Tuple[float, ...] = (0.0, 0.25, 0.50, 0.75, 1.0)
"""Anchor candidates per axis, as fractions of the directive cell expanded by
80% of the QC's COMPOSITION_CELL_TOLERANCE on each side -- a 5x5 grid whose
extremes exploit the tolerance the composition check grants, giving the
gradient search enough reach to escape a busy half of the cell while still
passing the cell check by construction."""

ANCHOR_TOL_FRACTION: float = 0.8
"""Fraction of COMPOSITION_CELL_TOLERANCE the anchor grid may exploit (the
remaining 20% absorbs the centroid rounding/clamping drift)."""

VARIANTS: Tuple[Dict, ...] = (
    {"font_mult": 1.00, "photo_nudge": 0.0},
    {"font_mult": 1.18, "photo_nudge": 0.0},
    {"font_mult": 0.86, "photo_nudge": 0.05},
)


def _variant(variant: int) -> Dict:
    return VARIANTS[variant % len(VARIANTS)]


# ============================================================
# Element grouping
# ============================================================


def _group_elements(spec: DesignSpec) -> Dict[str, List[Element]]:
    groups: Dict[str, List[Element]] = {
        "background": [],
        "photos": [],
        "underlays": [],
        "texts": [],
        "others": [],
    }
    for el in spec.elements:
        if el.semantic_type == SemanticType.BACKGROUND_IMAGE:
            groups["background"].append(el)
        elif el.visual_type == VisualType.TEXT:
            groups["texts"].append(el)
        elif el.semantic_type == SemanticType.PRODUCT_IMAGE:
            groups["photos"].append(el)
        elif el.semantic_type == SemanticType.DECORATIVE_IMAGE:
            groups["underlays"].append(el)
        else:
            groups["others"].append(el)
    groups["texts"].sort(key=lambda e: TEXT_PRIORITY.get(e.semantic_type, 9))
    return groups


def _asset_aspect(el: Element) -> float:
    """width / height of the native asset; 1.0 when unreadable (renderer
    letterboxes small assets anyway, so a wrong aspect only soft-degrades)."""
    if el.asset_ref:
        try:
            with Image.open(el.asset_ref) as img:
                if img.height > 0:
                    return img.width / img.height
        except (OSError, IOError, ValueError):
            pass
    return 1.0


# ============================================================
# Photo placement
# ============================================================


def _place_photo(
    el: Element,
    cell: str,
    area_ratio: float,
    cw: int,
    ch: int,
    bias: Optional[str] = None,
) -> LayoutElement:
    """Aspect-preserving bbox of ``area_ratio * canvas`` centered in ``cell``.

    Construction guarantees both halves of the QC composition contract: the
    center is the exact cell center (cell check) and the area is the bucket
    midpoint (size check), unless the canvas clamp forces a trade -- then the
    area is preserved over the aspect (the same trade the QC feedback asks
    the Generator to make).

    ``bias`` ("above" / "below" = side the TEXT band occupies under a stacked
    relation) pushes the photo center to the opposite cell edge -- still
    inside the cell, but freeing the maximum band for the text so the relation
    classifier cannot resolve the layout as text-on-photo.
    """
    aspect = _asset_aspect(el)
    target_area = area_ratio * cw * ch
    w = math.sqrt(target_area * aspect)
    h = target_area / w
    if w > cw:  # too wide at native aspect: pin width, keep the area
        w = float(cw)
        h = min(float(ch), target_area / w)
    if h > ch:
        h = float(ch)
        w = min(float(cw), target_area / h)
    xl, yt, xr, yb = cell_bounds(cell, cw, ch)
    cx, cy = (xl + xr) / 2.0, (yt + yb) / 2.0
    if bias == "above":  # text above -> photo hugs the cell's bottom edge
        cy = yb
    elif bias == "below":  # text below -> photo hugs the cell's top edge
        cy = yt
    left = int(round(min(max(cx - w / 2.0, 0.0), cw - w)))
    top = int(round(min(max(cy - h / 2.0, 0.0), ch - h)))
    return LayoutElement(
        id=el.id, left=left, top=top, width=int(round(w)), height=int(round(h)), z_index=1
    )


# ============================================================
# Text measurement and stacking
# ============================================================


def _style_for(el: Element, style_keywords: Sequence[str]) -> Tuple[str, str]:
    """(font_family, font_weight) defaults per semantic role."""
    scripty = {"elegant", "romantic", "wedding", "script", "handwritten", "cursive", "feminine"}
    kw = {k.strip().lower() for k in style_keywords}
    if el.semantic_type == SemanticType.TITLE:
        if kw & scripty:
            return "script", "regular"
        return "display", "bold"
    if el.semantic_type in (SemanticType.SUBTITLE, SemanticType.CTA):
        return "sans-serif", "bold"
    if el.semantic_type == SemanticType.PRICETAG:
        return "display", "bold"
    return "sans-serif", "regular"


def _measure_text(
    el: Element, font_size: int, family: str, weight: str, max_w: int
) -> Tuple[int, int]:
    """(bbox_w, bbox_h) of ``el.content`` wrapped to ``max_w`` at ``font_size``,
    using the renderer's own font resolution so render == measurement."""
    text = (el.content or el.id).strip() or el.id
    font = _resolve_font(family, weight, font_size)
    wrapped = text if "\n" in text else _wrap_to_width(text, font, max_w)
    bbox = _MEASURE_DRAW.multiline_textbbox((0, 0), wrapped, font=font)
    w = bbox[2] - bbox[0] + TEXT_PAD_X * 2
    h = bbox[3] - bbox[1] + int(font_size * TEXT_PAD_Y_SCALE) + 4
    return max(1, int(math.ceil(w))), max(1, int(math.ceil(h)))


class _StackItem:
    __slots__ = ("el", "w", "h", "font_size", "family", "weight", "rel_left", "rel_top")

    def __init__(self, el: Element, w: int, h: int, font_size: int, family: str, weight: str):
        self.el = el
        self.w = w
        self.h = h
        self.font_size = font_size
        self.family = family
        self.weight = weight
        self.rel_left = 0
        self.rel_top = 0


def _build_stack(
    texts: List[Element],
    base: float,
    font_mult: float,
    max_w: int,
    align: str,
    style_keywords: Sequence[str],
    gap_mult: float = 1.0,
) -> List[_StackItem]:
    """Measure every text and lay them out relative to a (0, 0) stack origin."""
    items: List[_StackItem] = []
    for el in texts:
        scale = FONT_SCALE.get(el.semantic_type, FONT_SCALE[SemanticType.OTHER])
        size = max(MIN_SOLVER_FONT, int(round(scale * base * font_mult)))
        family, weight = _style_for(el, style_keywords)
        w, h = _measure_text(el, size, family, weight, max_w)
        items.append(_StackItem(el, min(w, max_w), h, size, family, weight))
    gap = max(6, int(round(STACK_GAP_SCALE * base * gap_mult)))
    stack_w = max(item.w for item in items)
    y = 0
    for item in items:
        if align == "left":
            item.rel_left = 0
        elif align == "right":
            item.rel_left = stack_w - item.w
        else:
            item.rel_left = (stack_w - item.w) // 2
        item.rel_top = y
        y += item.h + gap
    return items


def _stack_centroid(items: List[_StackItem]) -> Tuple[float, float]:
    """Area-weighted centroid in stack-relative coordinates -- the exact
    quantity ``qc._text_mass_center`` checks against the directive cell."""
    total = tx = ty = 0.0
    for it in items:
        a = float(it.w * it.h)
        total += a
        tx += a * (it.rel_left + it.w / 2.0)
        ty += a * (it.rel_top + it.h / 2.0)
    return tx / total, ty / total


def _stack_bbox(items: List[_StackItem], ox: int, oy: int) -> Tuple[int, int, int, int]:
    xl = min(ox + it.rel_left for it in items)
    yt = min(oy + it.rel_top for it in items)
    xr = max(ox + it.rel_left + it.w for it in items)
    yb = max(oy + it.rel_top + it.h for it in items)
    return xl, yt, xr, yb


def _origin_for_centroid(
    items: List[_StackItem],
    target: Tuple[float, float],
    cw: int,
    ch: int,
    margin: int,
) -> Tuple[int, int]:
    """Stack origin that puts the area-weighted centroid on ``target``,
    clamped so the whole stack stays inside the canvas margins."""
    cx, cy = _stack_centroid(items)
    ox = int(round(target[0] - cx))
    oy = int(round(target[1] - cy))
    xl, yt, xr, yb = _stack_bbox(items, ox, oy)
    if xl < margin:
        ox += margin - xl
    if yt < margin:
        oy += margin - yt
    xl, yt, xr, yb = _stack_bbox(items, ox, oy)
    if xr > cw - margin:
        ox -= xr - (cw - margin)
    if yb > ch - margin:
        oy -= yb - (ch - margin)
    return ox, oy


LUMINANCE_WHITE_TEXT_MAX: float = 140.0
"""Mean 8-bit luminance of the ground under the text stack below which the
solver picks white text instead of near-black. Slightly above the 128
midpoint: on mid-tone grounds designers overwhelmingly reach for white."""


def _region_luminance(img_path: str, frac_box: Tuple[float, float, float, float]) -> Optional[float]:
    """Mean luminance of a fractional region of an image file, or None."""
    try:
        from PIL import ImageStat

        with Image.open(img_path) as img:
            g = img.convert("L")
            w, h = g.size
            xl = max(0, min(w - 1, int(frac_box[0] * w)))
            yt = max(0, min(h - 1, int(frac_box[1] * h)))
            xr = max(xl + 1, min(w, int(frac_box[2] * w)))
            yb = max(yt + 1, min(h, int(frac_box[3] * h)))
            crop = g.crop((xl, yt, xr, yb))
            crop.thumbnail((64, 64))
            return float(ImageStat.Stat(crop).mean[0])
    except (OSError, IOError, ValueError):
        return None


def _pick_text_color(
    spec: DesignSpec,
    photo_box: Optional[LayoutElement],
    photo_el: Optional[Element],
    riding: bool,
    stack_box: Tuple[int, int, int, int],
    bg: Optional[BackgroundAnalysis],
) -> str:
    """Luminance-aware text color (step66 smoke fix: low_contrast_text 15/15).

    ``bg.recommended_text_color`` keys off the global background luminance,
    which is wrong precisely where it matters most -- text riding a dark
    focal photo on a light canvas. The ground that decides readability is
    the region UNDER the text stack: the photo crop when riding, else the
    background-asset crop, else the canvas plate color.
    """
    sxl, syt, sxr, syb = stack_box
    lum: Optional[float] = None
    if riding and photo_box is not None and photo_el is not None and photo_el.asset_ref:
        pw = max(1.0, float(photo_box.width))
        ph = max(1.0, float(photo_box.height))
        lum = _region_luminance(
            photo_el.asset_ref,
            (
                (sxl - photo_box.left) / pw,
                (syt - photo_box.top) / ph,
                (sxr - photo_box.left) / pw,
                (syb - photo_box.top) / ph,
            ),
        )
    if lum is None and spec.canvas.background_asset_ref:
        cw = max(1.0, float(spec.canvas.width))
        ch = max(1.0, float(spec.canvas.height))
        lum = _region_luminance(
            spec.canvas.background_asset_ref,
            (sxl / cw, syt / ch, sxr / cw, syb / ch),
        )
    if lum is None and spec.canvas.background_color:
        rgb = qc._hex_to_rgb01(spec.canvas.background_color)
        if rgb is not None:
            lum = 255.0 * (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2])
    if lum is None:
        return (bg.recommended_text_color if bg else None) or "#111111"
    return "#FFFFFF" if lum < LUMINANCE_WHITE_TEXT_MAX else "#111111"


def _split_around_photo(
    items: List[_StackItem],
    photo: LayoutElement,
    cw: int,
    ch: int,
    margin: int,
) -> None:
    """Centered-mix placement: rewrite the stack items' rel coords (with a
    (0, 0) origin) so the texts straddle the photo, area-balanced.

    The relation classifier calls a candidate centered-mix when the text mass
    rides the photo < 30% AND the centroid sits within canvas/6 of the photo
    center on both axes -- so we split greedily by area, then if the centroid
    still drifts past the limit, push the lighter side outward to lengthen
    its lever arm.
    """
    above: List[_StackItem] = []
    below: List[_StackItem] = []
    a_area = b_area = 0.0
    for it in items:
        if a_area <= b_area:
            above.append(it)
            a_area += float(it.w * it.h)
        else:
            below.append(it)
            b_area += float(it.w * it.h)
    gap = max(6, margin // 2)
    cx = photo.left + photo.width / 2.0
    y = photo.top - gap
    for it in reversed(above):
        it.rel_top = int(round(y - it.h))
        it.rel_left = int(round(cx - it.w / 2.0))
        y = it.rel_top - gap
    y = photo.top + photo.height + gap
    for it in below:
        it.rel_top = int(round(y))
        it.rel_left = int(round(cx - it.w / 2.0))
        y = it.rel_top + it.h + gap
    for it in items:
        it.rel_left = max(margin, min(it.rel_left, cw - margin - it.w))
        it.rel_top = max(margin, min(it.rel_top, ch - margin - it.h))
    pcy = photo.top + photo.height / 2.0
    _, cyg = _stack_centroid(items)
    dy = cyg - pcy
    limit = ch * (qc.RELATION_AXIS_FRACTION - 0.03)
    if abs(dy) > limit and above and below:
        side = below if dy < 0 else above
        s_area = sum(float(it.w * it.h) for it in side)
        total = sum(float(it.w * it.h) for it in items)
        delta = int(round(abs(dy) * total / max(s_area, 1.0)))
        sign = 1 if dy < 0 else -1
        for it in side:
            it.rel_top = max(margin, min(it.rel_top + sign * delta, ch - margin - it.h))


# ============================================================
# Anchor search (gradient + safe zones)
# ============================================================


def _gradient_under(
    grad, boxes: List[Tuple[int, int, int, int]], cw: int, ch: int
) -> float:
    """Mean background gradient under the union of ``boxes`` (Step 59 map)."""
    import numpy as np

    mask = np.zeros((ch, cw), dtype=bool)
    for xl, yt, xr, yb in boxes:
        mask[max(0, yt): max(0, yb), max(0, xl): max(0, xr)] = True
    if not mask.any():
        return 0.0
    return float(grad[mask].mean())


def _safe_zone_containment(
    bbox: Tuple[int, int, int, int], bg: Optional[BackgroundAnalysis]
) -> float:
    """Fraction of ``bbox`` area inside the best single safe zone."""
    if bg is None or not bg.safe_zones:
        return 0.0
    xl, yt, xr, yb = bbox
    area = max(1, (xr - xl) * (yb - yt))
    best = 0.0
    for sz in bg.safe_zones:
        sl, st, sr, sb = sz.bbox
        ix = max(0, min(xr, sr) - max(xl, sl))
        iy = max(0, min(yb, sb) - max(yt, st))
        best = max(best, (ix * iy) / area)
    return best


def _pick_anchor(
    items: List[_StackItem],
    cell: str,
    spec: DesignSpec,
    bg: Optional[BackgroundAnalysis],
    margin: int,
    rank: int = 0,
) -> Tuple[int, int]:
    """Best (origin_x, origin_y) for the text stack inside the directive cell.

    Scores a 3x3 anchor grid by (gradient under text ASC, safe-zone
    containment DESC, distance to cell center ASC) and returns the
    ``rank``-th best -- rank > 0 lets retry variants take the runner-up
    instead of re-emitting the identical layout.
    """
    cw, ch = spec.canvas.width, spec.canvas.height
    xl, yt, xr, yb = cell_bounds(cell, cw, ch)
    tol_x = qc.COMPOSITION_CELL_TOLERANCE * cw * ANCHOR_TOL_FRACTION
    tol_y = qc.COMPOSITION_CELL_TOLERANCE * ch * ANCHOR_TOL_FRACTION
    xl, xr = xl - tol_x, xr + tol_x
    yt, yb = yt - tol_y, yb + tol_y
    grad = None
    if spec.canvas.background_asset_ref:
        grad = qc._bg_gradient_map(spec.canvas.background_asset_ref, cw, ch)
    scored: List[Tuple[float, float, float, Tuple[int, int]]] = []
    for fy in ANCHOR_GRID:
        for fx in ANCHOR_GRID:
            target = (xl + fx * (xr - xl), yt + fy * (yb - yt))
            origin = _origin_for_centroid(items, target, cw, ch, margin)
            boxes = [
                (origin[0] + it.rel_left, origin[1] + it.rel_top,
                 origin[0] + it.rel_left + it.w, origin[1] + it.rel_top + it.h)
                for it in items
            ]
            g = _gradient_under(grad, boxes, cw, ch) if grad is not None else 0.0
            sz = _safe_zone_containment(_stack_bbox(items, *origin), bg)
            dist = abs(fx - 0.5) + abs(fy - 0.5)
            # Gradient binned to 0.01: on a uniformly busy background the
            # per-anchor differences are sampling noise, and unbinned they
            # dragged the stack off-center for nothing (step66 smoke,
            # composition_unbalanced flag). Real busy-vs-flat contrasts span
            # multiple bins and still dominate the sort.
            scored.append((round(g, 2), round(-sz, 3), dist, origin))
    scored.sort(key=lambda t: t[:3])
    return scored[min(rank, len(scored) - 1)][3]


# ============================================================
# Public API
# ============================================================


def solve_placement(
    spec: DesignSpec,
    bg: Optional[BackgroundAnalysis] = None,
    variant: int = 0,
) -> Candidate:
    """Deterministically construct a QC-compliant candidate from the spec.

    Honors ``spec.composition`` when present (the Composition Director's
    directive); falls back to the GT-default centered composition otherwise.
    Same inputs + same ``variant`` always produce the same candidate.
    """
    cw, ch = spec.canvas.width, spec.canvas.height
    base = float(min(cw, ch))
    margin = max(4, int(round(EDGE_MARGIN_SCALE * base)))
    cfg = _variant(variant)
    groups = _group_elements(spec)
    comp = spec.composition

    photo_cell = (comp.photo_cell if comp else None) or "MC"
    text_cell = (comp.text_cell if comp else None) or "MC"
    relation = comp.relation if comp else None
    photo_size = (comp.photo_size if comp else None) or "medium"

    elements: List[LayoutElement] = []

    # 1. Background plate: full canvas at z=0 (renderer paints it via
    #    _make_canvas; the bbox exists for completeness/z-order checks).
    for el in groups["background"]:
        elements.append(
            LayoutElement(id=el.id, left=0, top=0, width=cw, height=ch, z_index=0)
        )

    # 2. Focal photo at the directive cell/bucket midpoint. Under a stacked
    #    relation the text band side decides the photo's in-cell bias.
    stack_side: Optional[str] = None
    if relation == "stacked" and groups["texts"]:
        t_row = "TMB".index(text_cell[0])
        p_row = "TMB".index(photo_cell[0])
        if t_row != p_row:
            stack_side = "above" if t_row < p_row else "below"
        else:
            stack_side = "above" if p_row >= 1 else "below"
    photo_box: Optional[LayoutElement] = None
    if groups["photos"]:
        lo, hi = SIZE_BUCKET_RANGES.get(photo_size, SIZE_BUCKET_RANGES["medium"])
        ratio = SIZE_BUCKET_TARGET.get(photo_size, SIZE_BUCKET_TARGET["medium"])
        ratio = min(max(ratio + cfg["photo_nudge"], lo + 0.02), hi - 0.02)
        photo_box = _place_photo(
            groups["photos"][0], photo_cell, ratio, cw, ch, bias=stack_side
        )
        elements.append(photo_box)

    # 3. Secondary photos: small row along the bottom edge, clear of the focal.
    secondaries = groups["photos"][1:]
    if secondaries:
        n = len(secondaries)
        sw = int(min(0.22 * cw, (cw - (n + 1) * margin) / n))
        sh = int(0.8 * sw)
        total = n * sw + (n - 1) * margin
        x = (cw - total) // 2
        for el in secondaries:
            elements.append(
                LayoutElement(
                    id=el.id, left=x, top=ch - margin - sh, width=sw, height=sh, z_index=2
                )
            )
            x += sw + margin

    # 4. Text stack measured with the renderer's own fonts.
    text_align = {"L": "left", "C": "center", "R": "right"}[text_cell[1]]
    stack: List[_StackItem] = []
    origin: Tuple[int, int] = (0, 0)
    if groups["texts"]:
        if relation == "text-on-photo" and photo_box is not None:
            max_w = max(60, int(photo_box.width * 0.86))
        elif relation == "side-by-side":
            max_w = max(60, int(cw * 0.30))
        else:
            max_w = max(60, int(cw * 0.76))
        font_mult = cfg["font_mult"]
        stack = _build_stack(
            groups["texts"], base, font_mult, max_w, text_align, spec.style_keywords
        )

        used_anchor = False
        if relation == "text-on-photo" and photo_box is not None:
            # Riding contract: shrink type until the stack fits INSIDE the
            # photo bbox, then center it there -- every text then overlaps the
            # photo 100% >= the 30% riding threshold, and the relation
            # classifier resolves to text-on-photo by construction.
            for _ in range(4):
                _, syt, _, syb = _stack_bbox(stack, 0, 0)
                if syb - syt <= photo_box.height * 0.92:
                    break
                font_mult *= 0.88
                stack = _build_stack(
                    groups["texts"], base, font_mult, max_w, text_align, spec.style_keywords
                )
            target = (
                photo_box.left + photo_box.width / 2.0,
                photo_box.top + photo_box.height / 2.0,
            )
            origin = _origin_for_centroid(stack, target, cw, ch, margin)
            # Clamp the stack inside the photo so partial riders cannot fall
            # under the 30% overlap threshold.
            xl, yt, xr, yb = _stack_bbox(stack, *origin)
            ox, oy = origin
            if yt < photo_box.top:
                oy += photo_box.top - yt
            if yb > photo_box.top + photo_box.height:
                oy -= yb - (photo_box.top + photo_box.height)
            origin = (ox, oy)
        elif relation == "stacked" and photo_box is not None and stack_side:
            # Stacked relation: the text band must NOT ride the photo (the
            # relation classifier turns >= 30% overlap into text-on-photo),
            # so shrink the stack into the band the photo bias freed up and
            # hard-clamp it clear of the photo edge.
            above = stack_side == "above"
            photo_bottom = photo_box.top + photo_box.height
            for _ in range(5):
                sxl, syt, sxr, syb = _stack_bbox(stack, 0, 0)
                avail = (
                    photo_box.top - 2 * margin
                    if above
                    else ch - photo_bottom - 2 * margin
                )
                if syb - syt <= max(20, avail):
                    break
                font_mult *= 0.85
                stack = _build_stack(
                    groups["texts"], base, font_mult, max_w, text_align, spec.style_keywords
                )
            xl_c, yt_c, xr_c, yb_c = cell_bounds(text_cell, cw, ch)
            band_mid = (
                (margin + photo_box.top) / 2.0
                if above
                else (photo_bottom + ch - margin) / 2.0
            )
            origin = _origin_for_centroid(
                stack, ((xl_c + xr_c) / 2.0, band_mid), cw, ch, margin
            )
            sxl, syt, sxr, syb = _stack_bbox(stack, *origin)
            if above and syb > photo_box.top - 2:
                origin = (origin[0], origin[1] - (syb - (photo_box.top - 2)))
            if not above and syt < photo_bottom + 2:
                origin = (origin[0], origin[1] + (photo_bottom + 2 - syt))
        elif relation == "centered-mix" and photo_box is not None:
            # Centered-mix: interleave the texts above and below the photo,
            # area-balanced so the combined centroid stays within the
            # classifier's canvas/6 offset of the photo center.
            _split_around_photo(stack, photo_box, cw, ch, margin)
            origin = (0, 0)
        else:
            used_anchor = True
            origin = _pick_anchor(stack, text_cell, spec, bg, margin, rank=variant)

        # 5. Coverage / dead-band repair (anchor mode only -- the photo
        #    relations above own their geometry): bump the type scale when
        #    the foreground union under-fills the canvas, spread the stack
        #    with a larger gap for vertical blank bands, nudge the anchor
        #    toward canvas center for horizontal bands.
        for _ in range(3):
            if not used_anchor:
                break
            fg_boxes = _fg_intervals(elements, stack, origin)
            shims = [
                SimpleNamespace(left=l, top=t, width=r - l, height=b - t)
                for (l, t, r, b) in fg_boxes
            ]
            cov = qc._union_coverage_ratio(shims, float(cw), float(ch))
            v_dead = qc._max_dead_band([(t, b) for (_, t, _, b) in fg_boxes], float(ch))
            h_dead = qc._max_dead_band([(l, r) for (l, _, r, _) in fg_boxes], float(cw))
            if (
                cov >= qc.CANVAS_COVERAGE_MIN + 0.02
                and v_dead <= qc.DEAD_BAND_MAX - 0.02
                and h_dead <= qc.DEAD_BAND_MAX - 0.02
            ):
                break
            if cov < qc.CANVAS_COVERAGE_MIN + 0.02 and photo_box is None:
                # Area scales ~quadratically with the font size, so one
                # sqrt-sized bump closes the gap instead of three blind +25%s.
                font_mult *= min(
                    1.8,
                    math.sqrt((qc.CANVAS_COVERAGE_MIN + 0.04) / max(cov, 0.01)),
                )
                stack = _build_stack(
                    groups["texts"], base, font_mult, max_w, text_align,
                    spec.style_keywords,
                )
                origin = _pick_anchor(stack, text_cell, spec, bg, margin, rank=variant)
                continue
            if v_dead > qc.DEAD_BAND_MAX - 0.02:
                stack = _build_stack(
                    groups["texts"], base, font_mult, max_w, text_align,
                    spec.style_keywords, gap_mult=3.5,
                )
                origin = _pick_anchor(stack, text_cell, spec, bg, margin, rank=variant)
            if h_dead > qc.DEAD_BAND_MAX - 0.02:
                xl, yt, xr, yb = _stack_bbox(stack, *origin)
                deficit = int((h_dead - qc.DEAD_BAND_MAX + 0.04) * cw)
                shift = deficit if xl > cw - xr else -deficit
                origin = (origin[0] + shift, origin[1])

        text_color = _pick_text_color(
            spec,
            photo_box,
            groups["photos"][0] if groups["photos"] else None,
            relation == "text-on-photo" and photo_box is not None,
            _stack_bbox(stack, *origin),
            bg,
        )
        for i, it in enumerate(stack):
            elements.append(
                LayoutElement(
                    id=it.el.id,
                    left=origin[0] + it.rel_left,
                    top=origin[1] + it.rel_top,
                    width=it.w,
                    height=it.h,
                    z_index=5 + i,
                    font_family=it.family,
                    font_size=it.font_size,
                    font_weight=it.weight,
                    color=text_color,
                    text_align=text_align,
                )
            )

    # 6. Underlay: expand the text-stack bbox so every riding text is fully
    #    covered (>= the 80% contract). Mandatory under text-on-photo; also
    #    deployed when the chosen anchor still sits on busy texture.
    underlay_used = 0
    if groups["underlays"] and stack:
        xl, yt, xr, yb = _stack_bbox(stack, *origin)
        need = relation == "text-on-photo"
        if not need and spec.canvas.background_asset_ref:
            grad = qc._bg_gradient_map(spec.canvas.background_asset_ref, cw, ch)
            if grad is not None:
                g = _gradient_under(grad, [(xl, yt, xr, yb)], cw, ch)
                need = g > qc.TEXT_GRADIENT_MAX
        if need:
            pad = int(round(UNDERLAY_PAD_SCALE * base))
            u = groups["underlays"][0]
            elements.append(
                LayoutElement(
                    id=u.id,
                    left=max(0, xl - pad),
                    top=max(0, yt - pad),
                    width=min(cw, xr + pad) - max(0, xl - pad),
                    height=min(ch, yb + pad) - max(0, yt - pad),
                    z_index=4,
                )
            )
            underlay_used = 1

    # 7. Remaining underlays + other images: small corner accents that never
    #    cross the text stack.
    accents = groups["underlays"][underlay_used:] + groups["others"]
    if accents:
        aw = max(24, int(0.10 * base))
        corners = [
            (margin, margin),
            (cw - margin - aw, margin),
            (margin, ch - margin - aw),
            (cw - margin - aw, ch - margin - aw),
        ]
        for i, el in enumerate(accents):
            x, y = corners[i % 4]
            elements.append(
                LayoutElement(id=el.id, left=x, top=y, width=aw, height=aw, z_index=3)
            )

    cand = Candidate(candidate_id=f"solver_v{variant}", elements=elements)

    # 8. Self-check against the full QC battery -- construction should leave
    #    the oracle's gate empty; anything else is logged for the experiment
    #    record, never raised (the pipeline must not die at placement).
    try:
        result = qc.check_candidate(cand, spec, bg=bg)
        gate = [
            v
            for v in result.violations
            if v.type
            in (
                qc.ViolationType.PRIMARY_OUTSIDE_SAFE_ZONE,
                qc.ViolationType.CANVAS_COVERAGE_LOW,
                qc.ViolationType.DEAD_BAND_EXCESSIVE,
                qc.ViolationType.TEXT_ON_BUSY_TEXTURE,
                qc.ViolationType.COMPOSITION_MISMATCH,
                qc.ViolationType.TEXT_ON_PHOTO_NO_UNDERLAY,
            )
        ]
        if gate:
            logger.warning(
                "solve_placement: %d residual gate violation(s): %s",
                len(gate),
                "; ".join(v.detail for v in gate),
            )
    except Exception as err:  # pragma: no cover - diagnostic only
        logger.warning(f"solve_placement self-check crashed: {err}")
    return cand


def _fg_intervals(
    placed: List[LayoutElement], stack: List[_StackItem], origin: Tuple[int, int]
) -> List[Tuple[int, int, int, int]]:
    """(left, top, right, bottom) of every foreground box, placed + pending
    stack items, for the dead-band repair pass."""
    out = [
        (el.left, el.top, el.left + el.width, el.top + el.height)
        for el in placed
        if el.z_index > 0
    ]
    for it in stack:
        out.append(
            (
                origin[0] + it.rel_left,
                origin[1] + it.rel_top,
                origin[0] + it.rel_left + it.w,
                origin[1] + it.rel_top + it.h,
            )
        )
    return out
