#!/usr/bin/env python
"""Evaluate the Elem2Design external baseline against the human oracle (meta env).

Zero-cost, read-only over the converted baseline run bundle: computes the
primary human-reference metrics (SGC/TLC/PCA via the shared
``evaluate_layout_realization``) plus the geometry axes Ali/Ove (shared
``sega_metrics``, PKU ``drop_invalid`` applied) for every sample, and
publishes a write-once bundle.  Rea/Occ need the render+saliency pipeline and
are explicitly deferred (recorded in the manifest, never reported as 0).

Failed samples stay explicit rows; nothing is silently dropped.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from metagpt.ext.agentlayout.evaluation.a3_tree_accuracy import write_bytes_once  # noqa: E402
from metagpt.ext.agentlayout.evaluation.sega_metrics import (  # noqa: E402
    CLS_IMAGE_LOGO,
    CLS_TEXT,
    drop_invalid_elements,
    metric_alignment,
    metric_overlay,
    to_xyxy,
)
from metagpt.ext.agentlayout.layout_tree_v3 import A3LayoutTree  # noqa: E402
from metagpt.ext.agentlayout.run_manifest import (  # noqa: E402
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
    utc_now,
    write_json_once,
)
from metagpt.ext.agentlayout.schema import Candidate  # noqa: E402
from metagpt.ext.agentlayout.tools.human_tree_metrics import (  # noqa: E402
    evaluate_layout_realization,
)

SCHEMA_VERSION = "a3.external-baseline-eval.v1"


def geometry_axes(candidate: Candidate, texts: dict, canvas_w: float, canvas_h: float) -> dict:
    layout = [
        (
            CLS_TEXT if texts.get(el.id) else CLS_IMAGE_LOGO,
            to_xyxy(el.left, el.top, el.width, el.height),
        )
        for el in candidate.elements
    ]
    layout = drop_invalid_elements(layout, canvas_w, canvas_h)
    return {
        "ali": metric_alignment([layout], canvas_w, canvas_h),
        "ove": metric_overlay([layout]),
    }


def evaluate_run(run_dir: Path, oracle_dir: Path, method: str) -> dict:
    sample_ids = json.loads((run_dir / "sample_ids.json").read_text())
    id_maps = json.loads((run_dir / "id_maps.json").read_text())
    rows, n_completed = [], 0
    for sample_id in sample_ids:
        row = {"schema_version": SCHEMA_VERSION, "sample_id": sample_id, "method": method}
        candidate_path = run_dir / "samples" / sample_id / "candidate.json"
        if not candidate_path.exists():
            error_path = run_dir / "samples" / sample_id / "error.json"
            reason = "unknown"
            if error_path.exists():
                reason = json.loads(error_path.read_text()).get("reason", "unknown")
            row.update(status="failed", reason=reason, sgc=None, tlc=None, pca=None,
                       ali=None, ove=None, skip_reasons=[])
            rows.append(row)
            continue
        n_completed += 1
        candidate = Candidate.model_validate_json(candidate_path.read_text())
        canvas = id_maps[sample_id]["canvas"]
        texts = id_maps[sample_id]["texts"]
        oracle = A3LayoutTree.model_validate_json((oracle_dir / f"{sample_id}.json").read_text())
        metrics = evaluate_layout_realization(
            tree=oracle,
            candidate=candidate,
            canvas_width=canvas["canvas_width"],
            canvas_height=canvas["canvas_height"],
            sample_id=sample_id,
            method=method,
        )
        row.update(
            status="completed",
            reason=None,
            sgc=metrics.sgc,
            tlc=metrics.tlc,
            pca=metrics.pca,
            skip_reasons=metrics.skip_reasons,
            candidate_sha256=sha256_file(candidate_path),
            **geometry_axes(candidate, texts, canvas["canvas_width"], canvas["canvas_height"]),
        )
        rows.append(row)
    return {"rows": rows, "n_total": len(sample_ids), "n_completed": n_completed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--oracle-dir", required=True, type=Path)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--method", default="elem2design")
    args = parser.parse_args()

    result = evaluate_run(args.run_dir.resolve(), args.oracle_dir.resolve(), args.method)
    rows = result["rows"]
    completed = [r for r in rows if r["status"] == "completed"]

    def mean_of(key: str):
        values = [r[key] for r in completed if r[key] is not None]
        return (sum(values) / len(values), len(values)) if values else (None, 0)

    aggregate = {
        "schema_version": SCHEMA_VERSION,
        "evaluation_id": args.evaluation_id,
        "method": args.method,
        "run_dir": str(args.run_dir.resolve()),
        "oracle_dir": str(args.oracle_dir.resolve()),
        "n_total": result["n_total"],
        "n_completed": result["n_completed"],
        "n_failed": result["n_total"] - result["n_completed"],
        "failed_samples": [
            {"sample_id": r["sample_id"], "reason": r["reason"]}
            for r in rows if r["status"] == "failed"
        ],
        "metric_means": {
            key: {"mean": mean, "n": n}
            for key in ("sgc", "tlc", "pca", "ali", "ove")
            for mean, n in [mean_of(key)]
        },
        "deferred_metrics": {
            "rea": "requires render+saliency pipeline; deferred, not zero",
            "occ": "requires render+saliency pipeline; deferred, not zero",
        },
    }

    out = args.output_root / SCHEMA_VERSION / args.evaluation_id
    per_sample_bytes = b"".join(canonical_json_bytes(r) for r in rows)
    aggregate_bytes = canonical_json_bytes(aggregate)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "evaluation_id": args.evaluation_id,
        "created_at": utc_now(),
        "input_hashes": {
            "run_summary.json": sha256_file(args.run_dir / "run_summary.json"),
            "id_maps.json": sha256_file(args.run_dir / "id_maps.json"),
            "sample_ids.json": sha256_file(args.run_dir / "sample_ids.json"),
        },
        "code_sha256": {
            "evaluate_external_baseline.py": sha256_file(Path(__file__)),
            "human_tree_metrics.py": sha256_file(
                REPO_ROOT / "metagpt/ext/agentlayout/tools/human_tree_metrics.py"
            ),
            "sega_metrics.py": sha256_file(
                REPO_ROOT / "metagpt/ext/agentlayout/evaluation/sega_metrics.py"
            ),
        },
        "artifact_sha256": {
            "aggregate.json": sha256_bytes(aggregate_bytes),
            "per_sample.jsonl": sha256_bytes(per_sample_bytes),
        },
        "write_once": True,
    }
    write_bytes_once(out / "aggregate.json", aggregate_bytes)
    write_bytes_once(out / "per_sample.jsonl", per_sample_bytes)
    write_json_once(out / "evaluation_manifest.json", manifest)
    print(json.dumps(aggregate["metric_means"], indent=1))
    print(f"completed {aggregate['n_completed']}/{aggregate['n_total']} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
