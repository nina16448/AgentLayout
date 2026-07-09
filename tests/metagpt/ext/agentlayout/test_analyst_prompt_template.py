"""Pinned-string regression tests for ``analyze_brief.PROMPT_TEMPLATE`` and the
related Canvas / pipeline changes that landed in step 7 (2026-05-14).

Motivation: step 6 confirmed plateau-72 is a *spec sparsity* problem -- a bare
3-element layout on a pure-white canvas cannot score above ~72 on
visual_coherence + layout_balance. Step 7 teaches the Analyst to fill in a
pleasant ``canvas.background_color`` by default and threads that color through
the renderer + Aesthetic Judge stub palette so the rendered PNG and the
``BackgroundAnalysis`` agree.

These tests do NOT call the LLM. They only assert that the guidance text,
worked palette, and downstream wiring stay in place.

Run:
    pytest tests/metagpt/ext/agentlayout/test_analyst_prompt_template.py -v --no-cov
"""
from __future__ import annotations

import pytest


# ============================================================
# 1. PROMPT_TEMPLATE pinning
# ============================================================


def test_analyst_prompt_pins_background_color_section_header():
    """The section header is the anchor for every other pinned string below."""
    from metagpt.ext.agentlayout.actions.analyze_brief import PROMPT_TEMPLATE

    assert "Background color inference" in PROMPT_TEMPLATE
    assert "canvas.background_color" in PROMPT_TEMPLATE


def test_analyst_prompt_pins_plateau_motivation_and_avoid_white():
    """The prompt must explain *why* a non-white default exists (plateau-72)
    and explicitly tell the LLM to avoid the pure-white fallback."""
    from metagpt.ext.agentlayout.actions.analyze_brief import PROMPT_TEMPLATE

    assert "plateau" in PROMPT_TEMPLATE
    assert "bare-white canvas" in PROMPT_TEMPLATE
    assert "AVOID" in PROMPT_TEMPLATE
    assert '"#FFFFFF"' in PROMPT_TEMPLATE


def test_build_prompt_str_format_is_safe_and_includes_zorder_guidance():
    """Regression for step 12b: PROMPT_TEMPLATE is consumed via str.format(),
    so any literal '{...}' in the template body (as opposed to the substituted
    FORMAT_EXAMPLE_JSON value) raises KeyError at runtime. The first content-
    aware live re-run crashed exactly this way (KeyError: '"hint"') because the
    z_order guidance line embedded a raw JSON object. This test exercises the
    real .format() path so an unescaped brace can never silently ship again,
    and pins that the z_order guidance survived the rewrite."""
    from metagpt.ext.agentlayout.actions.analyze_brief import AnalyzeBrief

    prompt = AnalyzeBrief()._build_prompt(
        "Create a 1008x1296 marketing graphic titled 'Demo'", [], None
    )
    assert "above_background" in prompt
    assert "z_order" in prompt


def test_analyst_prompt_enumerates_closed_position_hint_vocabulary():
    """Step 10b root-cause regression. Live #9rd crashed because the Analyst
    rebuilt the spec on RetryAnalyst with ``position_preference hint
    "below_title"`` -- a relational hint outside the QC 3x3 band whitelist --
    so every candidate hit UNKNOWN_HINT, 0 passed QC, and the run aborted.
    The prompt must enumerate the closed 9-region vocabulary and explicitly
    forbid inventing relational hints, mirroring the soft_constraints /
    semantic_type closed-enum pattern in the same template. Exercised through
    the real .format() path so it can never silently regress."""
    from metagpt.ext.agentlayout.actions.analyze_brief import AnalyzeBrief
    from metagpt.ext.agentlayout.tools.quality_checker import POSITION_HINT_TO_BANDS

    prompt = AnalyzeBrief()._build_prompt("Make a clean winter travel poster", [], None)

    assert "position_preference params.hint MUST be EXACTLY one of these 9" in prompt
    for region in (
        "top_left", "top_center", "top_right",
        "middle_left", "center", "middle_right",
        "bottom_left", "bottom_center", "bottom_right",
    ):
        assert region in prompt, f"missing region {region!r} in prompt"
        assert region in POSITION_HINT_TO_BANDS, f"{region!r} not QC-known"
    # The exact relational hint that crashed live #9rd must be named as forbidden.
    assert "below_title" in prompt
    assert "Do NOT invent relational hints" in prompt


