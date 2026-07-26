#!/usr/bin/env python3
"""Versioned, read-only SEGA/PKU evaluation harness for persisted A3 runs.

This command has no LLM/API code path.  Its only writes are new files below
``--output-root/<protocol-version>/<evaluation-id>``; an existing evaluation
directory is never overwritten.
"""
from __future__ import annotations

import argparse
import copy
import ctypes
import errno
import hashlib
import json
import os
import shlex
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from metagpt.ext.agentlayout.evaluation.a3_sega_evaluator import (  # noqa: E402
    A3EvaluationError,
    PROTOCOL_VERSION,
    SALIENCY_MODE_FROZEN,
    SALIENCY_MODE_SKIP,
    SCHEMA_VERSION,
    aggregate_metric_records,
    assert_output_is_sidecar,
    evaluate_sample,
    evaluation_code_runtime_lineage,
    extract_b0_sample,
    frozen_detector_lineage,
    load_run_summary,
    protocol_lineage,
    validate_evaluation_bundle,
    verify_source_artifacts_unchanged,
)

DEFAULT_OUTPUT_ROOT = REPO_ROOT / "layout_agent" / "evaluations" / "a3-sega"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-dir",
        action="append",
        required=True,
        type=Path,
        help="A3 source run directory; repeat for T0/T2/T3.",
    )
    parser.add_argument(
        "--evaluation-id",
        required=True,
        help="New sidecar identifier. Existing directories are rejected.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help=f"Evaluation sidecar root (default: {DEFAULT_OUTPUT_ROOT}).",
    )
    parser.add_argument(
        "--saliency-mode",
        choices=(SALIENCY_MODE_FROZEN, SALIENCY_MODE_SKIP),
        default=SALIENCY_MODE_FROZEN,
        help=(
            "Occ detector mode. basnet-isnet is frozen/local-only and fails closed; "
            "skip explicitly records Occ as skipped (never substitutes Sobel)."
        ),
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate/extract B0 sources without computing metrics or loading detectors.",
    )
    parser.add_argument(
        "--max-samples",
        type=int,
        default=None,
        help="Diagnostic prefix limit per run; omit for a formal complete evaluation.",
    )
    return parser


def _validate_evaluation_id(value: str) -> str:
    if not value or value in {".", ".."}:
        raise A3EvaluationError("evaluation-id must be non-empty")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
    if any(char not in allowed for char in value):
        raise A3EvaluationError(
            "evaluation-id may contain only ASCII letters, digits, dash, underscore, and dot"
        )
    return value


def _source_skipped(run_id: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "sample_id": str(entry.get("sample_id") or ""),
        "status": "source_skipped",
        "source_status": entry.get("status"),
        "reason": "A3 summary did not mark sample completed",
        "summary_entry": entry,
        "metrics": {},
        "source_artifacts": [],
    }


def _validated_record(sample: Dict[str, Any]) -> Dict[str, Any]:
    record = {
        key: value
        for key, value in sample.items()
        if key not in {"layout", "_background_payload"}
    }
    record["status"] = "validated"
    record["metrics"] = {}
    return record


