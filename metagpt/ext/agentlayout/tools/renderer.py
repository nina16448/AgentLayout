"""Renderer — turns one ``Candidate`` into a PIL Image (and optionally a PNG file).

Heaviest tool in the pipeline because it has to handle four kinds of work:
1. Background canvas (load ``canvas.background_asset_ref`` or fall back to white)
2. Image elements (load ``asset_ref``, resize, paste with alpha)
3. Text elements (resolve a system font, draw with alignment + color)
4. z-order layering (sort by ``z_index`` ascending; lower paints first)

Public API::

    render(candidate, spec) -> PIL.Image.Image          # in-memory render
    render_to_file(candidate, spec, path) -> Path       # render + save PNG
    image_to_base64(img, format='PNG') -> str           # for vision LLM input

Phase 1 limitations (deliberate, paper-worthy):
  * Text does not auto-wrap; overflow is allowed and shows up as overflow in
    the rendered image (Aesthetic Judge can penalise it).
  * Only image elements respect ``angle``; text rotation is not implemented.
  * Font resolution depends on system fonts (DejaVu / Noto CJK on Ubuntu);
    if none are found, falls back to PIL's bitmap default.
"""
from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from PIL import Image, ImageDraw, ImageFont

from metagpt.ext.agentlayout.schema import (
    Candidate,
    DesignSpec,
    Element,
    LayoutElement,
    SemanticType,
    VisualType,
)


# ============================================================
# Configuration
# ============================================================


DEFAULT_BACKGROUND_COLOR: Tuple[int, int, int, int] = (255, 255, 255, 255)
"""Per project memory: blank background defaults to opaque white."""


# Family/weight -> ordered list of font paths to try. Order = most specific first.
FONT_CANDIDATES: Dict[Tuple[str, str], List[str]] = {
    ("sans-serif", "regular"): [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans.ttf",
    ],
    ("sans-serif", "bold"): [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "DejaVuSans-Bold.ttf",
    ],
    ("serif", "regular"): [
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "DejaVuSerif.ttf",
    ],
    ("serif", "bold"): [
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        "DejaVuSerif-Bold.ttf",
    ],
}

# Tried after the family/weight candidates so CJK characters render correctly.
CJK_FONT_CANDIDATES: List[str] = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
]


# ============================================================
# Public API
# ============================================================


def render(candidate: Candidate, spec: DesignSpec) -> Image.Image:
    """Render one ``Candidate`` to an RGBA ``PIL.Image``."""
    canvas = _make_canvas(spec)
    # Lower z_index paints first so higher-z elements end up on top.
    sorted_elements = sorted(candidate.elements, key=lambda e: e.z_index)
    for layout_el in sorted_elements:
        spec_el = spec.get_element(layout_el.id)
        if spec_el is None:
            # Quality Checker would already flag this; renderer just skips.
            continue
        if spec_el.visual_type == VisualType.IMAGE:
            _paint_image_element(canvas, spec_el, layout_el)
        elif spec_el.visual_type == VisualType.TEXT:
            _paint_text_element(canvas, spec_el, layout_el)
    return canvas


def render_to_file(
    candidate: Candidate, spec: DesignSpec, path: Union[Path, str]
) -> Path:
    """Render and save as PNG. Returns the actual Path written to."""
    img = render(candidate, spec)
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, format="PNG")
    return out_path


def image_to_base64(img: Image.Image, format: str = "PNG") -> str:
    """Encode a PIL Image to a base64 string for vision-LLM input."""
    buf = BytesIO()
    img.convert("RGB").save(buf, format=format)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ============================================================
# Canvas
# ============================================================


def _make_canvas(spec: DesignSpec) -> Image.Image:
    """Create the base canvas: load ``background_asset_ref`` or fill with white."""
    size = (spec.canvas.width, spec.canvas.height)
    bg_ref = spec.canvas.background_asset_ref
    if bg_ref and Path(bg_ref).exists():
        try:
            bg = Image.open(bg_ref).convert("RGBA")
            if bg.size != size:
                bg = bg.resize(size, Image.LANCZOS)
            return bg
        except (OSError, IOError):
            pass  # corrupt file -> fall through to solid white
    return Image.new("RGBA", size, DEFAULT_BACKGROUND_COLOR)


# ============================================================
# Image elements
# ============================================================


