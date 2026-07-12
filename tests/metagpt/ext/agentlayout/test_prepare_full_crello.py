from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from PIL import Image

from layout_agent.prepare_full_crello import (
    BATCH_SCHEMA_VERSION,
    DATASET_REVISION,
    FullCrelloPreparationError,
    _pretty_json_bytes,
    _publish_text_sidecar,
    _sha256_bytes,
    _verify_cache_tree,
    build_batch_bundle,
    materialize_cache_row,
    snapshot_ordered_ids,
    verify_batch_bundle,
    verify_id_snapshot,
)
from metagpt.ext.agentlayout.tools.pfull_preprocessor import (
    A3_TEXT_BITMAP_SIDECAR_VERSION,
)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def blocked(*_args, **_kwargs):
        raise AssertionError("network access is forbidden in focused preparation tests")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)


class FakeDataset(list):
    def __init__(self, rows):
        super().__init__(rows)
        self.selected = None

    def select_columns(self, columns):
        self.selected = list(columns)
        return FakeDataset([{name: row[name] for name in columns} for row in self])


def _valid_config(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "loop": "L0",
                "internal_judge": "gpt-5.4-mini-2026-03-17",
                "evaluation_judge": None,
                "dataset_split": "crello-official-test-n1971-batched-v1",
                "seed": 42,
                "models": {
                    "analyst": {"model": "gpt-5.4-mini-2026-03-17"},
                    "asset_planner": {"model": "gpt-5.4-mini-2026-03-17"},
                    "composition_director": {"model": "gpt-5.4-mini-2026-03-17"},
                    "coordinate_mapper": {"model": "gpt-5.4-mini-2026-03-17"},
                    "judge_select": {"model": "gpt-5.4-mini-2026-03-17"},
                },
            }
        ),
        encoding="utf-8",
    )


def _solid(size, color):
    return Image.new("RGBA", size, color)


def _photo(size=(20, 20)):
    image = Image.new("RGBA", size)
    for y in range(size[1]):
        for x in range(size[0]):
            image.putpixel((x, y), ((x * 17) % 256, (y * 19) % 256, (x + y) % 256, 255))
    return image


def _ambiguous(size=(20, 20)):
    image = Image.new("RGBA", size)
    colors = [(index * 7, index * 5, index * 3, 255) for index in range(32)]
    for y in range(size[1]):
        for x in range(size[0]):
            image.putpixel((x, y), colors[(x + y * size[0]) % len(colors)])
    return image


def _row(sample_id="sample-1"):
    types = [3, 0, 2, 0, 1]
    return {
        "id": sample_id,
        "length": len(types),
        "title": "Synthetic",
        "canvas_width": 100,
        "canvas_height": 100,
        "type": types,
        "image": [
            _solid((100, 100), (20, 20, 20, 255)),
            _solid((20, 20), (200, 0, 0, 255)),
            _photo(),
            _ambiguous(),
            _solid((30, 10), (0, 0, 0, 255)),
        ],
        "text": ["", "", "", "", "HELLO"],
        "left": [0, 10, 20, 30, 40],
        "top": [0, 10, 20, 30, 40],
        "width": [100, 20, 20, 20, 30],
        "height": [100, 20, 20, 20, 10],
        "preview": _solid((100, 100), (240, 240, 240, 255)),
    }


def test_snapshot_ids_pins_revision_projects_id_only_and_is_idempotent(tmp_path):
    calls = []
    fake = FakeDataset([{"id": f"id-{index}", "large": "ignored"} for index in range(5)])

    def loader(*args, **kwargs):
        calls.append((args, kwargs))
        return fake

    target = tmp_path / "snapshot"
    status, result = snapshot_ordered_ids(
        target,
        load_dataset_fn=loader,
        expected_count=5,
    )
    assert status == "created"
    assert result["count"] == 5
    assert calls[0][1] == {
        "split": "test",
        "streaming": True,
        "revision": DATASET_REVISION,
    }
    assert fake.selected == ["id"]
    status, second = snapshot_ordered_ids(
        target,
        load_dataset_fn=lambda *_args, **_kwargs: pytest.fail("existing snapshot reloaded network"),
        expected_count=5,
    )
    assert status == "verified-existing"
    assert second["count"] == 5


