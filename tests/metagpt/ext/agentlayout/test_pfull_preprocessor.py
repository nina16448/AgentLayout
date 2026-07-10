from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from metagpt.ext.agentlayout.run_manifest import A3RunStore
from metagpt.ext.agentlayout.tools.pfull_preprocessor import (
    A3_TEXT_BITMAP_SIDECAR_VERSION,
    ASSET_MANIFEST_FILENAME,
    FORBIDDEN_GT_KEYS,
    PFULL_POLICY_VERSION,
    TEXT_BITMAP_SIDECAR_FILENAME,
    PFullInputError,
    build_prepared_input,
    prepare_pfull_sample,
)


def _png(path: Path, size, color) -> str:
    Image.new("RGBA", size, color).save(path)
    return str(path)


def _sample(tmp_path: Path, elements, *, width=200, height=100) -> Path:
    source = tmp_path / "source"
    source.mkdir(parents=True, exist_ok=True)
    meta = {
        "id": "sample-001",
        "title": "A Theme",
        "canvas_width": width,
        "canvas_height": height,
        "elements": elements,
    }
    (source / "meta.json").write_text(json.dumps(meta))
    return source


def test_pfull_keeps_every_non_background_asset_separate(tmp_path: Path):
    background = _png(tmp_path / "bg.png", (200, 100), (20, 30, 40, 255))
    product = _png(tmp_path / "product.png", (40, 60), (255, 0, 0, 255))
    text = _png(tmp_path / "text.png", (90, 20), (255, 255, 255, 255))
    elements = [
        {"idx": 0, "type_code": 2, "kind": "image", "asset_ref": background,
         "left": 91, "top": 92, "width": 1, "height": 2},
        {"idx": 1, "type_code": 4, "kind": "background_candidate", "asset_ref": product,
         "left": 0, "top": 0, "width": 200, "height": 100,
         "classifier_signals": {"area_ratio": 1.0}},
        {"idx": 2, "type_code": 1, "kind": "text", "asset_ref": text,
         "content": "SALE", "left": 50, "top": 50, "width": 90, "height": 20},
    ]
    source = _sample(tmp_path, elements)
    manifest = prepare_pfull_sample(source, tmp_path / "prepared")

    assert manifest.policy_version == PFULL_POLICY_VERSION
    assert manifest.background_asset_id == "asset_0000"
    assert [a.asset_id for a in manifest.foreground_assets()] == [
        "asset_0001",
        "asset_0002",
    ]
    assert all(Path(a.asset_ref).is_file() for a in manifest.assets)
    assert not (tmp_path / "prepared" / "bg_composite.png").exists()

    prepared = build_prepared_input(manifest)
    assert prepared.background_asset_ref == manifest.assets[0].asset_ref
    assert [a.asset_id for a in prepared.foreground_assets] == ["asset_0001", "asset_0002"]