def test_analyst_prompt_pins_palette_suggestions_per_keyword_bucket():
    """Five palette buckets, each containing at least one canonical hex --
    drop a bucket and the LLM loses guidance for that mood."""
    from metagpt.ext.agentlayout.actions.analyze_brief import PROMPT_TEMPLATE

    buckets = {
        "warm": "#F5E6D3",
        "cool": "#1B2B4A",
        "vibrant": "#FFE5B4",
        "dark": "#1A1A2E",
        "nature": "#E8F0E3",
    }
    for keyword, hex_color in buckets.items():
        assert keyword in PROMPT_TEMPLATE
        assert hex_color in PROMPT_TEMPLATE


def test_analyst_prompt_pins_explicit_white_escape_hatch():
    """Briefs explicitly asking for white must still be honored -- if this
    carve-out is removed, white-minimalist briefs will silently get tinted
    backgrounds the user did not request."""
    from metagpt.ext.agentlayout.actions.analyze_brief import PROMPT_TEMPLATE

    assert "white background" in PROMPT_TEMPLATE
    assert "blank canvas" in PROMPT_TEMPLATE
    assert "minimalist white" in PROMPT_TEMPLATE


def test_analyst_format_example_includes_background_color_field():
    """If the field is missing from the example JSON the LLM tends to omit it
    entirely (template-following bias). Keep it visible as null when the
    example asset is an image-backed background."""
    from metagpt.ext.agentlayout.actions.analyze_brief import FORMAT_EXAMPLE_JSON

    assert '"background_color": null' in FORMAT_EXAMPLE_JSON


# ============================================================
# 2. Canvas schema -- hex validator + default None
# ============================================================


def test_canvas_background_color_defaults_to_none():
    """Legacy specs predating step 7 must keep round-tripping unchanged."""
    from metagpt.ext.agentlayout.schema import Canvas

    c = Canvas(width=800, height=1200)
    assert c.background_color is None


def test_canvas_background_color_accepts_six_digit_hex_and_uppercases():
    """Hex is normalized to upper-case so downstream string compares are
    deterministic (palette diff, JSON pinning, etc.)."""
    from metagpt.ext.agentlayout.schema import Canvas

    c = Canvas(width=10, height=10, background_color="#f5e6d3")
    assert c.background_color == "#F5E6D3"


@pytest.mark.parametrize(
    "bad",
    ["red", "#fff", "#GGGGGG", "F5E6D3", "rgb(255,0,0)", "#1234567"],
)
def test_canvas_background_color_rejects_non_six_digit_hex(bad):
    """Anything that is not exactly ``#`` + 6 hex digits raises ValidationError
    so malformed Analyst output fails fast instead of corrupting the PNG."""
    from pydantic import ValidationError

    from metagpt.ext.agentlayout.schema import Canvas

    with pytest.raises(ValidationError):
        Canvas(width=10, height=10, background_color=bad)


# ============================================================
# 3. Pipeline default_white_background -- canvas-aware palette
# ============================================================


def test_default_white_background_uses_canvas_color_when_set():
    """The Aesthetic Judge reads ``BackgroundAnalysis.dominant_palette``; if it
    sees ``['#FFFFFF']`` while the PNG renders beige, color_harmony scoring
    becomes nonsense. This pinned test enforces consistency between the
    rendered canvas color and the palette the Judge sees."""
    from metagpt.ext.agentlayout.pipeline import default_white_background
    from metagpt.ext.agentlayout.schema import Canvas

    bg = default_white_background(Canvas(width=10, height=10, background_color="#F5E6D3"))
    assert bg.dominant_palette == ["#F5E6D3"]