def _run_counts(records: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    return {
        "selected_n": len(records),
        "source_valid_n": sum(
            record.get("status") in {"validated", "evaluated"} for record in records
        ),
        "source_skipped_n": sum(record.get("status") == "source_skipped" for record in records),
        "evaluated_n": sum(record.get("status") == "evaluated" for record in records),
        "validated_only_n": sum(record.get("status") == "validated" for record in records),
    }


def _command_argv(
    args: argparse.Namespace, run_dirs: Sequence[Path], evaluation_id: str
) -> List[str]:
    argv = [sys.executable, str(Path(__file__).resolve())]
    for run_dir in run_dirs:
        argv.extend(["--run-dir", str(run_dir)])
    argv.extend(
        [
            "--evaluation-id",
            evaluation_id,
            "--output-root",
            str(args.output_root.expanduser().resolve()),
            "--saliency-mode",
            args.saliency_mode,
        ]
    )
    if args.validate_only:
        argv.append("--validate-only")
    if args.max_samples is not None:
        argv.extend(["--max-samples", str(args.max_samples)])
    return argv


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _path_lexists(path: Path) -> bool:
    """Like exists(), but a broken symlink also consumes the output ID."""
    return os.path.lexists(os.fspath(path))


def _rename_directory_noreplace(source: Path, destination: Path) -> None:
    """Atomically publish a directory without ever replacing a competing path."""
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise A3EvaluationError(
            "safe sidecar publication requires renameat2(RENAME_NOREPLACE)"
        )
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    at_fdcwd = -100
    rename_noreplace = 1
    result = renameat2(
        at_fdcwd,
        os.fsencode(source),
        at_fdcwd,
        os.fsencode(destination),
        rename_noreplace,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise A3EvaluationError(f"evaluation directory already exists: {destination}")
    raise OSError(error_number, os.strerror(error_number), os.fspath(destination))


def _load_strict_json(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"), parse_constant=_reject_json_constant
    )


def _reject_json_constant(value: str):
    raise ValueError(f"non-finite JSON constant {value}")


def _verify_staging_round_trip(
    staging_dir: Path,
    manifest: Dict[str, Any],
    records: Sequence[Dict[str, Any]],
    aggregate: Dict[str, Any],
) -> None:
    loaded_manifest = _load_strict_json(staging_dir / "evaluation_manifest.json")
    loaded_aggregate = _load_strict_json(staging_dir / "aggregate.json")
    loaded_records = []
    lines = (staging_dir / "per_sample.jsonl").read_text(encoding="utf-8").splitlines()
    for line in lines:
        loaded_records.append(json.loads(line, parse_constant=_reject_json_constant))
    validate_evaluation_bundle(loaded_manifest, loaded_records, loaded_aggregate)
    if (
        loaded_manifest != manifest
        or loaded_aggregate != aggregate
        or loaded_records != list(records)
    ):
        raise A3EvaluationError("sidecar JSON round-trip changed the validated bundle")


def _write_results(
    output_dir: Path,
    manifest: Dict[str, Any],
    records: Sequence[Dict[str, Any]],
    aggregate: Dict[str, Any],
    pre_publish_check: Optional[Callable[[], None]] = None,
) -> None:
    """Write a complete staging tree, then publish it with one directory rename."""
    staging_dir: Optional[Path] = None
    try:
        validate_evaluation_bundle(manifest, records, aggregate)
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        if _path_lexists(output_dir):
            raise A3EvaluationError(f"evaluation directory already exists: {output_dir}")
        staging_dir = Path(
            tempfile.mkdtemp(
                prefix=f".{output_dir.name}.staging-",
                dir=str(output_dir.parent),
            )
        )
        _write_json(staging_dir / "evaluation_manifest.json", manifest)
        _write_json(staging_dir / "aggregate.json", aggregate)
        with (staging_dir / "per_sample.jsonl").open("x", encoding="utf-8") as handle:
            for record in records:
                handle.write(
                    json.dumps(
                        record,
                        ensure_ascii=False,
                        sort_keys=True,
                        allow_nan=False,
                    )
                    + "\n"
                )
        _verify_staging_round_trip(staging_dir, manifest, records, aggregate)
        if pre_publish_check is not None:
            pre_publish_check()
        _rename_directory_noreplace(staging_dir, output_dir)
        staging_dir = None
    except A3EvaluationError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise A3EvaluationError(
            f"cannot publish evaluation sidecar {output_dir}: {exc}"
        ) from exc
    finally:
        if staging_dir is not None:
            shutil.rmtree(staging_dir, ignore_errors=True)


def run(args: argparse.Namespace) -> Path:
    if args.max_samples is not None and args.max_samples <= 0:
        raise A3EvaluationError("--max-samples must be positive")
    evaluation_id = _validate_evaluation_id(args.evaluation_id)
    run_dirs = [path.expanduser().resolve() for path in args.run_dir]
    if len(run_dirs) != len(set(run_dirs)):
        raise A3EvaluationError("duplicate --run-dir values are not allowed")
    for run_dir in run_dirs:
        if not run_dir.is_dir():
            raise A3EvaluationError(f"run directory is missing: {run_dir}")

    output_dir = (
        args.output_root.expanduser().resolve() / PROTOCOL_VERSION / evaluation_id
    )
    assert_output_is_sidecar(output_dir, run_dirs)
    if _path_lexists(output_dir):
        raise A3EvaluationError(f"evaluation directory already exists: {output_dir}")

    code_runtime_before = evaluation_code_runtime_lineage(Path(__file__).resolve())
    loaded_runs = []
    run_ids = set()
    expected_sample_ids: Optional[List[str]] = None
    for run_dir in run_dirs:
        source_run, entries = load_run_summary(run_dir)
        run_id = source_run["run_id"]
        if run_id in run_ids:
            raise A3EvaluationError(f"duplicate run_id across inputs: {run_id}")
        run_ids.add(run_id)
        summary_is_complete = bool(source_run["summary_counts"]["formal_complete"])
        if args.max_samples is None and not summary_is_complete:
            raise A3EvaluationError(
                f"formal evaluation requires a complete summary for {run_id}: "
                f"{source_run['summary_counts']}"
            )
        sample_ids = [entry["sample_id"] for entry in entries]
        if expected_sample_ids is None:
            expected_sample_ids = sample_ids
        elif sample_ids != expected_sample_ids:
            raise A3EvaluationError(
                "matched evaluation requires identical sample IDs in identical order; "
                f"{run_id} differs from {loaded_runs[0][1]['run_id']}"
            )
        loaded_runs.append((run_dir, source_run, entries, summary_is_complete))
    if expected_sample_ids is None:
        raise A3EvaluationError("at least one --run-dir is required")

    detector: Optional[Dict[str, Any]] = None
    detector_artifacts_before: Optional[Dict[str, Any]] = None
    if not args.validate_only and args.saliency_mode == SALIENCY_MODE_FROZEN:
        # The frozen contract must never trigger a model download. Both local
        # detector files are verified and hashed before the first inference.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        detector_artifacts_before = frozen_detector_lineage()
        detector = copy.deepcopy(detector_artifacts_before)

    all_records: List[Dict[str, Any]] = []
    source_runs: List[Dict[str, Any]] = []
    aggregates: Dict[str, Any] = {}
    for run_dir, source_run, full_entries, summary_is_complete in loaded_runs:
        run_id = source_run["run_id"]
        entries = full_entries
        if args.max_samples is not None:
            entries = entries[: args.max_samples]
        run_records: List[Dict[str, Any]] = []
        for entry in entries:
            if entry.get("status") != "completed":
                record = _source_skipped(run_id, entry)
            else:
                extracted = extract_b0_sample(run_dir, run_id, entry)
                record = (
                    _validated_record(extracted)
                    if args.validate_only
                    else evaluate_sample(extracted, args.saliency_mode)
                )
            run_records.append(record)
        source_run["selection"] = {
            "max_samples": args.max_samples,
            "selected_n": len(entries),
            "formal_complete_run": args.max_samples is None and summary_is_complete,
        }
        source_run["observed_counts"] = _run_counts(run_records)
        source_runs.append(source_run)
        aggregates[run_id] = {
            "sample_counts": source_run["observed_counts"],
            "metrics": aggregate_metric_records(run_records) if not args.validate_only else {},
        }
        all_records.extend(run_records)

    def verify_executable_lineage_unchanged() -> None:
        code_runtime_after = evaluation_code_runtime_lineage(Path(__file__).resolve())
        if code_runtime_after != code_runtime_before:
            raise A3EvaluationError(
                "evaluation source code or dependency/runtime identity changed "
                "during execution"
            )
        if (
            detector_artifacts_before is not None
            and frozen_detector_lineage() != detector_artifacts_before
        ):
            raise A3EvaluationError("frozen detector artifacts changed during evaluation")

    def verify_all_integrity_before_publish() -> None:
        verify_executable_lineage_unchanged()
        root_artifacts = [
            source_run[key]
            for source_run in source_runs
            for key in ("summary", "manifest", "sample_ids")
        ]
        verify_source_artifacts_unchanged(
            [*all_records, {"source_artifacts": root_artifacts}]
        )

    verify_executable_lineage_unchanged()
    if detector is not None:
        from metagpt.ext.agentlayout.evaluation.saliency_basnet_isnet import (
            detector_runtime_identity,
        )

        try:
            detector["runtime_identity"] = detector_runtime_identity(
                require_loaded=any(
                    record.get("status") == "evaluated" for record in all_records
                )
            )
        except RuntimeError as exc:
            raise A3EvaluationError(f"detector runtime identity unavailable: {exc}") from exc

    now = datetime.now(timezone.utc).isoformat()
    command_argv = _command_argv(args, run_dirs, evaluation_id)
    lineage = protocol_lineage(
        args.saliency_mode,
        detector,
        code_runtime=code_runtime_before,
    )
    matched_ids_json = json.dumps(
        expected_sample_ids, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "evaluation_id": evaluation_id,
        "created_at": now,
        "mode": "validate-only" if args.validate_only else "evaluate",
        "command": shlex.join(command_argv),
        "command_argv": command_argv,
        "source_runs": source_runs,
        "matched_samples": {
            "count": len(expected_sample_ids),
            "ordered_sample_ids": expected_sample_ids,
            "ordered_sample_ids_sha256": hashlib.sha256(matched_ids_json).hexdigest(),
        },
        "protocol_lineage": lineage,
        "write_policy": {
            "source_runs_read_only": True,
            "output_is_versioned_sidecar": True,
            "existing_output_overwrite": False,
            "atomic_staging_publish": True,
            "atomic_no_replace_publish": "renameat2(RENAME_NOREPLACE)",
        },
        "cost": {"llm_api_calls": 0, "llm_cost_usd": 0.0, "model_downloads": 0},
    }
    aggregate = {
        "schema_version": SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "evaluation_id": evaluation_id,
        "runs": aggregates,
    }
    _write_results(
        output_dir,
        manifest,
        all_records,
        aggregate,
        pre_publish_check=verify_all_integrity_before_publish,
    )
    return output_dir


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        output_dir = run(args)
    except A3EvaluationError as exc:
        parser.error(str(exc))
    print(f"wrote {output_dir}")
    print("LLM/API calls: 0; LLM cost: $0.00; source A3 artifacts modified: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