def test_snapshot_rejects_duplicate_ids_without_publishing(tmp_path):
    fake = FakeDataset([{"id": "same"}, {"id": "same"}])
    target = tmp_path / "snapshot"
    with pytest.raises(FullCrelloPreparationError, match="duplicates"):
        snapshot_ordered_ids(
            target,
            load_dataset_fn=lambda *_args, **_kwargs: fake,
            expected_count=2,
        )
    assert not target.exists()
    assert not list(tmp_path.glob(".snapshot.staging-*"))


def test_materialize_cache_row_matches_classifier_mapping_and_is_no_replace(tmp_path):
    root = tmp_path / "cache"
    result = materialize_cache_row(_row(), root)
    final = root / "crello_sample-1"
    assert result["sample_id"] == "sample-1"
    meta = json.loads((final / "meta.json").read_text())
    assert [element["kind"] for element in meta["elements"]] == [
        "background_candidate",
        "underlay",
        "image",
        "image",
        "text",
    ]
    assert [element.get("classifier_label") for element in meta["elements"][:4]] == [
        "full_canvas",
        "shape",
        "photo",
        "ambiguous",
    ]
    assert (final / "asset_00_background.png").is_file()
    assert (final / "asset_01_underlay.png").is_file()
    assert (final / "asset_02_image.png").is_file()
    assert (final / "asset_03_image.png").is_file()
    assert (final / "a3_text_0004.png").is_file()
    assert (final / "ground_truth_preview.jpg").is_file()
    sidecar = json.loads((final / "a3_text_bitmaps.json").read_text())
    assert sidecar["bitmaps"] == {"4": "a3_text_0004.png"}
    assert json.loads((final / "a3_cache_provenance.json").read_text())[
        "dataset_revision"
    ] == DATASET_REVISION
    with pytest.raises(FullCrelloPreparationError, match="already exists"):
        materialize_cache_row(_row(), root)


def test_materialize_invalid_row_cleans_staging(tmp_path):
    row = _row("broken")
    row["height"] = row["height"][:-1]
    root = tmp_path / "cache"
    with pytest.raises(FullCrelloPreparationError, match="array lengths"):
        materialize_cache_row(row, root)
    assert not (root / "crello_broken").exists()
    assert not list(root.glob(".crello_broken.staging-*"))


def test_cache_provenance_detects_post_publish_tamper(tmp_path):
    root = tmp_path / "cache"
    materialize_cache_row(_row(), root)
    final = root / "crello_sample-1"
    with (final / "asset_01_underlay.png").open("ab") as handle:
        handle.write(b"tamper")
    with pytest.raises(FullCrelloPreparationError, match="file snapshot mismatch"):
        _verify_cache_tree(
            final,
            final_dir=final,
            sample_id="sample-1",
            require_provenance=True,
        )


def test_existing_cache_text_sidecar_never_changes_meta(tmp_path):
    root = tmp_path / "cache"
    final = root / "crello_sample-1"
    final.mkdir(parents=True)
    row = _row()
    meta = {
        "id": "sample-1",
        "title": "Synthetic",
        "canvas_width": 100,
        "canvas_height": 100,
        "n_elements": 5,
        "elements": [{"idx": index} for index in range(5)],
    }
    meta_payload = json.dumps(meta, indent=2).encode()
    (final / "meta.json").write_bytes(meta_payload)
    assert _publish_text_sidecar(row, final) == "created"
    assert (final / "meta.json").read_bytes() == meta_payload
    assert _publish_text_sidecar(row, final) == "verified-existing"


