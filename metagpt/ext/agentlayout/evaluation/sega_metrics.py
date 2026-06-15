"""SEGA / PKU PosterLayout rule-based metrics for AgentLayout.

This is a direct port of the reference implementation from:
  Hsu et al., "PosterLayout: A New Benchmark and Approach for Content-Aware
  Visual-Textual Presentation Layout", CVPR 2023.
  https://github.com/PKU-ICST-MIPL/PosterLayout-CVPR2023/blob/main/eval.py

The same 6 metrics are reported in SEGA (Wang et al., ICCV 2025, arXiv
2510.15749) Table 3 for Crello: alignment / overlay / underlay loose /
underlay strict / readability / occlusion. SEGA defers to the PKU
formulae, so this module reproduces them precisely so our numbers can be
placed in the same table as FlexDM / PosterLlama / SEGA-7B / SEGA-13B.

Differences vs PKU eval.py:
  * Canvas dimensions are passed in (not hardcoded 513x750) so we can
    evaluate Crello samples at their native resolutions.
  * Bbox input format: PKU uses xyxy corners; we use (left, top, width,
    height) so the module wires directly into AgentLayout's BBoxItem /
    Candidate / DesignSpec types. Internal helpers always operate on
    xyxy after a single conversion at the boundary.
  * Class codes follow PKU semantics:
        cls = 1 -> text / readable element  (text-shape metric inputs)
        cls = 2 -> image / logo (non-underlay overlay-able element)
        cls = 3 -> underlay (decoration shape)
    Crello / AgentLayout currently does not emit cls=3 (no decoration
    synthesis -- see layout_agent/README.md), so metric_underlay_loose
    and metric_underlay_strict will return 0 for our renders, which is
    the honest "no underlay produced" reading rather than a bug.

Output direction (lower-or-higher better) matches SEGA Table 3:
    Alignment       lower better
    Overlay         lower better
    Underlay loose  higher better
    Underlay strict higher better
    Readability     lower better
    Occlusion       lower better
"""
from __future__ import annotations

from math import log10
from typing import Iterable, List, Optional, Tuple

import numpy as np

# Type aliases. Layouts are passed as lists of (class_code, (xl, yl, xr, yr)).
ClsCode = int
BBoxXYXY = Tuple[float, float, float, float]
Element = Tuple[ClsCode, BBoxXYXY]
Layout = List[Element]


# ============================================================
# Class codes (matches PKU PosterLayout convention)
# ============================================================

CLS_TEXT: int = 1
CLS_IMAGE_LOGO: int = 2
CLS_UNDERLAY: int = 3


# ============================================================
# Geometry helpers (xyxy IO; lwh callers convert via to_xyxy)
# ============================================================


def to_xyxy(left: float, top: float, width: float, height: float) -> BBoxXYXY:
    """Convert AgentLayout (left, top, width, height) -> PKU (xl, yl, xr, yr)."""
    return (float(left), float(top), float(left) + float(width), float(top) + float(height))


def _bbox_iou(bb1: BBoxXYXY, bb2: BBoxXYXY) -> float:
    """Standard IoU on xyxy boxes. Returns 0 on degenerate or non-overlapping."""
    xl_1, yl_1, xr_1, yr_1 = bb1
    xl_2, yl_2, xr_2, yr_2 = bb2
    w_1 = xr_1 - xl_1
    h_1 = yr_1 - yl_1
    w_2 = xr_2 - xl_2
    h_2 = yr_2 - yl_2
    if w_1 <= 0 or h_1 <= 0 or w_2 <= 0 or h_2 <= 0:
        return 0.0
    w_inter = min(xr_1, xr_2) - max(xl_1, xl_2)
    h_inter = min(yr_1, yr_2) - max(yl_1, yl_2)
    if w_inter <= 0 or h_inter <= 0:
        return 0.0
    a_inter = w_inter * h_inter
    return a_inter / (w_1 * h_1 + w_2 * h_2 - a_inter)


