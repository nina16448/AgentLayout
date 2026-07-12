"""Tests for the A3 tree-prediction accuracy evaluation bundle."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

import pytest

from metagpt.ext.agentlayout.evaluation.a3_tree_accuracy import (
    bootstrap_ci,
    evaluate_tree_accuracy_run,
    publish_bundle,
    write_bytes_once,
)


def _asset_id(index: int) -> str:
    return f"asset_{index:04d}"


def _tree_payload(
    groups: Sequence[Sequence[int]],
    *,
    source: str,
    parents: Optional[Dict[int, int]] = None,
    confidences: Optional[Dict[int, float]] = None,
    semantic_types: Optional[Dict[int, str]] = None,
) -> Dict:
    parents = parents or {}
    confidences = confidences or {}
    semantic_types = semantic_types or {}
    nodes: List[Dict] = []
    group_payloads: List[Dict] = []
    for group_index, members in enumerate(groups):
        group_id = f"group_{group_index}"
        group_payloads.append(
            {
                "group_id": group_id,
                "label": f"group {group_index}",
                "member_ids": [_asset_id(m) for m in members],
                "ordering_priority": group_index,
                "confidence": 1.0,
            }
        )
        for member in members:
            parent = parents.get(member)
            nodes.append(
                {
                    "asset_id": _asset_id(member),
                    "semantic_type": semantic_types.get(member, "title"),
                    "semantic_role": f"role {member}",
                    "group_id": group_id,
                    "group_label": f"group {group_index}",
                    "parent_id": "root" if parent is None else _asset_id(parent),
                    "relation_to_parent": "root" if parent is None else "supports",
                    "ordering_priority": member,
                    "confidence": confidences.get(member, 1.0),
                }
            )
    return {
        "schema_version": "a3.layout-tree.v1",
        "source": source,
        "root_label": "foreground_layout",
        "nodes": nodes,
        "groups": group_payloads,
    }


def _make_run(
    tmp_path: Path,
    predictions: Mapping[str, Optional[Dict]],
    oracles: Mapping[str, Dict],
) -> Dict[str, Path]:
    run_dir = tmp_path / "run"
    oracle_dir = tmp_path / "oracles"
    oracle_dir.mkdir(parents=True)
    (run_dir / "samples").mkdir(parents=True)
    (run_dir / "sample_ids.json").write_text(json.dumps(sorted(predictions)))
    for sample_id, payload in predictions.items():
        if payload is None:
            continue
        stage_dir = run_dir / "samples" / sample_id / "stages" / "planner"
        stage_dir.mkdir(parents=True)
        (stage_dir / "layout_tree.json").write_text(json.dumps(payload))
    for sample_id, payload in oracles.items():
        (oracle_dir / f"{sample_id}.json").write_text(json.dumps(payload))
    return {"run_dir": run_dir, "oracle_dir": oracle_dir}


REPO_ROOT = Path(__file__).resolve().parents[4]


def _evaluate(paths: Dict[str, Path]) -> Dict:
    return evaluate_tree_accuracy_run(
        run_dir=paths["run_dir"],
        oracle_dir=paths["oracle_dir"],
        repo_root=REPO_ROOT,
    )


def test_perfect_prediction_scores_one(tmp_path: Path) -> None:
    groups = [[0, 1], [2]]
    parents = {1: 0}
    paths = _make_run(
        tmp_path,
        {"s1": _tree_payload(groups, source="predicted", parents=parents)},
        {"s1": _tree_payload(groups, source="human_oracle", parents=parents)},
    )
    result = _evaluate(paths)
    metrics = result["metrics"]
    assert metrics["same_group"]["macro"]["f1"]["mean"] == 1.0
    assert metrics["parent_child"]["macro"]["f1"]["mean"] == 1.0
    assert metrics["semantic_type_accuracy"]["micro"]["pooled"] == 1.0
    assert metrics["semantic_role_accuracy"]["role"] == "lower_bound_only"
    assert result["denominators"]["n_evaluated"] == 1


def test_planner_failure_is_explicit_denominator(tmp_path: Path) -> None:
    groups = [[0], [1]]
    paths = _make_run(
        tmp_path,
        {
            "s1": _tree_payload(groups, source="predicted"),
            "s2": None,  # Planner failed: no tree on disk
        },
        {
            "s1": _tree_payload(groups, source="human_oracle"),
            "s2": _tree_payload(groups, source="human_oracle"),
        },
    )
    result = _evaluate(paths)
    denominators = result["denominators"]
    assert denominators["n_sample_ids"] == 2
    assert denominators["n_evaluated"] == 1
    assert denominators["n_planner_failures"] == 1
    assert denominators["planner_failure_ids"] == ["s2"]
    failure_rows = [
        row for row in result["per_sample"] if row["status"] == "planner_failure"
    ]
    assert [row["sample_id"] for row in failure_rows] == ["s2"]
    assert failure_rows[0]["metrics"] is None


def test_asset_coverage_mismatch_recorded_not_silent(tmp_path: Path) -> None:
    paths = _make_run(
        tmp_path,
        {"s1": _tree_payload([[0], [1]], source="predicted")},
        {"s1": _tree_payload([[0], [1], [2]], source="human_oracle")},
    )
    result = _evaluate(paths)
    denominators = result["denominators"]
    assert denominators["n_evaluated"] == 0
    assert denominators["n_coverage_mismatch"] == 1
    assert denominators["coverage_mismatch_ids"] == ["s1"]
    row = result["per_sample"][0]
    assert row["status"] == "coverage_mismatch"
    assert "asset coverage mismatch" in row["reason"]


def test_uncertain_nodes_reported_in_denominators(tmp_path: Path) -> None:
    groups = [[0, 1], [2]]
    paths = _make_run(
        tmp_path,
        {"s1": _tree_payload(groups, source="predicted")},
        {
            "s1": _tree_payload(
                groups, source="human_oracle", confidences={2: 0.5}
            )
        },
    )
    result = _evaluate(paths)
    denominators = result["denominators"]
    assert denominators["n_uncertain_nodes_total"] == 1
    assert denominators["n_certain_nodes_total"] == 2
    assert denominators["n_samples_with_uncertain_nodes"] == 1
    row = result["per_sample"][0]
    assert row["metrics"]["uncertain_node_ids"] == [_asset_id(2)]


def test_empty_relation_sets_score_perfect(tmp_path: Path) -> None:
    # All singleton groups + all root parents on both sides: empty-vs-empty
    # same-group pairs and parent-child edges must score 1/1/1, not 0.
    groups = [[0], [1]]
    paths = _make_run(
        tmp_path,
        {"s1": _tree_payload(groups, source="predicted")},
        {"s1": _tree_payload(groups, source="human_oracle")},
    )
    result = _evaluate(paths)
    row = result["per_sample"][0]
    assert row["metrics"]["same_group"] == {
        "precision": 1.0,
        "recall": 1.0,
        "f1": 1.0,
        "n_true_positive": 0,
        "n_predicted": 0,
        "n_reference": 0,
    }
    assert row["metrics"]["parent_child"]["f1"] == 1.0


def test_micro_pooling_weights_by_counts(tmp_path: Path) -> None:
    # s1: 1 predicted pair, correct. s2: 3 reference pairs, prediction splits
    # the group so 1 of 1 predicted pairs is correct but recall is 1/3.
    paths = _make_run(
        tmp_path,
        {
            "s1": _tree_payload([[0, 1], [2]], source="predicted"),
            "s2": _tree_payload([[0, 1], [2]], source="predicted"),
        },
        {
            "s1": _tree_payload([[0, 1], [2]], source="human_oracle"),
            "s2": _tree_payload([[0, 1, 2]], source="human_oracle"),
        },
    )
    result = _evaluate(paths)
    micro = result["metrics"]["same_group"]["micro"]["pooled"]
    assert micro["n_true_positive"] == 2
    assert micro["n_predicted"] == 2
    assert micro["n_reference"] == 4
    assert micro["precision"] == 1.0
    assert micro["recall"] == 0.5
    macro = result["metrics"]["same_group"]["macro"]
    assert macro["recall"]["mean"] == pytest.approx((1.0 + 1 / 3) / 2)


def test_deterministic_rerun_produces_identical_aggregate(tmp_path: Path) -> None:
    groups = [[0, 1], [2, 3], [4]]
    parents = {1: 0, 3: 2}
    predictions = {
        "s1": _tree_payload([[0, 1], [2], [3, 4]], source="predicted", parents={1: 0}),
        "s2": _tree_payload(groups, source="predicted", parents=parents),
    }
    oracles = {
        "s1": _tree_payload(groups, source="human_oracle", parents=parents),
        "s2": _tree_payload(groups, source="human_oracle", parents=parents),
    }
    first = _evaluate(_make_run(tmp_path / "a", predictions, oracles))
    second = _evaluate(_make_run(tmp_path / "b", predictions, oracles))
    # Run/oracle dirs differ; everything else (metrics, CIs, denominators)
    # must be byte-identical across reruns.
    for key in ("metrics", "denominators", "bootstrap", "per_sample"):
        assert first[key] == second[key]
    ci = first["metrics"]["same_group"]["macro"]["f1"]["ci95"]
    assert ci is not None and ci["seed"] == 20260712 and ci["resamples"] == 10_000


def test_bootstrap_ci_requires_two_samples() -> None:
    assert bootstrap_ci([1.0], lambda values: sum(values)) is None
    ci = bootstrap_ci([0.0, 1.0], lambda values: sum(values) / len(values))
    assert ci is not None
    assert 0.0 <= ci["low"] <= ci["high"] <= 1.0


def test_publish_bundle_is_write_once(tmp_path: Path) -> None:
    groups = [[0], [1]]
    paths = _make_run(
        tmp_path,
        {"s1": _tree_payload(groups, source="predicted")},
        {"s1": _tree_payload(groups, source="human_oracle")},
    )
    output_dir = tmp_path / "out"
    publish_bundle(
        result=_evaluate(paths),
        output_dir=output_dir,
        evaluation_id="test-eval",
        command_argv=["test"],
    )
    assert (output_dir / "aggregate.json").exists()
    jsonl_lines = (output_dir / "per_sample.jsonl").read_text().splitlines()
    assert len(jsonl_lines) == 1  # exactly one line per sample, no blank lines
    assert json.loads(jsonl_lines[0])["sample_id"] == "s1"
    manifest = json.loads((output_dir / "evaluation_manifest.json").read_text())
    assert manifest["write_once"] is True
    assert manifest["artifact_sha256"].keys() == {"aggregate.json", "per_sample.jsonl"}
    with pytest.raises(FileExistsError):
        publish_bundle(
            result=_evaluate(paths),
            output_dir=output_dir,
            evaluation_id="test-eval",
            command_argv=["test"],
        )


def test_write_bytes_once_refuses_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "artifact.bin"
    write_bytes_once(target, b"first")
    with pytest.raises(FileExistsError):
        write_bytes_once(target, b"second")
    assert target.read_bytes() == b"first"


def test_missing_oracle_fails_loud(tmp_path: Path) -> None:
    paths = _make_run(
        tmp_path,
        {"s1": _tree_payload([[0], [1]], source="predicted")},
        {},
    )
    with pytest.raises(FileNotFoundError):
        _evaluate(paths)