def test_build_and_verify_batch_bundle_covers_union_without_overlap(tmp_path):
    official = [f"id-{index}" for index in range(7)]
    snapshot_dir = tmp_path / "snapshot"
    snapshot_ordered_ids(
        snapshot_dir,
        load_dataset_fn=lambda *_args, **_kwargs: FakeDataset(
            [{"id": sample_id} for sample_id in official]
        ),
        expected_count=7,
    )
    existing_path = tmp_path / "existing.json"
    existing_path.write_bytes(_pretty_json_bytes(official[:2]))
    config_path = tmp_path / "config.json"
    _valid_config(config_path)
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    for sample_id in official:
        directory = cache_root / f"crello_{sample_id}"
        directory.mkdir()
        (directory / "meta.json").write_text(json.dumps({"id": sample_id}))
        (directory / "a3_text_bitmaps.json").write_text(
            json.dumps(
                {
                    "version": A3_TEXT_BITMAP_SIDECAR_VERSION,
                    "sample_id": sample_id,
                    "bitmaps": {},
                }
            )
        )
    bundle = tmp_path / "bundle"
    status, result = build_batch_bundle(
        bundle,
        snapshot_dir=snapshot_dir,
        existing_ids_path=existing_path,
        config_path=config_path,
        crello_root=cache_root,
        runs_root=tmp_path / "runs",
        evaluations_root=tmp_path / "evaluations",
        batch_size=2,
        expected_official_count=7,
        expected_existing_ids_sha256=_sha256_bytes(existing_path.read_bytes()),
        completed_artifacts={},
    )
    assert status == "created"
    assert result == {
        "official_count": 7,
        "reused_count": 2,
        "new_count": 5,
        "new_batch_count": 3,
        "manifest_sha256": result["manifest_sha256"],
    }
    manifest = json.loads((bundle / "manifest.json").read_text())
    assert manifest["schema_version"] == BATCH_SCHEMA_VERSION
    assert [row["count"] for row in manifest["new_batches"]] == [2, 2, 1]
    assert manifest["cache_snapshot"]["valid_count"] == 7
    assert manifest["cache_snapshot"]["missing_count"] == 0
    verified = verify_batch_bundle(
        bundle,
        snapshot_dir=snapshot_dir,
        existing_ids_path=existing_path,
        expected_official_count=7,
    )
    assert verified["new_count"] == 5


def test_batch_bundle_refuses_incomplete_cache_readiness(tmp_path):
    official = ["one", "two", "three"]
    snapshot_dir = tmp_path / "snapshot"
    snapshot_ordered_ids(
        snapshot_dir,
        load_dataset_fn=lambda *_args, **_kwargs: FakeDataset(
            [{"id": sample_id} for sample_id in official]
        ),
        expected_count=3,
    )
    existing_path = tmp_path / "existing.json"
    existing_path.write_bytes(_pretty_json_bytes(["one"]))
    config_path = tmp_path / "config.json"
    _valid_config(config_path)
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    with pytest.raises(FullCrelloPreparationError, match="cache readiness incomplete"):
        build_batch_bundle(
            tmp_path / "bundle",
            snapshot_dir=snapshot_dir,
            existing_ids_path=existing_path,
            config_path=config_path,
            crello_root=cache_root,
            runs_root=tmp_path / "runs",
            evaluations_root=tmp_path / "evaluations",
            batch_size=2,
            expected_official_count=3,
            expected_existing_ids_sha256=_sha256_bytes(existing_path.read_bytes()),
            completed_artifacts={},
        )
    assert not (tmp_path / "bundle").exists()


def test_tool_source_exposes_no_paid_api_flag():
    source = (Path(__file__).parents[4] / "layout_agent" / "prepare_full_crello.py").read_text()
    assert "--allow-api-calls" not in source
    assert "--allow-network" in source
    assert "--allow-dataset-download" in source
    from layout_agent.prepare_full_crello import TEXT_BITMAP_SIDECAR_VERSION

    assert TEXT_BITMAP_SIDECAR_VERSION == A3_TEXT_BITMAP_SIDECAR_VERSION