def _bbox_inter_oneside(bb1: BBoxXYXY, bb2: BBoxXYXY) -> float:
    """Intersection / area(bb2). Used for underlay (bb1=deco, bb2=non-deco)."""
    xl_1, yl_1, xr_1, yr_1 = bb1
    xl_2, yl_2, xr_2, yr_2 = bb2
    w_2 = xr_2 - xl_2
    h_2 = yr_2 - yl_2
    if w_2 <= 0 or h_2 <= 0:
        return 0.0
    w_inter = min(xr_1, xr_2) - max(xl_1, xl_2)
    h_inter = min(yr_1, yr_2) - max(yl_1, yl_2)
    if w_inter <= 0 or h_inter <= 0:
        return 0.0
    return (w_inter * h_inter) / (w_2 * h_2)


def _is_contain(bb_outer: BBoxXYXY, bb_inner: BBoxXYXY) -> bool:
    """PKU-compatible containment for Und_s (metric-audit A5).

    PKU eval.py's ``is_contain`` has a known bug: its 3rd condition is
    ``xr_2 >= xr_2`` (the inner box's right edge compared to itself -- always
    True), so the right edge is NEVER checked; containment is effectively
    3-sided (left / top / bottom). SEGA Table 3 inherits the PKU evaluator
    verbatim, so to keep Und_s comparable we reproduce the bug rather than the
    intended 4-sided check. The right-edge term is intentionally omitted.
    """
    xl_o, yl_o, _xr_o, yr_o = bb_outer
    xl_i, yl_i, _xr_i, yr_i = bb_inner
    # NOTE: right-edge check (xr_o >= xr_i) intentionally OMITTED -- see above.
    return xl_o <= xl_i and yl_o <= yl_i and yr_o >= yr_i


# ============================================================
# Preprocessing helpers (metric-audit A2 / A7)
# ============================================================


def drop_invalid_elements(
    layout: Layout,
    canvas_w: float,
    canvas_h: float,
    area_frac: float = 0.001,
) -> Layout:
    """Remove elements smaller than ``area_frac`` of the canvas (PKU getRidOfInvalid).

    PKU eval.py invalidates any box whose canvas-clamped area is below
    ``5.13 * 7.50 * 10 = 384.75`` px at 513x750 -- i.e. 0.1% of the canvas
    area -- by setting its class to 0 before the geometry metrics run. We
    reproduce that threshold scale-independently (default ``area_frac=0.001``)
    against the per-sample native canvas. Callers should apply this once to
    each layout before metric_* so tiny/degenerate boxes don't pollute
    Alignment / Overlay / Occlusion / Readability (metric-audit A2).
    """
    thresh = float(area_frac) * float(canvas_w) * float(canvas_h)
    kept: Layout = []
    for cls, (xl, yl, xr, yr) in layout:
        # Clamp to canvas bounds before the area test, matching PKU.
        cxl = max(0.0, float(xl))
        cyl = max(0.0, float(yl))
        cxr = min(float(canvas_w), float(xr))
        cyr = min(float(canvas_h), float(yr))
        area = abs((cxr - cxl) * (cyr - cyl))
        if area >= thresh:
            kept.append((cls, (xl, yl, xr, yr)))
    return kept


def layout_has_underlay(layout: Layout) -> bool:
    """True iff the layout contains >=1 underlay (cls==3) element.

    Lets callers distinguish "no underlay emitted" (Und is N/A -- exclude
    from the mean, since PKU averages Und only over underlay-bearing layouts
    via ``avali``) from "underlay present but covers nothing" (a real 0).
    See metric-audit A7.
    """
    return any(cls == CLS_UNDERLAY for cls, _ in layout)


# ============================================================
# 1. Alignment (lower better)
# ============================================================


def _ali_g(min_delta: float) -> float:
    """PKU: g(x) = -log10(1 - x). Clipped so log doesn't blow up on near-1."""
    delta = max(0.0, min(0.999999, min_delta))
    return -log10(1.0 - delta)


def _ali_min_delta(xs: List[float]) -> float:
    """Minimum pairwise abs-diff in a 1D sample. inf if < 2 elements."""
    n = len(xs)
    if n < 2:
        return float("inf")
    min_delta = float("inf")
    for i in range(n):
        for j in range(i + 1, n):
            delta = abs(xs[i] - xs[j])
            if delta < min_delta:
                min_delta = delta
    return min_delta


