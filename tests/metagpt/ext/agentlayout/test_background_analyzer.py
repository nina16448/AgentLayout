"""Offline, pure-function tests for the BackgroundAnalyzer CV module.

These cover the deterministic helpers and the safe-zone geometry logic with
hand-built numpy masks -- they never invoke rembg/U2Net (no model download,
no network), so they are CI-offline-safe.

Run:

    pytest tests/metagpt/ext/agentlayout/test_background_analyzer.py -v --no-cov

Cost: $0. Runtime: <0.1s.
"""
from __future__ import annotations

import numpy as np

from metagpt.ext.agentlayout.schema import Canvas
from metagpt.ext.agentlayout.tools.background_analyzer import (
    _hex,
    _luminance_text_color,
    _safe_zones_from_mask,
    resolve_background,
)


# ---------------------------------------------------------------- helpers


def test_hex_clamps_and_formats():
    assert _hex((0, 0, 0)) == "#000000"
    assert _hex((255, 255, 255)) == "#FFFFFF"
    assert _hex((300, -5, 27)) == "#FF001B"  # out-of-range channels clamped


def test_luminance_text_color_dark_on_light_light_on_dark():
    assert _luminance_text_color((255, 255, 255)) == "#111111"  # light bg
    assert _luminance_text_color((10, 10, 10)) == "#F4F4F4"  # dark bg
    # Rec.601 luma threshold is 128: pure blue (0,0,255) -> luma ~29 -> dark bg
    assert _luminance_text_color((0, 0, 255)) == "#F4F4F4"


# ---------------------------------------------------------- safe zones


def test_empty_mask_yields_full_canvas_zone():
    mask = np.zeros((200, 300), dtype=bool)
    zones = _safe_zones_from_mask(mask, 300, 200)
    assert len(zones) == 1
    assert zones[0].region == "full"
    assert zones[0].bbox == [0, 0, 300, 200]
    assert zones[0].confidence == 1.0


def test_centered_subject_produces_margin_bands_that_avoid_it():
    # Busy block in the canvas centre: bands above/below/left/right must
    # exclude it (no band's bbox contains the subject's core).
    h, w = 400, 400
    mask = np.zeros((h, w), dtype=bool)
    mask[150:250, 150:250] = True  # central 100x100 subject
    zones = _safe_zones_from_mask(mask, w, h)

    assert zones, "expected non-empty safe zones around a central subject"
    labels = {z.region for z in zones}
    assert labels & {"top", "bottom", "left", "right"}
    for z in zones:
        l, t, r, b = z.bbox
        # The dense subject core (170..230) must not be inside any safe band.
        core_inside = (l <= 200 <= r) and (t <= 200 <= b)
        assert not core_inside, f"safe zone {z.region} overlaps subject core"
        assert 0.0 <= z.confidence <= 1.0


def test_fully_busy_mask_falls_back_to_grid_cells():
    # Subject everywhere -> no clean margin band -> 3 least-busy grid cells.
    mask = np.ones((300, 300), dtype=bool)
    zones = _safe_zones_from_mask(mask, 300, 300)
    assert 1 <= len(zones) <= 3
    for z in zones:
        assert z.confidence == 0.0  # 100% occupied everywhere


# ------------------------------------------------- resolve_background


def test_resolve_background_no_image_matches_white_stub():
    """Image-less specs must behave exactly like the historical stub."""
    canvas = Canvas(width=800, height=600)  # no background_asset_ref
    bg = resolve_background(canvas)
    assert bg.safe_zones == []  # white-stub contract: empty zones
    assert bg.dominant_palette == ["#FFFFFF"]
    assert bg.recommended_text_color == "#111111"


def test_resolve_background_missing_file_falls_back():
    canvas = Canvas(width=400, height=400, background_asset_ref="/no/such/file.png")
    bg = resolve_background(canvas)
    assert bg.safe_zones == []
    assert bg.dominant_palette  # non-empty palette from the stub