def test_manifest_contains_no_gt_geometry_or_derived_area(tmp_path: Path):
    image = _png(tmp_path / "image.png", (20, 20), (1, 2, 3, 255))
    source = _sample(
        tmp_path,
        [{"idx": 7, "type_code": 4, "kind": "background_candidate", "asset_ref": image,
          "left": -999, "top": 888, "width": 777, "height": 666,
          "classifier_signals": {"area_ratio": 99.0}}],
    )
    prepare_pfull_sample(source, tmp_path / "prepared")
    raw = (tmp_path / "prepared" / ASSET_MANIFEST_FILENAME).read_text()
    payload = json.loads(raw)

    def keys(value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from keys(child)

    assert FORBIDDEN_GT_KEYS.isdisjoint(set(keys(payload)))
    assert payload["assets"][0]["role"] == "placeable"


def test_geometry_changes_do_not_change_classification(tmp_path: Path):
    image = _png(tmp_path / "image.png", (30, 40), (10, 20, 30, 255))
    first = _sample(
        tmp_path / "a",
        [{"idx": 0, "type_code": 4, "kind": "image", "asset_ref": image,
          "left": 0, "top": 0, "width": 30, "height": 40}],
    )
    second = _sample(
        tmp_path / "b",
        [{"idx": 0, "type_code": 4, "kind": "background_candidate", "asset_ref": image,
          "left": -500, "top": 900, "width": 200, "height": 100,
          "classifier_signals": {"area_ratio": 1.0}}],
    )
    one = prepare_pfull_sample(first, tmp_path / "out-a")
    two = prepare_pfull_sample(second, tmp_path / "out-b")
    assert one.assets[0].role == two.assets[0].role == "placeable"
    assert one.assets[0].semantic_hint == two.assets[0].semantic_hint == "image"
    assert one.assets[0].native_width == two.assets[0].native_width == 30
    assert one.assets[0].native_height == two.assets[0].native_height == 40


def test_multiple_pixel_backgrounds_choose_lowest_index_and_keep_other_placeable(tmp_path: Path):
    a = _png(tmp_path / "a.png", (200, 100), (1, 1, 1, 255))
    b = _png(tmp_path / "b.png", (200, 100), (2, 2, 2, 255))
    source = _sample(
        tmp_path,
        [
            {"idx": 4, "type_code": 2, "asset_ref": a},
            {"idx": 2, "type_code": 2, "asset_ref": b},
        ],
    )
    manifest = prepare_pfull_sample(source, tmp_path / "prepared")
    assert manifest.background_asset_id == "asset_0002"
    assert [(a.asset_id, a.role) for a in manifest.assets] == [
        ("asset_0002", "background"),
        ("asset_0004", "placeable"),
    ]


def test_no_pixel_background_uses_blank_base_and_keeps_all_assets(tmp_path: Path):
    image = _png(tmp_path / "photo.png", (80, 80), (0, 0, 0, 255))
    source = _sample(tmp_path, [{"idx": 0, "type_code": 2, "asset_ref": image}])
    manifest = prepare_pfull_sample(source, tmp_path / "prepared")
    assert manifest.background_asset_id is None
    assert len(manifest.foreground_assets()) == 1
    assert build_prepared_input(manifest).background_asset_ref is None


def test_text_bitmap_sidecar_resolves_without_touching_meta(tmp_path: Path):
    # A3-09 data fix: sidecar supplies text bitmaps for caches step80 never
    # covered, and the legacy meta.json stays byte-identical.
    background = _png(tmp_path / "bg.png", (200, 100), (20, 30, 40, 255))
    elements = [
        {"idx": 0, "type_code": 2, "kind": "image", "asset_ref": background,
         "left": 0, "top": 0, "width": 200, "height": 100},
        {"idx": 1, "type_code": 1, "kind": "text", "content": "SALE",
         "left": 50, "top": 50, "width": 90, "height": 20},
    ]
    source = _sample(tmp_path, elements)
    meta_before = (source / "meta.json").read_bytes()
    _png(source / "a3_text_0001.png", (300, 64), (255, 255, 255, 255))
    (source / TEXT_BITMAP_SIDECAR_FILENAME).write_text(
        json.dumps(
            {
                "version": A3_TEXT_BITMAP_SIDECAR_VERSION,
                "sample_id": "sample-001",
                "bitmaps": {"1": "a3_text_0001.png"},
            }
        )
    )
    manifest = prepare_pfull_sample(source, tmp_path / "prepared_sidecar")
    text_asset = next(a for a in manifest.assets if a.asset_id == "asset_0001")
    assert text_asset.semantic_hint == "text_bitmap"
    # The snapshot copies the sidecar's RAW render size, not GT geometry.
    assert (text_asset.native_width, text_asset.native_height) == (300, 64)
    assert (source / "meta.json").read_bytes() == meta_before


def test_sidecar_takes_precedence_over_legacy_gt_sized_asset_ref(tmp_path: Path):
    background = _png(tmp_path / "bg.png", (200, 100), (20, 30, 40, 255))
    legacy = _png(tmp_path / "legacy_text.png", (90, 20), (0, 0, 0, 255))
    elements = [
        {"idx": 0, "type_code": 2, "kind": "image", "asset_ref": background,
         "left": 0, "top": 0, "width": 200, "height": 100},
        {"idx": 1, "type_code": 1, "kind": "text", "content": "SALE",
         "asset_ref": legacy, "left": 50, "top": 50, "width": 90, "height": 20},
    ]
    source = _sample(tmp_path, elements)
    _png(source / "a3_text_0001.png", (300, 64), (255, 255, 255, 255))
    (source / TEXT_BITMAP_SIDECAR_FILENAME).write_text(
        json.dumps(
            {
                "version": A3_TEXT_BITMAP_SIDECAR_VERSION,
                "sample_id": "sample-001",
                "bitmaps": {"1": "a3_text_0001.png"},
            }
        )
    )
    manifest = prepare_pfull_sample(source, tmp_path / "prepared_precedence")
    text_asset = next(a for a in manifest.assets if a.asset_id == "asset_0001")
    assert (text_asset.native_width, text_asset.native_height) == (300, 64)


def test_unsupported_sidecar_version_fails_closed(tmp_path: Path):
    background = _png(tmp_path / "bg.png", (200, 100), (20, 30, 40, 255))
    elements = [
        {"idx": 0, "type_code": 2, "kind": "image", "asset_ref": background,
         "left": 0, "top": 0, "width": 200, "height": 100},
    ]
    source = _sample(tmp_path, elements)
    (source / TEXT_BITMAP_SIDECAR_FILENAME).write_text(
        json.dumps({"version": "a3.text-bitmap-sidecar.v999", "bitmaps": {}})
    )
    with pytest.raises(PFullInputError, match="sidecar version"):
        prepare_pfull_sample(source, tmp_path / "prepared_badversion")


def test_missing_non_text_asset_fails_instead_of_silently_dropping(tmp_path: Path):
    source = _sample(
        tmp_path,
        [{"idx": 0, "type_code": 4, "kind": "image", "asset_ref": str(tmp_path / "missing.png")}],
    )
    with pytest.raises(PFullInputError, match="missing"):
        prepare_pfull_sample(source, tmp_path / "prepared")


def test_output_directory_is_non_overwritable(tmp_path: Path):
    image = _png(tmp_path / "photo.png", (20, 20), (0, 0, 0, 255))
    source = _sample(tmp_path, [{"idx": 0, "type_code": 2, "asset_ref": image}])
    output = tmp_path / "prepared"
    prepare_pfull_sample(source, output)
    with pytest.raises(FileExistsError):
        prepare_pfull_sample(source, output)


def test_cli_prepares_initialized_run_without_api_calls(tmp_path: Path):
    config = tmp_path / "config.json"
    ids = tmp_path / "ids.json"
    models = {
        stage: {"model": "gpt-test-snapshot"}
        for stage in (
            "analyst",
            "asset_planner",
            "composition_director",
            "coordinate_mapper",
            "judge_select",
        )
    }
    config.write_text(
        json.dumps(
            {
                "loop": "L0",
                "internal_judge": "gpt-test-snapshot",
                "dataset_split": "synthetic-test",
                "models": models,
            }
        )
    )
    ids.write_text(json.dumps(["sample-001"]))
    store = A3RunStore.create(
        runs_root=tmp_path / "runs",
        run_id="a3-pfull-cli",
        config_path=config,
        sample_ids_path=ids,
        repo_root=tmp_path,
    )

    crello_root = tmp_path / "crello"
    source = crello_root / "crello_sample-001"
    source.mkdir(parents=True)
    asset = _png(source / "asset.png", (20, 20), (1, 2, 3, 255))
    (source / "meta.json").write_text(
        json.dumps(
            {
                "id": "sample-001",
                "title": "CLI",
                "canvas_width": 200,
                "canvas_height": 100,
                "elements": [{"idx": 0, "type_code": 4, "asset_ref": asset}],
            }
        )
    )
    repo = Path(__file__).resolve().parents[4]
    proc = subprocess.run(
        [
            sys.executable,
            "layout_agent/run_a3.py",
            "prepare-pfull",
            "--run-dir",
            str(store.run_dir),
            "--crello-root",
            str(crello_root),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(proc.stdout) == {"total": 1, "failed": 0}
    output = store.run_dir / "samples" / "sample-001" / "inputs" / "pfull"
    assert (output / ASSET_MANIFEST_FILENAME).exists()
    assert json.loads((store.run_dir / "pfull_preparation.json").read_text())["failed"] == 0

    normalize = subprocess.run(
        [
            sys.executable,
            "layout_agent/run_a3.py",
            "normalize-r3",
            "--run-dir",
            str(store.run_dir),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(normalize.stdout) == {"total": 1, "failed": 0}
    assert (store.run_dir / "samples" / "sample-001" / "inputs" / "r3" /
            "r3_asset_manifest.json").exists()

    vision = subprocess.run(
        [
            sys.executable,
            "layout_agent/run_a3.py",
            "prepare-analyst-vision",
            "--run-dir",
            str(store.run_dir),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(vision.stdout) == {"total": 1, "failed": 0}
    vision_dir = store.run_dir / "samples" / "sample-001" / "inputs" / "analyst_vision"
    assert (vision_dir / "background_overview.png").exists()
    assert (vision_dir / "asset_contact_sheet_01.png").exists()
    assert (vision_dir / "analyst_request.json").exists()
