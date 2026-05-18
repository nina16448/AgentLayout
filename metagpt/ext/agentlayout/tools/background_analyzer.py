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


def _occupancy_mask(img: Image.Image) -> np.ndarray:
    """Bool mask (canvas-sized), True == visually busy => avoid placing here.

    Energy = max(local luminance std, rembg matte). Low-energy regions are
    the visually calm areas where text/elements read well -- the signal the
    content-aware poster-layout literature (e.g. PKU PosterLayout) actually
    uses, and robust to backgrounds with no photographic subject.
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
    return energy > _ENERGY_TAU


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

    mask = _occupancy_mask(img)
    zones = _safe_zones_from_mask(mask, canvas.width, canvas.height)

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
        f"palette[0]={palette[0] if palette else 'n/a'}, text={text_color}"
    )
    return BackgroundAnalysis(
        safe_zones=zones,
        dominant_palette=palette,
        recommended_text_color=text_color,
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
