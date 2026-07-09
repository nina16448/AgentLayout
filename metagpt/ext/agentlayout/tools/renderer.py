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

Step 55 (2026-06-11) lifted the former Phase 1 limitations, motivated by the
step54 render-parity decomposition (render channel = majority of the blind
gap vs designer GT):
  * Text auto-wraps to the bbox width and shrinks to fit (floor 8px); manual
    newlines in content are respected verbatim.
  * Text elements honour ``angle`` (same convention as images).
  * Bundled OFL fonts include variable fonts; bold is selected via named
    instances. System fonts / PIL bitmap default remain the fallback chain.
"""
from __future__ import annotations

import base64
import math
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

MAX_UPSCALE: float = 2.0
"""Step 47: cap on enlarging an image asset beyond its native resolution.
Backgrounds (painted by _make_canvas) are exempt -- they must fill the canvas."""


# Project-local font drop-in directory (OFL-licensed Google Fonts etc.).
# Checked before system paths so designers/CI can pin exact font files.
PROJECT_FONT_DIR: Path = Path(__file__).resolve().parent / "fonts"

# Family/weight -> ordered list of font paths to try. Order = most specific first.
# Bare filenames are tried in PROJECT_FONT_DIR first, then via the system font
# search path. Missing files are skipped silently (graceful degradation), so
# listing optional fonts here is safe.
FONT_CANDIDATES: Dict[Tuple[str, str], List[str]] = {
    # Step 55 (2026-06-11): Montserrat first. Step 54 render-parity showed the
    # render channel (incl. DejaVu's "engineering mockup" look) accounts for
    # the majority of the blind design_layout/typography gap vs designer GT.
    ("sans-serif", "regular"): [
        "Montserrat-Variable.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "DejaVuSans.ttf",
    ],
    ("sans-serif", "bold"): [
        "Montserrat-Variable.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "DejaVuSans-Bold.ttf",
    ],
    ("serif", "regular"): [
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
        "/usr/share/fonts/opentype/urw-base35/P052-Roman.otf",
        "DejaVuSerif.ttf",
    ],
    ("serif", "bold"): [
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
        "/usr/share/fonts/opentype/urw-base35/P052-Bold.otf",
        "DejaVuSerif-Bold.ttf",
    ],
    # Step 47 (2026-06-10): script / display families. Before this, EVERY text
    # element rendered in DejaVu regardless of the Generator's font_family,
    # which made all renders look like engineering mockups next to designer
    # GT typography in pairwise judging.
    ("script", "regular"): [
        "GreatVibes-Regular.ttf",
        "Pacifico-Regular.ttf",
        "DancingScript-Regular.ttf",
        "/usr/share/fonts/opentype/urw-base35/Z003-MediumItalic.otf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
    ],
    # Step 55: DancingScript-Bold.ttf / Oswald-Bold.ttf never existed in
    # PROJECT_FONT_DIR -- bold requests silently fell back to regular faces.
    # The bundled DancingScript/Oswald files are VARIABLE fonts with a Bold
    # named instance, applied at load time by _apply_weight_variation.
    ("script", "bold"): [
        "DancingScript-Regular.ttf",
        "GreatVibes-Regular.ttf",
        "Pacifico-Regular.ttf",
        "/usr/share/fonts/opentype/urw-base35/Z003-MediumItalic.otf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-BoldItalic.ttf",
    ],
    ("display", "regular"): [
        "Lobster-Regular.ttf",
        "BebasNeue-Regular.ttf",
        "Oswald-Regular.ttf",
        "/usr/share/fonts/opentype/urw-base35/C059-Roman.otf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
    ],
    # Baloo2 bold-rounded first: the face class designer GT titles use most
    # (step54 spot-check), previously unrepresented in the bundle.
    ("display", "bold"): [
        "Baloo2-Variable.ttf",
        "Oswald-Regular.ttf",
        "Lobster-Regular.ttf",
        "/usr/share/fonts/opentype/urw-base35/C059-Bold.otf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
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


def _hex_to_rgba(hex_color: str) -> Tuple[int, int, int, int]:
    """Convert ``'#RRGGBB'`` -> RGBA tuple (alpha=255).

    The Canvas schema validator guarantees a 6-digit hex; this is a defensive
    fallback for hand-built specs that bypassed validation.
    """
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return DEFAULT_BACKGROUND_COLOR
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)
    except ValueError:
        return DEFAULT_BACKGROUND_COLOR


def _make_canvas(spec: DesignSpec) -> Image.Image:
    """Create the base canvas.

    Precedence:
      1. ``background_asset_ref`` (if file exists and is loadable)
      2. ``background_color`` (solid hex fill) — added 2026-05-14 to replace
         the bare-white default that drove plateau-72 visual_coherence scores
      3. ``DEFAULT_BACKGROUND_COLOR`` (legacy white for specs predating
         ``background_color``)
    """
    size = (spec.canvas.width, spec.canvas.height)
    bg_ref = spec.canvas.background_asset_ref
    if bg_ref and Path(bg_ref).exists():
        try:
            bg = Image.open(bg_ref).convert("RGBA")
            if bg.size != size:
                bg = bg.resize(size, Image.LANCZOS)
            return bg
        except (OSError, IOError):
            pass  # corrupt file -> fall through to color/white
    if spec.canvas.background_color:
        return Image.new("RGBA", size, _hex_to_rgba(spec.canvas.background_color))
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
    paste_left, paste_top = layout_el.left, layout_el.top
    target_w, target_h = layout_el.width, layout_el.height
    if img.size != (target_w, target_h):
        # Step 47 (2026-06-10): cap upscaling at MAX_UPSCALE x the asset's
        # native resolution. A 68px heart stretched to ~400px renders as a
        # blurry blob and tanks the pairwise judge's graphics axis; better to
        # draw it sharp at <=2x and centre it inside the declared bbox.
        # Downscaling is unaffected.
        draw_w = min(target_w, max(1, int(img.width * MAX_UPSCALE)))
        draw_h = min(target_h, max(1, int(img.height * MAX_UPSCALE)))
        img = img.resize((draw_w, draw_h), Image.LANCZOS)
        paste_left += (target_w - draw_w) // 2
        paste_top += (target_h - draw_h) // 2
    if layout_el.angle:
        # Schema angle is clockwise-positive; PIL.rotate is counter-clockwise.
        img = img.rotate(-layout_el.angle, expand=True, resample=Image.BICUBIC)
    canvas.paste(img, (paste_left, paste_top), img)


# ============================================================
# Text elements
# ============================================================


# 1x1 dummy surface used only for text measurement (never painted on).
_MEASURE_DRAW = ImageDraw.Draw(Image.new("RGBA", (1, 1)))

MIN_FONT_SIZE: int = 8
"""Step 55: shrink-to-fit floor. Below this text is unreadable; overflow is
allowed instead (graceful degradation, never clipped)."""


def _wrap_to_width(text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    """Greedy word-wrap to a pixel width. A word that alone exceeds
    ``max_width`` gets its own line (the shrink loop deals with it)."""
    lines: List[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        cur = words[0]
        for word in words[1:]:
            trial = f"{cur} {word}"
            if _MEASURE_DRAW.textlength(trial, font=font) <= max_width:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return "\n".join(lines)


def _fit_text(
    text: str, layout_el: LayoutElement
) -> Tuple[ImageFont.ImageFont, str]:
    """Make text fit its bbox: wrap first, then shrink the font.

    Step 55 (motivated by the step54 render-parity decomposition): designer
    GT never overflows its box, so every overflow on our side is a pure
    render-channel loss in pairwise judging. Manual newlines in the content
    are respected verbatim (the author's line-break intent); only unbroken
    text is auto-wrapped. The declared font_size acts as an upper bound.
    """
    family = layout_el.font_family or "sans-serif"
    weight = layout_el.font_weight or "regular"
    manual_breaks = "\n" in text
    size = max(MIN_FONT_SIZE, layout_el.font_size or 32)
    # Rotated text must fit AFTER rotation: its axis-aligned extent is
    # (w cos + h sin, w sin + h cos). For 90deg this swaps width/height --
    # e.g. a vertical hashtag's line length is constrained by bbox HEIGHT.
    ang = math.radians(layout_el.angle or 0)
    cos_a, sin_a = abs(math.cos(ang)), abs(math.sin(ang))
    wrap_width = int(layout_el.width * cos_a + layout_el.height * sin_a)
    while True:
        font = _resolve_font(family, weight, size)
        fitted = text if manual_breaks else _wrap_to_width(text, font, wrap_width)
        bbox = _MEASURE_DRAW.multiline_textbbox((0, 0), fitted, font=font)
        text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        fits = (
            text_w * cos_a + text_h * sin_a <= layout_el.width
            and text_w * sin_a + text_h * cos_a <= layout_el.height
        )
        if fits or size <= MIN_FONT_SIZE:
            return font, fitted
        size = max(MIN_FONT_SIZE, int(size * 0.9))


def _paint_text_element(
    canvas: Image.Image, spec_el: Element, layout_el: LayoutElement
) -> None:
    """Draw text inside the layout element's bbox.

    Step 55: text is wrapped/shrunk to fit the bbox (see _fit_text); the
    pre-55 behaviour let overflow through, which the step54 render-parity
    experiment identified as a render-channel penalty vs designer GT.
    """
    text = spec_el.content or ""
    if not text:
        return
    draw = ImageDraw.Draw(canvas)
    font, text = _fit_text(text, layout_el)
    color = _parse_color(layout_el.color or "#000000")
    align = (layout_el.text_align or "left").lower()
    if layout_el.angle:
        # Step 55: rotated text (pre-55: only images honoured angle). Drawn
        # on its own transparent layer, rotated about the bbox centre.
        _paint_rotated_text(canvas, text, font, color, align, layout_el)
        return
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


def _paint_rotated_text(
    canvas: Image.Image,
    text: str,
    font: ImageFont.ImageFont,
    color: Tuple[int, int, int, int],
    align: str,
    layout_el: LayoutElement,
) -> None:
    """Draw text on a transparent layer, rotate it, paste at the bbox centre.

    Schema angle is clockwise-positive; PIL.rotate is counter-clockwise
    (same convention as _paint_image_element). ``align`` only controls
    line alignment inside the text block -- the block itself is centred on
    the bbox, which is the natural reading of a rotated element.
    """
    pil_align = align if align in ("center", "right") else "left"
    pad = 4
    bbox = _MEASURE_DRAW.multiline_textbbox((0, 0), text, font=font, align=pil_align)
    # Pillow's multiline_textbbox can return float coords (esp. for the default
    # bitmap font fallback), and Image.new requires an int size tuple -- without
    # the int() coercion this raises "TypeError: integer argument expected, got
    # float" on some samples. max(1, ...) guards against a zero-size layer.
    layer_w = max(1, int(round(bbox[2] - bbox[0] + 2 * pad)))
    layer_h = max(1, int(round(bbox[3] - bbox[1] + 2 * pad)))
    layer = Image.new("RGBA", (layer_w, layer_h), (0, 0, 0, 0))
    ImageDraw.Draw(layer).multiline_text(
        (pad - bbox[0], pad - bbox[1]), text, fill=color, font=font, align=pil_align
    )
    layer = layer.rotate(-layout_el.angle, expand=True, resample=Image.BICUBIC)
    paste_left = layout_el.left + (layout_el.width - layer.width) // 2
    paste_top = layout_el.top + (layout_el.height - layer.height) // 2
    canvas.paste(layer, (paste_left, paste_top), layer)


# ============================================================
# Font resolution
# ============================================================


_SCRIPT_KEYWORDS = ("script", "cursive", "hand", "callig", "brush", "vibes", "pacifico")
_DISPLAY_KEYWORDS = (
    "display", "slab", "poster", "decorative", "condensed",
    "lobster", "oswald", "impact", "bebas", "baloo", "rounded",
)


def _normalize_family(family: str) -> str:
    """Map a free-form font_family string to one of the four supported families.

    Step 47: the Generator emits free text ('cursive', 'Dancing Script',
    'Playfair Display', ...). Keyword buckets beat exact matching here because
    we only need the right *class* of font, not the exact face.
    """
    family_lc = (family or "").lower()
    if any(k in family_lc for k in _SCRIPT_KEYWORDS):
        return "script"
    if any(k in family_lc for k in _DISPLAY_KEYWORDS):
        return "display"
    if "serif" in family_lc and "sans" not in family_lc:
        return "serif"
    return "sans-serif"


def _candidate_paths(name: str) -> List[str]:
    """Expand a candidate entry: bare filenames also resolve in PROJECT_FONT_DIR."""
    if "/" in name:
        return [name]
    return [str(PROJECT_FONT_DIR / name), name]


def _apply_weight_variation(
    font: ImageFont.FreeTypeFont, weight_norm: str
) -> ImageFont.FreeTypeFont:
    """Select the named weight instance on a variable font.

    Step 55: Montserrat/Baloo2/DancingScript/Oswald ship as variable fonts.
    The default instance is NOT always Regular (Montserrat defaults to Thin),
    so both weights must be selected explicitly. Static fonts raise OSError
    on get_variation_names() and pass through unchanged.
    """
    target = "Bold" if weight_norm == "bold" else "Regular"
    try:
        names = [
            n.decode("utf-8", "ignore") if isinstance(n, bytes) else n
            for n in font.get_variation_names()
        ]
        if target in names:
            font.set_variation_by_name(target)
    except (OSError, IOError):
        pass
    return font


def _resolve_font(family: str, weight: str, size: int) -> ImageFont.ImageFont:
    """Try family/weight-specific fonts, then graceful fallbacks.

    Fallback chain: (family, weight) -> (family, regular) ->
    (sans-serif, weight) -> CJK -> PIL default. Each missing file is skipped,
    so an empty PROJECT_FONT_DIR degrades to the pre-Step-47 behaviour.
    """
    family_norm = _normalize_family(family)
    weight_lc = (weight or "").lower()
    weight_norm = "bold" if weight_lc in {"bold", "700", "800", "900"} else "regular"

    candidates: List[str] = []
    for key in (
        (family_norm, weight_norm),
        (family_norm, "regular"),
        ("sans-serif", weight_norm),
    ):
        for name in FONT_CANDIDATES.get(key, []):
            candidates.extend(_candidate_paths(name))
    candidates.extend(CJK_FONT_CANDIDATES)

    for path in candidates:
        try:
            font = ImageFont.truetype(path, size=size)
            return _apply_weight_variation(font, weight_norm)
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
