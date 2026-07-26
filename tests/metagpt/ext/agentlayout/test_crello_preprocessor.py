"""Tests for the Step 76 SEGA-style Crello preprocessor.

All samples are synthetic (tiny PNGs built in tmp_path) -- no cached Crello
data or LLM calls are touched, so the file is CI-safe.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from metagpt.ext.agentlayout.schema import BackgroundAnalysis, UnderlayRegion
from metagpt.ext.agentlayout.tools.crello_preprocessor import (
    BG_FILENAME,
    INPUT_FILENAME,
    PreprocessedSample,
    _dominant_color,
    _luminance_text_color,
    preprocess_sample,
)

CANVAS_W, CANVAS_H = 200, 100


def _png(path: Path, size, color) -> str:
    img = Image.new("RGBA", size, color)
    img.save(path, format="PNG")
    return str(path)


def _make_sample(tmp_path: Path, elements) -> Path:
    sample_dir = tmp_path / "crello_test"
    sample_dir.mkdir()
    meta = {
        "id": "test123",
        "title": "Synthetic sample",
        "canvas_width": CANVAS_W,
        "canvas_height": CANVAS_H,
        "n_elements": len(elements),
        "elements": elements,
    }
    (sample_dir / "meta.json").write_text(json.dumps(meta))
    return sample_dir


@pytest.fixture()
def full_sample(tmp_path: Path):
    """Background + photo + large dark underlay + thin divider + text."""
    sample_dir = tmp_path / "crello_full"
    sample_dir.mkdir()
    bg = _png(sample_dir / "asset_00_background.png", (CANVAS_W, CANVAS_H), (0, 0, 255, 255))
    photo = _png(sample_dir / "asset_01_image.png", (80, 60), (255, 0, 0, 255))
    panel = _png(sample_dir / "asset_02_underlay.png", (100, 40), (32, 32, 32, 255))
    divider = _png(sample_dir / "asset_03_underlay.png", (100, 5), (32, 32, 32, 255))
    elements = [
        {"idx": 0, "kind": "background_candidate", "left": 0, "top": 0,
         "width": CANVAS_W, "height": CANVAS_H, "asset_ref": bg},
        {"idx": 1, "kind": "image", "left": 10, "top": 10, "width": 80, "height": 60,
         "asset_ref": photo},
        {"idx": 2, "kind": "underlay", "left": 50, "top": 30, "width": 100, "height": 40,
         "asset_ref": panel},
        {"idx": 3, "kind": "underlay", "left": 50, "top": 80, "width": 100, "height": 5,
         "asset_ref": divider},
        {"idx": 4, "kind": "text", "left": 60, "top": 40, "width": 80, "height": 20,
         "content": "HELLO WORLD"},
    ]
    meta = {
        "id": "full123",
        "title": "Full synthetic",
        "canvas_width": CANVAS_W,
        "canvas_height": CANVAS_H,
        "n_elements": len(elements),
        "elements": elements,
    }
    (sample_dir / "meta.json").write_text(json.dumps(meta))
    return sample_dir


# ------------------------------------------------------------------
# Compositing
# ------------------------------------------------------------------


def test_composite_bakes_all_non_text_layers(full_sample, tmp_path):
    out = tmp_path / "out"
    result = preprocess_sample(full_sample, out)

    bg = Image.open(out / BG_FILENAME).convert("RGB")
    assert bg.size == (CANVAS_W, CANVAS_H)
    # Outside every foreground layer: background blue shows through.
    assert bg.getpixel((195, 95)) == (0, 0, 255)
    # Photo pasted over background at (10,10).
    assert bg.getpixel((15, 15)) == (255, 0, 0)
    # Underlay pasted LAST in z-order wins over the photo on their overlap.
    assert bg.getpixel((60, 40)) == (32, 32, 32)
    assert result.baked_counts == {"background_candidate": 1, "image": 1, "underlay": 2}


def test_text_stays_placeable_not_baked(full_sample, tmp_path):
    result = preprocess_sample(full_sample, tmp_path / "out")
    assert result.text_contents == ["HELLO WORLD"]


def test_negative_offset_plate_crops_cleanly(tmp_path):
    photo = _png(tmp_path / "asset_01_image.png", (80, 60), (0, 255, 0, 255))
    sample_dir = _make_sample(
        tmp_path,
        [{"idx": 0, "kind": "image", "left": -40, "top": -20, "width": 80, "height": 60,
          "asset_ref": photo}],
    )
    result = preprocess_sample(sample_dir, tmp_path / "out")
    bg = Image.open(tmp_path / "out" / BG_FILENAME).convert("RGB")
    assert bg.getpixel((10, 10)) == (0, 255, 0)  # visible part of the plate
    assert bg.getpixel((80, 80)) == (255, 255, 255)  # white base elsewhere
    assert result.baked_counts == {"image": 1}


def test_unreadable_asset_is_skipped_not_fatal(tmp_path):
    sample_dir = _make_sample(
        tmp_path,
        [{"idx": 0, "kind": "image", "left": 0, "top": 0, "width": 50, "height": 50,
          "asset_ref": str(tmp_path / "missing.png")}],
    )
    result = preprocess_sample(sample_dir, tmp_path / "out")
    assert result.skipped_assets == 1
    assert result.baked_counts == {}


# ------------------------------------------------------------------
# Underlay regions (feed-forward hints)
# ------------------------------------------------------------------


def test_large_underlay_emits_region_thin_divider_filtered(full_sample, tmp_path):
    result = preprocess_sample(full_sample, tmp_path / "out")
    # Canvas 200x100: min region = max(60, 20)=60 wide, max(24, 6)=24 tall.
    # The 100x40 panel passes; the 100x5 divider is filtered out.
    assert len(result.underlay_regions) == 1
    region = result.underlay_regions[0]
    assert region.bbox == [50, 30, 150, 70]
    assert region.dominant_color == "#202020"
    assert region.recommended_text_color == "#F4F4F4"  # dark panel -> light text


def test_region_bbox_clamped_to_canvas(tmp_path):
    panel = _png(tmp_path / "asset_02_underlay.png", (120, 60), (240, 240, 240, 255))
    sample_dir = _make_sample(
        tmp_path,
        [{"idx": 0, "kind": "underlay", "left": 120, "top": 60, "width": 120, "height": 60,
          "asset_ref": panel},
         {"idx": 1, "kind": "text", "left": 130, "top": 70, "width": 60, "height": 20,
          "content": "ON PANEL"}],
    )
    result = preprocess_sample(sample_dir, tmp_path / "out")
    assert len(result.underlay_regions) == 1
    region = result.underlay_regions[0]
    assert region.bbox == [120, 60, CANVAS_W, CANVAS_H]
    assert region.recommended_text_color == "#111111"  # light panel -> dark text


def test_fully_transparent_panel_becomes_frame_with_backdrop_color(tmp_path):
    """Step 79 semantics change: a fully transparent shape the GT put text on
    is a degenerate FRAME -- the region hint stays valid because its colour is
    sampled from the composite backdrop (white base canvas here), not from the
    shape's own (non-existent) opaque pixels."""
    panel = _png(tmp_path / "asset_02_underlay.png", (100, 40), (0, 0, 0, 0))
    sample_dir = _make_sample(
        tmp_path,
        [{"idx": 0, "kind": "underlay", "left": 50, "top": 30, "width": 100, "height": 40,
          "asset_ref": panel},
         {"idx": 1, "kind": "text", "left": 60, "top": 40, "width": 80, "height": 20,
          "content": "ON PANEL"}],
    )
    result = preprocess_sample(sample_dir, tmp_path / "out")
    assert len(result.underlay_regions) == 1
    region = result.underlay_regions[0]
    assert region.panel_type == "frame"
    assert region.dominant_color == "#FFFFFF"  # white base canvas shows through
    assert region.recommended_text_color == "#111111"


