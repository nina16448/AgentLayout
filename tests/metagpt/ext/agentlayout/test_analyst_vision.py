from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from metagpt.ext.agentlayout.tools.analyst_vision import (
    A3AnalystOutput,
    A3AssetUnderstanding,
    analyst_output_to_design_spec,
    build_analyst_prompt,
    build_background_overview,
    build_contact_sheets,
    build_vision_packet,
    parse_analyst_output,
    save_vision_packet,
    validate_asset_coverage,
)
from metagpt.ext.agentlayout.tools.pfull_preprocessor import prepare_pfull_sample
from metagpt.ext.agentlayout.tools.text_bitmap_normalizer import (
    R3NormalizationConfig,
    prepare_r3_sample,
)


def _png(path: Path, size, color, *, transparent_border=False) -> str:
    image = Image.new("RGBA", size, (0, 0, 0, 0) if transparent_border else color)
    if transparent_border:
        ImageDraw.Draw(image).rectangle((5, 5, size[0] - 6, size[1] - 6), fill=color)
    image.save(path)
    return str(path)


def _r3_manifest(tmp_path: Path, *, foreground_count=2, with_background=True):
    source = tmp_path / "source"
    source.mkdir(parents=True)
    elements = []
    index = 0
    if with_background:
        background = _png(source / "background.png", (200, 100), (10, 20, 180, 255))
        elements.append({"idx": index, "type_code": 2, "asset_ref": background})
        index += 1
    for offset in range(foreground_count):
        if offset % 2 == 0:
            ref = _png(
                source / f"text_{offset}.png",
                (100 + offset, 30),
                (220, 20, 20, 255),
                transparent_border=True,
            )
            elements.append(
                {
                    "idx": index,
                    "type_code": 1,
                    "kind": "text",
                    "content": f"TEXT {offset}",
                    "asset_ref": ref,
                    "left": 900,
                    "top": 800,
                    "width": 700,
                    "height": 600,
                }
            )
        else:
            ref = _png(source / f"image_{offset}.png", (40, 80), (20, 180, 20, 255))
            elements.append(
                {"idx": index, "type_code": 4, "kind": "background_candidate", "asset_ref": ref}
            )
        index += 1
    (source / "meta.json").write_text(
        json.dumps(
            {
                "id": "vision-sample",
                "title": "Vision Theme",
                "canvas_width": 200,
                "canvas_height": 100,
                "elements": elements,
            }
        )
    )
    pfull = tmp_path / "pfull"
    r3 = tmp_path / "r3"
    prepare_pfull_sample(source, pfull)
    return prepare_r3_sample(
        pfull / "asset_manifest.json", r3, R3NormalizationConfig()
    )


def _valid_output(manifest) -> A3AnalystOutput:
    return A3AnalystOutput(
        background_summary="Blue quiet background.",
        design_intent="Promote the supplied message.",
        style_keywords=["clean"],
        language="en",
        assets=[
            A3AssetUnderstanding(
                asset_id=asset.asset_id,
                semantic_type="title" if asset.media_type == "text_bitmap" else "product_image",
                description="Visible foreground asset",
                semantic_role="primary message" if asset.media_type == "text_bitmap" else "supporting image",
            )
            for asset in manifest.foreground_assets()
        ],
    )


def test_background_overview_contains_only_base_background(tmp_path: Path):
    manifest = _r3_manifest(tmp_path)
    overview = build_background_overview(manifest).convert("RGB")
    colors = set(overview.getdata())
    assert colors == {(10, 20, 180)}
    assert (220, 20, 20) not in colors
    assert (20, 180, 20) not in colors


def test_contact_sheets_cover_all_foregrounds_in_stable_order(tmp_path: Path):
    manifest = _r3_manifest(tmp_path, foreground_count=23)
    sheets = build_contact_sheets(manifest)
    assert len(sheets) == 2
    packet = build_vision_packet(manifest, "Use every asset")
    assert len(packet.images) == 3  # background + two contact pages
    assert packet.image_labels == [
        "background_overview.png",
        "asset_contact_sheet_01.png",
        "asset_contact_sheet_02.png",
    ]
    prompt_positions = [packet.prompt.index(asset.asset_id) for asset in manifest.foreground_assets()]
    assert prompt_positions == sorted(prompt_positions)