def test_default_white_background_falls_back_to_white_for_legacy_spec():
    """Specs without ``background_color`` must keep getting white so existing
    test fixtures (test_judge_corner / test_generator_corner) don't break."""
    from metagpt.ext.agentlayout.pipeline import default_white_background
    from metagpt.ext.agentlayout.schema import Canvas

    bg = default_white_background(Canvas(width=10, height=10))
    assert bg.dominant_palette == ["#FFFFFF"]
    assert bg.recommended_text_color == "#111111"


@pytest.mark.parametrize(
    "bg_hex,expected_text",
    [
        ("#FFFFFF", "#111111"),
        ("#F5E6D3", "#111111"),
        ("#1B2B4A", "#F4F4F4"),
        ("#000000", "#F4F4F4"),
        ("#1A1A2E", "#F4F4F4"),
    ],
)
def test_default_white_background_picks_readable_text_color_by_luminance(
    bg_hex, expected_text
):
    """The text-on-background recommendation must flip at the 128-luminance
    threshold so dark backgrounds get light recommended_text_color (and vice
    versa), preventing low-contrast title rendering."""
    from metagpt.ext.agentlayout.pipeline import default_white_background
    from metagpt.ext.agentlayout.schema import Canvas

    bg = default_white_background(Canvas(width=10, height=10, background_color=bg_hex))
    assert bg.recommended_text_color == expected_text


# ============================================================
# 4. Renderer -- background_color path produces the right fill
# ============================================================


def test_renderer_fills_canvas_with_background_color_when_no_asset():
    """The renderer must consume ``canvas.background_color`` instead of the
    legacy white fallback. Probe the top-left pixel because painted elements
    never overlap (0, 0) in our fixtures."""
    from metagpt.ext.agentlayout.schema import (
        Candidate,
        Canvas,
        DesignSpec,
    )
    from metagpt.ext.agentlayout.tools.renderer import render

    spec = DesignSpec(
        canvas=Canvas(width=20, height=20, background_color="#1B2B4A"),
        elements=[],
        hard_constraints=[],
        soft_constraints=[],
        style_keywords=[],
        language="en-US",
        inferred_fields={},
    )
    cand = Candidate(candidate_id="cand_test", elements=[])

    img = render(cand, spec)
    r, g, b, a = img.convert("RGBA").getpixel((0, 0))
    assert (r, g, b) == (0x1B, 0x2B, 0x4A)
    assert a == 255


def test_renderer_keeps_white_default_for_legacy_spec():
    """Legacy spec without ``background_color`` still hits the white fallback,
    so existing fixtures keep their historical pure-white canvas."""
    from metagpt.ext.agentlayout.schema import (
        Candidate,
        Canvas,
        DesignSpec,
    )
    from metagpt.ext.agentlayout.tools.renderer import render

    spec = DesignSpec(
        canvas=Canvas(width=20, height=20),
        elements=[],
        hard_constraints=[],
        soft_constraints=[],
        style_keywords=[],
        language="en-US",
        inferred_fields={},
    )
    cand = Candidate(candidate_id="cand_legacy", elements=[])

    img = render(cand, spec)
    r, g, b, a = img.convert("RGBA").getpixel((0, 0))
    assert (r, g, b) == (255, 255, 255)
    assert a == 255


# ---------------------------------------------------------------- Step 80 text-as-image


def test_asset_input_allows_both_ref_and_content():
    """Step 80: pre-rendered text assets carry the bitmap AND its string."""
    import pytest as _pytest

    from metagpt.ext.agentlayout.actions.analyze_brief import AssetInput

    both = AssetInput(asset_ref="/x/asset_08_text.png", content="CLEAN UP")
    assert both.asset_ref and both.content
    ref_only = AssetInput(asset_ref="/x/a.png")
    content_only = AssetInput(content="hello")
    assert ref_only.asset_ref and content_only.content
    with _pytest.raises(ValueError):
        AssetInput()


def test_analyst_prompt_has_text_png_rules():
    from metagpt.ext.agentlayout.actions.analyze_brief import PROMPT_TEMPLATE

    assert "_text.png" in PROMPT_TEMPLATE
    assert "PRE-RENDERED TEXT" in PROMPT_TEMPLATE
    assert "never re-typeset" in PROMPT_TEMPLATE
    assert "Do NOT additionally emit a plain text element" in PROMPT_TEMPLATE
