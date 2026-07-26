"""SEGA-style Crello preprocessor -- bake non-text layers into the background.

Step 76 (2026-07-02). Motivation: the raw Crello asset list mixes backgrounds,
decorative shapes, logos and underlay plates with no reliable type signal --
even a human cannot tell them apart from the asset list alone (Step 27 audit:
most underlays hide in type_code 0). Steps 61/66 showed the system loses to the
designer GT on *composition semantics*, not geometry: mathematically-optimal
placement still lost 55/55 because the model cannot infer which messy asset
plays which role.

SEGA (ICCV 2025) sidesteps this by reformatting Crello into a "half-finished
poster": every non-text layer is composited into the background at the
designer's GT position, and the model only places text. This module replicates
that protocol on our cached samples (layout_agent/output/crello_<id>/):

    * kind in {background_candidate, image, underlay}  -> composited (baked)
    * kind == text                                     -> stays placeable

Because WE do the compositing, underlay panel positions and colours are known
exactly -- they are emitted as :class:`UnderlayRegion` feed-forward hints
(bbox + dominant colour + contrasting text colour) instead of being routed
through judge feedback, which was measured dead three times (Steps 20b/59/65).

Paper-protocol note: baked layers reuse designer GT positions. This matches
SEGA's released "PKU-style Crello" and must be disclosed as such -- candidates
differ from GT only in text placement/styling.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image
from pydantic import BaseModel, Field

from metagpt.ext.agentlayout.schema import UnderlayRegion
from metagpt.logs import logger

# Underlay panels smaller than this cannot hold text; emitting them as hints
# would only mislead the Composition Director (thin divider lines are the
# common case: 337x16px rules under headings). Fractions are of canvas size;
# the absolute floors guard tiny canvases.
MIN_REGION_W_FRAC: float = 0.10
MIN_REGION_H_FRAC: float = 0.06
MIN_REGION_W_PX: int = 60
MIN_REGION_H_PX: int = 24

# A shape only counts as a TEXT underlay when the designer actually put text on
# it: some GT text element must have at least this fraction of its area inside
# the shape's bbox. Without this check the N=20 eyeball run emitted regions on
# decorative illustrations (fish ornaments, a skier silhouette) -- kind=underlay
# is the classifier's shape bucket, not a semantic text-holder label.
TEXT_OVERLAP_MIN: float = 0.5

# Step 79: solid plate vs transparent outlined frame. Below this opaque-pixel
# fraction the shape is a FRAME: its visible backdrop is the background
# showing through, so the region colour must be sampled from the composited
# canvas inside the bbox, not from the shape's own (border) pixels. The Step
# 78 eyeball run caught the failure this fixes: a white outline box over a
# dark forest was reported as a white plate -> dark recommended text -> dark
# text on dark forest (GT uses white text there).
SOLID_MIN_OPAQUE_COVERAGE: float = 0.35

# Kinds that get baked into the background (everything except text).
BAKED_KINDS = ("background_candidate", "image", "underlay")

BG_FILENAME = "bg_composite.png"
INPUT_FILENAME = "sega_input.json"


class PreprocessedSample(BaseModel):
    """Output contract of :func:`preprocess_sample` (also serialised to JSON)."""

    sample_id: str
    title: str
    canvas_width: int
    canvas_height: int
    background_path: str = Field(..., description="Absolute path to the composited background PNG.")
    text_contents: List[str] = Field(default_factory=list, description="Placeable text, GT z-order.")
    text_assets: List[Dict] = Field(
        default_factory=list,
        description=(
            "Step 80 text-as-image mode: per text element with a cached "
            "pre-rendered bitmap, {content, asset_ref, width, height} where "
            "width/height are the designer's natural canvas size. Empty when "
            "the cache has no text images (pre-Step-80 snapshots)."
        ),
    )
    underlay_regions: List[UnderlayRegion] = Field(default_factory=list)
    baked_counts: Dict[str, int] = Field(
        default_factory=dict, description="kind -> number of layers baked into the background."
    )
    skipped_assets: int = Field(default=0, description="Baked-kind layers whose PNG failed to load.")


def _luminance_text_color(hex_color: str) -> str:
    """Dark text on light panels, light text on dark panels (same formula as
    pipeline._text_color_for_background; duplicated to avoid a tools->pipeline
    import cycle)."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return "#111111" if luminance >= 128 else "#F4F4F4"


