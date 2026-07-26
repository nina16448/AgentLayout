"""Direct tree-prediction accuracy for one A3 run against human oracle trees.

Zero-cost, read-only: loads frozen ``stages/planner/layout_tree.json``
artifacts and the merged human oracle trees, scores each successful pair with
:func:`metagpt.ext.agentlayout.tools.human_tree_metrics.evaluate_tree_prediction`,
and publishes a write-once evaluation bundle (manifest + aggregate JSON +
per-sample JSONL).

Denominator contract (fail-loud):

* every frozen sample ID is accounted for exactly once as ``evaluated``,
  ``planner_failure`` (no predicted tree on disk) or ``coverage_mismatch``
  (trees load but cover different asset IDs);
* nothing is silently dropped — non-evaluated samples appear in the
  per-sample artifact with their status and reason.

Primary metric is same-group P/R/F1 over confidence-certain oracle nodes;
exact ``semantic_role_accuracy`` is a case-sensitive-string lower bound only.
Bootstrap CIs are sample-level percentile intervals with a fixed seed; each
statistic uses a fresh generator so results do not depend on computation
order.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from metagpt.ext.agentlayout.layout_tree_v3 import A3LayoutTree
from metagpt.ext.agentlayout.run_manifest import (
    canonical_json_bytes,
    load_sample_ids,
    sha256_bytes,
    sha256_file,
    utc_now,
    write_json_once,
)
from metagpt.ext.agentlayout.tools.human_tree_metrics import evaluate_tree_prediction

A3_TREE_ACCURACY_SCHEMA_VERSION = "a3.tree-accuracy.v1"
BOOTSTRAP_SEED = 20260712
BOOTSTRAP_RESAMPLES = 10_000
CONFIDENCE_LEVEL = 0.95

_PRF_FIELDS = ("precision", "recall", "f1")
_CODE_FILES = (
    "metagpt/ext/agentlayout/evaluation/a3_tree_accuracy.py",
    "metagpt/ext/agentlayout/tools/human_tree_metrics.py",
    "metagpt/ext/agentlayout/layout_tree_v3.py",
)


def write_bytes_once(path: Path, payload: bytes) -> None:
    """Atomically publish raw bytes and refuse to replace an existing file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temp.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            raise FileExistsError(f"refusing to overwrite existing artifact: {path}")
        os.link(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def bootstrap_ci(
    values: Sequence[Any],
    statistic,
    *,
    seed: int = BOOTSTRAP_SEED,
    resamples: int = BOOTSTRAP_RESAMPLES,
    confidence: float = CONFIDENCE_LEVEL,
) -> Optional[Dict[str, Any]]:
    """Percentile bootstrap CI over sample-level rows with a fresh, fixed RNG.

    ``values`` may hold any per-sample payload (floats or tuples); ``statistic``
    maps one resampled list to a float. Returns ``None`` when fewer than two
    samples exist, because a resampled singleton has no sampling variability.
    """
    n = len(values)
    if n < 2:
        return None
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, n, size=(resamples, n))
    stats = np.empty(resamples, dtype=float)
    for row in range(resamples):
        stats[row] = statistic([values[i] for i in indices[row]])
    alpha = (1.0 - confidence) / 2.0
    low, high = np.quantile(stats, [alpha, 1.0 - alpha])
    return {
        "low": float(low),
        "high": float(high),
        "confidence": confidence,
        "resamples": resamples,
        "seed": seed,
        "method": "percentile",
    }


def _mean(values: Sequence[float]) -> float:
    return float(sum(values) / len(values))


def _exact_count(accuracy: Optional[float], denominator: int, label: str) -> int:
    """Recover the integer match count behind ``matches/denominator``."""
    if accuracy is None or denominator == 0:
        return 0
    count = accuracy * denominator
    rounded = round(count)
    if abs(count - rounded) > 1e-6:
        raise ValueError(f"{label}: accuracy {accuracy} is not a /{denominator} ratio")
    return int(rounded)


def _pooled_prf(rows: Sequence[Tuple[int, int, int]]) -> Dict[str, float]:
    """Micro P/R/F1 from per-sample (true_positive, n_predicted, n_reference)."""
    tp = sum(row[0] for row in rows)
    n_pred = sum(row[1] for row in rows)
    n_ref = sum(row[2] for row in rows)
    if n_pred == 0 and n_ref == 0:
        precision = recall = f1 = 1.0
    else:
        precision = tp / n_pred if n_pred else 0.0
        recall = tp / n_ref if n_ref else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "n_true_positive": tp,
        "n_predicted": n_pred,
        "n_reference": n_ref,
    }


