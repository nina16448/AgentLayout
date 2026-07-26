"""Statistical reanalysis of Relation N=100 T0/T2/T3 SGC/TLC/PCA.

Zero-cost, read-only: recomputes the deterministic per-sample human-reference
layout-realization metrics from frozen run artifacts (final selected candidate
in ``pipeline/l0_result.json`` + P-Full canvas dimensions + the merged human
oracle trees) — no layout is regenerated and no API is called. Generation
failures come from ``a3_run_summary.json`` and stay explicit rows.

For each of the nine paired comparisons ({T2-T0, T3-T0, T3-T2} x {SGC, TLC,
PCA}) on the both-arms-successful intersection it reports wins/losses/ties,
the exact two-sided sign-test p (ties excluded), the paired mean difference
with a sample-level percentile-bootstrap 95% CI (fixed seed, fresh generator
per comparison), and Holm plus Bonferroni adjustments across the nine tests.

Non-significant results must be read as "no difference detected", never as
equivalence; the rendered Results paragraph encodes that wording.
"""
from __future__ import annotations

import json
import sys
from math import comb
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from metagpt.ext.agentlayout.evaluation.a3_tree_accuracy import (
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    CONFIDENCE_LEVEL,
    bootstrap_ci,
    write_bytes_once,
)
from metagpt.ext.agentlayout.layout_tree_v3 import A3LayoutTree
from metagpt.ext.agentlayout.run_manifest import (
    canonical_json_bytes,
    load_sample_ids,
    sha256_bytes,
    sha256_file,
    utc_now,
    write_json_once,
)
from metagpt.ext.agentlayout.schema import Candidate
from metagpt.ext.agentlayout.tools.human_tree_metrics import evaluate_layout_realization

A3_RELATION_STATS_SCHEMA_VERSION = "a3.relation-stats.v1"
METRICS = ("sgc", "tlc", "pca")
COMPARISONS = (("T2", "T0"), ("T3", "T0"), ("T3", "T2"))

_CODE_FILES = (
    "metagpt/ext/agentlayout/evaluation/a3_relation_stats.py",
    "metagpt/ext/agentlayout/evaluation/a3_tree_accuracy.py",
    "metagpt/ext/agentlayout/tools/human_tree_metrics.py",
    "metagpt/ext/agentlayout/layout_tree_v3.py",
)


def sign_test_two_sided(wins: int, losses: int) -> Optional[float]:
    """Exact two-sided binomial sign test with ties excluded.

    ``p = min(1, 2 * P(X <= min(wins, losses)))`` for X ~ Binomial(n, 0.5).
    Returns None when every pair is a tie (no informative pairs).
    """
    n = wins + losses
    if n == 0:
        return None
    k = min(wins, losses)
    tail = sum(comb(n, i) for i in range(k + 1)) / (2**n)
    return min(1.0, 2.0 * tail)


def holm_adjust(p_values: Sequence[Optional[float]]) -> List[Optional[float]]:
    """Holm step-down adjustment; ``None`` entries pass through untouched."""
    indexed = [(i, p) for i, p in enumerate(p_values) if p is not None]
    m = len(indexed)
    adjusted: List[Optional[float]] = [None] * len(p_values)
    running_max = 0.0
    for rank, (index, p) in enumerate(sorted(indexed, key=lambda item: item[1])):
        value = min(1.0, (m - rank) * p)
        running_max = max(running_max, value)
        adjusted[index] = running_max
    return adjusted


def bonferroni_adjust(p_values: Sequence[Optional[float]]) -> List[Optional[float]]:
    m = sum(1 for p in p_values if p is not None)
    return [None if p is None else min(1.0, m * p) for p in p_values]


