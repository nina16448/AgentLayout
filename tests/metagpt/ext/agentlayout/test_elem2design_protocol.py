"""Tests for the Elem2Design external-baseline protocol logic (pure Python)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_MODULE_PATH = (
    Path(__file__).resolve().parents[4]
    / "layout_agent" / "external_baselines" / "elem2design" / "common_protocol.py"
)
_spec = importlib.util.spec_from_file_location("e2d_common_protocol", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
proto = importlib.util.module_from_spec(_spec)
sys.modules["e2d_common_protocol"] = proto
_spec.loader.exec_module(proto)


def _ids(n: int):
    return [f"asset_{i:04d}" for i in range(n)]


# ---------------------------------------------------------------------------
# Deterministic ordering
# ---------------------------------------------------------------------------


def test_layer_order_is_deterministic_and_layer_sorted() -> None:
    ids = _ids(30)  # >25: must not be truncated
    roles = {a: (i % 5) for i, a in enumerate(ids)}
    first = proto.deterministic_layer_order(ids, roles, "sampleA")
    second = proto.deterministic_layer_order(ids, roles, "sampleA")
    assert first == second  # deterministic rerun
    assert len(first) == 30 and set(first) == set(ids)  # no truncation
    assert [roles[a] for a in first] == sorted(roles[a] for a in ids)  # layer sort


def test_layer_order_differs_across_samples_but_not_reruns() -> None:
    ids = _ids(10)
    roles = {a: 2 for a in ids}  # single layer -> pure shuffle visible
    order_a = proto.deterministic_layer_order(ids, roles, "sampleA")
    order_b = proto.deterministic_layer_order(ids, roles, "sampleB")
    assert order_a != order_b  # per-sample keying
    assert sorted(order_a) == sorted(order_b) == ids


def test_index_map_round_trip() -> None:
    ordered = ["asset_0002", "asset_0000", "asset_0001"]
    mapping = proto.build_index_map(ordered)
    for index, asset_id in enumerate(ordered):
        assert mapping["index_to_asset"][index] == asset_id
        assert mapping["asset_to_index"][asset_id] == index


# ---------------------------------------------------------------------------
# Leakage guards
# ---------------------------------------------------------------------------


def test_forbidden_key_scan_catches_gt_geometry() -> None:
    payload = {
        "id": "s1",
        "conversations": [{"from": "human", "value": "canvas width 600px"}],
        "leak": {"left": 10, "top": 20, "width": 30, "height": 40},
    }
    violations = proto.scan_forbidden_keys(payload)
    assert {"$.leak.left", "$.leak.top", "$.leak.width", "$.leak.height"} <= set(violations)


def test_forbidden_key_scan_allows_canvas_keys_and_clean_payload() -> None:
    clean = {"id": "s1", "canvas_width": 600, "canvas_height": 1200,
             "conversations": [{"from": "gpt", "value": "{}"}]}
    assert proto.scan_forbidden_keys(clean) == []


def test_placeholder_gpt_turns_enforced() -> None:
    good = [{"from": "human", "value": "x"}, {"from": "gpt", "value": "{}"}] * 5
    proto.assert_placeholder_gpt_turns(good)
    leaked = [dict(t) for t in good]
    leaked[1]["value"] = '{"index": 0, "left": 1}'  # GT-shaped content
    with pytest.raises(ValueError, match="placeholder"):
        proto.assert_placeholder_gpt_turns(leaked)
    with pytest.raises(ValueError, match="expected 5 gpt turns"):
        proto.assert_placeholder_gpt_turns(good[:4])


# ---------------------------------------------------------------------------
# Prediction parsing
# ---------------------------------------------------------------------------


def test_split_and_parse_official_concatenation() -> None:
    prediction = (
        ' ##### {"index": 0, "left": 1, "top": 2, "width": 3, "height": 4} $$$$$ '
        " ##### {} $$$$$ "
        ' ##### {"index": 1, "left": 5, "top": 6, "width": 7, "height": 8}'
        ' {"index": 2, "left": 0, "top": 0, "width": 1, "height": 1} $$$$$ '
    )
    turns = proto.split_prediction_turns(prediction)
    assert len(turns) == 3
    assert len(proto.parse_elements(turns[0])) == 1
    assert proto.parse_elements(turns[1]) == []  # '{}' marker -> no elements
    assert len(proto.parse_elements(turns[2])) == 2


def test_parse_elements_skips_malformed_json() -> None:
    assert proto.parse_elements('{"index": 0, "left": bad} {"index": 1, "left": 2.0, '
                                '"top": 1, "width": 2, "height": 2}') != []
    assert proto.parse_elements("no json here") == []


# ---------------------------------------------------------------------------
# Conversion (fail-closed)
# ---------------------------------------------------------------------------


def _element(index: int, **overrides):
    base = {"index": index, "left": 10.0, "top": 20.0, "width": 30.0, "height": 40.0}
    base.update(overrides)
    return base


def test_convert_complete_sample_and_z_order() -> None:
    index_to_asset = ["asset_0000", "asset_0001", "asset_0002"]
    turns = [[_element(0)], [], [_element(2), _element(1)], [], []]
    elements, errors = proto.convert_sample(turns, index_to_asset, {})
    assert errors == []
    by_id = {e["id"]: e for e in elements}
    assert by_id["asset_0000"]["z_index"] == 0          # layer 0, order 0
    assert by_id["asset_0002"]["z_index"] == 2000       # layer 2, order 0
    assert by_id["asset_0001"]["z_index"] == 2001       # layer 2, order 1
    assert by_id["asset_0000"]["left"] == 10 and by_id["asset_0000"]["height"] == 40


def test_convert_missing_and_duplicate_fail_closed() -> None:
    index_to_asset = ["asset_0000", "asset_0001"]
    turns = [[_element(0), _element(0)], [], [], [], []]
    _, errors = proto.convert_sample(turns, index_to_asset, {})
    assert any("duplicate prediction" in e for e in errors)
    assert any("missing predictions" in e and "asset_0001" in e for e in errors)


def test_convert_rejects_invalid_geometry() -> None:
    index_to_asset = ["asset_0000"]
    for bad in (
        _element(0, width=-5.0),
        _element(0, height=0.0),
        _element(0, left=float("nan")),
        _element(0, top=float("inf")),
        _element(0, width="wide"),
        {"index": 0, "left": 1.0},  # missing keys
    ):
        _, errors = proto.convert_sample([[bad], [], [], [], []], index_to_asset, {})
        assert errors, f"expected failure for {bad!r}"


def test_convert_rejects_out_of_range_or_extra_index() -> None:
    index_to_asset = ["asset_0000"]
    turns = [[_element(0), _element(7)], [], [], [], []]
    _, errors = proto.convert_sample(turns, index_to_asset, {})
    assert any("bad element index 7" in e for e in errors)


def test_convert_text_attributes_mapped_only_for_text_assets() -> None:
    index_to_asset = ["asset_0000", "asset_0001"]
    texts = {"asset_0000": "Hello", "asset_0001": ""}
    turns = [
        [_element(0, font="Roboto", font_size=24.4, color=[255, 0, 10], text_align="center"),
         _element(1, font="Roboto", font_size=24.4)],
        [], [], [], [],
    ]
    elements, errors = proto.convert_sample(turns, index_to_asset, texts)
    assert errors == []
    text_el = next(e for e in elements if e["id"] == "asset_0000")
    image_el = next(e for e in elements if e["id"] == "asset_0001")
    assert text_el["font_family"] == "Roboto" and text_el["font_size"] == 24
    assert text_el["color"] == "#ff000a" and text_el["text_align"] == "center"
    assert "font_size" not in image_el and "color" not in image_el
