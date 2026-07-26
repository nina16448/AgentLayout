"""Direct tests for the persisted-A3 SEGA evaluator and sidecar CLI.

Every fixture is local and synthetic. No detector, MLLM, API, or repository
run artifact is accessed.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import sys
from types import SimpleNamespace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import layout_agent.evaluate_a3_sega as sega_cli
import metagpt.ext.agentlayout.evaluation.a3_sega_evaluator as a3_evaluator
import metagpt.ext.agentlayout.evaluation.saliency_basnet_isnet as saliency_adapter
from metagpt.ext.agentlayout.evaluation.a3_sega_evaluator import (
    A3EvaluationError,
    METRIC_KEYS,
    PROTOCOL_VERSION,
    SALIENCY_MODE_FROZEN,
    SALIENCY_MODE_SKIP,
    SCHEMA_VERSION,
    aggregate_metric_records,
    clip_and_filter_layout,
    evaluate_sample,
    extract_b0_sample,
    frozen_detector_lineage,
    load_run_summary,
    sha256_array,
    sha256_file,
    verify_source_artifacts_unchanged,
)
from metagpt.ext.agentlayout.evaluation.sega_metrics import (
    CLS_IMAGE_LOGO,
    CLS_TEXT,
    CLS_UNDERLAY,
)


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def _write_png(path: Path, rgb=(255, 255, 255)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (10, 10), rgb).save(path)


def _make_run(
    tmp_path: Path,
    *,
    with_background: bool = True,
    run_id: str = "a3-test-run",
    sample_id: str = "sample-1",
) -> dict:
    """Create one minimal schema-valid completed A3 L0 run."""
    run_dir = tmp_path / "source-runs" / run_id
    sample_dir = run_dir / "samples" / sample_id
    pfull_dir = sample_dir / "inputs" / "pfull"
    r3_dir = sample_dir / "inputs" / "r3"
    render_path = sample_dir / "renders" / "r0_candidate_01.png"
    text_bitmap = r3_dir / "assets" / "asset_0001_r3_text.png"
    _write_png(render_path)
    _write_png(text_bitmap, (0, 0, 0))

    pfull_assets = []
    r3_assets = []
    background_id = None
    background_path = None
    if with_background:
        background_id = "asset_0000"
        background_path = pfull_dir / "assets" / "asset_0000.png"
        _write_png(background_path, (20, 40, 60))
        background_sha = sha256_file(background_path)
        pfull_assets.append(
            {
                "asset_id": background_id,
                "source_index": 0,
                "role": "background",
                "media_type": "raster",
                "semantic_hint": "base_background",
                "content": None,
                "asset_ref": str(background_path),
                "sha256": background_sha,
                "mime_type": "image/png",
                "native_width": 10,
                "native_height": 10,
                "classification_reason": "synthetic full-canvas background",
            }
        )
        r3_assets.append(
            {
                "asset_id": background_id,
                "role": "background",
                "media_type": "raster",
                "content": None,
                "asset_ref": str(background_path),
                "sha256": background_sha,
                "bitmap_width": 10,
                "bitmap_height": 10,
                "bitmap_aspect_ratio": 1.0,
                "source_bitmap_sha256": None,
            }
        )

    source_index = 1 if with_background else 0
    pfull_assets.append(
        {
            "asset_id": "asset_0001",
            "source_index": source_index,
            "role": "placeable",
            "media_type": "text",
            "semantic_hint": "text",
            "content": "hello",
            "asset_ref": None,
            "sha256": None,
            "mime_type": None,
            "native_width": None,
            "native_height": None,
            "classification_reason": "synthetic text",
        }
    )
    r3_assets.append(
        {
            "asset_id": "asset_0001",
            "role": "placeable",
            "media_type": "text_bitmap",
            "content": "hello",
            "asset_ref": str(text_bitmap),
            "sha256": sha256_file(text_bitmap),
            "bitmap_width": 10,
            "bitmap_height": 10,
            "bitmap_aspect_ratio": 1.0,
            "source_bitmap_sha256": None,
        }
    )

    pfull_path = pfull_dir / "asset_manifest.json"
    _write_json(
        pfull_path,
        {
            "schema_version": "a3.pfull-asset-manifest.v1",
            "policy_version": "pfull.crello.pixel-only-background.v1",
            "sample_id": sample_id,
            "title": "synthetic",
            "canvas_width": 10,
            "canvas_height": 10,
            "background_asset_id": background_id,
            "assets": pfull_assets,
            "source_meta_sha256": "0" * 64,
        },
    )
    r3_path = r3_dir / "r3_asset_manifest.json"
    _write_json(
        r3_path,
        {
            "schema_version": "a3.r3-asset-manifest.v1",
            "sample_id": sample_id,
            "canvas_width": 10,
            "canvas_height": 10,
            "background_asset_id": background_id,
            "normalization": {
                "version": "r3.alpha-tight-long-edge.v1",
                "long_edge_px": 512,
                "padding_px": 8,
                "alpha_threshold": 1,
                "resize_filter": "lanczos",
            },
            "source_pfull_manifest_sha256": sha256_file(pfull_path),
            "assets": r3_assets,
        },
    )

    render_sha = sha256_file(render_path)
    slot_ids = ["r0_candidate_01", "r0_candidate_02", "r0_candidate_03"]
    elements = [
        {
            "id": "asset_0001",
            "left": 1,
            "top": 1,
            "width": 8,
            "height": 8,
            "z_index": 1,
        }
    ]
    slots = [
        {
            "slot_id": slot_id,
            "status": "completed",
            "candidate": {"candidate_id": slot_id, "elements": elements},
            "render_ref": str(render_path),
            "render_sha256": render_sha,
            "qc_passed": True,
            "qc_violations": [],
            "qc_completeness": 1.0,
        }
        for slot_id in slot_ids
    ]
    _write_json(
        sample_dir / "pipeline" / "l0_result.json",
        {
            "pipeline_version": "a3.l0-pipeline.v1",
            "loop": "L0",
            "tree_arm": "T0",
            "degradations": [],
            "bundle": {
                "schema_version": "a3.r0-bundle.v1",
                "policy_version": "a3.l0-candidate-policy.v1",
                "slots": slots,
            },
            "judge_select": {
                "schema_version": "a3.judge-select-result.v1",
                "ranking": slot_ids,
                "selected_candidate_id": slot_ids[0],
            },
            "b0_slot_id": slot_ids[0],
            "stop_reason": "l0_unconditional_stop",
        },
    )

    entry = {
        "sample_id": sample_id,
        "status": "completed",
        "final": slot_ids[0],
        "stage_calls": 0,
    }
    sample_ids_path = run_dir / "sample_ids.json"
    _write_json(sample_ids_path, [sample_id])
    _write_json(
        run_dir / "run_manifest.json",
        {
            "run_id": run_id,
            "sample_ids_snapshot": {
                "count": 1,
                "sha256": sha256_file(sample_ids_path),
                "source_path": str(sample_ids_path),
                "stored_path": "sample_ids.json",
            },
        },
    )
    _write_json(
        run_dir / "a3_run_summary.json",
        {"total": 1, "failed": 0, "samples": [entry]},
    )
    return {
        "run_dir": run_dir,
        "run_id": run_id,
        "sample_id": sample_id,
        "entry": entry,
        "background_path": background_path,
        "pfull_path": pfull_path,
        "r3_path": r3_path,
    }


def _blank_sample(layout) -> dict:
    return {
        "run_id": "run",
        "sample_id": "sample",
        "status": "source_valid",
        "canvas": {"width": 10, "height": 10},
        "layout": layout,
        # Deliberately stale/raw-looking metadata: evaluate_sample must derive
        # applicability from the already filtered layout, not trust this flag.
        "underlay_protocol": {"applicable": True},
        "background": {
            "kind": "blank_canvas",
            "rgb": [255, 255, 255],
            "asset_ref": None,
            "asset_sha256": None,
        },
        "source_artifacts": [],
    }


def _metric_row(sample_id: str, ali: float) -> dict:
    metrics = {}
    for key in METRIC_KEYS:
        if key in {"Und_l", "Und_s"}:
            metrics[key] = {"status": "not_applicable", "value": None, "reason": "none"}
        else:
            metrics[key] = {"status": "ok", "value": ali, "reason": None}
    return {
        "run_id": "run",
        "sample_id": sample_id,
        "status": "evaluated",
        "metrics": metrics,
    }


def _valid_sidecar_bundle(*, evaluated: bool = False):
    sample_id = "sample-1"
    run_id = "run-1"

    def artifact(path, sha):
        return {"path": path, "sha256": sha, "size_bytes": 1}

    if evaluated:
        metrics = _metric_row(sample_id, 0.25)["metrics"]
        metrics["Occ"] = {
            "status": "skipped",
            "value": None,
            "reason": "explicit --saliency-mode skip",
        }
        record = {
            "run_id": run_id,
            "sample_id": sample_id,
            "status": "evaluated",
            "canvas": {"width": 10, "height": 10},
            "b0_slot_id": "r0_candidate_01",
            "b0_render_sha256": "1" * 64,
            "elements": [],
            "element_counts": {
                "raw": 0,
                "valid": 0,
                "invalid_below_0_1pct": 0,
            },
            "underlay_protocol": {
                "label_source": "synthetic",
                "applicable": False,
                "valid_underlay_ids": [],
                "raster_inference_forbidden": True,
            },
            "background": {
                "kind": "blank_canvas",
                "rgb": [255, 255, 255],
                "renderer_contract": "R3 DEFAULT_BACKGROUND_COLOR",
                "asset_id": None,
                "asset_ref": None,
                "asset_sha256": None,
                "asset_size_bytes": None,
            },
            "source_artifacts": [
                artifact("/synthetic/l0_result.json", "a" * 64),
                artifact("/synthetic/asset_manifest.json", "b" * 64),
                artifact("/synthetic/render.png", "1" * 64),
                artifact("/synthetic/r3_asset_manifest.json", "c" * 64),
            ],
            "background_array_sha256": "2" * 64,
            "metrics": metrics,
            "saliency": {"status": "skipped_explicit", "map_sha256": None},
        }
        mode = "evaluate"
        counts = {
            "selected_n": 1,
            "source_valid_n": 1,
            "source_skipped_n": 0,
            "evaluated_n": 1,
            "validated_only_n": 0,
        }
        metrics_aggregate = aggregate_metric_records([record])
    else:
        record = {
            "run_id": run_id,
            "sample_id": sample_id,
            "status": "source_skipped",
            "source_status": "failed",
            "reason": "synthetic source failure",
            "summary_entry": {
                "error_type": "SyntheticError",
                "message": "synthetic source failure",
                "sample_id": sample_id,
                "status": "failed",
            },
            "metrics": {},
            "source_artifacts": [],
        }
        mode = "validate-only"
        counts = {
            "selected_n": 1,
            "source_valid_n": 0,
            "source_skipped_n": 1,
            "evaluated_n": 0,
            "validated_only_n": 0,
        }
        metrics_aggregate = {}
    encoded_ids = json.dumps(
        [sample_id], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "evaluation_id": "test-evaluation",
        "created_at": "2026-07-12T00:00:00+00:00",
        "mode": mode,
        "command": "synthetic",
        "command_argv": ["synthetic"],
        "source_runs": [
            {
                "run_id": run_id,
                "run_dir": "/synthetic/run-1",
                "summary": artifact("/synthetic/a3_run_summary.json", "d" * 64),
                "manifest": artifact("/synthetic/run_manifest.json", "e" * 64),
                "sample_ids": artifact("/synthetic/sample_ids.json", "f" * 64),
                "summary_counts": {
                    "reported_total": 1,
                    "reported_completed": None,
                    "reported_failed": 0 if evaluated else 1,
                    "reported_sample_n": 1,
                    "completed_n": 1 if evaluated else 0,
                    "failed_n": 0 if evaluated else 1,
                    "other_status_n": 0,
                    "formal_complete": True,
                },
                "selection": {
                    "max_samples": None,
                    "selected_n": 1,
                    "formal_complete_run": True,
                },
                "observed_counts": counts,
            }
        ],
        "matched_samples": {
            "count": 1,
            "ordered_sample_ids": [sample_id],
            "ordered_sample_ids_sha256": hashlib.sha256(encoded_ids).hexdigest(),
        },
        "protocol_lineage": {
            "schema_version": SCHEMA_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "bbox_input": "A3 B0 left-top-width-height",
            "bbox_metric_frame": "xyxy, clipped to canvas",
            "validity_filter": {
                "minimum_canvas_fraction": 0.001,
                "comparison": "area >= threshold",
            },
            "alignment": "PKU layout-wide-min aggregation quirk",
            "overlay_denominator": "non-underlay element count",
            "underlay": "P-Full v1 has no legal underlay class; current A3 is N/A",
            "readability": "background-only float64 Sobel",
            "blank_background": {"renderer_contract": "R3", "rgb": [255, 255, 255]},
            "occlusion": {
                "mode": "skip",
                "detector": None,
                "sobel_fallback_forbidden": True,
            },
            "code_runtime_lineage": {
                "sources": {
                    "sega_metrics.py": artifact("/code/sega_metrics.py", "4" * 64),
                    "a3_sega_evaluator.py": artifact(
                        "/code/a3_sega_evaluator.py", "5" * 64
                    ),
                    "evaluate_a3_sega.py": artifact(
                        "/code/evaluate_a3_sega.py", "6" * 64
                    ),
                    "saliency_basnet_isnet.py": artifact(
                        "/code/saliency_basnet_isnet.py", "7" * 64
                    ),
                },
                "runtime": {
                    "python": {
                        "version": "3.9",
                        "implementation": "CPython",
                        "executable": "/python",
                    },
                    "platform": {
                        "system": "Linux",
                        "release": "synthetic",
                        "machine": "x86_64",
                        "platform": "Linux-synthetic",
                    },
                    "numpy": "1",
                    "cv2": "1",
                    "Pillow": "1",
                    "torch": "1",
                    "torchvision": "1",
                    "transformers": "1",
                    "rembg": "1",
                    "onnxruntime": "1",
                    "pydantic": "2",
                    "pooch": "1",
                    "onnxruntime_available_providers": ["CPUExecutionProvider"],
                },
            },
        },
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
        "evaluation_id": "test-evaluation",
        "runs": {
            run_id: {"sample_counts": counts, "metrics": metrics_aggregate}
        },
    }
    return manifest, [record], aggregate


def _synthetic_code_runtime():
    manifest, _, _ = _valid_sidecar_bundle()
    return copy.deepcopy(manifest["protocol_lineage"]["code_runtime_lineage"])


def _synthetic_detector_without_runtime():
    def artifact(path, sha):
        return {"path": path, "sha256": sha, "size_bytes": 1}

    revision = "a" * 40
    snapshot = "/models/BASNet/snapshot"
    return {
        "contract": "frozen BASNet + ISNet, pixel-wise maximum",
        "fusion": "pixelwise_max",
        "fail_closed": True,
        "network_downloads_allowed": False,
        "basnet": {
            "model_id": "creative-graphic-design/BASNet",
            "revision": revision,
            "load_contract": {
                "revision_argument": revision,
                "from_pretrained_path": snapshot,
                "local_files_only": True,
                "trust_remote_code": True,
                "force_download": False,
            },
            "snapshot_path": snapshot,
            "artifacts": {
                "config.json": artifact(f"{snapshot}/config.json", "1" * 64),
                "model.safetensors": artifact(
                    f"{snapshot}/model.safetensors", "2" * 64
                ),
                "configuration_basnet.py": artifact(
                    f"{snapshot}/configuration_basnet.py", "3" * 64
                ),
                "modeling_basnet.py": artifact(
                    f"{snapshot}/modeling_basnet.py", "4" * 64
                ),
            },
        },
        "isnet": {
            "model_id": "rembg/isnet-general-use",
            "artifact": {
                "path": "/models/isnet.onnx",
                "sha256": saliency_adapter._ISNET_EXPECTED_SHA256,
                "md5": saliency_adapter._ISNET_EXPECTED_MD5,
                "size_bytes": 178648008,
            },
            "provider": "CPUExecutionProvider",
            "download_path": "forbidden; direct verified ONNX bytes",
        },
        "pku_deviation": "ISNet replaces PFPN",
        "implementation_sha256": "5" * 64,
        "runtime_identity": None,
    }


def _validate_only_args(fixture: dict, tmp_path: Path, evaluation_id: str):
    return argparse.Namespace(
        run_dir=[fixture["run_dir"]],
        evaluation_id=evaluation_id,
        output_root=tmp_path / "output",
        saliency_mode=SALIENCY_MODE_SKIP,
        validate_only=True,
        max_samples=None,
    )


def _set_failed_sample_order(fixture: dict, sample_ids) -> None:
    run_dir = fixture["run_dir"]
    entries = [
        {"sample_id": sample_id, "status": "failed", "final": None, "stage_calls": 0}
        for sample_id in sample_ids
    ]
    _write_json(
        run_dir / "a3_run_summary.json",
        {"total": len(entries), "failed": len(entries), "samples": entries},
    )
    sample_ids_path = run_dir / "sample_ids.json"
    _write_json(sample_ids_path, list(sample_ids))
    manifest_path = run_dir / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sample_ids_snapshot"].update(
        {"count": len(entries), "sha256": sha256_file(sample_ids_path)}
    )
    _write_json(manifest_path, manifest)


def test_clip_filter_drives_underlay_na_and_blank_background():
    raw_layout = [
        (CLS_UNDERLAY, (0.0, 0.0, 0.1, 0.1)),  # 0.01 < 0.1% of 10x10
        (CLS_TEXT, (-2.0, -2.0, 8.0, 8.0)),
    ]
    layout, dropped = clip_and_filter_layout(raw_layout, 10, 10)
    assert dropped == 1
    assert layout == [(CLS_TEXT, (0.0, 0.0, 8.0, 8.0))]

    result = evaluate_sample(_blank_sample(layout), SALIENCY_MODE_SKIP)

    assert result["metrics"]["Und_l"]["status"] == "not_applicable"
    assert result["metrics"]["Und_l"]["value"] is None
    assert result["metrics"]["Und_s"]["value"] is None
    expected_white = np.full((10, 10, 3), 255, dtype=np.uint8)
    assert result["background_array_sha256"] == sha256_array(expected_white)
    assert result["metrics"]["Rea"]["value"] == pytest.approx(0.0)


def test_aggregate_counts_source_failed_rows_as_skipped():
    records = [
        _metric_row("ok-zero", 0.0),
        _metric_row("ok-one", 1.0),
        {
            "run_id": "run",
            "sample_id": "failed",
            "status": "source_skipped",
            "reason": "B0 failed",
            "metrics": {},
        },
    ]

    aggregate = aggregate_metric_records(records)

    assert aggregate["Ali"]["value"] == pytest.approx(0.5)
    assert aggregate["Ali"]["valid_n"] == 2
    assert aggregate["Ali"]["skipped_n"] == 1
    assert aggregate["Ali"]["zero_contribution_n"] == 1
    assert aggregate["Und_l"]["value"] is None
    assert aggregate["Und_l"]["applicable_n"] == 0
    assert aggregate["Und_l"]["not_applicable_n"] == 2
    assert aggregate["Und_l"]["skipped_n"] == 1


def test_load_run_summary_validates_manifest_snapshot_and_counts(tmp_path):
    fixture = _make_run(tmp_path)
    source, entries = load_run_summary(fixture["run_dir"])
    assert len(entries) == 1
    assert source["summary_counts"]["formal_complete"] is True
    assert source["summary_counts"]["completed_n"] == 1

    summary_path = fixture["run_dir"] / "a3_run_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["total"] = 2
    _write_json(summary_path, summary)
    with pytest.raises(A3EvaluationError, match="total"):
        load_run_summary(fixture["run_dir"])


def test_load_run_summary_rejects_failed_count_mismatch(tmp_path):
    fixture = _make_run(tmp_path)
    summary_path = fixture["run_dir"] / "a3_run_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["failed"] = 1
    _write_json(summary_path, summary)

    with pytest.raises(A3EvaluationError, match="failed"):
        load_run_summary(fixture["run_dir"])


def test_load_run_summary_rejects_snapshot_id_mismatch(tmp_path):
    fixture = _make_run(tmp_path)
    sample_ids_path = fixture["run_dir"] / "sample_ids.json"
    _write_json(sample_ids_path, ["different-sample"])
    manifest_path = fixture["run_dir"] / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sample_ids_snapshot"]["sha256"] = sha256_file(sample_ids_path)
    _write_json(manifest_path, manifest)

    with pytest.raises(A3EvaluationError, match="sample IDs|snapshot"):
        load_run_summary(fixture["run_dir"])


def test_formal_cli_rejects_nonterminal_summary(tmp_path):
    fixture = _make_run(tmp_path)
    summary_path = fixture["run_dir"] / "a3_run_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["samples"][0]["status"] = "pending"
    _write_json(summary_path, summary)
    output_root = tmp_path / "output"
    args = argparse.Namespace(
        run_dir=[fixture["run_dir"]],
        evaluation_id="formal-must-reject",
        output_root=output_root,
        saliency_mode=SALIENCY_MODE_SKIP,
        validate_only=True,
        max_samples=None,
    )

    with pytest.raises(A3EvaluationError, match="complete"):
        sega_cli.run(args)
    assert not output_root.exists()


@pytest.mark.parametrize(
    ("saliency", "message"),
    [
        (np.zeros((10, 10, 1), dtype=np.float32), "shape"),
        (np.full((10, 10), np.nan, dtype=np.float32), "NaN|finite"),
        (np.full((10, 10), -0.01, dtype=np.float32), r"\[0, 1\]|range"),
        (np.full((10, 10), 1.01, dtype=np.float32), r"\[0, 1\]|range"),
    ],
)
def test_saliency_contract_fails_closed(saliency, message):
    layout = [(CLS_IMAGE_LOGO, (0.0, 0.0, 10.0, 10.0))]
    with pytest.raises(A3EvaluationError, match=message):
        evaluate_sample(
            _blank_sample(layout),
            SALIENCY_MODE_FROZEN,
            saliency_fn=lambda _background, _shape: saliency,
        )


def test_extract_records_background_and_load_rechecks_hash(tmp_path):
    fixture = _make_run(tmp_path, with_background=True)
    sample = extract_b0_sample(
        fixture["run_dir"], fixture["run_id"], fixture["entry"]
    )
    background_path = fixture["background_path"].resolve()
    artifacts = {Path(item["path"]): item["sha256"] for item in sample["source_artifacts"]}
    assert artifacts[background_path] == sha256_file(background_path)

    _write_png(background_path, (200, 100, 50))
    result = evaluate_sample(sample, SALIENCY_MODE_SKIP)
    assert result["background_array_sha256"]
    with pytest.raises(A3EvaluationError, match="changed"):
        verify_source_artifacts_unchanged([result])


def test_extract_rejects_schema_invalid_sega_class_code(tmp_path):
    fixture = _make_run(tmp_path, with_background=False)
    pfull = json.loads(fixture["pfull_path"].read_text(encoding="utf-8"))
    pfull["assets"][0]["sega_class_code"] = CLS_UNDERLAY
    _write_json(fixture["pfull_path"], pfull)
    r3 = json.loads(fixture["r3_path"].read_text(encoding="utf-8"))
    r3["source_pfull_manifest_sha256"] = sha256_file(fixture["pfull_path"])
    _write_json(fixture["r3_path"], r3)

    with pytest.raises(A3EvaluationError, match="schema|extra"):
        extract_b0_sample(fixture["run_dir"], fixture["run_id"], fixture["entry"])


def test_extract_requires_completed_summary_final(tmp_path):
    fixture = _make_run(tmp_path)
    entry = dict(fixture["entry"])
    entry["final"] = None

    with pytest.raises(A3EvaluationError, match="lacks final"):
        extract_b0_sample(fixture["run_dir"], fixture["run_id"], entry)


def test_extract_requires_persisted_render_hash(tmp_path):
    fixture = _make_run(tmp_path)
    l0_path = (
        fixture["run_dir"]
        / "samples"
        / fixture["sample_id"]
        / "pipeline"
        / "l0_result.json"
    )
    l0 = json.loads(l0_path.read_text(encoding="utf-8"))
    selected = next(
        slot for slot in l0["bundle"]["slots"] if slot["slot_id"] == l0["b0_slot_id"]
    )
    selected["render_sha256"] = None
    _write_json(l0_path, l0)

    with pytest.raises(A3EvaluationError, match="render_sha256"):
        extract_b0_sample(
            fixture["run_dir"], fixture["run_id"], fixture["entry"]
        )


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "nonfinite"])
def test_extract_rejects_invalid_candidate_element_contract(tmp_path, mutation):
    fixture = _make_run(tmp_path)
    l0_path = (
        fixture["run_dir"]
        / "samples"
        / fixture["sample_id"]
        / "pipeline"
        / "l0_result.json"
    )
    l0 = json.loads(l0_path.read_text(encoding="utf-8"))
    selected = next(
        slot for slot in l0["bundle"]["slots"] if slot["slot_id"] == l0["b0_slot_id"]
    )
    elements = selected["candidate"]["elements"]
    if mutation == "duplicate":
        elements.append(dict(elements[0]))
    elif mutation == "missing":
        elements.clear()
    else:
        elements[0]["left"] = float("nan")
    _write_json(l0_path, l0)

    with pytest.raises(A3EvaluationError, match="finite|unique|exactly once"):
        extract_b0_sample(
            fixture["run_dir"], fixture["run_id"], fixture["entry"]
        )


def test_sidecar_is_non_overwriting(tmp_path):
    output_dir = tmp_path / "sidecar" / "evaluation"
    bundle = _valid_sidecar_bundle()
    sega_cli._write_results(output_dir, *bundle)
    original = (output_dir / "evaluation_manifest.json").read_bytes()

    with pytest.raises(A3EvaluationError, match="already exists"):
        sega_cli._write_results(output_dir, *bundle)

    assert (output_dir / "evaluation_manifest.json").read_bytes() == original


def test_sidecar_write_failure_cleans_staging_and_preserves_final_id(tmp_path, monkeypatch):
    output_dir = tmp_path / "sidecar" / "evaluation"
    bundle = _valid_sidecar_bundle()
    original_write_json = sega_cli._write_json
    calls = 0

    def fail_second_write(path, data):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic disk failure")
        original_write_json(path, data)

    with monkeypatch.context() as patch_context:
        patch_context.setattr(sega_cli, "_write_json", fail_second_write)
        with pytest.raises(A3EvaluationError, match="publish|write|disk"):
            sega_cli._write_results(output_dir, *bundle)

    assert not output_dir.exists()
    assert list(output_dir.parent.glob(".evaluation.staging-*")) == []

    # A failed staging write must not consume the final evaluation ID.
    sega_cli._write_results(output_dir, *bundle)
    assert (output_dir / "evaluation_manifest.json").is_file()


def test_sidecar_broken_symlink_consumes_id(tmp_path):
    output_dir = tmp_path / "sidecar" / "evaluation"
    output_dir.parent.mkdir(parents=True)
    output_dir.symlink_to(tmp_path / "missing-target", target_is_directory=True)

    with pytest.raises(A3EvaluationError, match="already exists"):
        sega_cli._write_results(output_dir, *_valid_sidecar_bundle())

    assert output_dir.is_symlink()
    assert list(output_dir.parent.glob(".evaluation.staging-*")) == []


def test_sidecar_publish_race_never_replaces_contender(tmp_path, monkeypatch):
    output_dir = tmp_path / "sidecar" / "evaluation"
    original_noreplace = sega_cli._rename_directory_noreplace

    def install_contender_then_publish(source, destination):
        destination.symlink_to(tmp_path / "missing-race-target", target_is_directory=True)
        original_noreplace(source, destination)

    monkeypatch.setattr(
        sega_cli, "_rename_directory_noreplace", install_contender_then_publish
    )
    with pytest.raises(A3EvaluationError, match="already exists"):
        sega_cli._write_results(output_dir, *_valid_sidecar_bundle())

    assert output_dir.is_symlink()
    assert list(output_dir.parent.glob(".evaluation.staging-*")) == []


@pytest.mark.parametrize(
    "case",
    ["nan", "inf", "unknown_status", "ok_none", "na_with_value", "missing_metric"],
)
def test_sidecar_rejects_invalid_metric_contract_and_cleans_staging(tmp_path, case):
    manifest, records, aggregate = copy.deepcopy(_valid_sidecar_bundle(evaluated=True))
    metrics = records[0]["metrics"]
    if case == "nan":
        metrics["Ali"]["value"] = float("nan")
    elif case == "inf":
        metrics["Ali"]["value"] = float("inf")
    elif case == "unknown_status":
        metrics["Ali"]["status"] = "unknown"
    elif case == "ok_none":
        metrics["Ali"]["value"] = None
    elif case == "na_with_value":
        metrics["Und_l"]["value"] = 0.0
    else:
        metrics.pop("Occ")
    output_dir = tmp_path / case / "evaluation"

    with pytest.raises(A3EvaluationError):
        sega_cli._write_results(output_dir, manifest, records, aggregate)

    assert not sega_cli._path_lexists(output_dir)
    if output_dir.parent.exists():
        assert list(output_dir.parent.glob(".evaluation.staging-*")) == []


def test_sidecar_rejects_aggregate_count_mismatch(tmp_path):
    manifest, records, aggregate = copy.deepcopy(_valid_sidecar_bundle(evaluated=True))
    aggregate["runs"]["run-1"]["sample_counts"]["selected_n"] = 2
    output_dir = tmp_path / "count-mismatch" / "evaluation"

    with pytest.raises(A3EvaluationError, match="counts|count"):
        sega_cli._write_results(output_dir, manifest, records, aggregate)

    assert not sega_cli._path_lexists(output_dir)


@pytest.mark.parametrize(
    "case",
    [
        "protocol_field",
        "code_artifact_hash",
        "cost",
        "write_policy",
        "source_run_artifact",
        "canvas_type",
        "element_count_type",
        "background_field",
        "saliency_field",
        "runtime_provider",
        "command_argv",
    ],
)
def test_strict_nested_bundle_contract_rejects_provenance_mutations(tmp_path, case):
    manifest, records, aggregate = copy.deepcopy(_valid_sidecar_bundle(evaluated=True))
    if case == "protocol_field":
        manifest["protocol_lineage"].pop("readability")
    elif case == "code_artifact_hash":
        manifest["protocol_lineage"]["code_runtime_lineage"]["sources"][
            "sega_metrics.py"
        ].pop("sha256")
    elif case == "cost":
        manifest["cost"]["model_downloads"] = 1
    elif case == "write_policy":
        manifest["write_policy"]["source_runs_read_only"] = False
    elif case == "source_run_artifact":
        manifest["source_runs"][0]["summary"].pop("size_bytes")
    elif case == "canvas_type":
        records[0]["canvas"]["width"] = "10"
    elif case == "element_count_type":
        records[0]["element_counts"]["raw"] = "0"
    elif case == "background_field":
        records[0]["background"].pop("asset_size_bytes")
    elif case == "saliency_field":
        records[0]["saliency"].pop("status")
    elif case == "runtime_provider":
        manifest["protocol_lineage"]["code_runtime_lineage"]["runtime"][
            "onnxruntime_available_providers"
        ] = []
    else:
        manifest["command_argv"] = ["different"]
    output_dir = tmp_path / case / "evaluation"

    with pytest.raises(A3EvaluationError):
        sega_cli._write_results(output_dir, manifest, records, aggregate)

    assert not sega_cli._path_lexists(output_dir)
    if output_dir.parent.exists():
        assert list(output_dir.parent.glob(".evaluation.staging-*")) == []


def test_empty_protocol_and_write_policy_fixtures_are_rejected(tmp_path):
    manifest, records, aggregate = copy.deepcopy(_valid_sidecar_bundle())
    manifest["protocol_lineage"] = {}
    manifest["write_policy"] = {}

    with pytest.raises(A3EvaluationError, match="manifest contract"):
        sega_cli._write_results(tmp_path / "evaluation", manifest, records, aggregate)


def test_frozen_evaluation_rejects_missing_detector_runtime(tmp_path):
    manifest, records, aggregate = copy.deepcopy(_valid_sidecar_bundle(evaluated=True))
    manifest["protocol_lineage"]["occlusion"] = {
        "mode": SALIENCY_MODE_FROZEN,
        "detector": _synthetic_detector_without_runtime(),
        "sobel_fallback_forbidden": True,
    }

    with pytest.raises(A3EvaluationError, match="detector|manifest contract"):
        sega_cli._write_results(tmp_path / "evaluation", manifest, records, aggregate)


def test_cli_rejects_cross_run_sample_id_mismatch_before_extraction(
    tmp_path, monkeypatch
):
    first = _make_run(tmp_path, run_id="a3-first", sample_id="sample-a")
    second = _make_run(tmp_path, run_id="a3-second", sample_id="sample-b")
    monkeypatch.setattr(sega_cli, "evaluation_code_runtime_lineage", lambda _path: {})
    args = argparse.Namespace(
        run_dir=[first["run_dir"], second["run_dir"]],
        evaluation_id="mismatch",
        output_root=tmp_path / "output",
        saliency_mode=SALIENCY_MODE_SKIP,
        validate_only=True,
        max_samples=None,
    )

    with pytest.raises(A3EvaluationError, match="identical sample IDs"):
        sega_cli.run(args)


def test_cli_rejects_cross_run_sample_order_mismatch_before_extraction(
    tmp_path, monkeypatch
):
    first = _make_run(tmp_path, run_id="a3-first")
    second = _make_run(tmp_path, run_id="a3-second")
    _set_failed_sample_order(first, ["sample-a", "sample-b"])
    _set_failed_sample_order(second, ["sample-b", "sample-a"])
    monkeypatch.setattr(sega_cli, "evaluation_code_runtime_lineage", lambda _path: {})
    args = argparse.Namespace(
        run_dir=[first["run_dir"], second["run_dir"]],
        evaluation_id="order-mismatch",
        output_root=tmp_path / "output",
        saliency_mode=SALIENCY_MODE_SKIP,
        validate_only=True,
        max_samples=None,
    )

    with pytest.raises(A3EvaluationError, match="identical sample IDs"):
        sega_cli.run(args)


def test_cli_fails_if_code_runtime_lineage_changes_before_publish(
    tmp_path, monkeypatch
):
    fixture = _make_run(tmp_path)
    calls = 0

    def changing_lineage(_path):
        nonlocal calls
        calls += 1
        return {"capture": calls}

    monkeypatch.setattr(sega_cli, "evaluation_code_runtime_lineage", changing_lineage)
    args = argparse.Namespace(
        run_dir=[fixture["run_dir"]],
        evaluation_id="code-race",
        output_root=tmp_path / "output",
        saliency_mode=SALIENCY_MODE_SKIP,
        validate_only=True,
        max_samples=None,
    )

    with pytest.raises(A3EvaluationError, match="source code|runtime identity"):
        sega_cli.run(args)
    assert not (tmp_path / "output").exists()


def test_cli_rehashes_detector_artifacts_after_evaluation(tmp_path, monkeypatch):
    fixture = _make_run(tmp_path)
    detector_calls = 0

    def changing_detector_lineage():
        nonlocal detector_calls
        detector_calls += 1
        return {"artifact_generation": detector_calls}

    real_evaluate_sample = evaluate_sample
    monkeypatch.setattr(sega_cli, "evaluation_code_runtime_lineage", lambda _path: {})
    monkeypatch.setattr(sega_cli, "frozen_detector_lineage", changing_detector_lineage)
    monkeypatch.setattr(
        sega_cli,
        "evaluate_sample",
        lambda extracted, _mode: real_evaluate_sample(extracted, SALIENCY_MODE_SKIP),
    )
    args = argparse.Namespace(
        run_dir=[fixture["run_dir"]],
        evaluation_id="detector-race",
        output_root=tmp_path / "output",
        saliency_mode=SALIENCY_MODE_FROZEN,
        validate_only=False,
        max_samples=None,
    )

    with pytest.raises(A3EvaluationError, match="detector artifacts changed"):
        sega_cli.run(args)
    assert detector_calls == 2
    assert not (tmp_path / "output").exists()


def test_source_mutation_after_read_once_capture_fails_at_final_publish(
    tmp_path, monkeypatch
):
    fixture = _make_run(tmp_path)
    original_load = a3_evaluator._load_json_captured
    mutated = False

    def capture_then_mutate(path):
        nonlocal mutated
        data, artifact = original_load(path)
        if path.name == "l0_result.json" and not mutated:
            path.write_bytes(path.read_bytes() + b" ")
            mutated = True
        return data, artifact

    monkeypatch.setattr(a3_evaluator, "_load_json_captured", capture_then_mutate)
    monkeypatch.setattr(
        sega_cli,
        "evaluation_code_runtime_lineage",
        lambda _path: _synthetic_code_runtime(),
    )
    args = _validate_only_args(fixture, tmp_path, "capture-window-mutation")
    final_dir = (
        args.output_root / PROTOCOL_VERSION / "capture-window-mutation"
    )

    with pytest.raises(A3EvaluationError, match="source artifact changed"):
        sega_cli.run(args)

    assert mutated is True
    assert not sega_cli._path_lexists(final_dir)
    assert list(final_dir.parent.glob(".capture-window-mutation.staging-*")) == []


def test_source_mutation_after_staging_roundtrip_cleans_staging(
    tmp_path, monkeypatch
):
    fixture = _make_run(tmp_path)
    render_path = (
        fixture["run_dir"]
        / "samples"
        / fixture["sample_id"]
        / "renders"
        / "r0_candidate_01.png"
    )
    original_roundtrip = sega_cli._verify_staging_round_trip

    def roundtrip_then_mutate(*args, **kwargs):
        original_roundtrip(*args, **kwargs)
        render_path.write_bytes(render_path.read_bytes() + b"mutated")

    monkeypatch.setattr(
        sega_cli, "_verify_staging_round_trip", roundtrip_then_mutate
    )
    monkeypatch.setattr(
        sega_cli,
        "evaluation_code_runtime_lineage",
        lambda _path: _synthetic_code_runtime(),
    )
    args = _validate_only_args(fixture, tmp_path, "staging-window-mutation")
    final_dir = args.output_root / PROTOCOL_VERSION / "staging-window-mutation"

    with pytest.raises(A3EvaluationError, match="source artifact changed"):
        sega_cli.run(args)

    assert not sega_cli._path_lexists(final_dir)
    assert list(final_dir.parent.glob(".staging-window-mutation.staging-*")) == []


def test_same_source_path_with_different_capture_hashes_fails_immediately():
    records = [
        {
            "source_artifacts": [
                {"path": "/same/path", "sha256": "a" * 64, "size_bytes": 1},
                {"path": "/same/path", "sha256": "b" * 64, "size_bytes": 1},
            ]
        }
    ]

    with pytest.raises(A3EvaluationError, match="same source path"):
        verify_source_artifacts_unchanged(records)


def test_cli_records_canonical_arguments_in_manifest(tmp_path, monkeypatch):
    fixture = _make_run(tmp_path)
    monkeypatch.setattr(
        sega_cli,
        "evaluation_code_runtime_lineage",
        lambda _path: _synthetic_code_runtime(),
    )
    args = _validate_only_args(fixture, tmp_path, "canonical-args")

    output_dir = sega_cli.run(args)
    manifest = json.loads(
        (output_dir / "evaluation_manifest.json").read_text(encoding="utf-8")
    )

    assert manifest["command_argv"][0] == sys.executable
    assert manifest["command_argv"][1] == str(Path(sega_cli.__file__).resolve())
    assert manifest["command"] == __import__("shlex").join(manifest["command_argv"])


@pytest.mark.parametrize("state", ["missing", "wrong"])
def test_isnet_invalid_artifact_fails_before_factory_or_pooch(
    tmp_path, monkeypatch, state
):
    import pooch
    import rembg

    u2net_home = tmp_path / "u2net"
    u2net_home.mkdir()
    if state == "wrong":
        (u2net_home / "isnet-general-use.onnx").write_bytes(b"wrong")
    calls = {"factory": 0, "pooch": 0}

    def factory(*_args, **_kwargs):
        calls["factory"] += 1
        raise AssertionError("rembg factory must not run")

    def retrieve(*_args, **_kwargs):
        calls["pooch"] += 1
        raise AssertionError("pooch must not run")

    monkeypatch.setenv("U2NET_HOME", str(u2net_home))
    monkeypatch.setattr(rembg, "new_session", factory)
    monkeypatch.setattr(pooch, "retrieve", retrieve)
    monkeypatch.setattr(saliency_adapter, "_ISNET_SESSION", None)
    monkeypatch.setattr(saliency_adapter, "_ISNET_RUNTIME_IDENTITY", None)

    with pytest.raises(RuntimeError, match="missing|hash mismatch"):
        saliency_adapter._load_isnet_session()
    assert calls == {"factory": 0, "pooch": 0}


def test_isnet_exact_session_records_runtime_provider_identity(monkeypatch):
    import onnxruntime

    artifact = {
        "path": "/verified/isnet-general-use.onnx",
        "sha256": saliency_adapter._ISNET_EXPECTED_SHA256,
        "md5": saliency_adapter._ISNET_EXPECTED_MD5,
        "size_bytes": 4,
    }
    calls = []

    class FakeInnerSession:
        def __init__(self, model_bytes, providers, sess_options):
            calls.append((model_bytes, providers, sess_options))

        @staticmethod
        def get_providers():
            return ["CPUExecutionProvider"]

    monkeypatch.setattr(
        saliency_adapter,
        "_read_verified_isnet_artifact",
        lambda: (artifact, b"onnx"),
    )
    monkeypatch.setattr(onnxruntime, "InferenceSession", FakeInnerSession)
    monkeypatch.setattr(
        onnxruntime, "get_available_providers", lambda: ["CPUExecutionProvider"]
    )
    monkeypatch.setattr(saliency_adapter, "_ISNET_SESSION", None)
    monkeypatch.setattr(saliency_adapter, "_ISNET_RUNTIME_IDENTITY", None)

    session = saliency_adapter._load_isnet_session()
    identity = saliency_adapter.detector_runtime_identity(require_loaded=False)["isnet"]

    assert session is not None
    assert identity["session_reported_name"] == "isnet-general-use"
    assert identity["session_class_module"] == "rembg.sessions.dis_general_use"
    assert identity["session_class_name"] == "DisSession"
    assert identity["active_providers"] == ["CPUExecutionProvider"]
    assert identity["verified_artifact"] == artifact
    assert calls[0][0] == b"onnx"
    assert calls[0][1] == ["CPUExecutionProvider"]


def test_basnet_load_binds_cached_revision_and_remote_code_files(tmp_path, monkeypatch):
    revision = "a" * 40
    repo = tmp_path / "hub" / "models--creative-graphic-design--BASNet"
    snapshot = repo / "snapshots" / revision
    snapshot.mkdir(parents=True)
    (repo / "refs").mkdir()
    (repo / "refs" / "main").write_text(revision, encoding="utf-8")
    for filename in saliency_adapter._BASNET_REQUIRED_FILES:
        (snapshot / filename).write_bytes(filename.encode("ascii"))
    u2net_home = tmp_path / "u2net"
    u2net_home.mkdir()
    (u2net_home / "isnet-general-use.onnx").write_bytes(b"isnet")
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.setenv("U2NET_HOME", str(u2net_home))
    monkeypatch.setattr(saliency_adapter, "_BASNET_MODEL", None)
    monkeypatch.setattr(saliency_adapter, "_BASNET_RUNTIME_IDENTITY", None)
    monkeypatch.setattr(
        saliency_adapter,
        "_verify_basnet_executed_code",
        lambda _model, _snapshot: {
            "configuration_basnet.py": {
                "executed_path": "/dynamic/configuration_basnet.py",
                "executed_sha256": "1" * 64,
                "authoritative_path": str(snapshot / "configuration_basnet.py"),
                "authoritative_sha256": "1" * 64,
            },
            "modeling_basnet.py": {
                "executed_path": "/dynamic/modeling_basnet.py",
                "executed_sha256": "2" * 64,
                "authoritative_path": str(snapshot / "modeling_basnet.py"),
                "authoritative_sha256": "2" * 64,
            },
        },
    )
    monkeypatch.setattr(
        saliency_adapter,
        "_verify_isnet_artifact",
        lambda: {
            "path": str(u2net_home / "isnet-general-use.onnx"),
            "sha256": "3" * 64,
            "md5": "4" * 32,
            "size_bytes": 5,
        },
    )

    calls = []

    class FakeModel:
        def eval(self):
            return self

    import transformers

    def fake_from_pretrained(model_id, **kwargs):
        calls.append((model_id, kwargs))
        return FakeModel()

    monkeypatch.setattr(transformers.AutoModel, "from_pretrained", fake_from_pretrained)
    saliency_adapter._load_basnet()

    assert calls[0][0] == str(snapshot.resolve())
    assert calls[0][1]["revision"] == revision
    assert calls[0][1]["local_files_only"] is True
    assert calls[0][1]["trust_remote_code"] is True
    assert calls[0][1]["force_download"] is False
    runtime = saliency_adapter.detector_runtime_identity(require_loaded=False)["basnet"]
    assert runtime["requested_revision"] == revision
    assert runtime["resolved_snapshot"] == str(snapshot.resolve())
    lineage = frozen_detector_lineage()
    assert set(lineage["basnet"]["artifacts"]) == set(
        saliency_adapter._BASNET_REQUIRED_FILES
    )
    assert lineage["basnet"]["load_contract"]["revision_argument"] == revision


@pytest.mark.parametrize("mismatch", [False, True])
def test_basnet_executed_dynamic_code_is_bound_to_snapshot(
    tmp_path, monkeypatch, mismatch
):
    snapshot = tmp_path / "snapshot"
    dynamic = tmp_path / "dynamic"
    snapshot.mkdir()
    dynamic.mkdir()
    config_source = "class BASNetConfig:\n    pass\n"
    model_source = "class BASNetModel:\n    pass\n"
    (snapshot / "configuration_basnet.py").write_text(
        config_source, encoding="utf-8"
    )
    (snapshot / "modeling_basnet.py").write_text(model_source, encoding="utf-8")
    (dynamic / "configuration_basnet.py").write_text(
        config_source, encoding="utf-8"
    )
    (dynamic / "modeling_basnet.py").write_text(
        model_source + ("# stale dynamic cache\n" if mismatch else ""),
        encoding="utf-8",
    )

    def load_module(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module

    config_module = load_module(
        f"dynamic_config_{mismatch}", dynamic / "configuration_basnet.py"
    )
    model_module = load_module(
        f"dynamic_model_{mismatch}", dynamic / "modeling_basnet.py"
    )
    model = model_module.BASNetModel()
    model.config = config_module.BASNetConfig()

    if mismatch:
        with pytest.raises(RuntimeError, match="differs from snapshot"):
            saliency_adapter._verify_basnet_executed_code(model, snapshot)
    else:
        executed = saliency_adapter._verify_basnet_executed_code(model, snapshot)
        assert executed["configuration_basnet.py"]["executed_path"] == str(
            (dynamic / "configuration_basnet.py").resolve()
        )
        assert (
            executed["modeling_basnet.py"]["executed_sha256"]
            == executed["modeling_basnet.py"]["authoritative_sha256"]
        )


def test_basnet_primary_unwrap_selects_refined_dout():
    import torch

    primary = torch.ones((1, 1, 2, 2))
    secondary = torch.zeros((1, 1, 2, 2))
    output = SimpleNamespace(
        activated=SimpleNamespace(dout=primary, d1=secondary, db=secondary)
    )

    assert saliency_adapter._unwrap_basnet_primary(output, torch) is primary
