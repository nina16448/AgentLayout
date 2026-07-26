#!/usr/bin/env python
"""Selection-asymmetry sensitivity: A3-T2 without candidate selection vs E2D.

The formal comparison uses A3's judge-selected B0 (best of three candidates)
against Elem2Design's single generation.  This zero-cost reanalysis removes
the selection advantage two ways, from the same frozen T2 bundles:

* ``first``  — literally the first candidate slot (one draw, no selection);
* ``mean3`` — per-sample mean over all three candidate slots (the expected
  value of a single draw).

Each variant is paired against the published E2D per-sample rows with the
same sign test / Holm / bootstrap machinery as the formal comparison.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from metagpt.ext.agentlayout.evaluation.a3_relation_stats import (  # noqa: E402
    compare_arms,
    holm_adjust,
)
from metagpt.ext.agentlayout.evaluation.a3_tree_accuracy import (  # noqa: E402
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    write_bytes_once,
)
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
    load_sample_ids,
    sha256_bytes,
    sha256_file,
    utc_now,
    write_json_once,
)
from metagpt.ext.agentlayout.schema import Candidate  # noqa: E402
from metagpt.ext.agentlayout.tools.human_tree_metrics import (  # noqa: E402
    evaluate_layout_realization,
)

SCHEMA_VERSION = "a3.external-baseline-sensitivity.v1"
METRICS = ("sgc", "tlc", "pca", "ali", "ove")
PRIMARY = ("sgc", "tlc", "pca")


def candidate_metrics(candidate, texts, canvas_w, canvas_h, oracle, sample_id, method):
    realized = evaluate_layout_realization(
        tree=oracle, candidate=candidate, canvas_width=canvas_w,
        canvas_height=canvas_h, sample_id=sample_id, method=method,
    )
    layout = [
        (CLS_TEXT if texts.get(el.id) else CLS_IMAGE_LOGO,
         to_xyxy(el.left, el.top, el.width, el.height))
        for el in candidate.elements
    ]
    layout = drop_invalid_elements(layout, canvas_w, canvas_h)
    return {
        "sgc": realized.sgc,
        "tlc": realized.tlc,
        "pca": realized.pca,
        "ali": metric_alignment([layout], canvas_w, canvas_h),
        "ove": metric_overlay([layout]),
    }


def build_variant_rows(a3_run_dir: Path, oracle_dir: Path):
    sample_ids = load_sample_ids(a3_run_dir / "sample_ids.json")
    summary = json.loads((a3_run_dir / "a3_run_summary.json").read_text())
    failed = {s["sample_id"] for s in summary["samples"] if s["status"] != "completed"}
    first_rows, mean_rows = [], []
    for sample_id in sample_ids:
        base = {"schema_version": SCHEMA_VERSION, "sample_id": sample_id}
        if sample_id in failed:
            row = dict(base, status="generation_failure",
                       **{m: None for m in METRICS})
            first_rows.append(dict(row, arm="A3-T2-first"))
            mean_rows.append(dict(row, arm="A3-T2-mean3"))
            continue
        sample_dir = a3_run_dir / "samples" / sample_id
        l0 = json.loads((sample_dir / "pipeline" / "l0_result.json").read_text())
        pfull = json.loads(
            (sample_dir / "inputs" / "pfull" / "asset_manifest.json").read_text()
        )
        texts = {
            a["asset_id"]: (a.get("content") or "")
            if a.get("content") not in (None, "None") else ""
            for a in pfull["assets"]
        }
        oracle = A3LayoutTree.model_validate_json(
            (oracle_dir / f"{sample_id}.json").read_text()
        )
        slots = sorted(l0["bundle"]["slots"], key=lambda s: s["slot_id"])
        per_slot = []
        for slot in slots:
            candidate = Candidate.model_validate(slot["candidate"])
            per_slot.append(candidate_metrics(
                candidate, texts, pfull["canvas_width"], pfull["canvas_height"],
                oracle, sample_id, f"A3-T2:{slot['slot_id']}",
            ))
        first_rows.append(dict(base, arm="A3-T2-first", status="completed",
                               slot_id=slots[0]["slot_id"], **per_slot[0]))
        mean_metrics = {}
        for metric in METRICS:
            values = [s[metric] for s in per_slot if s[metric] is not None]
            mean_metrics[metric] = sum(values) / len(values) if values else None
        mean_rows.append(dict(base, arm="A3-T2-mean3", status="completed",
                              n_slots=len(per_slot), **mean_metrics))
    return first_rows, mean_rows


def paired_block(variant_rows, e2d_rows, variant_name):
    comparisons = []
    for metric in METRICS:
        entry = compare_arms(variant_rows, e2d_rows, metric)
        entry["comparison"] = f"{variant_name}_vs_elem2design"
        entry["family"] = "primary" if metric in PRIMARY else "geometry"
        comparisons.append(entry)
    for family in ("primary", "geometry"):
        members = [e for e in comparisons if e["family"] == family]
        for entry, holm in zip(members, holm_adjust([e["sign_test_p_raw"] for e in members])):
            entry["sign_test_p_holm"] = holm
    return comparisons


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--external-eval-dir", required=True, type=Path)
    parser.add_argument("--a3-run-dir", required=True, type=Path)
    parser.add_argument("--oracle-dir", required=True, type=Path)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()

    e2d_rows = [
        json.loads(line)
        for line in (args.external_eval_dir / "per_sample.jsonl").read_text().splitlines()
    ]
    first_rows, mean_rows = build_variant_rows(
        args.a3_run_dir.resolve(), args.oracle_dir.resolve()
    )

    def arm_means(rows):
        completed = [r for r in rows if r["status"] == "completed"]
        return {
            m: {
                "mean": (lambda v: sum(v) / len(v) if v else None)(
                    [r[m] for r in completed if r.get(m) is not None]
                ),
                "n": len([r for r in completed if r.get(m) is not None]),
            }
            for m in METRICS
        }

    aggregate = {
        "schema_version": SCHEMA_VERSION,
        "evaluation_id": args.evaluation_id,
        "note": (
            "selection-asymmetry sensitivity: A3-T2 without judge selection "
            "(first candidate / mean over 3 candidates) vs Elem2Design"
        ),
        "arm_metric_means": {
            "A3-T2-first": arm_means(first_rows),
            "A3-T2-mean3": arm_means(mean_rows),
        },
        "comparisons": {
            "first_vs_e2d": paired_block(first_rows, e2d_rows, "A3-T2-first"),
            "mean3_vs_e2d": paired_block(mean_rows, e2d_rows, "A3-T2-mean3"),
        },
        "bootstrap": {"seed": BOOTSTRAP_SEED, "resamples": BOOTSTRAP_RESAMPLES},
    }

    out = args.output_root / SCHEMA_VERSION / args.evaluation_id
    per_sample_bytes = b"".join(
        canonical_json_bytes(r) for r in first_rows + mean_rows
    )
    aggregate_bytes = canonical_json_bytes(aggregate)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "evaluation_id": args.evaluation_id,
        "created_at": utc_now(),
        "external_per_sample_sha256": sha256_file(args.external_eval_dir / "per_sample.jsonl"),
        "code_sha256": {"sensitivity_single_candidate.py": sha256_file(Path(__file__))},
        "artifact_sha256": {
            "aggregate.json": sha256_bytes(aggregate_bytes),
            "per_sample.jsonl": sha256_bytes(per_sample_bytes),
        },
        "write_once": True,
    }
    write_bytes_once(out / "aggregate.json", aggregate_bytes)
    write_bytes_once(out / "per_sample.jsonl", per_sample_bytes)
    write_json_once(out / "evaluation_manifest.json", manifest)

    for name, block in aggregate["comparisons"].items():
        print(f"== {name} ==")
        for e in block:
            p = e["sign_test_p_holm"]
            print(f"  {e['metric'].upper():4s} paired={e['paired_n']} "
                  f"W/L/T={e['wins']}/{e['losses']}/{e['ties']} "
                  f"diff={e['mean_diff']:+.4f} "
                  f"CI=[{e['mean_diff_ci95']['low']:+.4f},{e['mean_diff_ci95']['high']:+.4f}] "
                  f"holm_p={p:.2e}" if e["mean_diff"] is not None else "  (no pairs)")
    print(f"bundle -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
