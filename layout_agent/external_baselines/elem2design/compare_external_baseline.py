#!/usr/bin/env python
"""Paired comparison: frozen A3-T2 vs the Elem2Design external baseline (meta env).

Zero-cost, read-only.  A3-T2 per-sample SGC/TLC/PCA come from the same
deterministic recomputation as the Relation reanalysis
(`a3_relation_stats.load_arm_per_sample`); its Ali/Ove come from the frozen
final candidates with the same shared ``sega_metrics`` path used for the
baseline.  Each metric pairs only samples that completed in both arms; both
failure rates are reported independently.

Statistics per metric: wins/losses/ties (A3-T2 minus baseline), exact
two-sided sign test, paired mean difference with sample-level percentile
bootstrap 95% CI (seed 20260712, 10,000 resamples).  Holm correction is
applied within the primary family {SGC,TLC,PCA} and separately within the
geometry family {Ali,Ove} (Rea/Occ deferred with the render+saliency
pipeline, so the geometry family has 2 tests, not 4 — recorded explicitly).
Ali/Ove are lower-is-better; sign conventions are stated in the outputs.
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
    load_arm_per_sample,
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
from metagpt.ext.agentlayout.run_manifest import (  # noqa: E402
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
    utc_now,
    write_json_once,
)
from metagpt.ext.agentlayout.schema import Candidate  # noqa: E402

SCHEMA_VERSION = "a3.external-baseline-compare.v1"
PRIMARY = ("sgc", "tlc", "pca")
GEOMETRY = ("ali", "ove")


def a3_rows_with_geometry(a3_run_dir: Path, oracle_dir: Path) -> list:
    rows, _ = load_arm_per_sample(run_dir=a3_run_dir, oracle_dir=oracle_dir, arm="A3-T2")
    for row in rows:
        if row["status"] != "completed":
            row["ali"] = row["ove"] = None
            continue
        sample_dir = a3_run_dir / "samples" / row["sample_id"]
        l0 = json.loads((sample_dir / "pipeline" / "l0_result.json").read_text())
        slot = next(s for s in l0["bundle"]["slots"] if s["slot_id"] == l0["b0_slot_id"])
        candidate = Candidate.model_validate(slot["candidate"])
        pfull = json.loads(
            (sample_dir / "inputs" / "pfull" / "asset_manifest.json").read_text()
        )
        texts = {
            a["asset_id"]: (a.get("content") or "")
            if a.get("content") not in (None, "None") else ""
            for a in pfull["assets"]
        }
        layout = [
            (
                CLS_TEXT if texts.get(el.id) else CLS_IMAGE_LOGO,
                to_xyxy(el.left, el.top, el.width, el.height),
            )
            for el in candidate.elements
        ]
        layout = drop_invalid_elements(
            layout, pfull["canvas_width"], pfull["canvas_height"]
        )
        row["ali"] = metric_alignment(
            [layout], pfull["canvas_width"], pfull["canvas_height"]
        )
        row["ove"] = metric_overlay([layout])
    return rows


def external_rows(eval_dir: Path) -> list:
    rows = [json.loads(line) for line in (eval_dir / "per_sample.jsonl").read_text().splitlines()]
    for row in rows:
        row["arm"] = "elem2design"
    return rows


def _fmt_p(p):
    if p is None:
        return "--"
    return f"{p:.1e}" if p < 1e-4 else f"{p:.4f}"


def _fmt_ci(ci):
    return "--" if ci is None else f"[{ci['low']:+.4f}, {ci['high']:+.4f}]"


def render_markdown(aggregate: dict) -> str:
    lines = [
        "# A3-T2 vs Elem2Design (Relation N=100, matched inputs)",
        "",
        f"Direction: diff = A3-T2 − Elem2Design. SGC/TLC/PCA higher is better; "
        f"Ali/Ove lower is better. Holm within families (primary 3 tests, geometry "
        f"2 tests; Rea/Occ deferred). Bootstrap seed {BOOTSTRAP_SEED}, "
        f"{BOOTSTRAP_RESAMPLES:,} resamples.",
        "",
        f"Arm completion: A3-T2 {aggregate['arms']['A3-T2']['n_completed']}/"
        f"{aggregate['arms']['A3-T2']['n_total']}; Elem2Design "
        f"{aggregate['arms']['elem2design']['n_completed']}/"
        f"{aggregate['arms']['elem2design']['n_total']}.",
        "",
        "| Metric | A3-T2 mean (n) | E2D mean (n) | Paired N | W/L/T | Mean diff | 95% CI | p raw | p Holm |",
        "| --- | ---: | ---: | ---: | --- | ---: | --- | ---: | ---: |",
    ]
    for entry in aggregate["comparisons"]:
        m = entry["metric"]
        a3m, e2m = aggregate["arm_metric_means"]["A3-T2"][m], aggregate["arm_metric_means"]["elem2design"][m]
        lines.append(
            "| " + " | ".join([
                m.upper() + (" ↓" if m in GEOMETRY else ""),
                f"{a3m['mean']:.4f} ({a3m['n']})" if a3m["mean"] is not None else "--",
                f"{e2m['mean']:.4f} ({e2m['n']})" if e2m["mean"] is not None else "--",
                str(entry["paired_n"]),
                f"{entry['wins']}/{entry['losses']}/{entry['ties']}",
                f"{entry['mean_diff']:+.4f}" if entry["mean_diff"] is not None else "--",
                _fmt_ci(entry["mean_diff_ci95"]),
                _fmt_p(entry["sign_test_p_raw"]),
                _fmt_p(entry["sign_test_p_holm"]),
            ]) + " |"
        )
    lines += ["", "Failures are excluded pairwise only; both arms' failure counts are "
              "reported above and in aggregate.json. Non-significant results mean no "
              "difference was detected, not equivalence."]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--external-eval-dir", required=True, type=Path)
    parser.add_argument("--a3-run-dir", required=True, type=Path)
    parser.add_argument("--oracle-dir", required=True, type=Path)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()

    a3_rows = a3_rows_with_geometry(args.a3_run_dir.resolve(), args.oracle_dir.resolve())
    e2d_rows = external_rows(args.external_eval_dir.resolve())

    comparisons = []
    for metric in PRIMARY + GEOMETRY:
        entry = compare_arms(a3_rows, e2d_rows, metric)
        entry["comparison"] = "A3-T2_vs_elem2design"
        entry["direction"] = "A3-T2 - elem2design"
        entry["family"] = "primary" if metric in PRIMARY else "geometry"
        comparisons.append(entry)
    for family in ("primary", "geometry"):
        family_entries = [e for e in comparisons if e["family"] == family]
        adjusted = holm_adjust([e["sign_test_p_raw"] for e in family_entries])
        for entry, holm in zip(family_entries, adjusted):
            entry["sign_test_p_holm"] = holm

    def arm_summary(rows):
        completed = [r for r in rows if r["status"] == "completed"]
        return {
            "n_total": len(rows),
            "n_completed": len(completed),
            "n_failed": len(rows) - len(completed),
            "failed_samples": [
                {"sample_id": r["sample_id"],
                 "reason": r.get("reason") or r.get("error_type")}
                for r in rows if r["status"] != "completed"
            ],
        }

    def arm_means(rows):
        completed = [r for r in rows if r["status"] == "completed"]
        out = {}
        for metric in PRIMARY + GEOMETRY:
            values = [r[metric] for r in completed if r.get(metric) is not None]
            out[metric] = {
                "mean": sum(values) / len(values) if values else None,
                "n": len(values),
            }
        return out

    aggregate = {
        "schema_version": SCHEMA_VERSION,
        "evaluation_id": args.evaluation_id,
        "arms": {"A3-T2": arm_summary(a3_rows), "elem2design": arm_summary(e2d_rows)},
        "arm_metric_means": {"A3-T2": arm_means(a3_rows), "elem2design": arm_means(e2d_rows)},
        "comparisons": comparisons,
        "bootstrap": {"seed": BOOTSTRAP_SEED, "resamples": BOOTSTRAP_RESAMPLES,
                      "method": "percentile"},
        "notes": [
            "diff = A3-T2 - elem2design; ali/ove lower is better",
            "Holm within families: primary={sgc,tlc,pca}, geometry={ali,ove}",
            "rea/occ deferred (render+saliency pipeline), not reported as 0",
            "results are Relation N=100 only; do not generalize",
        ],
    }

    out = args.output_root / SCHEMA_VERSION / args.evaluation_id
    per_sample_rows = [dict(r, arm="A3-T2") for r in a3_rows] + e2d_rows
    per_sample_bytes = b"".join(canonical_json_bytes(r) for r in per_sample_rows)
    aggregate_bytes = canonical_json_bytes(aggregate)
    markdown_bytes = render_markdown(aggregate).encode()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "evaluation_id": args.evaluation_id,
        "created_at": utc_now(),
        "external_eval_dir": str(args.external_eval_dir.resolve()),
        "a3_run_dir": str(args.a3_run_dir.resolve()),
        "external_per_sample_sha256": sha256_file(args.external_eval_dir / "per_sample.jsonl"),
        "code_sha256": {"compare_external_baseline.py": sha256_file(Path(__file__))},
        "artifact_sha256": {
            "aggregate.json": sha256_bytes(aggregate_bytes),
            "per_sample.jsonl": sha256_bytes(per_sample_bytes),
            "results.md": sha256_bytes(markdown_bytes),
        },
        "write_once": True,
    }
    write_bytes_once(out / "aggregate.json", aggregate_bytes)
    write_bytes_once(out / "per_sample.jsonl", per_sample_bytes)
    write_bytes_once(out / "results.md", markdown_bytes)
    write_json_once(out / "evaluation_manifest.json", manifest)
    print(render_markdown(aggregate))
    print(f"bundle -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