def _dominant_color(img: Image.Image) -> Optional[str]:
    """Dominant colour of the opaque pixels via quantised-bucket voting.

    A plain mean turns two-tone panels into mud; instead pixels are quantised
    to 32-level buckets, the most common bucket wins, and the actual pixels in
    that bucket are averaged. Panels classified as shapes have <=16 unique
    colours (Step 27 classifier signal), so one bucket dominates cleanly.
    Returns None when no pixel is sufficiently opaque.
    """
    rgba = img.convert("RGBA")
    # Downsample large panels: colour voting does not need full resolution.
    if rgba.width * rgba.height > 65536:
        rgba.thumbnail((256, 256), Image.NEAREST)
    px = list(rgba.getdata())
    buckets: Counter = Counter()
    sums: Dict[Tuple[int, int, int], List[int]] = {}
    for r, g, b, a in px:
        if a < 128:
            continue
        key = (r // 32, g // 32, b // 32)
        buckets[key] += 1
        acc = sums.setdefault(key, [0, 0, 0])
        acc[0] += r
        acc[1] += g
        acc[2] += b
    if not buckets:
        return None
    key, n = buckets.most_common(1)[0]
    acc = sums[key]
    return "#{:02X}{:02X}{:02X}".format(acc[0] // n, acc[1] // n, acc[2] // n)


def _opaque_coverage(img: Image.Image) -> float:
    """Fraction of pixels with alpha >= 128 (downsampled for speed)."""
    rgba = img.convert("RGBA")
    if rgba.width * rgba.height > 65536:
        rgba = rgba.copy()
        rgba.thumbnail((256, 256), Image.NEAREST)
    alpha = rgba.getchannel("A")
    opaque = sum(1 for a in alpha.getdata() if a >= 128)
    return opaque / max(1, rgba.width * rgba.height)


def _paste_layer(canvas: Image.Image, plate: Image.Image, elem: dict) -> Image.Image:
    """Composite one layer at its GT position via a transparent full-canvas
    buffer, so negative offsets / oversize plates crop correctly (Step 47)."""
    cw, ch = canvas.size
    w = max(1, int(round(float(elem.get("width", cw)))))
    h = max(1, int(round(float(elem.get("height", ch)))))
    left = int(round(float(elem.get("left", 0.0))))
    top = int(round(float(elem.get("top", 0.0))))
    plate = plate.convert("RGBA").resize((w, h), Image.LANCZOS)
    layer = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    layer.paste(plate, (left, top), plate)
    return Image.alpha_composite(canvas, layer)


def _clamped_bbox(elem: dict, cw: int, ch: int) -> List[int]:
    left = int(round(float(elem["left"])))
    top = int(round(float(elem["top"])))
    right = left + int(round(float(elem["width"])))
    bottom = top + int(round(float(elem["height"])))
    return [max(0, left), max(0, top), min(cw, right), min(ch, bottom)]


def _region_large_enough(bbox: List[int], cw: int, ch: int) -> bool:
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    return (
        w >= max(MIN_REGION_W_PX, MIN_REGION_W_FRAC * cw)
        and h >= max(MIN_REGION_H_PX, MIN_REGION_H_FRAC * ch)
    )


def _holds_gt_text(bbox: List[int], text_bboxes: List[List[int]]) -> bool:
    """True when some GT text element sits (mostly) on this shape.

    Overlap is measured relative to the TEXT area, so a big panel behind a
    small caption qualifies, while a divider line crossing a heading does not.
    """
    for tb in text_bboxes:
        ix = max(0, min(bbox[2], tb[2]) - max(bbox[0], tb[0]))
        iy = max(0, min(bbox[3], tb[3]) - max(bbox[1], tb[1]))
        text_area = max(1, (tb[2] - tb[0]) * (tb[3] - tb[1]))
        if ix * iy / text_area >= TEXT_OVERLAP_MIN:
            return True
    return False


def preprocess_sample(sample_dir: Path, out_dir: Path) -> PreprocessedSample:
    """Convert one cached Crello sample into SEGA-style pipeline input.

    Reads ``<sample_dir>/meta.json`` (+ asset PNGs), writes
    ``<out_dir>/bg_composite.png`` and ``<out_dir>/sega_input.json``, and
    returns the parsed :class:`PreprocessedSample`.
    """
    meta = json.loads((sample_dir / "meta.json").read_text())
    cw = int(meta["canvas_width"])
    ch = int(meta["canvas_height"])
    out_dir.mkdir(parents=True, exist_ok=True)

    canvas = Image.new("RGBA", (cw, ch), (255, 255, 255, 255))
    baked_counts: Dict[str, int] = {}
    skipped = 0
    regions: List[UnderlayRegion] = []
    pending_regions: List[dict] = []  # Step 79: colours resolved post-composite
    texts: List[str] = []
    text_assets: List[Dict] = []  # Step 80: pre-rendered text bitmaps

    # GT text bboxes, needed to tell text underlays from decorative shapes.
    text_bboxes = [
        _clamped_bbox(e, cw, ch) for e in meta["elements"] if e.get("kind") == "text"
    ]

    for elem in meta["elements"]:
        kind = elem.get("kind")
        if kind == "text":
            content = (elem.get("content") or "").strip()
            if content:
                texts.append(content)
                if elem.get("asset_ref"):
                    text_assets.append(
                        {
                            "content": content,
                            "asset_ref": elem["asset_ref"],
                            "width": max(1, int(round(float(elem["width"])))),
                            "height": max(1, int(round(float(elem["height"])))),
                        }
                    )
            continue
        if kind not in BAKED_KINDS or not elem.get("asset_ref"):
            continue
        try:
            plate = Image.open(elem["asset_ref"])
        except (OSError, IOError) as err:
            logger.warning(f"crello_preprocessor: skipping unreadable asset {elem['asset_ref']} ({err})")
            skipped += 1
            continue
        canvas = _paste_layer(canvas, plate, elem)
        baked_counts[kind] = baked_counts.get(kind, 0) + 1

        if kind == "underlay":
            bbox = _clamped_bbox(elem, cw, ch)
            if not _region_large_enough(bbox, cw, ch):
                continue
            if not _holds_gt_text(bbox, text_bboxes):
                continue
            # Step 79: colour resolution is deferred until compositing is
            # DONE -- a frame's backdrop depends on every layer under (and
            # after) it. Here we only record the plate's own stats.
            pending_regions.append(
                {
                    "bbox": bbox,
                    "plate_color": _dominant_color(plate),
                    "plate_coverage": _opaque_coverage(plate),
                }
            )

    # Step 79: resolve each region's colour against the FINISHED composite.
    for pend in pending_regions:
        bbox = pend["bbox"]
        if pend["plate_coverage"] >= SOLID_MIN_OPAQUE_COVERAGE:
            color = pend["plate_color"]  # opaque plate: its fill IS the backdrop
            panel_type = "solid"
        else:
            # Transparent frame: the text backdrop is whatever the composite
            # shows inside the bbox (photo, gradient, ...).
            crop = canvas.crop(tuple(bbox))
            color = _dominant_color(crop)
            panel_type = "frame"
        if color is None:
            continue
        regions.append(
            UnderlayRegion(
                bbox=bbox,
                dominant_color=color,
                recommended_text_color=_luminance_text_color(color),
                panel_type=panel_type,
            )
        )

    bg_path = out_dir / BG_FILENAME
    canvas.convert("RGB").save(bg_path, format="PNG")

    result = PreprocessedSample(
        sample_id=str(meta["id"]),
        title=str(meta.get("title", "")),
        canvas_width=cw,
        canvas_height=ch,
        background_path=str(bg_path),
        text_contents=texts,
        text_assets=text_assets,
        underlay_regions=regions,
        baked_counts=baked_counts,
        skipped_assets=skipped,
    )
    (out_dir / INPUT_FILENAME).write_text(result.model_dump_json(indent=2))
    return result