def evaluate_tree_accuracy_run(
    *,
    run_dir: Path,
    oracle_dir: Path,
    repo_root: Path,
) -> Dict[str, Any]:
    """Score every frozen sample of one run; return per-sample rows + aggregate."""
    run_dir = run_dir.resolve()
    oracle_dir = oracle_dir.resolve()
    sample_ids_path = run_dir / "sample_ids.json"
    sample_ids = load_sample_ids(sample_ids_path)

    per_sample: List[Dict[str, Any]] = []
    input_hashes: Dict[str, Dict[str, str]] = {}
    evaluated_ids: List[str] = []
    planner_failure_ids: List[str] = []
    coverage_mismatch_ids: List[str] = []

    for sample_id in sample_ids:
        predicted_path = (
            run_dir / "samples" / sample_id / "stages" / "planner" / "layout_tree.json"
        )
        oracle_path = oracle_dir / f"{sample_id}.json"
        if not oracle_path.exists():
            raise FileNotFoundError(f"missing human oracle tree: {oracle_path}")
        row: Dict[str, Any] = {
            "schema_version": A3_TREE_ACCURACY_SCHEMA_VERSION,
            "sample_id": sample_id,
            "oracle_sha256": sha256_file(oracle_path),
        }
        if not predicted_path.exists():
            planner_failure_ids.append(sample_id)
            row.update(
                status="planner_failure",
                reason="no predicted layout_tree.json on disk (Planner failed)",
                predicted_sha256=None,
                metrics=None,
            )
            per_sample.append(row)
            input_hashes[sample_id] = {"oracle": row["oracle_sha256"]}
            continue

        row["predicted_sha256"] = sha256_file(predicted_path)
        input_hashes[sample_id] = {
            "predicted": row["predicted_sha256"],
            "oracle": row["oracle_sha256"],
        }
        predicted = A3LayoutTree.model_validate_json(predicted_path.read_text())
        oracle = A3LayoutTree.model_validate_json(oracle_path.read_text())
        try:
            metrics = evaluate_tree_prediction(predicted, oracle)
        except ValueError as error:
            if "asset coverage mismatch" not in str(error):
                raise
            coverage_mismatch_ids.append(sample_id)
            row.update(status="coverage_mismatch", reason=str(error), metrics=None)
            per_sample.append(row)
            continue
        evaluated_ids.append(sample_id)
        row.update(status="evaluated", reason=None, metrics=metrics.model_dump())
        per_sample.append(row)

    evaluated_rows = [row for row in per_sample if row["status"] == "evaluated"]
    aggregate_metrics = _aggregate(evaluated_rows)

    denominators = {
        "n_sample_ids": len(sample_ids),
        "n_evaluated": len(evaluated_ids),
        "n_planner_failures": len(planner_failure_ids),
        "planner_failure_ids": planner_failure_ids,
        "n_coverage_mismatch": len(coverage_mismatch_ids),
        "coverage_mismatch_ids": coverage_mismatch_ids,
        "n_certain_nodes_total": sum(
            row["metrics"]["n_certain_nodes"] for row in evaluated_rows
        ),
        "n_uncertain_nodes_total": sum(
            row["metrics"]["n_uncertain_nodes"] for row in evaluated_rows
        ),
        "n_samples_with_uncertain_nodes": sum(
            1 for row in evaluated_rows if row["metrics"]["n_uncertain_nodes"] > 0
        ),
    }
    if (
        denominators["n_evaluated"]
        + denominators["n_planner_failures"]
        + denominators["n_coverage_mismatch"]
        != denominators["n_sample_ids"]
    ):
        raise RuntimeError("denominator accounting does not cover every sample ID")

    return {
        "schema_version": A3_TREE_ACCURACY_SCHEMA_VERSION,
        "run_dir": str(run_dir),
        "oracle_dir": str(oracle_dir),
        "sample_ids_sha256": sha256_file(sample_ids_path),
        "code_sha256": {
            rel: sha256_file(repo_root / rel) for rel in _CODE_FILES
        },
        "bootstrap": {
            "seed": BOOTSTRAP_SEED,
            "resamples": BOOTSTRAP_RESAMPLES,
            "confidence": CONFIDENCE_LEVEL,
            "method": "percentile",
            "note": "fresh np.random.default_rng(seed) per statistic",
        },
        "denominators": denominators,
        "metrics": aggregate_metrics,
        "per_sample": per_sample,
        "input_hashes": input_hashes,
    }