def metric_alignment(
    layouts: Iterable[Layout],
    canvas_w: float,
    canvas_h: float,
) -> float:
    """Indicator of pair-wise non-alignment across elements in a layout.

    For each element, we compute 6 normalised position features
    (left, top, center_x, center_y, right, bottom) -> the minimum pairwise
    delta on each axis among all elements -> g(delta). The per-element
    score is the min across the 6 axes (the strongest alignment evidence).
    These are the same 6 edge/center axes as PKU eval.py (NOT width/height;
    alignment measures edge/center coincidence, not size similarity).
    Layout score = sum of per-element scores. Mean over layouts.

    Lower is better -- 0 means every element has another perfectly
    aligned counterpart on at least one of the 6 axes.
    """
    total = 0.0
    n_layouts = 0
    for layout in layouts:
        n_layouts += 1
        elements = [el for el in layout if el[0] > 0]
        if len(elements) <= 1:
            continue
        # Normalise to [0, 1] using canvas dims. PKU hardcoded 513/750; we
        # use the caller's canvas so per-sample scale is correct.
        theda = []
        for _, (xl, yl, xr, yr) in elements:
            l = xl / canvas_w
            t = yl / canvas_h
            r = xr / canvas_w
            b = yr / canvas_h
            # PKU eval.py order: [left, top, center_x, center_y, right, bottom].
            # NOT width/height -- alignment is edge/center coincidence, so the
            # 5th/6th axes must be right (xr) and bottom (yr), matching
            # PKU's theda = [xl, yl, (xl+xr)/2, (yl+yr)/2, xr, yr].
            theda.append([l, t, (l + r) / 2.0, (t + b) / 2.0, r, b])
        theda_arr = np.array(theda)
        ali = 0.0
        n = len(elements)
        for i in range(n):
            g_val = []
            for j in range(6):
                xys = list(theda_arr[:, j])
                delta = _ali_min_delta(xys)
                g_val.append(_ali_g(delta))
            ali += min(g_val)
        total += ali
    if n_layouts == 0:
        return 0.0
    return total / n_layouts


# ============================================================
# 2. Overlay (lower better)
# ============================================================


def metric_overlay(layouts: Iterable[Layout]) -> float:
    """Sum of pairwise IoU among non-underlay elements, divided by n elements.

    PKU uses ``sum_pairs_IoU / n_elements`` (not n*(n-1)/2). We match.
    Underlay elements (cls == 3) are excluded -- they're decoration shapes
    that are *supposed* to underlay other elements, so their overlap is
    not a defect.

    Lower is better -- 0 means no two non-underlay elements overlap.
    """
    total = 0.0
    n_layouts = 0
    for layout in layouts:
        n_layouts += 1
        non_underlay = [
            bbox for cls, bbox in layout if cls > 0 and cls != CLS_UNDERLAY
        ]
        n = len(non_underlay)
        if n == 0:
            continue
        ove = 0.0
        for i in range(n):
            for j in range(i + 1, n):
                ove += _bbox_iou(non_underlay[i], non_underlay[j])
        total += ove / n
    if n_layouts == 0:
        return 0.0
    return total / n_layouts


# ============================================================
# 3. Underlay loose (higher better)
# ============================================================


def metric_underlay_loose(layouts: Iterable[Layout]) -> float:
    """Per-underlay: max intersection-over-self_area with any non-underlay.

    A "perfect" underlay covers a non-underlay element completely (IoS=1).
    Layout score = mean over underlay elements. Final score = mean over
    layouts that have >= 1 underlay element. Layouts with no underlay
    are skipped (they cannot earn an underlay score).

    Higher is better. Returns 0.0 if no layout has any underlay element
    (the honest reading for AgentLayout's Crello renders, which do not
    emit decoration shapes).
    """
    total = 0.0
    n_with_underlay = 0
    for layout in layouts:
        underlay_boxes = [bbox for cls, bbox in layout if cls == CLS_UNDERLAY]
        other_boxes = [
            bbox for cls, bbox in layout if cls > 0 and cls != CLS_UNDERLAY
        ]
        n1 = len(underlay_boxes)
        if n1 == 0:
            continue
        n_with_underlay += 1
        und = 0.0
        for u in underlay_boxes:
            max_ios = 0.0
            for o in other_boxes:
                ios = _bbox_inter_oneside(u, o)
                if ios > max_ios:
                    max_ios = ios
            und += max_ios
        total += und / n1
    if n_with_underlay == 0:
        return 0.0
    return total / n_with_underlay


# ============================================================
# 4. Underlay strict (higher better)
# ============================================================


