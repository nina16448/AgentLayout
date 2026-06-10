"""Step 47 renderer fixes (2026-06-10).

Covers the two renderer-side quality gaps found in the Step 46 post-mortem:

1. Font family expansion — ``font_family`` strings like 'cursive' or
   'Playfair Display' previously collapsed to DejaVu sans/serif, so every
   render lost the typography pairwise axis against designer GT by default.
   ``_normalize_family`` now buckets free-form names into
   sans-serif / serif / script / display, and ``_resolve_font`` falls back
   gracefully ((family, weight) -> (family, regular) -> (sans-serif, weight)
   -> CJK -> PIL default) so a machine without the optional fonts behaves
   exactly like the pre-Step-47 renderer.

2. Upscale cap — small assets (e.g. a 68px heart) were stretched to fill
   arbitrarily large bboxes and rendered as blurry blobs. ``MAX_UPSCALE``
   now caps enlargement at 2x native resolution per axis, centring the
   sharp image inside the declared bbox. Downscaling is unaffected.
"""
from __future__ import annotations

import pytest
from PIL import Image, ImageFont

from metagpt.ext.agentlayout.schema import (
    Candidate,
    Canvas,
    DesignSpec,
    Element,
    LayoutElement,
    SemanticType,
    VisualType,
)
from metagpt.ext.agentlayout.tools.renderer import (
    MAX_UPSCALE,
    _normalize_family,
    _resolve_font,
    render,
)


# ============================================================
# Font family normalization
# ============================================================


@pytest.mark.parametrize(
    ("family", "expected"),
    [
        ("sans-serif", "sans-serif"),
        ("Roboto", "sans-serif"),
        ("", "sans-serif"),
        (None, "sans-serif"),
        ("serif", "serif"),
        ("Times New Roman serif", "serif"),
        ("cursive", "script"),
        ("Dancing Script", "script"),
        ("handwriting", "script"),
        ("Great Vibes", "script"),
        ("Playfair Display", "display"),
        ("slab-serif", "display"),
        ("Lobster", "display"),
        ("Oswald Condensed", "display"),
    ],
)
def test_normalize_family(family, expected):
    assert _normalize_family(family) == expected


@pytest.mark.parametrize("family", ["sans-serif", "serif", "script", "display", "cursive", "whatever"])
@pytest.mark.parametrize("weight", ["regular", "bold", "700", None])
def test_resolve_font_never_crashes(family, weight):
    """Missing optional fonts must degrade to *a* usable font, never raise."""
    font = _resolve_font(family, weight, 32)
    assert isinstance(font, (ImageFont.FreeTypeFont, ImageFont.ImageFont))


def test_script_and_sans_resolve_independently():
    """Whatever files exist, resolution is deterministic per family."""
    sans = _resolve_font("sans-serif", "regular", 32)
    script = _resolve_font("cursive", "regular", 32)
    assert isinstance(sans, (ImageFont.FreeTypeFont, ImageFont.ImageFont))
    assert isinstance(script, (ImageFont.FreeTypeFont, ImageFont.ImageFont))


# ============================================================
# Upscale cap
# ============================================================


def _spec_with_image(asset_path: str, canvas_px: int = 200) -> DesignSpec:
    return DesignSpec(
        canvas=Canvas(width=canvas_px, height=canvas_px, background_color="#FFFFFF"),
        elements=[
            Element(
                id="img1",
                semantic_type=SemanticType.DECORATIVE_IMAGE,
                visual_type=VisualType.IMAGE,
                asset_ref=asset_path,
            )
        ],
    )


def _candidate(left: int, top: int, width: int, height: int) -> Candidate:
    return Candidate(
        candidate_id="c1",
        elements=[
            LayoutElement(
                id="img1", left=left, top=top, width=width, height=height, z_index=1
            )
        ],
    )


def test_upscale_capped_at_max_upscale(tmp_path):
    """A 10px asset in a 100px bbox draws at 20px (2x), centred — not stretched."""
    asset = tmp_path / "red10.png"
    Image.new("RGBA", (10, 10), (255, 0, 0, 255)).save(asset)

    spec = _spec_with_image(str(asset))
    img = render(_candidate(left=50, top=50, width=100, height=100), spec)

    # bbox centre (100, 100) is covered by the capped 20x20 paste at (90, 90).
    assert img.getpixel((100, 100))[:3] == (255, 0, 0)
    # bbox corner (55, 55) is OUTSIDE the capped paste -> still background white.
    # (Pre-Step-47 stretch-to-bbox behaviour would have painted it red.)
    assert img.getpixel((55, 55))[:3] == (255, 255, 255)


def test_downscale_still_fills_bbox(tmp_path):
    """Downscaling (native > bbox) keeps the old fill-the-bbox behaviour."""
    asset = tmp_path / "red100.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(asset)

    spec = _spec_with_image(str(asset))
    img = render(_candidate(left=0, top=0, width=50, height=50), spec)

    assert img.getpixel((2, 2))[:3] == (255, 0, 0)
    assert img.getpixel((47, 47))[:3] == (255, 0, 0)
    assert img.getpixel((60, 60))[:3] == (255, 255, 255)


def test_exact_2x_upscale_fills_bbox(tmp_path):
    """At exactly MAX_UPSCALE the asset still fills the whole bbox."""
    native = 25
    target = int(native * MAX_UPSCALE)
    asset = tmp_path / "red25.png"
    Image.new("RGBA", (native, native), (255, 0, 0, 255)).save(asset)

    spec = _spec_with_image(str(asset))
    img = render(_candidate(left=0, top=0, width=target, height=target), spec)

    assert img.getpixel((1, 1))[:3] == (255, 0, 0)
    assert img.getpixel((target - 2, target - 2))[:3] == (255, 0, 0)
