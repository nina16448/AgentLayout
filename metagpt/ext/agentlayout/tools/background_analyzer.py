"""BackgroundAnalyzer -- content-aware CV module (no LLM).

This is the missing producer for :class:`BackgroundAnalysis`. The schema and
every consumer (Layout Generator prompt, Aesthetic Judge prompt) were wired
long ago, but until now the only producer was
:func:`metagpt.ext.agentlayout.pipeline.default_white_background`, a stub that
emits an *empty* ``safe_zones`` list -- i.e. the pipeline was running
brief-driven layout on a blank canvas, **not** content-aware layout
generation.

``analyze_background`` closes that gap. Given a background image it:

  1. Runs U2Net saliency (via ``rembg``) to extract the foreground subject
     mask -- the region elements must *avoid* (cf. PKU PosterLayout / PosterO).
  2. Derives rectangular ``safe_zones`` from the margin bands around the
     subject's bounding box (the interpretable "place around the product"
     prior used throughout the content-aware layout literature).
  3. Extracts a ``dominant_palette`` via k-means on the pixels.
  4. Picks ``recommended_text_color`` from the luminance of the largest safe
     zone.

Robustness contract: **this module never raises into the pipeline**. Any
failure (missing file, corrupt image, rembg/onnx error) falls back to the
historical white/solid-color :class:`BackgroundAnalysis` so live runs cannot
crash on it. ``resolve_background`` is the single entry point both the Role
path and the LayoutPipeline path call.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image

from metagpt.logs import logger

from metagpt.ext.agentlayout.schema import (
    BackgroundAnalysis,
    Canvas,
    SafeZone,
)

# Downscale longest side to this before running U2Net: CPU-fast and the mask
# is upscaled back to canvas resolution afterwards (saliency is low-frequency
# so the downscale costs almost no accuracy).
_REMBG_MAX_SIDE = 512

# A grid cell / margin band counts as "safe" only if the subject occupies less
# than this fraction of it.
_SUBJECT_OCCUPANCY_TAU = 0.10

# A safe zone smaller than this fraction of the canvas is dropped as noise.
_MIN_SAFE_AREA_FRAC = 0.03

# Normalized energy (max of luminance-std and rembg matte) above this counts
# as "visually busy -> avoid". Calibrated on the Crello validation set so a
# black empty region reads as calm and doodle/edge clutter reads as busy.
_ENERGY_TAU = 0.18

# F2 (Step 72) — continuous-saliency export config.
# Downsampled grid resolution for saliency_map field (HxW). 32x32 keeps the
# JSON serialization small (~1024 floats) while preserving placement-scale
# structure (the QC rule TEXT_ON_HIGH_SALIENCY only needs ~canvas/32 spatial
# resolution to detect "text on hero subject").
_SALIENCY_MAP_SIDE = 32

# How many low-saliency rectangles to surface. Top-K by (1 - mean_saliency)
# from a sliding-window scan; Generator prompt will list these as preferred
# text-placement targets.
_LOW_SAL_K = 5

# Minimum area (as fraction of canvas) for a low-saliency rectangle to count.
# Same rationale as _MIN_SAFE_AREA_FRAC: tiny calm patches are useless for
# placement.
_LOW_SAL_MIN_AREA_FRAC = 0.04


def _luminance_text_color(rgb: Tuple[int, int, int]) -> str:
    """Dark text on light backgrounds, light text on dark (Rec. 601 luma)."""
    r, g, b = rgb
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return "#111111" if luminance >= 128 else "#F4F4F4"


def _hex(rgb: Tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*(int(max(0, min(255, c))) for c in rgb))


def _rembg_alpha(img: Image.Image) -> Optional[np.ndarray]:
    """U2Net foreground matte (0-1, canvas-sized) or ``None`` on failure.

    rembg only contributes a *photographic subject* signal. It is unreliable
    on the decorative / abstract / sparse "background" images common in
    Crello (it can invert and flag empty space as the subject), so it is
    fused with -- never trusted over -- the variance energy map below.
    """
    try:
        from rembg import remove  # local import: optional heavy dep
    except Exception as e:  # pragma: no cover - depends on env
        logger.warning(f"BackgroundAnalyzer: rembg unavailable ({e}); variance-only.")
        return None

    w, h = img.size
    scale = min(1.0, _REMBG_MAX_SIDE / float(max(w, h)))
    small = img if scale >= 1.0 else img.resize(
        (max(1, int(w * scale)), max(1, int(h * scale))), Image.BILINEAR
    )
    try:
        cut = remove(small)
    except Exception as e:
        logger.warning(f"BackgroundAnalyzer: rembg.remove failed ({e}); variance-only.")
        return None

    alpha = np.array(cut.convert("RGBA"))[:, :, 3].astype(np.float32) / 255.0
    if alpha.shape != (h, w):
        alpha = np.array(
            Image.fromarray((alpha * 255).astype(np.uint8)).resize((w, h), Image.BILINEAR)
        ).astype(np.float32) / 255.0
    # Guard against the common inversion: if rembg calls >70% of the canvas
    # "foreground" it has misfired on a non-photo background -- drop it.
    if float((alpha > 0.5).mean()) > 0.70:
        logger.info("BackgroundAnalyzer: rembg matte covers >70% (likely inverted); dropped.")
        return None
    return alpha


def _energy_map(img: Image.Image) -> np.ndarray:
    """Continuous saliency map (canvas-sized, float32 in [0, 1]).

    Energy = max(local luminance std, rembg matte). Low-energy regions are
    the visually calm areas where text/elements read well -- the signal the
    content-aware poster-layout literature (e.g. PKU PosterLayout) actually
    uses, and robust to backgrounds with no photographic subject.

    F2 (Step 72) split out from the old _occupancy_mask: callers wanting the
    binary occupancy mask should call ``_occupancy_mask`` (now a thin wrapper),
    callers wanting the raw continuous saliency call this function directly.
    """
    import cv2

    w, h = img.size
    arr = np.asarray(img).astype(np.float32)
    lum = 0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.114 * arr[:, :, 2]

    # Local standard deviation over a ~canvas/20 window: var = E[x^2]-E[x]^2.
    k = max(7, (min(w, h) // 20) | 1)  # odd kernel
    mean = cv2.boxFilter(lum, ddepth=-1, ksize=(k, k), normalize=True)
    mean_sq = cv2.boxFilter(lum * lum, ddepth=-1, ksize=(k, k), normalize=True)
    std = np.sqrt(np.clip(mean_sq - mean * mean, 0.0, None))
    std_norm = std / (std.max() + 1e-6)

    alpha = _rembg_alpha(img)
    energy = std_norm if alpha is None else np.maximum(std_norm, alpha)
    return energy.astype(np.float32)


def _occupancy_mask(img: Image.Image) -> np.ndarray:
    """Bool mask (canvas-sized), True == visually busy => avoid placing here.

    Thin wrapper around :func:`_energy_map` + ``_ENERGY_TAU`` threshold,
    kept for the existing binary safe_zones derivation path.
    """
    return _energy_map(img) > _ENERGY_TAU


def _downsample_saliency(energy: np.ndarray, side: int = _SALIENCY_MAP_SIDE) -> np.ndarray:
    """Downsample the canvas-sized saliency map to a ``side x side`` grid by
    block-mean. Keeps values in [0, 1]. Used to populate
    ``BackgroundAnalysis.saliency_map`` without bloating prompt JSON.
    """
    import cv2

    return cv2.resize(energy, (side, side), interpolation=cv2.INTER_AREA).astype(np.float32)


def _saliency_3x3_histogram(energy: np.ndarray) -> List[float]:
    """Row-major 3x3 mean saliency (length 9): TL, TM, TR, ML, MM, MR, BL,
    BM, BR. Compact prompt summary that the Generator can reason over without
    parsing a 32x32 grid.
    """
    h, w = energy.shape
    cells: List[float] = []
    for ri in range(3):
        for ci in range(3):
            y0 = (h * ri) // 3
            y1 = (h * (ri + 1)) // 3
            x0 = (w * ci) // 3
            x1 = (w * (ci + 1)) // 3
            cells.append(round(float(energy[y0:y1, x0:x1].mean()), 3))
    return cells


def _rank_low_saliency_regions(
    energy: np.ndarray,
    canvas_w: int,
    canvas_h: int,
    k: int = _LOW_SAL_K,
) -> List[SafeZone]:
    """Top-K low-saliency rectangles via a coarse sliding-window scan.

    We scan over a 6x6 grid of candidate rectangles spanning the canvas,
    score each by ``1 - mean_saliency``, filter by ``_LOW_SAL_MIN_AREA_FRAC``,
    suppress overlapping picks, and return the top-K as SafeZone records.
    Pixel-precise enough for placement guidance, cheap enough to be free.

    Distinct from the binary subject-avoidance bands in ``_safe_zones_from_mask``:
    those describe "where the subject isn't"; this returns "the calmest
    rectangles in the whole canvas" regardless of subject geometry.
    """
    h, w = energy.shape
    sx = w / float(canvas_w)
    sy = h / float(canvas_h)
    canvas_area = float(canvas_w * canvas_h)
    min_area = canvas_area * _LOW_SAL_MIN_AREA_FRAC

    # 6x6 = 36 base cells; also consider 1x2 / 2x1 / 2x2 unions for larger
    # rectangles that the Generator can use for headlines / body blocks.
    GRID = 6
    cell_w = canvas_w / GRID
    cell_h = canvas_h / GRID
    candidates: List[Tuple[float, str, List[int]]] = []  # (score, label, bbox)

    def _score_and_add(r0: int, c0: int, r1: int, c1: int) -> None:
        bbox_canvas = [
            int(c0 * cell_w),
            int(r0 * cell_h),
            int(c1 * cell_w),
            int(r1 * cell_h),
        ]
        l, t, r, b = bbox_canvas
        if (r - l) * (b - t) < min_area:
            return
        y0 = max(0, int(t * sy))
        y1 = min(h, int(b * sy))
        x0 = max(0, int(l * sx))
        x1 = min(w, int(r * sx))
        if y1 <= y0 or x1 <= x0:
            return
        m = float(energy[y0:y1, x0:x1].mean())
        score = round(max(0.0, 1.0 - m), 3)
        label = f"low_sal_r{r0}-{r1 - 1}c{c0}-{c1 - 1}"
        candidates.append((score, label, bbox_canvas))

    for r0 in range(GRID):
        for c0 in range(GRID):
            _score_and_add(r0, c0, r0 + 1, c0 + 1)  # 1x1 cell
            if r0 + 2 <= GRID:
                _score_and_add(r0, c0, r0 + 2, c0 + 1)  # 2x1
            if c0 + 2 <= GRID:
                _score_and_add(r0, c0, r0 + 1, c0 + 2)  # 1x2
            if r0 + 2 <= GRID and c0 + 2 <= GRID:
                _score_and_add(r0, c0, r0 + 2, c0 + 2)  # 2x2

    candidates.sort(key=lambda x: -x[0])

    # Non-max suppression: drop later picks that overlap >50% with any
    # already-picked region. Keeps the top-K diverse.
    picked: List[Tuple[float, str, List[int]]] = []
    for sc, lbl, bbox in candidates:
        ok = True
        for _, _, prev in picked:
            if _bbox_iou(bbox, prev) > 0.5:
                ok = False
                break
        if ok:
            picked.append((sc, lbl, bbox))
        if len(picked) >= k:
            break

    return [
        SafeZone(region=lbl, bbox=bbox, confidence=sc)
        for sc, lbl, bbox in picked
    ]


def _bbox_iou(a: List[int], b: List[int]) -> float:
    """IoU of two [l, t, r, b] rectangles. 0 if disjoint."""
    xl = max(a[0], b[0])
    yt = max(a[1], b[1])
    xr = min(a[2], b[2])
    yb = min(a[3], b[3])
    if xr <= xl or yb <= yt:
        return 0.0
    inter = (xr - xl) * (yb - yt)
    area_a = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
    area_b = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
    denom = area_a + area_b - inter
    return inter / denom if denom > 0 else 0.0


def _safe_zones_from_mask(mask: np.ndarray, w: int, h: int) -> List[SafeZone]:
    """Margin bands around the subject bbox that are clear enough to use.

    The four bands (above / below / left / right of the subject's bounding
    box) are the interpretable content-aware prior: place elements where the
    product is not. Each surviving band's confidence is ``1 - occupancy``.
    """
    canvas_area = float(w * h)
    ys, xs = np.where(mask)
    if xs.size == 0:
        # No subject detected -> the whole canvas is placeable.
        return [SafeZone(region="full", bbox=[0, 0, w, h], confidence=1.0)]

    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())

    # (label, [left, top, right, bottom]) for each non-degenerate band.
    bands: List[Tuple[str, List[int]]] = []
    if y0 > 0:
        bands.append(("top", [0, 0, w, y0]))
    if y1 < h - 1:
        bands.append(("bottom", [0, y1 + 1, w, h]))
    if x0 > 0:
        bands.append(("left", [0, 0, x0, h]))
    if x1 < w - 1:
        bands.append(("right", [x1 + 1, 0, w, h]))

    zones: List[SafeZone] = []
    for label, (l, t, r, b) in bands:
        bw, bh = r - l, b - t
        if bw <= 0 or bh <= 0:
            continue
        if (bw * bh) / canvas_area < _MIN_SAFE_AREA_FRAC:
            continue
        occupancy = float(mask[t:b, l:r].mean())  # fraction of subject px in band
        if occupancy >= _SUBJECT_OCCUPANCY_TAU:
            continue
        zones.append(
            SafeZone(
                region=label,
                bbox=[l, t, r, b],
                confidence=round(max(0.0, 1.0 - occupancy), 3),
            )
        )

    if not zones:
        # Subject spans the canvas: fall back to a 3x3 grid and keep the
        # least-occupied cells so the Generator still gets *some* signal.
        gx, gy = w / 3.0, h / 3.0
        cells: List[Tuple[float, str, List[int]]] = []
        for ri in range(3):
            for ci in range(3):
                l, t = int(ci * gx), int(ri * gy)
                r, b = int((ci + 1) * gx), int((ri + 1) * gy)
                occ = float(mask[t:b, l:r].mean()) if b > t and r > l else 1.0
                cells.append((occ, f"r{ri}c{ci}", [l, t, r, b]))
        cells.sort(key=lambda c: c[0])
        for occ, label, bbox in cells[:3]:
            zones.append(
                SafeZone(region=label, bbox=bbox, confidence=round(max(0.0, 1.0 - occ), 3))
            )
    return zones


def _dominant_palette(arr: np.ndarray, k: int = 5) -> List[str]:
    """Top-k cluster centroids as hex, ordered by cluster population."""
    pixels = arr.reshape(-1, 3)
    if pixels.shape[0] > 20000:  # subsample for speed + determinism
        idx = np.linspace(0, pixels.shape[0] - 1, 20000).astype(int)
        pixels = pixels[idx]
    try:
        from sklearn.cluster import KMeans

        km = KMeans(n_clusters=k, n_init=4, random_state=0).fit(pixels)
        counts = np.bincount(km.labels_, minlength=k)
        order = np.argsort(-counts)
        return [_hex(tuple(km.cluster_centers_[i])) for i in order]
    except Exception as e:
        logger.warning(f"BackgroundAnalyzer: k-means palette failed ({e}); using mean.")
        return [_hex(tuple(pixels.mean(axis=0)))]


def _largest_zone(zones: List[SafeZone]) -> Optional[SafeZone]:
    if not zones:
        return None
    return max(zones, key=lambda z: (z.bbox[2] - z.bbox[0]) * (z.bbox[3] - z.bbox[1]))


def analyze_background(image_path: str, canvas: Canvas) -> BackgroundAnalysis:
    """Content-aware analysis of ``image_path`` against ``canvas`` geometry.

    Never raises: any failure degrades to :func:`resolve_background`'s
    solid-color fallback via the caller.
    """
    img = Image.open(image_path).convert("RGB")
    if img.size != (canvas.width, canvas.height):
        img = img.resize((canvas.width, canvas.height), Image.BILINEAR)
    arr = np.asarray(img)

    # F2 (Step 72) — compute the continuous saliency map ONCE; the binary
    # occupancy mask used for safe_zones is just energy > _ENERGY_TAU, and
    # the three new fields (saliency_map / histogram / low_saliency_regions)
    # are all summaries of the same energy field.
    energy = _energy_map(img)
    mask = energy > _ENERGY_TAU
    zones = _safe_zones_from_mask(mask, canvas.width, canvas.height)
    saliency_map_small = _downsample_saliency(energy).round(3)
    saliency_histogram = _saliency_3x3_histogram(energy)
    low_sal_regions = _rank_low_saliency_regions(energy, canvas.width, canvas.height)

    palette = _dominant_palette(arr)

    biggest = _largest_zone(zones)
    if biggest is not None:
        l, t, r, b = biggest.bbox
        region_mean = arr[t:b, l:r].reshape(-1, 3).mean(axis=0)
    else:
        region_mean = arr.reshape(-1, 3).mean(axis=0)
    text_color = _luminance_text_color(tuple(region_mean))

    logger.info(
        f"BackgroundAnalyzer: {Path(image_path).name} -> {len(zones)} safe zone(s), "
        f"{len(low_sal_regions)} low-saliency region(s), "
        f"palette[0]={palette[0] if palette else 'n/a'}, text={text_color}"
    )
    return BackgroundAnalysis(
        safe_zones=zones,
        dominant_palette=palette,
        recommended_text_color=text_color,
        saliency_map=saliency_map_small.tolist(),
        saliency_histogram=saliency_histogram,
        low_saliency_regions=low_sal_regions,
    )


def resolve_background(canvas: Canvas) -> BackgroundAnalysis:
    """Single entry point for both pipeline drivers.

    If ``canvas.background_asset_ref`` points to a loadable image, run real
    content-aware analysis; otherwise (or on *any* error) fall back to the
    historical solid-color stub so behaviour for image-less specs is
    unchanged and live runs never crash on this path.
    """
    # Deferred import breaks the pipeline <-> background_analyzer import cycle.
    from metagpt.ext.agentlayout.pipeline import default_white_background

    ref = canvas.background_asset_ref
    if not ref or not Path(ref).is_file():
        return default_white_background(canvas)
    try:
        return analyze_background(ref, canvas)
    except Exception as e:
        logger.warning(
            f"BackgroundAnalyzer: analyze_background failed for {ref!r} ({e}); "
            f"falling back to solid-color BackgroundAnalysis."
        )
        return default_white_background(canvas)