def test_decorative_shape_without_gt_text_is_not_a_region(tmp_path):
    """A large opaque shape nobody put text on (e.g. an illustration) must not
    emit a feed-forward hint -- the N=20 eyeball run caught fish ornaments and
    a skier silhouette being offered as 'panels'."""
    shape = _png(tmp_path / "asset_02_underlay.png", (100, 40), (32, 32, 32, 255))
    sample_dir = _make_sample(
        tmp_path,
        [{"idx": 0, "kind": "underlay", "left": 50, "top": 30, "width": 100, "height": 40,
          "asset_ref": shape},
         {"idx": 1, "kind": "text", "left": 10, "top": 80, "width": 60, "height": 15,
          "content": "ELSEWHERE"}],
    )
    result = preprocess_sample(sample_dir, tmp_path / "out")
    assert result.underlay_regions == []
    assert result.baked_counts == {"underlay": 1}  # still baked into the background


def test_solid_panel_gets_panel_type_solid(full_sample, tmp_path):
    result = preprocess_sample(full_sample, tmp_path / "out")
    assert result.underlay_regions[0].panel_type == "solid"


def test_outlined_frame_samples_backdrop_not_border(tmp_path):
    """Step 79: a white outline box over a dark background must NOT be
    reported as a white plate -- the backdrop is dark, so light text is
    recommended (the 590afa87 forest-poster failure mode)."""
    sample_dir = tmp_path / "crello_frame"
    sample_dir.mkdir()
    # Dark full-canvas background.
    bg = _png(sample_dir / "asset_00_background.png", (CANVAS_W, CANVAS_H), (20, 40, 20, 255))
    # White outline frame: transparent interior, 3px white border.
    frame = Image.new("RGBA", (100, 40), (0, 0, 0, 0))
    for x in range(100):
        for y in range(40):
            if x < 3 or x >= 97 or y < 3 or y >= 37:
                frame.putpixel((x, y), (255, 255, 255, 255))
    frame_path = sample_dir / "asset_01_underlay.png"
    frame.save(frame_path, format="PNG")
    elements = [
        {"idx": 0, "kind": "background_candidate", "left": 0, "top": 0,
         "width": CANVAS_W, "height": CANVAS_H, "asset_ref": str(bg)},
        {"idx": 1, "kind": "underlay", "left": 50, "top": 30, "width": 100, "height": 40,
         "asset_ref": str(frame_path)},
        {"idx": 2, "kind": "text", "left": 60, "top": 40, "width": 80, "height": 20,
         "content": "ON FRAME"},
    ]
    meta = {"id": "frame123", "title": "Frame", "canvas_width": CANVAS_W,
            "canvas_height": CANVAS_H, "n_elements": 3, "elements": elements}
    (sample_dir / "meta.json").write_text(json.dumps(meta))

    result = preprocess_sample(sample_dir, tmp_path / "out")
    assert len(result.underlay_regions) == 1
    region = result.underlay_regions[0]
    assert region.panel_type == "frame"
    # Backdrop sampled from the composite = dark green, NOT the white border.
    assert region.dominant_color == "#142814"
    assert region.recommended_text_color == "#F4F4F4"  # light text on dark backdrop