def test_prompt_has_content_and_aspect_but_no_paths_or_gt_geometry(tmp_path: Path):
    manifest = _r3_manifest(tmp_path)
    packet = build_vision_packet(manifest, "Brief")
    assert "TEXT 0" in packet.prompt
    assert "bitmap_aspect_ratio" in packet.prompt
    assert str(tmp_path) not in packet.prompt
    for forbidden in ("asset_ref", "native_width", "native_height", '"left"', '"top"', '"bbox"'):
        assert forbidden not in packet.prompt
    assert len(packet.prompt_sha256) == 64


def test_saved_packet_is_versioned_and_non_overwritable(tmp_path: Path):
    packet = build_vision_packet(_r3_manifest(tmp_path), "Brief")
    output = tmp_path / "packet"
    save_vision_packet(packet, output)
    request = json.loads((output / "analyst_request.json").read_text())
    assert request["prompt_sha256"] == packet.prompt_sha256
    assert request["image_labels"] == packet.image_labels
    assert all((output / label).is_file() for label in packet.image_labels)
    with pytest.raises(FileExistsError):
        save_vision_packet(packet, output)


def test_analyst_output_requires_exact_asset_coverage(tmp_path: Path):
    manifest = _r3_manifest(tmp_path)
    output = _valid_output(manifest)
    validate_asset_coverage(output, manifest)
    incomplete = output.model_copy(update={"assets": output.assets[:-1]})
    with pytest.raises(ValueError, match="coverage mismatch"):
        validate_asset_coverage(incomplete, manifest)
    reclassified = output.model_copy(
        update={
            "assets": [
                output.assets[0].model_copy(update={"semantic_type": "background_image"}),
                *output.assets[1:],
            ]
        }
    )
    with pytest.raises(ValueError, match="cannot be reclassified"):
        validate_asset_coverage(reclassified, manifest)


def test_asset_understanding_rejects_background_image_at_schema_level(tmp_path: Path):
    # A3-08 smoke fix: the parse/retry loop must see a per-asset error, not
    # only the post-hoc coverage failure.
    manifest = _r3_manifest(tmp_path)
    payload = _valid_output(manifest).model_dump(mode="json")
    payload["assets"][0]["semantic_type"] = "background_image"
    with pytest.raises(ValueError, match="cannot use"):
        A3AnalystOutput.model_validate(payload)
    prompt = build_analyst_prompt(manifest, "brief")
    assert 'must NEVER be "background_image"' in prompt


def test_analyst_output_parser_accepts_fenced_json(tmp_path: Path):
    manifest = _r3_manifest(tmp_path)
    expected = _valid_output(manifest)
    parsed = parse_analyst_output(
        "Here is the result:\n```json\n" + expected.model_dump_json() + "\n```"
    )
    assert parsed == expected


def test_design_spec_uses_manifest_ids_refs_and_no_invented_assets(tmp_path: Path):
    manifest = _r3_manifest(tmp_path)
    spec = analyst_output_to_design_spec(_valid_output(manifest), manifest)
    assert spec.canvas.background_asset_ref == next(
        asset.asset_ref for asset in manifest.assets if asset.role == "background"
    )
    assert [element.id for element in spec.foreground_elements()] == [
        asset.asset_id for asset in manifest.foreground_assets()
    ]
    by_id = {asset.asset_id: asset for asset in manifest.assets}
    assert all(element.asset_ref == by_id[element.id].asset_ref for element in spec.elements)


def test_no_background_produces_explicit_blank_overview(tmp_path: Path):
    manifest = _r3_manifest(tmp_path, with_background=False)
    overview = build_background_overview(manifest)
    assert overview.size == (768, 384)
    assert manifest.background_asset_id is None


def test_action_source_forbids_text_fallback_and_enforces_model_and_images():
    repo = Path(__file__).resolve().parents[4]
    source = (repo / "metagpt/ext/agentlayout/actions/analyze_a3.py").read_text()
    assert "text-only fallback is forbidden" in source
    assert "actual_model != self.expected_model" in source
    assert "aask(prompt, images=images)" in source
    assert "Previous response validation error" in source