def _paint_image_element(
    canvas: Image.Image, spec_el: Element, layout_el: LayoutElement
) -> None:
    """Paste an image element onto the canvas.

    The background image is already painted by ``_make_canvas`` so we skip it
    here to avoid double-paint. Missing or corrupt asset files are replaced
    with a translucent grey placeholder so debugging is easy.
    """
    if spec_el.semantic_type == SemanticType.BACKGROUND_IMAGE:
        return
    img: Optional[Image.Image] = None
    if spec_el.asset_ref and Path(spec_el.asset_ref).exists():
        try:
            img = Image.open(spec_el.asset_ref).convert("RGBA")
        except (OSError, IOError):
            img = None
    if img is None:
        img = Image.new(
            "RGBA", (layout_el.width, layout_el.height), (200, 200, 200, 180)
        )
    if img.size != (layout_el.width, layout_el.height):
        img = img.resize((layout_el.width, layout_el.height), Image.LANCZOS)
    if layout_el.angle:
        # Schema angle is clockwise-positive; PIL.rotate is counter-clockwise.
        img = img.rotate(-layout_el.angle, expand=True, resample=Image.BICUBIC)
    canvas.paste(img, (layout_el.left, layout_el.top), img)


# ============================================================
# Text elements
# ============================================================


def _paint_text_element(
    canvas: Image.Image, spec_el: Element, layout_el: LayoutElement
) -> None:
    """Draw text inside the layout element's bbox.

    No auto-wrap (Phase 1 limitation): overflowing text is left as-is so the
    Aesthetic Judge can flag the visual imbalance.
    """
    text = spec_el.content or ""
    if not text:
        return
    draw = ImageDraw.Draw(canvas)
    font = _resolve_font(
        layout_el.font_family or "sans-serif",
        layout_el.font_weight or "regular",
        layout_el.font_size or 32,
    )
    color = _parse_color(layout_el.color or "#000000")
    align = (layout_el.text_align or "left").lower()
    # PIL note: draw.text(anchor=...) does NOT work when the string contains
    # newlines -- anchor is only valid for single-line text. For multiline we
    # measure the text block's bbox and position it manually to emulate the
    # intended anchor; for single-line we keep the simpler anchor path.
    if "\n" in text:
        bbox = draw.multiline_textbbox((0, 0), text, font=font, align="left")
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        if align == "center":
            x = layout_el.left + (layout_el.width - text_w) // 2
            y = layout_el.top + (layout_el.height - text_h) // 2
            pil_align = "center"
        elif align == "right":
            x = layout_el.left + layout_el.width - text_w
            y = layout_el.top
            pil_align = "right"
        else:  # 'left' or 'justify'
            x = layout_el.left
            y = layout_el.top
            pil_align = "left"
        draw.multiline_text((x, y), text, fill=color, font=font, align=pil_align)
    elif align == "center":
        anchor_xy = (
            layout_el.left + layout_el.width // 2,
            layout_el.top + layout_el.height // 2,
        )
        draw.text(anchor_xy, text, fill=color, font=font, anchor="mm", align="center")
    elif align == "right":
        anchor_xy = (layout_el.left + layout_el.width, layout_el.top)
        draw.text(anchor_xy, text, fill=color, font=font, anchor="rt", align="right")
    else:  # 'left' or 'justify' (PIL has no native justify; fall back to left)
        draw.text(
            (layout_el.left, layout_el.top),
            text,
            fill=color,
            font=font,
            anchor="lt",
        )


# ============================================================
# Font resolution
# ============================================================


def _resolve_font(family: str, weight: str, size: int) -> ImageFont.ImageFont:
    """Try family/weight-specific fonts, then CJK fallbacks, then PIL default."""
    family_lc = family.lower()
    family_norm = (
        "serif" if "serif" in family_lc and "sans" not in family_lc else "sans-serif"
    )
    weight_lc = (weight or "").lower()
    weight_norm = "bold" if weight_lc in {"bold", "700", "800", "900"} else "regular"

    candidates = list(FONT_CANDIDATES.get((family_norm, weight_norm), []))
    candidates.extend(CJK_FONT_CANDIDATES)

    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


# ============================================================
# Color parsing
# ============================================================


def _parse_color(hex_str: str) -> Tuple[int, int, int, int]:
    """Parse '#RRGGBB' or '#RGB' into an RGBA 4-tuple. Black on parse failure."""
    h = (hex_str or "").lstrip("#").strip()
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return (0, 0, 0, 255)
    try:
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
    except ValueError:
        return (0, 0, 0, 255)
    return (r, g, b, 255)
