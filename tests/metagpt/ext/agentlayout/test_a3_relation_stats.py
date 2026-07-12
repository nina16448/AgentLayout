"""Tests for the Relation N=100 SGC/TLC/PCA statistical reanalysis."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Sequence

import pytest

from metagpt.ext.agentlayout.evaluation.a3_relation_stats import (
    analyze,
    bonferroni_adjust,
    compare_arms,
    holm_adjust,
    publish_bundle,
    render_markdown,
    render_results_paragraph,
    sign_test_two_sided,
)

REPO_ROOT = Path(__file__).resolve().parents[4]


# ---------------------------------------------------------------------------
# Pure statistics helpers
# ---------------------------------------------------------------------------


def test_sign_test_matches_published_values() -> None:
    # Reference points from the frozen §23.3 experiment log.
    assert sign_test_two_sided(64, 34) == pytest.approx(0.0032, abs=5e-5)
    assert sign_test_two_sided(63, 32) == pytest.approx(0.0019, abs=5e-5)
    assert sign_test_two_sided(47, 25) == pytest.approx(0.0128, abs=5e-5)
    assert sign_test_two_sided(46, 29) == pytest.approx(0.064, abs=5e-4)


def test_sign_test_edge_cases() -> None:
    assert sign_test_two_sided(0, 0) is None  # all ties: uninformative
    assert sign_test_two_sided(3, 3) == 1.0
    assert sign_test_two_sided(1, 0) == 1.0
    assert sign_test_two_sided(10, 0) == pytest.approx(2 / 2**10)


def test_holm_adjustment_step_down_with_monotonicity() -> None:
    raw = [0.01, 0.04, 0.03, 0.005]
    adjusted = holm_adjust(raw)
    # sorted: 0.005*4=0.02, 0.01*3=0.03, 0.03*2=0.06, 0.04*1=0.04 -> max 0.06
    assert adjusted == pytest.approx([0.03, 0.06, 0.06, 0.02])


def test_holm_passes_none_through() -> None:
    adjusted = holm_adjust([0.02, None, 0.01])
    assert adjusted[1] is None
    # family size m=2: 0.01*2=0.02, 0.02*1=0.02
    assert adjusted[0] == pytest.approx(0.02)
    assert adjusted[2] == pytest.approx(0.02)


def test_bonferroni_caps_at_one() -> None:
    # None entries do not count toward the family size (m=2 here).
    assert bonferroni_adjust([0.2, 0.001, None]) == [0.4, 0.002, None]
    assert bonferroni_adjust([0.6, 0.7]) == [1.0, 1.0]


# ---------------------------------------------------------------------------
# Paired-intersection logic
# ---------------------------------------------------------------------------


def _row(sample_id: str, status: str = "completed", **metrics) -> Dict:
    return {
        "sample_id": sample_id,
        "status": status,
        "sgc": metrics.get("sgc"),
        "tlc": metrics.get("tlc"),
        "pca": metrics.get("pca"),
    }


def test_compare_arms_uses_both_success_intersection() -> None:
    rows_a = [
        _row("s1", sgc=0.9),
        _row("s2", sgc=0.8),
        _row("s3", status="generation_failure"),
        _row("s4", sgc=None),  # metric undefined (e.g. single group)
        _row("s5", sgc=0.5),
    ]
    rows_b = [
        _row("s1", sgc=0.7),
        _row("s2", status="generation_failure"),
        _row("s3", sgc=0.6),
        _row("s4", sgc=0.4),
        _row("s5", sgc=0.5),
    ]
    result = compare_arms(rows_a, rows_b, "sgc")
    assert result["paired_n"] == 2  # only s1 and s5
    assert sorted(result["paired_sample_ids_excluded"]) == ["s2", "s3", "s4"]
    assert result["wins"] == 1 and result["losses"] == 0 and result["ties"] == 1
    assert result["mean_diff"] == pytest.approx((0.2 + 0.0) / 2)
    assert result["sign_test_p_raw"] == 1.0  # 1W/0L


def test_compare_arms_empty_intersection() -> None:
    result = compare_arms(
        [_row("s1", status="generation_failure")],
        [_row("s1", sgc=0.5)],
        "sgc",
    )
    assert result["paired_n"] == 0
    assert result["sign_test_p_raw"] is None
    assert result["mean_diff"] is None
    assert result["mean_diff_ci95"] is None


# ---------------------------------------------------------------------------
# End-to-end over synthetic frozen run artifacts
# ---------------------------------------------------------------------------


def _asset_id(index: int) -> str:
    return f"asset_{index:04d}"


def _oracle_payload(groups: Sequence[Sequence[int]], parents: Dict[int, int]) -> Dict:
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
                    "semantic_type": "title",
                    "semantic_role": f"role {member}",
                    "group_id": group_id,
                    "group_label": f"group {group_index}",
                    "parent_id": "root" if parent is None else _asset_id(parent),
                    "relation_to_parent": "root" if parent is None else "supports",
                    "ordering_priority": member,
                    "confidence": 1.0,
                }
            )
    return {
        "schema_version": "a3.layout-tree.v1",
        "source": "human_oracle",
        "root_label": "foreground_layout",
        "nodes": nodes,
        "groups": group_payloads,
    }


def _element(index: int, left: int, top: int) -> Dict:
    return {
        "id": _asset_id(index),
        "left": left,
        "top": top,
        "width": 10,
        "height": 10,
        "z_index": index,
    }


def _write_arm(
    root: Path,
    arm: str,
    sample_ids: Sequence[str],
    failures: Dict[str, str],
    layouts: Dict[str, List[Dict]],
) -> Path:
    run_dir = root / f"run-{arm.lower()}"
    (run_dir / "samples").mkdir(parents=True)
    (run_dir / "sample_ids.json").write_text(json.dumps(list(sample_ids)))
    summary = {
        "samples": [
            {
                "sample_id": sample_id,
                "status": "failed" if sample_id in failures else "completed",
                **(
                    {"error_type": failures[sample_id]}
                    if sample_id in failures
                    else {}
                ),
            }
            for sample_id in sample_ids
        ]
    }
    (run_dir / "a3_run_summary.json").write_text(json.dumps(summary))
    for sample_id in sample_ids:
        if sample_id in failures:
            continue
        sample_dir = run_dir / "samples" / sample_id
        (sample_dir / "pipeline").mkdir(parents=True)
        (sample_dir / "inputs" / "pfull").mkdir(parents=True)
        l0 = {
            "b0_slot_id": "slot_final",
            "bundle": {
                "slots": [
                    {
                        "slot_id": "slot_other",
                        "candidate": {"candidate_id": "other", "elements": []},
                    },
                    {
                        "slot_id": "slot_final",
                        "candidate": {
                            "candidate_id": "final",
                            "elements": layouts[sample_id],
                        },
                    },
                ]
            },
        }
        # slot_other has an empty/invalid layout on purpose: selection must
        # follow b0_slot_id, never slot order.
        l0["bundle"]["slots"][0]["candidate"]["elements"] = [
            _element(0, 0, 0),
            _element(1, 90, 90),
            _element(2, 40, 40),
        ]
        (sample_dir / "pipeline" / "l0_result.json").write_text(json.dumps(l0))
        (sample_dir / "inputs" / "pfull" / "asset_manifest.json").write_text(
            json.dumps({"canvas_width": 100, "canvas_height": 100})
        )
    return run_dir


@pytest.fixture()
def synthetic_arms(tmp_path: Path) -> Dict:
    sample_ids = ["s1", "s2", "s3"]
    oracle_dir = tmp_path / "oracles"
    oracle_dir.mkdir()
    groups = [[0, 1], [2]]
    parents = {1: 0}
    for sample_id in sample_ids:
        (oracle_dir / f"{sample_id}.json").write_text(
            json.dumps(_oracle_payload(groups, parents))
        )
    # "good" layout: group members adjacent, other group far away.
    good = [_element(0, 0, 0), _element(1, 10, 0), _element(2, 80, 80)]
    # "bad" layout: group member 1 placed next to the other group.
    bad = [_element(0, 0, 0), _element(1, 70, 80), _element(2, 80, 80)]
    run_dirs = {
        "T0": _write_arm(
            tmp_path, "T0", sample_ids, {},
            {"s1": bad, "s2": bad, "s3": bad},
        ),
        "T2": _write_arm(
            tmp_path, "T2", sample_ids, {"s3": "PlannerError"},
            {"s1": good, "s2": bad},
        ),
        "T3": _write_arm(
            tmp_path, "T3", sample_ids, {},
            {"s1": good, "s2": good, "s3": good},
        ),
    }
    return {"run_dirs": run_dirs, "oracle_dir": oracle_dir}


def test_analyze_denominators_and_intersection(synthetic_arms: Dict) -> None:
    aggregate = analyze(
        run_dirs=synthetic_arms["run_dirs"],
        oracle_dir=synthetic_arms["oracle_dir"],
        repo_root=REPO_ROOT,
    )
    assert aggregate["frozen_sample_n"] == 3
    assert aggregate["total_generation_attempts"] == 9
    assert aggregate["total_generation_failures"] == 1
    t2_meta = aggregate["arms"]["T2"]
    assert t2_meta["n_completed"] == 2
    assert t2_meta["generation_failures"] == [
        {"sample_id": "s3", "error_type": "PlannerError"}
    ]
    by_key = {
        (entry["comparison"], entry["metric"]): entry
        for entry in aggregate["comparisons"]
    }
    assert len(by_key) == 9
    # T2 failed s3, so every T2 comparison pairs only s1+s2.
    assert by_key[("T2_vs_T0", "sgc")]["paired_n"] == 2
    assert by_key[("T2_vs_T0", "sgc")]["paired_sample_ids_excluded"] == ["s3"]
    # T0 and T3 both completed all three samples.
    assert by_key[("T3_vs_T0", "sgc")]["paired_n"] == 3
    # T2's good layout beats T0's bad layout on s1, ties on s2.
    entry = by_key[("T2_vs_T0", "sgc")]
    assert entry["wins"] == 1 and entry["losses"] == 0 and entry["ties"] == 1
    # Failure rows stay explicit in the per-sample artifact.
    failure_rows = [
        row
        for row in aggregate["per_sample"]
        if row["status"] == "generation_failure"
    ]
    assert [(row["arm"], row["sample_id"]) for row in failure_rows] == [("T2", "s3")]


def test_analyze_is_deterministic(synthetic_arms: Dict) -> None:
    kwargs = dict(
        run_dirs=synthetic_arms["run_dirs"],
        oracle_dir=synthetic_arms["oracle_dir"],
        repo_root=REPO_ROOT,
    )
    first = analyze(**kwargs)
    second = analyze(**kwargs)
    assert first["comparisons"] == second["comparisons"]
    assert first["per_sample"] == second["per_sample"]
    assert first["bootstrap"]["seed"] == 20260712


def test_holm_and_bonferroni_attached_to_all_nine(synthetic_arms: Dict) -> None:
    aggregate = analyze(
        run_dirs=synthetic_arms["run_dirs"],
        oracle_dir=synthetic_arms["oracle_dir"],
        repo_root=REPO_ROOT,
    )
    for entry in aggregate["comparisons"]:
        if entry["sign_test_p_raw"] is None:
            assert entry["sign_test_p_holm"] is None
            assert entry["sign_test_p_bonferroni"] is None
        else:
            assert entry["sign_test_p_holm"] >= entry["sign_test_p_raw"]
            assert entry["sign_test_p_bonferroni"] >= entry["sign_test_p_raw"]


def test_results_paragraph_never_claims_equivalence(synthetic_arms: Dict) -> None:
    aggregate = analyze(
        run_dirs=synthetic_arms["run_dirs"],
        oracle_dir=synthetic_arms["oracle_dir"],
        repo_root=REPO_ROOT,
    )
    paragraph = render_results_paragraph(aggregate)
    assert "no difference was detected" in paragraph
    assert "must not be interpreted as evidence that the arms are equivalent" in paragraph
    assert "equivalent." not in paragraph.replace(
        "must not be interpreted as evidence that the arms are equivalent.", ""
    )


def test_publish_bundle_write_once_and_renders(
    synthetic_arms: Dict, tmp_path: Path
) -> None:
    aggregate = analyze(
        run_dirs=synthetic_arms["run_dirs"],
        oracle_dir=synthetic_arms["oracle_dir"],
        repo_root=REPO_ROOT,
    )
    markdown = render_markdown({k: v for k, v in aggregate.items() if k != "per_sample"})
    assert "| Comparison | Metric |" in markdown
    output_dir = tmp_path / "bundle"
    paths = publish_bundle(
        aggregate=aggregate,
        output_dir=output_dir,
        evaluation_id="test-stats",
        command_argv=["test"],
    )
    for path in paths.values():
        assert path.exists()
    jsonl_lines = (output_dir / "per_sample.jsonl").read_text().splitlines()
    assert len(jsonl_lines) == 9  # 3 arms x 3 samples, no blank lines
    assert all(json.loads(line)["arm"] in {"T0", "T2", "T3"} for line in jsonl_lines)
    manifest = json.loads((output_dir / "evaluation_manifest.json").read_text())
    assert set(manifest["artifact_sha256"]) == {
        "aggregate.json",
        "per_sample.jsonl",
        "results.md",
        "results.tex",
    }
    tex = (output_dir / "results.tex").read_text()
    assert r"\begin{table}" in tex and "20,260,712" not in tex
    with pytest.raises(FileExistsError):
        publish_bundle(
            aggregate=analyze(
                run_dirs=synthetic_arms["run_dirs"],
                oracle_dir=synthetic_arms["oracle_dir"],
                repo_root=REPO_ROOT,
            ),
            output_dir=output_dir,
            evaluation_id="test-stats",
            command_argv=["test"],
        )


def test_mismatched_frozen_ids_fail_loud(synthetic_arms: Dict, tmp_path: Path) -> None:
    other = _write_arm(
        tmp_path, "TX", ["s1", "s2"], {}, {
            "s1": [_element(0, 0, 0), _element(1, 10, 0), _element(2, 80, 80)],
            "s2": [_element(0, 0, 0), _element(1, 10, 0), _element(2, 80, 80)],
        },
    )
    run_dirs = dict(synthetic_arms["run_dirs"])
    run_dirs["T3"] = other
    with pytest.raises(ValueError, match="different frozen sample-ID list"):
        analyze(
            run_dirs=run_dirs,
            oracle_dir=synthetic_arms["oracle_dir"],
            repo_root=REPO_ROOT,
        )