def test_text_asset_ref_collected_into_text_assets(tmp_path):
    """Step 80: text elements with a cached bitmap surface in text_assets."""
    text_png = _png(tmp_path / "asset_01_text.png", (80, 20), (10, 10, 10, 255))
    sample_dir = _make_sample(
        tmp_path,
        [{"idx": 0, "kind": "text", "left": 10, "top": 10, "width": 80.4, "height": 20.2,
          "content": "HELLO", "asset_ref": text_png},
         {"idx": 1, "kind": "text", "left": 10, "top": 50, "width": 60, "height": 15,
          "content": "NO IMAGE"}],
    )
    result = preprocess_sample(sample_dir, tmp_path / "out")
    assert result.text_contents == ["HELLO", "NO IMAGE"]
    assert result.text_assets == [
        {"content": "HELLO", "asset_ref": text_png, "width": 80, "height": 20}
    ]


def test_dominant_color_bucket_voting_ignores_minority_tone():
    img = Image.new("RGBA", (100, 40), (32, 32, 32, 255))
    # Paint a minority stripe (25% of pixels) in near-white.
    for x in range(100):
        for y in range(30, 40):
            img.putpixel((x, y), (250, 250, 250, 255))
    assert _dominant_color(img) == "#202020"


def test_luminance_text_color_threshold():
    assert _luminance_text_color("#FFFFFF") == "#111111"
    assert _luminance_text_color("#000000") == "#F4F4F4"


# ------------------------------------------------------------------
# Serialisation + schema integration
# ------------------------------------------------------------------


def test_sega_input_json_round_trips(full_sample, tmp_path):
    out = tmp_path / "out"
    result = preprocess_sample(full_sample, out)
    loaded = PreprocessedSample.model_validate_json((out / INPUT_FILENAME).read_text())
    assert loaded == result


def test_background_analysis_underlay_regions_default_empty():
    assert BackgroundAnalysis().underlay_regions == []


def test_background_analysis_accepts_underlay_regions():
    bg = BackgroundAnalysis(
        underlay_regions=[
            UnderlayRegion(bbox=[0, 0, 100, 50], dominant_color="#1A2B3C",
                           recommended_text_color="#F4F4F4")
        ]
    )
    assert bg.underlay_regions[0].dominant_color == "#1A2B3C"