def metric_underlay_strict(layouts: Iterable[Layout]) -> float:
    """Fraction of underlay elements that fully contain >=1 non-underlay.

    PKU's is_contain check (xl_o<=xl_i & yl_o<=yl_i & xr_o>=xr_i &
    yr_o>=yr_i). Mean over underlay elements per layout, then mean over
    layouts with underlay.

    Higher is better. Returns 0.0 for AgentLayout (no underlay class).
    """
    total = 0.0
    n_with_underlay = 0
    for layout in layouts:
        underlay_boxes = [bbox for cls, bbox in layout if cls == CLS_UNDERLAY]
        other_boxes = [
            bbox for cls, bbox in layout if cls > 0 and cls != CLS_UNDERLAY
        ]
        n1 = len(underlay_boxes)
        if n1 == 0:
            continue
        n_with_underlay += 1
        und = 0
        for u in underlay_boxes:
            for o in other_boxes:
                if _is_contain(u, o):
                    und += 1
                    break
        total += und / n1
    if n_with_underlay == 0:
        return 0.0
    return total / n_with_underlay


# ============================================================
# 5. Readability (lower better)
# ============================================================


def _sobel_gradient_normalised(bg_rgb: "np.ndarray") -> "np.ndarray":
    """Compute the Sobel gradient magnitude image, normalised to [0, 1].

    Follows PKU's ``img_to_g_xy``: grayscale, Sobel dx + dy (ddepth=-1, so
    negative gradients saturate to 0 exactly as PKU does), sqrt((dx^2+dy^2)/2),
    then normalise by the image's own max. cv2 is already a repo dep via PIL.

    Metric-audit A6 -- ONE intentional deviation from PKU, documented not
    replicated: PKU keeps the Sobel output as uint8 and computes ``dx**2`` in
    uint8, which OVERFLOWS (255**2 mod 256 = 1), scrambling strong edges. We
    cast to float64 before squaring, so our gradient is the mathematically
    correct magnitude. We deliberately do NOT reproduce the overflow because
    (a) it makes the metric semantically meaningless for strong edges, and
    (b) the QC TEXT_ON_BUSY_TEXTURE threshold (Step 57/58, T=0.065) is
    GT-calibrated against THIS float gradient via quality_checker
    ``_bg_gradient_map`` -- replicating the overflow would silently break that
    calibration. Consequence: our Readability is not bit-identical to PKU's
    but is monotonic/correct; treat cross-paper Read as indicative (already a
    caveat for the whole table).
    """
    import cv2  # local import so module-level import doesn't fail without cv2

    gray = cv2.cvtColor(bg_rgb, cv2.COLOR_RGB2GRAY)
    grad_x = cv2.Sobel(gray, -1, 1, 0)
    grad_y = cv2.Sobel(gray, -1, 0, 1)
    grad_xy = ((grad_x.astype(np.float64) ** 2 + grad_y.astype(np.float64) ** 2) / 2.0) ** 0.5
    max_v = float(grad_xy.max())
    if max_v <= 0:
        return np.zeros_like(grad_xy, dtype=np.float64)
    grad_xy = grad_xy / max_v  # 0..1 (PKU then *255 then /255; equivalent)
    return grad_xy