def _aggregate(evaluated_rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for relation in ("same_group", "parent_child"):
        block: Dict[str, Any] = {"macro": {}, "micro": {}}
        for field in _PRF_FIELDS:
            values = [row["metrics"][relation][field] for row in evaluated_rows]
            block["macro"][field] = {
                "mean": _mean(values) if values else None,
                "n": len(values),
                "ci95": bootstrap_ci(values, _mean),
            }
        counts = [
            (
                row["metrics"][relation]["n_true_positive"],
                row["metrics"][relation]["n_predicted"],
                row["metrics"][relation]["n_reference"],
            )
            for row in evaluated_rows
        ]
        pooled = _pooled_prf(counts) if counts else None
        block["micro"]["pooled"] = pooled
        if pooled is not None:
            for field in _PRF_FIELDS:
                block["micro"][f"{field}_ci95"] = bootstrap_ci(
                    counts, lambda rows, f=field: _pooled_prf(rows)[f]
                )
        result[relation] = block

    for accuracy_field, primary in (
        ("semantic_type_accuracy", True),
        ("semantic_role_accuracy", False),
    ):
        macro_values = [
            row["metrics"][accuracy_field]
            for row in evaluated_rows
            if row["metrics"][accuracy_field] is not None
        ]
        pooled_counts = []
        for row in evaluated_rows:
            n_nodes = row["metrics"]["n_certain_nodes"]
            matches = _exact_count(
                row["metrics"][accuracy_field], n_nodes,
                f"{row['sample_id']}:{accuracy_field}",
            )
            pooled_counts.append((matches, n_nodes))
        total_nodes = sum(count[1] for count in pooled_counts)
        result[accuracy_field] = {
            "role": "primary" if primary else "lower_bound_only",
            "macro": {
                "mean": _mean(macro_values) if macro_values else None,
                "n": len(macro_values),
                "n_excluded_all_uncertain": len(evaluated_rows) - len(macro_values),
                "ci95": bootstrap_ci(macro_values, _mean),
            },
            "micro": {
                "pooled": (
                    sum(count[0] for count in pooled_counts) / total_nodes
                    if total_nodes
                    else None
                ),
                "n_nodes": total_nodes,
                "ci95": bootstrap_ci(
                    pooled_counts,
                    lambda rows: (
                        sum(r[0] for r in rows) / sum(r[1] for r in rows)
                        if sum(r[1] for r in rows)
                        else float("nan")
                    ),
                ),
            },
        }
    return result


def publish_bundle(
    *,
    result: Dict[str, Any],
    output_dir: Path,
    evaluation_id: str,
    command_argv: Sequence[str],
) -> Dict[str, Path]:
    """Write-once publish: manifest + aggregate + per-sample JSONL."""
    per_sample = result.pop("per_sample")
    input_hashes = result.pop("input_hashes")
    aggregate = dict(result)
    aggregate["evaluation_id"] = evaluation_id

    # canonical_json_bytes already terminates each payload with "\n".
    per_sample_bytes = b"".join(canonical_json_bytes(row) for row in per_sample)
    aggregate_bytes = canonical_json_bytes(aggregate)

    manifest = {
        "schema_version": A3_TREE_ACCURACY_SCHEMA_VERSION,
        "evaluation_id": evaluation_id,
        "created_at": utc_now(),
        "command_argv": list(command_argv),
        "run_dir": aggregate["run_dir"],
        "oracle_dir": aggregate["oracle_dir"],
        "sample_ids_sha256": aggregate["sample_ids_sha256"],
        "code_sha256": aggregate["code_sha256"],
        "bootstrap": aggregate["bootstrap"],
        "input_tree_sha256": input_hashes,
        "artifact_sha256": {
            "aggregate.json": sha256_bytes(aggregate_bytes),
            "per_sample.jsonl": sha256_bytes(per_sample_bytes),
        },
        "write_once": True,
    }

    paths = {
        "aggregate": output_dir / "aggregate.json",
        "per_sample": output_dir / "per_sample.jsonl",
        "manifest": output_dir / "evaluation_manifest.json",
    }
    write_bytes_once(paths["aggregate"], aggregate_bytes)
    write_bytes_once(paths["per_sample"], per_sample_bytes)
    write_json_once(paths["manifest"], manifest)
    return paths


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--oracle-dir", required=True, type=Path)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[4])
    args = parser.parse_args(argv)

    result = evaluate_tree_accuracy_run(
        run_dir=args.run_dir, oracle_dir=args.oracle_dir, repo_root=args.repo_root
    )
    output_dir = (
        args.output_root / A3_TREE_ACCURACY_SCHEMA_VERSION / args.evaluation_id
    )
    paths = publish_bundle(
        result=result,
        output_dir=output_dir,
        evaluation_id=args.evaluation_id,
        command_argv=list(sys.argv) if argv is None else ["<embedded>", *argv],
    )
    for name, path in paths.items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