def load_arm_per_sample(
    *, run_dir: Path, oracle_dir: Path, arm: str
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Recompute per-sample SGC/TLC/PCA for one arm from frozen artifacts.

    Returns (rows, sample_ids). Failed generations become explicit
    ``status='generation_failure'`` rows sourced from ``a3_run_summary.json``.
    """
    run_dir = run_dir.resolve()
    sample_ids = load_sample_ids(run_dir / "sample_ids.json")
    summary = json.loads((run_dir / "a3_run_summary.json").read_text())
    failed_info = {
        entry["sample_id"]: entry
        for entry in summary["samples"]
        if entry["status"] != "completed"
    }

    rows: List[Dict[str, Any]] = []
    for sample_id in sample_ids:
        row: Dict[str, Any] = {
            "schema_version": A3_RELATION_STATS_SCHEMA_VERSION,
            "arm": arm,
            "sample_id": sample_id,
        }
        if sample_id in failed_info:
            row.update(
                status="generation_failure",
                error_type=failed_info[sample_id].get("error_type"),
                sgc=None,
                tlc=None,
                pca=None,
                skip_reasons=[],
            )
            rows.append(row)
            continue

        sample_dir = run_dir / "samples" / sample_id
        l0_path = sample_dir / "pipeline" / "l0_result.json"
        l0 = json.loads(l0_path.read_text())
        slot_id = l0["b0_slot_id"]
        slot = next(s for s in l0["bundle"]["slots"] if s["slot_id"] == slot_id)
        candidate = Candidate.model_validate(slot["candidate"])
        manifest = json.loads(
            (sample_dir / "inputs" / "pfull" / "asset_manifest.json").read_text()
        )
        oracle_path = oracle_dir / f"{sample_id}.json"
        oracle = A3LayoutTree.model_validate_json(oracle_path.read_text())
        metrics = evaluate_layout_realization(
            tree=oracle,
            candidate=candidate,
            canvas_width=manifest["canvas_width"],
            canvas_height=manifest["canvas_height"],
            sample_id=sample_id,
            method=arm,
        )
        row.update(
            status="completed",
            error_type=None,
            sgc=metrics.sgc,
            tlc=metrics.tlc,
            pca=metrics.pca,
            skip_reasons=metrics.skip_reasons,
            l0_result_sha256=sha256_file(l0_path),
            oracle_sha256=sha256_file(oracle_path),
        )
        rows.append(row)
    return rows, sample_ids


def compare_arms(
    rows_a: Sequence[Dict[str, Any]],
    rows_b: Sequence[Dict[str, Any]],
    metric: str,
) -> Dict[str, Any]:
    """Paired stats for arm_a - arm_b on the both-successful intersection."""
    by_id_a = {row["sample_id"]: row for row in rows_a}
    by_id_b = {row["sample_id"]: row for row in rows_b}
    paired_ids = sorted(
        sample_id
        for sample_id in by_id_a
        if sample_id in by_id_b
        and by_id_a[sample_id]["status"] == "completed"
        and by_id_b[sample_id]["status"] == "completed"
        and by_id_a[sample_id][metric] is not None
        and by_id_b[sample_id][metric] is not None
    )
    diffs = [by_id_a[s][metric] - by_id_b[s][metric] for s in paired_ids]
    wins = sum(1 for d in diffs if d > 0)
    losses = sum(1 for d in diffs if d < 0)
    ties = len(diffs) - wins - losses
    mean_diff = sum(diffs) / len(diffs) if diffs else None
    return {
        "metric": metric,
        "paired_n": len(diffs),
        "paired_sample_ids_excluded": sorted(
            set(by_id_a) & set(by_id_b) - set(paired_ids)
        ),
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "sign_test_p_raw": sign_test_two_sided(wins, losses),
        "mean_diff": mean_diff,
        "mean_diff_ci95": bootstrap_ci(
            diffs, lambda values: sum(values) / len(values)
        ),
    }


def analyze(
    *,
    run_dirs: Dict[str, Path],
    oracle_dir: Path,
    repo_root: Path,
) -> Dict[str, Any]:
    """Full reanalysis over the three arms; returns aggregate + per-sample rows."""
    oracle_dir = oracle_dir.resolve()
    arm_rows: Dict[str, List[Dict[str, Any]]] = {}
    arm_meta: Dict[str, Any] = {}
    frozen_ids: Optional[List[str]] = None
    for arm, run_dir in run_dirs.items():
        rows, sample_ids = load_arm_per_sample(
            run_dir=run_dir, oracle_dir=oracle_dir, arm=arm
        )
        if frozen_ids is None:
            frozen_ids = sample_ids
        elif frozen_ids != sample_ids:
            raise ValueError(f"arm {arm} uses a different frozen sample-ID list")
        arm_rows[arm] = rows
        failures = [r for r in rows if r["status"] == "generation_failure"]
        arm_meta[arm] = {
            "run_dir": str(run_dir.resolve()),
            "sample_ids_sha256": sha256_file(run_dir / "sample_ids.json"),
            "run_summary_sha256": sha256_file(run_dir / "a3_run_summary.json"),
            "n_frozen": len(sample_ids),
            "n_completed": sum(1 for r in rows if r["status"] == "completed"),
            "n_generation_failures": len(failures),
            "generation_failures": [
                {"sample_id": r["sample_id"], "error_type": r["error_type"]}
                for r in failures
            ],
            "metric_valid_n": {
                metric: sum(
                    1
                    for r in rows
                    if r["status"] == "completed" and r[metric] is not None
                )
                for metric in METRICS
            },
            "metric_mean": {
                metric: (
                    lambda values: sum(values) / len(values) if values else None
                )(
                    [
                        r[metric]
                        for r in rows
                        if r["status"] == "completed" and r[metric] is not None
                    ]
                )
                for metric in METRICS
            },
        }

    comparisons: List[Dict[str, Any]] = []
    for arm_a, arm_b in COMPARISONS:
        for metric in METRICS:
            entry = compare_arms(arm_rows[arm_a], arm_rows[arm_b], metric)
            entry["comparison"] = f"{arm_a}_vs_{arm_b}"
            entry["direction"] = f"{arm_a} - {arm_b}"
            comparisons.append(entry)

    raw_ps = [entry["sign_test_p_raw"] for entry in comparisons]
    for entry, holm, bonferroni in zip(
        comparisons, holm_adjust(raw_ps), bonferroni_adjust(raw_ps)
    ):
        entry["sign_test_p_holm"] = holm
        entry["sign_test_p_bonferroni"] = bonferroni

    total_failures = sum(
        arm_meta[arm]["n_generation_failures"] for arm in run_dirs
    )
    return {
        "schema_version": A3_RELATION_STATS_SCHEMA_VERSION,
        "oracle_dir": str(oracle_dir),
        "frozen_sample_n": len(frozen_ids or []),
        "arms": arm_meta,
        "total_generation_failures": total_failures,
        "total_generation_attempts": len(frozen_ids or []) * len(run_dirs),
        "family_size": len([p for p in raw_ps if p is not None]),
        "comparisons": comparisons,
        "bootstrap": {
            "seed": BOOTSTRAP_SEED,
            "resamples": BOOTSTRAP_RESAMPLES,
            "confidence": CONFIDENCE_LEVEL,
            "method": "percentile",
            "note": "fresh np.random.default_rng(seed) per comparison",
        },
        "code_sha256": {rel: sha256_file(repo_root / rel) for rel in _CODE_FILES},
        "per_sample": [row for arm in run_dirs for row in arm_rows[arm]],
    }


def _fmt_p(p: Optional[float]) -> str:
    if p is None:
        return "--"
    if p < 1e-4:
        return f"{p:.1e}"
    return f"{p:.4f}"


def _fmt_ci(ci: Optional[Dict[str, Any]]) -> str:
    if ci is None:
        return "--"
    return f"[{ci['low']:+.4f}, {ci['high']:+.4f}]"


def render_markdown(aggregate: Dict[str, Any]) -> str:
    lines = [
        "# Relation N=100 SGC/TLC/PCA statistical reanalysis",
        "",
        f"Schema `{aggregate['schema_version']}`; sign test = exact two-sided binomial "
        "(ties excluded); Holm adjustment over all "
        f"{aggregate['family_size']} tests; Bonferroni shown as sensitivity analysis; "
        f"bootstrap CI = sample-level percentile, seed {aggregate['bootstrap']['seed']}, "
        f"{aggregate['bootstrap']['resamples']:,} resamples.",
        "",
        "## Arm summary",
        "",
        "| Arm | Frozen N | Completed | Failures | SGC mean (n) | TLC mean (n) | PCA mean (n) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for arm, meta in aggregate["arms"].items():
        cells = [
            arm,
            str(meta["n_frozen"]),
            str(meta["n_completed"]),
            str(meta["n_generation_failures"]),
        ]
        for metric in METRICS:
            mean = meta["metric_mean"][metric]
            n = meta["metric_valid_n"][metric]
            cells.append(f"{mean:.4f} ({n})" if mean is not None else "--")
        lines.append("| " + " | ".join(cells) + " |")
    failures = [
        f"{arm} `{f['sample_id']}` ({f['error_type']})"
        for arm, meta in aggregate["arms"].items()
        for f in meta["generation_failures"]
    ]
    lines += [
        "",
        f"Generation failures: {aggregate['total_generation_failures']}/"
        f"{aggregate['total_generation_attempts']} — " + "; ".join(failures) + ".",
        "",
        "## Paired comparisons",
        "",
        "| Comparison | Metric | Paired N | W/L/T | Mean diff | 95% CI | p raw | p Holm | p Bonf |",
        "| --- | --- | ---: | --- | ---: | --- | ---: | ---: | ---: |",
    ]
    for entry in aggregate["comparisons"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    entry["comparison"].replace("_vs_", " vs "),
                    entry["metric"].upper(),
                    str(entry["paired_n"]),
                    f"{entry['wins']}/{entry['losses']}/{entry['ties']}",
                    f"{entry['mean_diff']:+.4f}" if entry["mean_diff"] is not None else "--",
                    _fmt_ci(entry["mean_diff_ci95"]),
                    _fmt_p(entry["sign_test_p_raw"]),
                    _fmt_p(entry["sign_test_p_holm"]),
                    _fmt_p(entry["sign_test_p_bonferroni"]),
                ]
            )
            + " |"
        )
    lines += ["", "## Conservative Results paragraph", "", render_results_paragraph(aggregate), ""]
    return "\n".join(lines)


def render_latex(aggregate: Dict[str, Any]) -> str:
    rows = []
    for entry in aggregate["comparisons"]:
        rows.append(
            " & ".join(
                [
                    entry["comparison"].replace("_vs_", " vs "),
                    entry["metric"].upper(),
                    str(entry["paired_n"]),
                    f"{entry['wins']}/{entry['losses']}/{entry['ties']}",
                    f"{entry['mean_diff']:+.4f}" if entry["mean_diff"] is not None else "--",
                    _fmt_ci(entry["mean_diff_ci95"]).replace("[", "$[").replace("]", "]$"),
                    _fmt_p(entry["sign_test_p_raw"]),
                    _fmt_p(entry["sign_test_p_holm"]),
                    _fmt_p(entry["sign_test_p_bonferroni"]),
                ]
            )
            + r" \\"
        )
    body = "\n".join(rows)
    seed = aggregate["bootstrap"]["seed"]
    resamples = aggregate["bootstrap"]["resamples"]
    return "\n".join(
        [
            r"\begin{table}[t]",
            r"\centering",
            r"\small",
            r"\caption{Relation $N{=}100$ paired reanalysis of human-reference"
            r" SGC/TLC/PCA. Exact two-sided sign tests (ties excluded) with"
            r" Holm adjustment across all nine tests (Bonferroni as sensitivity"
            r" analysis); mean paired differences with sample-level percentile"
            rf" bootstrap 95\% CIs (seed {seed}, {resamples:,} resamples).}}",
            r"\label{tab:relation100-stats}",
            r"\begin{tabular}{llrlrlrrr}",
            r"\toprule",
            r"Comparison & Metric & $N$ & W/L/T & $\Delta$ mean & 95\% CI"
            r" & $p$ & $p_{\mathrm{Holm}}$ & $p_{\mathrm{Bonf}}$ \\",
            r"\midrule",
            body,
            r"\bottomrule",
            r"\end{tabular}",
            r"\end{table}",
        ]
    )


def render_results_paragraph(aggregate: Dict[str, Any]) -> str:
    """One conservative paragraph; never claims equivalence from null results."""
    significant = [
        entry
        for entry in aggregate["comparisons"]
        if entry["sign_test_p_holm"] is not None and entry["sign_test_p_holm"] < 0.05
    ]
    non_significant = [
        entry
        for entry in aggregate["comparisons"]
        if entry["sign_test_p_holm"] is None or entry["sign_test_p_holm"] >= 0.05
    ]

    def _describe(entry: Dict[str, Any]) -> str:
        return (
            f"{entry['comparison'].replace('_vs_', ' vs ')} {entry['metric'].upper()} "
            f"({entry['wins']}W/{entry['losses']}L/{entry['ties']}T, "
            f"mean diff {entry['mean_diff']:+.4f}, "
            f"95% CI {_fmt_ci(entry['mean_diff_ci95'])}, "
            f"Holm p={_fmt_p(entry['sign_test_p_holm'])})"
        )

    parts = [
        f"Across {aggregate['frozen_sample_n']} frozen Relation samples per arm "
        f"({aggregate['total_generation_failures']}/"
        f"{aggregate['total_generation_attempts']} generation failures excluded "
        "pairwise, so each comparison uses the intersection of samples that "
        "completed in both arms), we ran nine paired two-sided sign tests with "
        "Holm correction."
    ]
    if significant:
        parts.append(
            "After Holm adjustment, the following comparisons remained "
            "significant at alpha=0.05: " + "; ".join(_describe(e) for e in significant) + "."
        )
    else:
        parts.append("No comparison remained significant after Holm adjustment.")
    if non_significant:
        names = ", ".join(
            f"{e['comparison'].replace('_vs_', ' vs ')} {e['metric'].upper()}"
            for e in non_significant
        )
        parts.append(
            f"For the remaining comparisons ({names}) no difference was detected; "
            "because no equivalence test was performed, these null results must "
            "not be interpreted as evidence that the arms are equivalent."
        )
    return " ".join(parts)


def publish_bundle(
    *,
    aggregate: Dict[str, Any],
    output_dir: Path,
    evaluation_id: str,
    command_argv: Sequence[str],
) -> Dict[str, Path]:
    aggregate = dict(aggregate)
    per_sample = aggregate.pop("per_sample")
    aggregate["evaluation_id"] = evaluation_id

    # canonical_json_bytes already terminates each payload with "\n".
    per_sample_bytes = b"".join(canonical_json_bytes(row) for row in per_sample)
    aggregate_bytes = canonical_json_bytes(aggregate)
    markdown_bytes = render_markdown(aggregate).encode("utf-8")
    latex_bytes = render_latex(aggregate).encode("utf-8")

    manifest = {
        "schema_version": A3_RELATION_STATS_SCHEMA_VERSION,
        "evaluation_id": evaluation_id,
        "created_at": utc_now(),
        "command_argv": list(command_argv),
        "arms": {
            arm: {
                key: meta[key]
                for key in ("run_dir", "sample_ids_sha256", "run_summary_sha256")
            }
            for arm, meta in aggregate["arms"].items()
        },
        "oracle_dir": aggregate["oracle_dir"],
        "code_sha256": aggregate["code_sha256"],
        "bootstrap": aggregate["bootstrap"],
        "artifact_sha256": {
            "aggregate.json": sha256_bytes(aggregate_bytes),
            "per_sample.jsonl": sha256_bytes(per_sample_bytes),
            "results.md": sha256_bytes(markdown_bytes),
            "results.tex": sha256_bytes(latex_bytes),
        },
        "write_once": True,
    }

    paths = {
        "aggregate": output_dir / "aggregate.json",
        "per_sample": output_dir / "per_sample.jsonl",
        "results_md": output_dir / "results.md",
        "results_tex": output_dir / "results.tex",
        "manifest": output_dir / "evaluation_manifest.json",
    }
    write_bytes_once(paths["aggregate"], aggregate_bytes)
    write_bytes_once(paths["per_sample"], per_sample_bytes)
    write_bytes_once(paths["results_md"], markdown_bytes)
    write_bytes_once(paths["results_tex"], latex_bytes)
    write_json_once(paths["manifest"], manifest)
    return paths


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--t0-run-dir", required=True, type=Path)
    parser.add_argument("--t2-run-dir", required=True, type=Path)
    parser.add_argument("--t3-run-dir", required=True, type=Path)
    parser.add_argument("--oracle-dir", required=True, type=Path)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[4])
    args = parser.parse_args(argv)

    aggregate = analyze(
        run_dirs={
            "T0": args.t0_run_dir,
            "T2": args.t2_run_dir,
            "T3": args.t3_run_dir,
        },
        oracle_dir=args.oracle_dir,
        repo_root=args.repo_root,
    )
    output_dir = (
        args.output_root / A3_RELATION_STATS_SCHEMA_VERSION / args.evaluation_id
    )
    paths = publish_bundle(
        aggregate=aggregate,
        output_dir=output_dir,
        evaluation_id=args.evaluation_id,
        command_argv=list(sys.argv) if argv is None else ["<embedded>", *argv],
    )
    for name, path in paths.items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