def metric_readability(
    layouts: Iterable[Layout],
    bg_images: Iterable["np.ndarray"],
    canvas_w: float,
    canvas_h: float,
) -> float:
    """Average Sobel gradient under text-element pixels (text minus deco).

    bg_images must be uint8 RGB numpy arrays of the SAME (canvas_h, canvas_w)
    resolution as the bboxes. If text bbox sits on a flat background the
    gradient is near 0 (highly readable); if it sits on a textured area
    the gradient is large (hard to read -> high score). Lower is better.

    Deco elements (cls == 3) MASK OUT text-covered gradients (PKU
    convention: a deco/underlay shape under the text shields the text
    from background texture so readability is restored).
    """
    bg_list = list(bg_images)
    layouts_list = list(layouts)
    if len(bg_list) != len(layouts_list):
        raise ValueError(
            f"metric_readability: got {len(bg_list)} bg images for "
            f"{len(layouts_list)} layouts; one bg per layout required."
        )
    if not layouts_list:
        return 0.0
    total = 0.0
    # A4: denominator = every evaluable sample (non-None bg), matching PKU
    # `return metrics / len(img_names)`. A sample with no text region (or zero
    # gradient) contributes 0 to the numerator but is still counted -- unlike
    # the old code, which divided by only the contributing samples and so
    # biased Readability high.
    n_eval = 0
    cw = int(round(canvas_w))
    ch = int(round(canvas_h))
    for layout, bg_rgb in zip(layouts_list, bg_list):
        if bg_rgb is None:
            continue
        n_eval += 1
        # PKU resizes everything to (513, 750); we resize to the *layout*
        # canvas so bbox indices line up. The bbox coords are in the
        # layout canvas frame, so we resize the bg image to match.
        import cv2

        if bg_rgb.shape[1] != cw or bg_rgb.shape[0] != ch:
            bg_rgb = cv2.resize(bg_rgb, (cw, ch))
        grad = _sobel_gradient_normalised(bg_rgb)
        mask = np.zeros((ch, cw), dtype=np.float64)
        for cls, (xl, yl, xr, yr) in layout:
            if cls == CLS_TEXT:
                xli = max(0, int(round(xl)))
                yli = max(0, int(round(yl)))
                xri = min(cw, int(round(xr)))
                yri = min(ch, int(round(yr)))
                if xri > xli and yri > yli:
                    mask[yli:yri, xli:xri] = 1.0
        for cls, (xl, yl, xr, yr) in layout:
            if cls == CLS_UNDERLAY:
                xli = max(0, int(round(xl)))
                yli = max(0, int(round(yl)))
                xri = min(cw, int(round(xr)))
                yri = min(ch, int(round(yr)))
                if xri > xli and yri > yli:
                    mask[yli:yri, xli:xri] = 0.0
        total_area = float(mask.sum())
        if total_area <= 0:
            # No text region: contributes 0 to numerator, still in n_eval.
            continue
        total_grad = float(grad[mask > 0.5].sum())
        # total_grad == 0 (flat bg) adds 0 -- matches PKU's `if total_grad`.
        total += total_grad / total_area
    if n_eval == 0:
        return 0.0
    return total / n_eval


# ============================================================
# 6. Occlusion (lower better)
# ============================================================


def metric_occlusion(
    layouts: Iterable[Layout],
    saliency_maps: Iterable[Optional["np.ndarray"]],
    canvas_w: float,
    canvas_h: float,
) -> float:
    """Average saliency under foreground-covered pixels.

    saliency_maps must be float32 in [0, 1] of shape (canvas_h, canvas_w);
    a None entry skips that layout. Higher saliency under foreground =
    we're covering the visually important part of the background = bad.
    Lower is better.
    """
    sal_list = list(saliency_maps)
    layouts_list = list(layouts)
    if len(sal_list) != len(layouts_list):
        raise ValueError(
            f"metric_occlusion: got {len(sal_list)} saliency maps for "
            f"{len(layouts_list)} layouts; one map per layout required."
        )
    total = 0.0
    # A4: denominator = every evaluable sample (non-None saliency), matching
    # PKU `return metrics / len(img_names)`. A layout covering no salient
    # pixels contributes 0 but is still counted (old code divided by only the
    # contributing samples, biasing Occ).
    n_eval = 0
    cw = int(round(canvas_w))
    ch = int(round(canvas_h))
    for layout, sal in zip(layouts_list, sal_list):
        if sal is None:
            continue
        n_eval += 1
        import cv2

        if sal.shape[1] != cw or sal.shape[0] != ch:
            sal = cv2.resize(sal.astype(np.float32), (cw, ch))
        mask = np.zeros((ch, cw), dtype=np.float64)
        for cls, (xl, yl, xr, yr) in layout:
            if cls <= 0:
                continue
            xli = max(0, int(round(xl)))
            yli = max(0, int(round(yl)))
            xri = min(cw, int(round(xr)))
            yri = min(ch, int(round(yr)))
            if xri > xli and yri > yli:
                mask[yli:yri, xli:xri] = 1.0
        total_area = float(mask.sum())
        if total_area <= 0:
            # No element covers anything: contributes 0, still in n_eval.
            continue
        total_sal = float(sal[mask > 0.5].sum())
        # total_sal == 0 (elements over flat/non-salient bg) adds 0.
        total += total_sal / total_area
    if n_eval == 0:
        return 0.0
    return total / n_eval
