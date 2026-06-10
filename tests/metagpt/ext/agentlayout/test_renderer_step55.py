"""Step 55 renderer ceiling lift (2026-06-11).

Motivated by the Step 54 render-parity decomposition: with IDENTICAL geometry
the blind judge still preferred designer GT 31/40 on design_layout, i.e. the
render channel (fonts, alignment, overflow, rotation) is the majority of the
blind gap. Step 55 lifts the renderer limitations behind that ceiling:

1. Variable-font weight selection — Montserrat/Baloo2/DancingScript/Oswald
   ship as variable TTFs; ``_apply_weight_variation`` selects the Bold or
   Regular named instance (Montserrat's default instance is Thin!).
2. Auto-wrap + shrink-to-fit — ``_fit_text`` word-wraps unbroken text to the
   bbox width and shrinks the font (floor ``MIN_FONT_SIZE``) until the block
   fits; manual newlines are respected verbatim.
3. Text rotation — ``layout_el.angle`` is honoured for text (clockwise, same
   convention as images); fitting uses the rotated axis-aligned extent.
"""
from __future__ import annotations

import pytest
from PIL import ImageFont

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
    MIN_FONT_SIZE,
    PROJECT_FONT_DIR,
    _MEASURE_DRAW,
    _fit_text,
    _resolve_font,
    _wrap_to_width,
    render,
)


def _has_bundled(name: str) -> bool:
    return (PROJECT_FONT_DIR / name).exists()


# ============================================================
# Variable-font weight selection
# ============================================================


@pytest.mark.skipif(not _has_bundled("Montserrat-Variable.ttf"), reason="font not bundled")
def test_montserrat_regular_is_not_thin():
    """Montserrat's variable default instance is Thin; we must select Regular."""
    font = _resolve_font("sans-serif", "regular", 32)
    assert font.getname() == ("Montserrat", "Regular")


@pytest.mark.skipif(not _has_bundled("Montserrat-Variable.ttf"), reason="font not bundled")
def test_montserrat_bold_instance_selected():
    font = _resolve_font("sans-serif", "bold", 32)
    assert font.getname() == ("Montserrat", "Bold")


@pytest.mark.skipif(not _has_bundled("Baloo2-Variable.ttf"), reason="font not bundled")
def test_display_bold_resolves_to_baloo_bold():
    font = _resolve_font("display", "bold", 32)
    assert font.getname() == ("Baloo 2", "Bold")


@pytest.mark.skipif(not _has_bundled("DancingScript-Regular.ttf"), reason="font not bundled")
def test_script_bold_no_longer_falls_back_to_regular():
    """Pre-55 bug: DancingScript-Bold.ttf never existed, bold rendered regular."""
    font = _resolve_font("script", "bold", 32)
    assert font.getname()[1] == "Bold"


def test_static_font_weight_request_never_crashes():
    """Static fonts have no variations; the variation step must pass through."""
    font = _resolve_font("serif", "bold", 32)
    assert isinstance(font, (ImageFont.FreeTypeFont, ImageFont.ImageFont))


@pytest.mark.parametrize("family", ["rounded", "Baloo 2", "Bebas Neue"])
def test_new_display_keywords(family):
    from metagpt.ext.agentlayout.tools.renderer import _normalize_family

    assert _normalize_family(family) == "display"


# ============================================================
# Auto-wrap
# ============================================================


def test_wrap_to_width_each_line_fits():
    font = _resolve_font("sans-serif", "regular", 24)
    text = "join us for an amazing weekend of live performances and fun"
    wrapped = _wrap_to_width(text, font, 200)
    lines = wrapped.split("\n")
    assert len(lines) > 1
    for line in lines:
        assert _MEASURE_DRAW.textlength(line, font=font) <= 200
    assert wrapped.replace("\n", " ") == text


def test_wrap_noop_when_text_already_fits():
    font = _resolve_font("sans-serif", "regular", 16)
    assert _wrap_to_width("hello", font, 500) == "hello"


# ============================================================
# Shrink-to-fit
# ============================================================


def _text_layout(**overrides) -> LayoutElement:
    base = dict(id="t1", left=0, top=0, width=200, height=80, z_index=1)
    base.update(overrides)
    return LayoutElement(**base)


def test_fit_text_shrinks_oversized_font():
    el = _text_layout(font_size=72)
    font, fitted = _fit_text("A VERY LONG FESTIVAL HEADLINE", el)
    assert font.size < 72
    bbox = _MEASURE_DRAW.multiline_textbbox((0, 0), fitted, font=font)
    assert bbox[2] - bbox[0] <= el.width
    assert bbox[3] - bbox[1] <= el.height


def test_fit_text_keeps_size_when_it_fits():
    el = _text_layout(font_size=20, width=500, height=100)
    font, fitted = _fit_text("hello", el)
    assert font.size == 20
    assert fitted == "hello"


def test_fit_text_respects_manual_newlines():
    el = _text_layout(font_size=18, width=400, height=200)
    text = "We stay at work for you.\nStay at home for us."
    _, fitted = _fit_text(text, el)
    assert fitted == text


def test_fit_text_floor_allows_overflow():
    """A word that can never fit stops at MIN_FONT_SIZE instead of looping."""
    el = _text_layout(font_size=40, width=10, height=10)
    font, _ = _fit_text("unbreakablesuperlongword", el)
    assert font.size == MIN_FONT_SIZE


# ============================================================
# Rotation
# ============================================================


def test_fit_text_rotation_uses_rotated_extent():
    """A 90deg vertical hashtag is constrained by bbox HEIGHT, not width."""
    flat = _text_layout(font_size=40, width=60, height=400)
    rot = _text_layout(font_size=40, width=60, height=400, angle=90)
    font_flat, _ = _fit_text("#StayHome", flat)
    font_rot, _ = _fit_text("#StayHome", rot)
    assert font_rot.size > font_flat.size


def _render_text(angle: int):
    spec = DesignSpec(
        canvas=Canvas(width=300, height=300, background_color="#FFFFFF"),
        elements=[
            Element(
                id="t1",
                semantic_type=SemanticType.TITLE,
                visual_type=VisualType.TEXT,
                content="ROTATE",
            )
        ],
    )
    cand = Candidate(
        candidate_id="c1",
        elements=[
            LayoutElement(
                id="t1",
                left=50,
                top=50,
                width=200,
                height=200,
                z_index=1,
                font_size=40,
                angle=angle,
            )
        ],
    )
    return render(cand, spec)


def test_rotated_text_differs_from_unrotated():
    img0 = _render_text(0)
    img90 = _render_text(90)
    assert list(img0.getdata()) != list(img90.getdata())


def test_rotated_text_actually_paints_pixels():
    img = _render_text(90)
    non_white = sum(1 for px in img.convert("RGB").getdata() if px != (255, 255, 255))
    assert non_white > 50
