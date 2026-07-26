from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pytest
from PIL import Image, ImageChops, ImageDraw

from metagpt.ext.agentlayout.schema import (
    Candidate,
    Canvas,
    DesignSpec,
    Element,
    LayoutElement,
)
from metagpt.ext.agentlayout.tools.pfull_preprocessor import prepare_pfull_sample
from metagpt.ext.agentlayout.tools.renderer import render
from metagpt.ext.agentlayout.tools.text_bitmap_normalizer import (
    R3_MANIFEST_FILENAME,
    R3NormalizationConfig,
    R3NormalizationError,
    alpha_tight_bbox,
    contain_size,
    normalize_text_bitmap,
    prepare_r3_sample,
    r3_prompt_descriptor,
)


def _text_bitmap(path: Path, canvas_size, glyph_box, color=(220, 20, 20, 255)) -> str:
    image = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle(glyph_box, fill=color)
    image.save(path)
    return str(path)


def _pfull_source(tmp_path: Path, bitmap_ref: Optional[str]) -> Path:
    source = tmp_path / "source"
    source.mkdir(parents=True)
    element = {
        "idx": 3,
        "type_code": 1,
        "kind": "text",
        "content": "SUMMER SALE",
        "left": 111,
        "top": 222,
        "width": 333,
        "height": 44,
        "font_size": 55,
    }
    if bitmap_ref:
        element["asset_ref"] = bitmap_ref
    (source / "meta.json").write_text(
        json.dumps(
            {
                "id": "r3-sample",
                "title": "R3",
                "canvas_width": 800,
                "canvas_height": 600,
                "elements": [element],
            }
        )
    )
    return source


def test_alpha_tight_crop_padding_and_frozen_long_edge(tmp_path: Path):
    source = Path(_text_bitmap(tmp_path / "source.png", (300, 120), (70, 40, 229, 79)))
    destination = tmp_path / "asset_0003_r3_text.png"
    config = R3NormalizationConfig(long_edge_px=512, padding_px=8, alpha_threshold=1)
    width, height, _ = normalize_text_bitmap(source, destination, config)

    normalized = Image.open(destination).convert("RGBA")
    assert (width, height) == normalized.size
    assert max(normalized.size) == 512
    assert alpha_tight_bbox(normalized, 1) == (8, 8, width - 8, height - 8)


def test_same_glyph_with_different_gt_canvas_and_offset_normalizes_identically(tmp_path: Path):
    first = Path(_text_bitmap(tmp_path / "first.png", (300, 120), (70, 40, 229, 79)))
    second = Path(_text_bitmap(tmp_path / "second.png", (500, 250), (180, 130, 339, 169)))
    config = R3NormalizationConfig(long_edge_px=256, padding_px=6)
    one = normalize_text_bitmap(first, tmp_path / "one_r3_text.png", config)
    two = normalize_text_bitmap(second, tmp_path / "two_r3_text.png", config)
    assert one == two
    assert (tmp_path / "one_r3_text.png").read_bytes() == (
        tmp_path / "two_r3_text.png"
    ).read_bytes()


def test_r3_manifest_removes_original_text_geometry_and_dimensions(tmp_path: Path):
    bitmap = _text_bitmap(tmp_path / "text.png", (333, 44), (20, 10, 310, 35))
    source = _pfull_source(tmp_path, bitmap)
    pfull_dir = tmp_path / "pfull"
    prepare_pfull_sample(source, pfull_dir)
    r3_dir = tmp_path / "r3"
    manifest = prepare_r3_sample(
        pfull_dir / "asset_manifest.json",
        r3_dir,
        R3NormalizationConfig(long_edge_px=512, padding_px=8),
    )
    text = manifest.assets[0]
    assert text.asset_id == "asset_0003"
    assert text.media_type == "text_bitmap"
    assert text.content == "SUMMER SALE"
    assert max(text.bitmap_width, text.bitmap_height) == 512

    payload = json.loads((r3_dir / R3_MANIFEST_FILENAME).read_text())
    serialized = json.dumps(payload)
    for forbidden in ('"left"', '"top"', '"font_size"', '"native_width"', '"native_height"'):
        assert forbidden not in serialized


def test_r3_rejects_text_without_bitmap(tmp_path: Path):
    source = _pfull_source(tmp_path, None)
    pfull_dir = tmp_path / "pfull"
    prepare_pfull_sample(source, pfull_dir)
    with pytest.raises(R3NormalizationError, match="no bitmap"):
        prepare_r3_sample(
            pfull_dir / "asset_manifest.json",
            tmp_path / "r3",
            R3NormalizationConfig(),
        )


def test_prompt_descriptor_exposes_aspect_not_pixel_or_natural_size(tmp_path: Path):
    bitmap = Path(_text_bitmap(tmp_path / "asset_0003_r3_text.png", (512, 128), (8, 8, 503, 119)))
    descriptor = r3_prompt_descriptor(str(bitmap))
    assert "aspect ratio 4.000000" in descriptor
    assert "512" not in descriptor
    assert "128" not in descriptor
    assert "natural size" not in descriptor.lower()


def test_generator_and_observer_route_r3_around_legacy_natural_size_branch():
    repo = Path(__file__).resolve().parents[4]
    generator = (
        repo / "metagpt/ext/agentlayout/actions/generate_layout.py"
    ).read_text()
    observer = (
        repo / "metagpt/ext/agentlayout/actions/judge_aesthetic.py"
    ).read_text()
    r3_branch = generator.index("if is_r3_text_bitmap(el.asset_ref):")
    legacy_branch = generator.index('elif el.asset_ref and el.asset_ref.endswith("_text.png"):')
    assert r3_branch < legacy_branch
    assert "and not is_r3_text_bitmap(spec_el.asset_ref)" in observer


def test_contain_size_preserves_aspect_ratio():
    assert contain_size((400, 100), (200, 200)) == (200, 50)
    assert contain_size((100, 400), (200, 100)) == (25, 100)


def test_r3_renderer_contains_bitmap_without_stretching(tmp_path: Path):
    bitmap = tmp_path / "asset_0003_r3_text.png"
    image = Image.new("RGBA", (400, 100), (220, 20, 20, 255))
    image.save(bitmap)
    spec = DesignSpec(
        canvas=Canvas(width=300, height=300, background_color="#FFFFFF"),
        elements=[
            Element(
                id="asset_0003",
                semantic_type="title",
                visual_type="image",
                content="SALE",
                asset_ref=str(bitmap),
            )
        ],
    )
    candidate = Candidate(
        candidate_id="r3",
        elements=[
            LayoutElement(
                id="asset_0003",
                left=50,
                top=50,
                width=200,
                height=200,
                z_index=1,
            )
        ],
    )
    rendered = render(candidate, spec).convert("RGB")
    white = Image.new("RGB", rendered.size, "white")
    bbox = ImageChops.difference(rendered, white).getbbox()
    assert bbox == (50, 125, 250, 175)
    assert (bbox[2] - bbox[0]) / (bbox[3] - bbox[1]) == pytest.approx(4.0)


def test_normalizer_refuses_transparent_bitmap(tmp_path: Path):
    source = tmp_path / "blank.png"
    Image.new("RGBA", (100, 40), (0, 0, 0, 0)).save(source)
    with pytest.raises(R3NormalizationError, match="no alpha pixels"):
        normalize_text_bitmap(
            source,
            tmp_path / "blank_r3_text.png",
            R3NormalizationConfig(),
        )
