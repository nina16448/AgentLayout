import json
from pathlib import Path

import pytest

from layout_agent.select_a3_general import (
    GeneralSelectionError,
    _canonical_json,
    _write_once_or_verify,
    build_selection,
    discover_cached_test_pool,
)


def _sample(root: Path, sample_id: str, **extra: object) -> None:
    sample_dir = root / f"crello_{sample_id}"
    sample_dir.mkdir()
    payload = {"id": sample_id, **extra}
    (sample_dir / "meta.json").write_text(json.dumps(payload), encoding="utf-8")


def test_general_selection_is_deterministic_and_content_unfiltered(tmp_path: Path) -> None:
    for index in range(8):
        _sample(
            tmp_path,
            f"sample-{index}",
            elements=[] if index == 0 else [{"kind": "anything"}],
        )

    selected_a, provenance_a = build_selection(
        tmp_path, count=5, seed=42, documented_raw_test_count=1971
    )
    selected_b, provenance_b = build_selection(
        tmp_path, count=5, seed=42, documented_raw_test_count=1971
    )

    assert selected_a == selected_b
    assert provenance_a == provenance_b
    assert len(selected_a) == 5
    assert provenance_a["candidate_pool_count"] == 8
    assert provenance_a["semantic_filter"] is None
    assert provenance_a["geometry_filter"] is None
    assert provenance_a["asset_count_filter"] is None


def test_general_selection_records_only_source_availability_exclusions(
    tmp_path: Path,
) -> None:
    _sample(tmp_path, "valid")
    (tmp_path / "crello_missing").mkdir()
    invalid = tmp_path / "crello_invalid"
    invalid.mkdir()
    (invalid / "meta.json").write_text("not-json", encoding="utf-8")
    mismatch = tmp_path / "crello_expected"
    mismatch.mkdir()
    (mismatch / "meta.json").write_text('{"id":"other"}', encoding="utf-8")

    pool, _, exclusions = discover_cached_test_pool(tmp_path)

    assert pool == ["valid"]
    assert exclusions == {
        "missing_meta": 1,
        "invalid_json": 1,
        "non_object_meta": 0,
        "id_mismatch": 1,
    }


def test_write_once_verifies_identical_and_rejects_different(tmp_path: Path) -> None:
    destination = tmp_path / "selection.json"
    payload = _canonical_json(["a", "b"])

    assert _write_once_or_verify(destination, payload) == "created"
    assert _write_once_or_verify(destination, payload) == "verified-existing"
    with pytest.raises(GeneralSelectionError, match="refusing to overwrite"):
        _write_once_or_verify(destination, _canonical_json(["different"]))
