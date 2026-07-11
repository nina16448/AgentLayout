"""Read-only, zero-LLM SEGA/PKU metric evaluation for persisted A3 runs.

The evaluator consumes the selected B0 candidate recorded in each A3
``pipeline/l0_result.json``.  It never renders a new candidate and never writes
inside a run directory.  Results are written by the companion CLI to a
versioned evaluation directory.

Underlay metrics are deliberately conservative: P-Full v1 has no legal
underlay class. Raster assets are therefore *not* guessed to be underlays and
current A3 samples report Und_l/Und_s as not applicable.
"""
from __future__ import annotations

import hashlib
import io
import importlib.metadata
import json
import math
import platform
import shlex
import statistics
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Literal, Optional, Sequence, Tuple

import numpy as np
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator

from metagpt.ext.agentlayout.evaluation.sega_metrics import (
    CLS_IMAGE_LOGO,
    CLS_TEXT,
    CLS_UNDERLAY,
    Layout,
    metric_alignment,
    metric_occlusion,
    metric_overlay,
    metric_readability,
    metric_underlay_loose,
    metric_underlay_strict,
    to_xyxy,
)

SCHEMA_VERSION = "a3.sega-evaluation.v1"
PROTOCOL_VERSION = "a3.sega-pku-protocol.v1"
VALID_AREA_FRACTION = 0.001
METRIC_KEYS = ("Ali", "Ove", "Und_l", "Und_s", "Rea", "Occ")
TEXT_MEDIA_TYPES = frozenset({"text", "text_bitmap"})
BLANK_BACKGROUND_RGB = (255, 255, 255)
SALIENCY_MODE_FROZEN = "basnet-isnet"
SALIENCY_MODE_SKIP = "skip"


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, protected_namespaces=(), populate_by_name=True
    )


class MetricValue(_StrictModel):
    status: Literal["ok", "not_applicable", "skipped"]
    value: Optional[float]
    reason: Optional[str]

    @model_validator(mode="after")
    def validate_status_value(self):
        if self.status == "ok":
            if self.value is None or not math.isfinite(self.value):
                raise ValueError("ok metric requires a finite value")
        elif self.value is not None:
            raise ValueError(f"{self.status} metric must have a null value")
        return self


class SixMetrics(_StrictModel):
    Ali: MetricValue
    Ove: MetricValue
    Und_l: MetricValue
    Und_s: MetricValue
    Rea: MetricValue
    Occ: MetricValue


class AggregateMetric(_StrictModel):
    value: Optional[float]
    applicable_n: int = Field(ge=0)
    valid_n: int = Field(ge=0)
    skipped_n: int = Field(ge=0)
    metric_skipped_n: int = Field(ge=0)
    source_skipped_n: int = Field(ge=0)
    not_applicable_n: int = Field(ge=0)
    zero_contribution_n: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self):
        if self.skipped_n != self.metric_skipped_n + self.source_skipped_n:
            raise ValueError("skipped_n must equal metric_skipped_n + source_skipped_n")
        if self.applicable_n != self.valid_n + self.metric_skipped_n:
            raise ValueError("applicable_n must equal valid_n + metric_skipped_n")
        if self.zero_contribution_n > self.valid_n:
            raise ValueError("zero_contribution_n cannot exceed valid_n")
        if self.valid_n == 0:
            if self.value is not None:
                raise ValueError("aggregate without valid rows must have a null value")
        elif self.value is None or not math.isfinite(self.value):
            raise ValueError("aggregate with valid rows requires a finite value")
        return self


class SixAggregates(_StrictModel):
    Ali: AggregateMetric
    Ove: AggregateMetric
    Und_l: AggregateMetric
    Und_s: AggregateMetric
    Rea: AggregateMetric
    Occ: AggregateMetric


class ArtifactRecord(_StrictModel):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class CanvasRecord(_StrictModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class ElementRecord(_StrictModel):
    asset_id: str = Field(min_length=1)
    class_code: Literal[1, 2, 3]
    class_source: str = Field(min_length=1)
    bbox_lwh_raw: List[float] = Field(min_length=4, max_length=4)
    bbox_xyxy_clipped: Optional[List[float]] = Field(
        default=None, min_length=4, max_length=4
    )
    valid: bool

    @model_validator(mode="after")
    def validate_bbox_state(self):
        if self.valid != (self.bbox_xyxy_clipped is not None):
            raise ValueError("valid must agree with bbox_xyxy_clipped presence")
        for value in self.bbox_lwh_raw + (self.bbox_xyxy_clipped or []):
            if not math.isfinite(value):
                raise ValueError("element bbox values must be finite")
        return self


class ElementCountsRecord(_StrictModel):
    raw: int = Field(ge=0)
    valid: int = Field(ge=0)
    invalid_below_0_1pct: int = Field(ge=0)

    @model_validator(mode="after")
    def conserve_elements(self):
        if self.raw != self.valid + self.invalid_below_0_1pct:
            raise ValueError("raw element count must equal valid + invalid")
        return self


class UnderlayProtocolRecord(_StrictModel):
    label_source: str = Field(min_length=1)
    applicable: bool
    valid_underlay_ids: List[str]
    raster_inference_forbidden: Literal[True]

    @model_validator(mode="after")
    def validate_applicability(self):
        if self.applicable != bool(self.valid_underlay_ids):
            raise ValueError("underlay applicability must agree with valid IDs")
        return self


class BackgroundRecord(_StrictModel):
    kind: Literal["blank_canvas", "asset"]
    rgb: Optional[List[int]]
    renderer_contract: str = Field(min_length=1)
    asset_id: Optional[str]
    asset_ref: Optional[str]
    asset_sha256: Optional[str]
    asset_size_bytes: Optional[int]
    pfull_asset_sha256: Optional[str] = None
    r3_asset_sha256: Optional[str] = None

    @model_validator(mode="after")
    def validate_background_variant(self):
        hashes = (
            self.asset_sha256,
            self.pfull_asset_sha256,
            self.r3_asset_sha256,
        )
        if self.kind == "blank_canvas":
            if self.rgb != list(BLANK_BACKGROUND_RGB):
                raise ValueError("blank canvas must use frozen RGB")
            if any(
                value is not None
                for value in (
                    self.asset_id,
                    self.asset_ref,
                    self.asset_sha256,
                    self.asset_size_bytes,
                    self.pfull_asset_sha256,
                    self.r3_asset_sha256,
                )
            ):
                raise ValueError("blank canvas cannot carry asset provenance")
        else:
            if self.rgb is not None:
                raise ValueError("asset background RGB must be null")
            if not self.asset_id or not self.asset_ref or self.asset_size_bytes is None:
                raise ValueError("asset background provenance is incomplete")
            if any(value is None for value in hashes):
                raise ValueError("asset background hashes are required")
            if any(
                not isinstance(value, str)
                or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)
                for value in hashes
            ):
                raise ValueError("asset background hashes must be SHA-256")
            if len(set(hashes)) != 1:
                raise ValueError("asset background producer hashes must agree")
        return self


class SaliencyRecord(_StrictModel):
    status: Literal["computed", "skipped_explicit"]
    map_sha256: Optional[str]

    @model_validator(mode="after")
    def validate_map_hash(self):
        if self.status == "computed":
            if (
                not isinstance(self.map_sha256, str)
                or len(self.map_sha256) != 64
                or any(char not in "0123456789abcdef" for char in self.map_sha256)
            ):
                raise ValueError("computed saliency requires a SHA-256 map hash")
        elif self.map_sha256 is not None:
            raise ValueError("skipped saliency cannot have a map hash")
        return self


class CompletedSampleBase(_StrictModel):
    run_id: str = Field(min_length=1)
    sample_id: str = Field(min_length=1)
    canvas: CanvasRecord
    b0_slot_id: str = Field(min_length=1)
    b0_render_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    elements: List[ElementRecord]
    element_counts: ElementCountsRecord
    underlay_protocol: UnderlayProtocolRecord
    background: BackgroundRecord
    source_artifacts: List[ArtifactRecord] = Field(min_length=4)

    @model_validator(mode="after")
    def validate_completed_sample(self):
        if self.element_counts.raw != len(self.elements):
            raise ValueError("element_counts.raw must equal element rows")
        if self.element_counts.valid != sum(element.valid for element in self.elements):
            raise ValueError("element_counts.valid must equal valid element rows")
        paths = [artifact.path for artifact in self.source_artifacts]
        if len(paths) != len(set(paths)):
            raise ValueError("source artifact paths must be unique per sample")
        expected_artifact_n = 4 if self.background.kind == "blank_canvas" else 5
        if len(self.source_artifacts) != expected_artifact_n:
            raise ValueError("completed sample source artifact set is incomplete")
        hashes = {artifact.sha256 for artifact in self.source_artifacts}
        if self.b0_render_sha256 not in hashes:
            raise ValueError("B0 render hash is absent from source artifacts")
        if self.background.kind == "asset":
            by_path = {artifact.path: artifact for artifact in self.source_artifacts}
            background_artifact = by_path.get(self.background.asset_ref or "")
            if (
                background_artifact is None
                or background_artifact.sha256 != self.background.asset_sha256
                or background_artifact.size_bytes != self.background.asset_size_bytes
            ):
                raise ValueError("background artifact provenance mismatch")
        return self


class EvaluatedSampleRecord(CompletedSampleBase):
    status: Literal["evaluated"]
    background_array_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    metrics: SixMetrics
    saliency: SaliencyRecord


class ValidatedSampleRecord(CompletedSampleBase):
    status: Literal["validated"]
    metrics: Dict[str, Any]

    @model_validator(mode="after")
    def metrics_must_be_empty(self):
        if self.metrics:
            raise ValueError("validated-only metrics must be empty")
        return self


class FailedSummaryEntry(_StrictModel):
    error_type: str = Field(min_length=1)
    message: str = Field(min_length=1)
    sample_id: str = Field(min_length=1)
    status: Literal["failed"]


class SourceSkippedRecord(_StrictModel):
    run_id: str = Field(min_length=1)
    sample_id: str = Field(min_length=1)
    status: Literal["source_skipped"]
    source_status: Literal["failed"]
    reason: str = Field(min_length=1)
    summary_entry: FailedSummaryEntry
    metrics: Dict[str, Any]
    source_artifacts: List[Any]

    @model_validator(mode="after")
    def validate_skipped(self):
        if self.metrics or self.source_artifacts:
            raise ValueError("source-skipped metrics/artifacts must be empty")
        if self.sample_id != self.summary_entry.sample_id:
            raise ValueError("source-skipped sample ID mismatch")
        return self


class RunCounts(_StrictModel):
    selected_n: int = Field(ge=0)
    source_valid_n: int = Field(ge=0)
    source_skipped_n: int = Field(ge=0)
    evaluated_n: int = Field(ge=0)
    validated_only_n: int = Field(ge=0)

    @model_validator(mode="after")
    def conserve(self):
        if self.selected_n != self.source_valid_n + self.source_skipped_n:
            raise ValueError("selected count is not conserved")
        if self.source_valid_n != self.evaluated_n + self.validated_only_n:
            raise ValueError("source-valid count is not conserved")
        return self


class SummaryCounts(_StrictModel):
    reported_total: int = Field(ge=0)
    reported_completed: Optional[int] = Field(default=None, ge=0)
    reported_failed: int = Field(ge=0)
    reported_sample_n: int = Field(ge=0)
    completed_n: int = Field(ge=0)
    failed_n: int = Field(ge=0)
    other_status_n: int = Field(ge=0)
    formal_complete: bool

    @model_validator(mode="after")
    def conserve(self):
        if self.reported_total != self.reported_sample_n:
            raise ValueError("reported sample count mismatch")
        if self.reported_sample_n != self.completed_n + self.failed_n + self.other_status_n:
            raise ValueError("summary terminal counts are not conserved")
        if self.reported_failed != self.failed_n:
            raise ValueError("reported failed count mismatch")
        if self.reported_completed is not None and self.reported_completed != self.completed_n:
            raise ValueError("reported completed count mismatch")
        if self.formal_complete != (self.other_status_n == 0):
            raise ValueError("formal_complete flag mismatch")
        return self


class RunSelection(_StrictModel):
    max_samples: Optional[int] = Field(default=None, gt=0)
    selected_n: int = Field(ge=0)
    formal_complete_run: bool


class SourceRunRecord(_StrictModel):
    run_id: str = Field(min_length=1)
    run_dir: str = Field(min_length=1)
    summary: ArtifactRecord
    manifest: ArtifactRecord
    sample_ids: ArtifactRecord
    summary_counts: SummaryCounts
    selection: RunSelection
    observed_counts: RunCounts

    @model_validator(mode="after")
    def validate_selection(self):
        if self.selection.selected_n != self.observed_counts.selected_n:
            raise ValueError("source-run selection/observed count mismatch")
        if self.selection.selected_n > self.summary_counts.reported_total:
            raise ValueError("selected rows exceed source summary total")
        expected_formal = (
            self.selection.max_samples is None and self.summary_counts.formal_complete
        )
        if self.selection.formal_complete_run != expected_formal:
            raise ValueError("formal complete selection flag mismatch")
        return self


class MatchedSamplesRecord(_StrictModel):
    count: int = Field(ge=0)
    ordered_sample_ids: List[str]
    ordered_sample_ids_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PythonRuntime(_StrictModel):
    version: str = Field(min_length=1)
    implementation: str = Field(min_length=1)
    executable: str = Field(min_length=1)


class PlatformRuntime(_StrictModel):
    system: str = Field(min_length=1)
    release: str = Field(min_length=1)
    machine: str = Field(min_length=1)
    platform: str = Field(min_length=1)


class DependencyRuntime(_StrictModel):
    python: PythonRuntime
    platform: PlatformRuntime
    numpy: str = Field(min_length=1)
    cv2: str = Field(min_length=1)
    Pillow: str = Field(min_length=1)
    torch: str = Field(min_length=1)
    torchvision: str = Field(min_length=1)
    transformers: str = Field(min_length=1)
    rembg: str = Field(min_length=1)
    onnxruntime: str = Field(min_length=1)
    pydantic: str = Field(min_length=1)
    pooch: str = Field(min_length=1)
    onnxruntime_available_providers: List[str] = Field(min_length=1)


class CodeSources(_StrictModel):
    sega_metrics_py: ArtifactRecord = Field(alias="sega_metrics.py")
    a3_sega_evaluator_py: ArtifactRecord = Field(alias="a3_sega_evaluator.py")
    evaluate_a3_sega_py: ArtifactRecord = Field(alias="evaluate_a3_sega.py")
    saliency_basnet_isnet_py: ArtifactRecord = Field(alias="saliency_basnet_isnet.py")


class CodeRuntimeLineage(_StrictModel):
    sources: CodeSources
    runtime: DependencyRuntime


class BasnetArtifacts(_StrictModel):
    config_json: ArtifactRecord = Field(alias="config.json")
    model_safetensors: ArtifactRecord = Field(alias="model.safetensors")
    configuration_basnet_py: ArtifactRecord = Field(alias="configuration_basnet.py")
    modeling_basnet_py: ArtifactRecord = Field(alias="modeling_basnet.py")


class BasnetLoadContract(_StrictModel):
    revision_argument: str = Field(min_length=1)
    from_pretrained_path: str = Field(min_length=1)
    local_files_only: Literal[True]
    trust_remote_code: Literal[True]
    force_download: Literal[False]


class BasnetLineage(_StrictModel):
    model_id: Literal["creative-graphic-design/BASNet"]
    revision: str = Field(min_length=1)
    load_contract: BasnetLoadContract
    snapshot_path: str = Field(min_length=1)
    artifacts: BasnetArtifacts


class IsnetArtifact(_StrictModel):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    md5: str = Field(pattern=r"^[0-9a-f]{32}$")
    size_bytes: int = Field(gt=0)


class IsnetLineage(_StrictModel):
    model_id: Literal["rembg/isnet-general-use"]
    artifact: IsnetArtifact
    provider: Literal["CPUExecutionProvider"]
    download_path: Literal["forbidden; direct verified ONNX bytes"]


class ExecutedCodeRecord(_StrictModel):
    executed_path: str = Field(min_length=1)
    executed_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    authoritative_path: str = Field(min_length=1)
    authoritative_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def hashes_agree(self):
        if self.executed_sha256 != self.authoritative_sha256:
            raise ValueError("executed and authoritative code hashes differ")
        return self


class BasnetExecutedCode(_StrictModel):
    configuration_basnet_py: ExecutedCodeRecord = Field(alias="configuration_basnet.py")
    modeling_basnet_py: ExecutedCodeRecord = Field(alias="modeling_basnet.py")


class BasnetRuntimeIdentity(_StrictModel):
    model_id: Literal["creative-graphic-design/BASNet"]
    requested_revision: str = Field(min_length=1)
    resolved_snapshot: str = Field(min_length=1)
    from_pretrained_path: str = Field(min_length=1)
    local_files_only: Literal[True]
    trust_remote_code: Literal[True]
    force_download: Literal[False]
    model_class_module: str = Field(min_length=1)
    model_class_name: str = Field(min_length=1)
    config_class_module: str = Field(min_length=1)
    config_class_name: str = Field(min_length=1)
    torch_version: str = Field(min_length=1)
    transformers_version: str = Field(min_length=1)
    executed_code: BasnetExecutedCode


class IsnetRuntimeIdentity(_StrictModel):
    requested_model_name: Literal["isnet-general-use"]
    session_model_name: Literal["isnet-general-use"]
    session_reported_name: Literal["isnet-general-use"]
    session_class_module: Literal["rembg.sessions.dis_general_use"]
    session_class_name: Literal["DisSession"]
    rembg_version: str = Field(min_length=1)
    onnxruntime_version: str = Field(min_length=1)
    requested_providers: List[Literal["CPUExecutionProvider"]]
    active_providers: List[Literal["CPUExecutionProvider"]]
    available_providers: List[str] = Field(min_length=1)
    verified_artifact: IsnetArtifact
    session_construction: Literal[
        "direct verified bytes; downloader bypassed"
    ]


class DetectorRuntimeIdentity(_StrictModel):
    basnet: BasnetRuntimeIdentity
    isnet: IsnetRuntimeIdentity


class DetectorLineage(_StrictModel):
    contract: Literal["frozen BASNet + ISNet, pixel-wise maximum"]
    fusion: Literal["pixelwise_max"]
    fail_closed: Literal[True]
    network_downloads_allowed: Literal[False]
    basnet: BasnetLineage
    isnet: IsnetLineage
    pku_deviation: str = Field(min_length=1)
    implementation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_identity: Optional[DetectorRuntimeIdentity] = None


class OcclusionProtocol(_StrictModel):
    mode: Literal["basnet-isnet", "skip"]
    detector: Optional[DetectorLineage]
    sobel_fallback_forbidden: Literal[True]


class ValidityFilterProtocol(_StrictModel):
    minimum_canvas_fraction: float
    comparison: Literal["area >= threshold"]

    @model_validator(mode="after")
    def frozen_threshold(self):
        if self.minimum_canvas_fraction != VALID_AREA_FRACTION:
            raise ValueError("validity threshold differs from frozen protocol")
        return self


class BlankBackgroundProtocol(_StrictModel):
    renderer_contract: Literal["R3"]
    rgb: List[int]

    @model_validator(mode="after")
    def frozen_rgb(self):
        if self.rgb != list(BLANK_BACKGROUND_RGB):
            raise ValueError("protocol blank background RGB mismatch")
        return self


class ProtocolLineageRecord(_StrictModel):
    schema_version: Literal["a3.sega-evaluation.v1"]
    protocol_version: Literal["a3.sega-pku-protocol.v1"]
    bbox_input: str = Field(min_length=1)
    bbox_metric_frame: str = Field(min_length=1)
    validity_filter: ValidityFilterProtocol
    alignment: str = Field(min_length=1)
    overlay_denominator: str = Field(min_length=1)
    underlay: str = Field(min_length=1)
    readability: str = Field(min_length=1)
    blank_background: BlankBackgroundProtocol
    occlusion: OcclusionProtocol
    code_runtime_lineage: CodeRuntimeLineage


class WritePolicyRecord(_StrictModel):
    source_runs_read_only: Literal[True]
    output_is_versioned_sidecar: Literal[True]
    existing_output_overwrite: Literal[False]
    atomic_staging_publish: Literal[True]
    atomic_no_replace_publish: Literal["renameat2(RENAME_NOREPLACE)"]


class CostRecord(_StrictModel):
    llm_api_calls: int
    llm_cost_usd: float
    model_downloads: int

    @model_validator(mode="after")
    def must_be_zero(self):
        if self.llm_api_calls != 0 or self.llm_cost_usd != 0.0 or self.model_downloads != 0:
            raise ValueError("zero-cost evaluator must record zero calls/cost/downloads")
        return self


class EvaluationManifestRecord(_StrictModel):
    schema_version: Literal["a3.sega-evaluation.v1"]
    protocol_version: Literal["a3.sega-pku-protocol.v1"]
    evaluation_id: str = Field(min_length=1)
    created_at: str = Field(min_length=1)
    mode: Literal["evaluate", "validate-only"]
    command: str = Field(min_length=1)
    command_argv: List[str] = Field(min_length=1)
    source_runs: List[SourceRunRecord] = Field(min_length=1)
    matched_samples: MatchedSamplesRecord
    protocol_lineage: ProtocolLineageRecord
    write_policy: WritePolicyRecord
    cost: CostRecord


class A3EvaluationError(RuntimeError):
    """Raised when persisted A3 evidence is missing, inconsistent, or mutated."""


def _require_finite_tree(value: Any, context: str) -> None:
    """Reject JSON-compatible trees containing NaN or infinity."""
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise A3EvaluationError(f"{context} contains a non-finite number")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _require_finite_tree(item, f"{context}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _require_finite_tree(item, f"{context}[{index}]")
        return


def _finite_number(value: Any, context: str) -> float:
    if isinstance(value, bool):
        raise A3EvaluationError(f"{context} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise A3EvaluationError(f"{context} must be a finite number") from exc
    if not math.isfinite(result):
        raise A3EvaluationError(f"{context} must be a finite number")
    return result


def sha256_file(path: Path) -> str:
    """Return the SHA-256 of a file without changing it."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_array(array: np.ndarray) -> str:
    """Hash array dtype, shape, and contiguous bytes for saliency lineage."""
    contiguous = np.ascontiguousarray(array)
    digest = hashlib.sha256()
    digest.update(str(contiguous.dtype).encode("ascii"))
    digest.update(json.dumps(list(contiguous.shape)).encode("ascii"))
    digest.update(contiguous.tobytes())
    return digest.hexdigest()


def _capture_file(path: Path) -> Tuple[bytes, Dict[str, Any]]:
    """Read source bytes once and derive every capture field from those bytes."""
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise A3EvaluationError(f"cannot capture required artifact {path}: {exc}") from exc
    return payload, {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }


def _load_json_captured(path: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    payload, artifact = _capture_file(path)
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise A3EvaluationError(f"cannot read JSON artifact {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise A3EvaluationError(f"JSON artifact must contain an object: {path}")
    return data, artifact


def _load_json_list_captured(path: Path) -> Tuple[List[Any], Dict[str, Any]]:
    payload, artifact = _capture_file(path)
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise A3EvaluationError(f"cannot read JSON artifact {path}: {exc}") from exc
    if not isinstance(data, list):
        raise A3EvaluationError(f"JSON artifact must contain an array: {path}")
    return data, artifact


def _required_nonnegative_int(data: Dict[str, Any], key: str, context: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise A3EvaluationError(f"{context} {key} must be a non-negative integer")
    return value


def _resolve_artifact_ref(ref: str, run_dir: Path) -> Path:
    path = Path(ref).expanduser()
    if not path.is_absolute():
        path = run_dir / path
    return path.resolve()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def assert_output_is_sidecar(output_dir: Path, run_dirs: Sequence[Path]) -> None:
    """Reject output paths inside the A3 source run tree."""
    resolved_output = output_dir.resolve()
    canonical_runs_root = (
        Path(__file__).resolve().parents[4] / "layout_agent" / "runs" / "a3"
    )
    if _is_relative_to(resolved_output, canonical_runs_root.resolve()):
        raise A3EvaluationError(
            "evaluation output must be outside layout_agent/runs/a3: "
            f"{resolved_output}"
        )
    for run_dir in run_dirs:
        resolved_run = run_dir.resolve()
        if _is_relative_to(resolved_output, resolved_run):
            raise A3EvaluationError(
                "evaluation output must be outside every source run directory: "
                f"{resolved_output}"
            )


def clip_and_filter_layout(
    layout: Layout,
    canvas_w: float,
    canvas_h: float,
    area_fraction: float = VALID_AREA_FRACTION,
) -> Tuple[Layout, int]:
    """Clip xyxy boxes to canvas, then remove intersections below 0.1%.

    The returned layout contains clipped boxes, so every downstream metric
    receives one canonical canvas coordinate frame.  Boxes exactly at the
    threshold remain valid, matching PKU's ``area < threshold`` rejection.
    """
    if canvas_w <= 0 or canvas_h <= 0:
        raise A3EvaluationError(f"invalid canvas: {canvas_w}x{canvas_h}")
    threshold = float(area_fraction) * float(canvas_w) * float(canvas_h)
    kept: Layout = []
    dropped = 0
    for cls, (xl, yl, xr, yr) in layout:
        cxl = min(float(canvas_w), max(0.0, float(xl)))
        cyl = min(float(canvas_h), max(0.0, float(yl)))
        cxr = min(float(canvas_w), max(0.0, float(xr)))
        cyr = min(float(canvas_h), max(0.0, float(yr)))
        area = max(0.0, cxr - cxl) * max(0.0, cyr - cyl)
        if area < threshold:
            dropped += 1
            continue
        kept.append((int(cls), (cxl, cyl, cxr, cyr)))
    return kept, dropped


def _asset_class(asset: Dict[str, Any]) -> Tuple[int, str]:
    """Map a valid P-Full v1 asset without inventing an underlay class."""
    media_type = str(asset.get("media_type") or "").lower()
    semantic_hint = str(asset.get("semantic_hint") or "").lower()
    if media_type in TEXT_MEDIA_TYPES or semantic_hint in TEXT_MEDIA_TYPES:
        return CLS_TEXT, "pfull.text-media"
    return CLS_IMAGE_LOGO, "pfull.placeable-raster"


def _background_descriptor(
    run_dir: Path,
    pfull: Dict[str, Any],
    r3: Dict[str, Any],
) -> Tuple[Dict[str, Any], Optional[bytes], Optional[Dict[str, Any]]]:
    background_id = pfull.get("background_asset_id")
    if not background_id:
        return (
            {
                "kind": "blank_canvas",
                "rgb": list(BLANK_BACKGROUND_RGB),
                "renderer_contract": "R3 DEFAULT_BACKGROUND_COLOR",
                "asset_id": None,
                "asset_ref": None,
                "asset_sha256": None,
                "asset_size_bytes": None,
            },
            None,
            None,
        )

    asset = next(
        (item for item in r3.get("assets", []) if item.get("asset_id") == background_id),
        None,
    )
    if asset is None:
        raise A3EvaluationError(f"background asset {background_id!r} is absent from manifest")
    ref = asset.get("asset_ref")
    if not ref:
        raise A3EvaluationError(f"background asset {background_id!r} has no asset_ref")
    path = _resolve_artifact_ref(str(ref), run_dir)
    payload, captured_artifact = _capture_file(path)
    actual_sha = captured_artifact["sha256"]
    pfull_asset = next(
        (item for item in pfull.get("assets", []) if item.get("asset_id") == background_id),
        None,
    )
    if pfull_asset is None:
        raise A3EvaluationError(f"background asset {background_id!r} is absent from P-Full")
    r3_sha = str(asset.get("sha256") or "")
    pfull_sha = str(pfull_asset.get("sha256") or "")
    if not r3_sha or not pfull_sha:
        raise A3EvaluationError(
            f"background asset {background_id!r} lacks a producer manifest hash"
        )
    if r3_sha != actual_sha:
        raise A3EvaluationError(
            f"R3 background hash mismatch for {background_id}: {actual_sha} != {r3_sha}"
        )
    if pfull_sha != actual_sha:
        raise A3EvaluationError(
            f"P-Full background hash mismatch for {background_id}: "
            f"{actual_sha} != {pfull_sha}"
        )
    return (
        {
            "kind": "asset",
            "rgb": None,
            "renderer_contract": "R3 background_asset_ref",
            "asset_id": background_id,
            "asset_ref": captured_artifact["path"],
            "asset_sha256": actual_sha,
            "asset_size_bytes": captured_artifact["size_bytes"],
            "pfull_asset_sha256": pfull_sha,
            "r3_asset_sha256": r3_sha,
        },
        payload,
        captured_artifact,
    )


def _validate_source_contracts(
    l0: Dict[str, Any],
    pfull: Dict[str, Any],
    r3: Dict[str, Any],
    pfull_sha256: str,
) -> None:
    """Validate persisted JSON against the schemas that produced A3 runs."""
    from pydantic import ValidationError

    from metagpt.ext.agentlayout.a3_pipeline import A3L0Result
    from metagpt.ext.agentlayout.tools.pfull_preprocessor import PFullAssetManifest
    from metagpt.ext.agentlayout.tools.text_bitmap_normalizer import R3AssetManifest

    try:
        A3L0Result.model_validate(l0)
        PFullAssetManifest.model_validate(pfull)
        R3AssetManifest.model_validate(r3)
    except ValidationError as exc:
        raise A3EvaluationError(f"source artifact violates its A3 schema: {exc}") from exc

    expected_pfull_sha = str(r3.get("source_pfull_manifest_sha256") or "")
    if expected_pfull_sha != pfull_sha256:
        raise A3EvaluationError(
            "R3 source_pfull_manifest_sha256 mismatch: "
            f"{expected_pfull_sha!r} != {pfull_sha256!r}"
        )


def _record_artifact(path: Path) -> Dict[str, Any]:
    _, artifact = _capture_file(path)
    return artifact


def extract_b0_sample(
    run_dir: Path,
    run_id: str,
    summary_entry: Dict[str, Any],
) -> Dict[str, Any]:
    """Extract and validate one completed A3 B0 sample from persisted JSON."""
    sample_id = str(summary_entry.get("sample_id") or "")
    if not sample_id:
        raise A3EvaluationError(f"run {run_id} has a summary entry without sample_id")
    sample_dir = run_dir / "samples" / sample_id
    l0_path = sample_dir / "pipeline" / "l0_result.json"
    pfull_path = sample_dir / "inputs" / "pfull" / "asset_manifest.json"
    r3_path = sample_dir / "inputs" / "r3" / "r3_asset_manifest.json"
    l0, l0_artifact = _load_json_captured(l0_path)
    pfull, pfull_artifact = _load_json_captured(pfull_path)
    r3, r3_artifact = _load_json_captured(r3_path)
    _validate_source_contracts(l0, pfull, r3, pfull_artifact["sha256"])

    if pfull.get("sample_id") != sample_id:
        raise A3EvaluationError(
            f"P-Full sample_id mismatch: {pfull.get('sample_id')!r} != {sample_id!r}"
        )
    canvas_w = int(pfull.get("canvas_width") or 0)
    canvas_h = int(pfull.get("canvas_height") or 0)
    if r3.get("sample_id") != sample_id:
        raise A3EvaluationError(
            f"R3 sample_id mismatch: {r3.get('sample_id')!r} != {sample_id!r}"
        )
    if (
        int(r3.get("canvas_width") or 0) != canvas_w
        or int(r3.get("canvas_height") or 0) != canvas_h
        or r3.get("background_asset_id") != pfull.get("background_asset_id")
    ):
        raise A3EvaluationError(f"R3/P-Full canvas or background mismatch for {sample_id}")

    b0_slot_id = str(l0.get("b0_slot_id") or "")
    if not b0_slot_id:
        raise A3EvaluationError(f"missing b0_slot_id for {run_id}/{sample_id}")
    summary_final = summary_entry.get("final")
    if not isinstance(summary_final, str) or not summary_final:
        raise A3EvaluationError(
            f"completed summary row lacks final B0: {run_id}/{sample_id}"
        )
    if summary_final != b0_slot_id:
        raise A3EvaluationError(
            f"summary final {summary_final!r} != B0 {b0_slot_id!r}"
        )
    slots = ((l0.get("bundle") or {}).get("slots") or [])
    selected = next((slot for slot in slots if slot.get("slot_id") == b0_slot_id), None)
    if selected is None or selected.get("status") != "completed":
        raise A3EvaluationError(f"selected B0 slot is not completed: {run_id}/{sample_id}")
    candidate = selected.get("candidate") or {}
    _require_finite_tree(candidate, f"B0 candidate {run_id}/{sample_id}")
    elements = candidate.get("elements") or []
    if not isinstance(elements, list):
        raise A3EvaluationError(f"B0 elements are not a list: {run_id}/{sample_id}")

    assets = {
        str(asset.get("asset_id")): asset
        for asset in pfull.get("assets", [])
        if asset.get("role") == "placeable"
    }
    raw_layout: Layout = []
    output_elements: List[Dict[str, Any]] = []
    element_ids = [str(element.get("id") or "") for element in elements]
    if not all(element_ids) or len(element_ids) != len(set(element_ids)):
        raise A3EvaluationError(
            f"B0 element IDs must be non-empty and unique: {run_id}/{sample_id}"
        )
    if set(element_ids) != set(assets) or len(element_ids) != len(assets):
        missing = sorted(set(assets) - set(element_ids))
        extra = sorted(set(element_ids) - set(assets))
        raise A3EvaluationError(
            "B0 must place every P-Full placeable asset exactly once for "
            f"{run_id}/{sample_id}; missing={missing}, extra={extra}"
        )
    for element in elements:
        asset_id = str(element.get("id") or "")
        asset = assets.get(asset_id)
        if asset is None:
            raise A3EvaluationError(f"B0 element {asset_id!r} has no P-Full placeable asset")
        cls, class_source = _asset_class(asset)
        try:
            left = _finite_number(element["left"], f"{asset_id}.left")
            top = _finite_number(element["top"], f"{asset_id}.top")
            width = _finite_number(element["width"], f"{asset_id}.width")
            height = _finite_number(element["height"], f"{asset_id}.height")
            bbox = to_xyxy(left, top, width, height)
        except (KeyError, TypeError, ValueError) as exc:
            raise A3EvaluationError(f"invalid B0 bbox for {asset_id}: {exc}") from exc
        raw_layout.append((cls, bbox))
        output_elements.append(
            {
                "asset_id": asset_id,
                "class_code": cls,
                "class_source": class_source,
                "bbox_lwh_raw": [
                    left,
                    top,
                    width,
                    height,
                ],
            }
        )
    layout, invalid_n = clip_and_filter_layout(raw_layout, canvas_w, canvas_h)
    # Recompute one-at-a-time so a dropped box cannot shift the asset-to-bbox
    # binding of later elements.
    valid_by_id = {}
    for item, (cls, raw_bbox) in zip(output_elements, raw_layout):
        prepared, _ = clip_and_filter_layout([(cls, raw_bbox)], canvas_w, canvas_h)
        if prepared:
            valid_by_id[item["asset_id"]] = list(prepared[0][1])
    for item in output_elements:
        item["bbox_xyxy_clipped"] = valid_by_id.get(item["asset_id"])
        item["valid"] = item["asset_id"] in valid_by_id

    render_ref = selected.get("render_ref")
    if not render_ref:
        raise A3EvaluationError(f"B0 slot has no render_ref: {run_id}/{sample_id}")
    render_path = _resolve_artifact_ref(str(render_ref), run_dir)
    _, render_artifact = _capture_file(render_path)
    render_sha = render_artifact["sha256"]
    stored_render_sha = selected.get("render_sha256")
    if not isinstance(stored_render_sha, str) or len(stored_render_sha) != 64:
        raise A3EvaluationError(
            f"B0 slot lacks an exact render_sha256: {run_id}/{sample_id}"
        )
    if stored_render_sha != render_sha:
        raise A3EvaluationError(f"B0 render hash mismatch: {run_id}/{sample_id}")

    background, background_payload, background_artifact = _background_descriptor(
        run_dir, pfull, r3
    )
    artifacts = [
        l0_artifact,
        pfull_artifact,
        render_artifact,
        r3_artifact,
    ]
    if background_artifact is not None:
        artifacts.append(background_artifact)
    valid_underlay_ids = [
        item["asset_id"]
        for item in output_elements
        if item["valid"] and item["class_code"] == CLS_UNDERLAY
    ]
    return {
        "run_id": run_id,
        "sample_id": sample_id,
        "status": "source_valid",
        "canvas": {"width": canvas_w, "height": canvas_h},
        "b0_slot_id": b0_slot_id,
        "b0_render_sha256": render_sha,
        "elements": output_elements,
        "layout": layout,
        "element_counts": {
            "raw": len(raw_layout),
            "valid": len(layout),
            "invalid_below_0_1pct": invalid_n,
        },
        "underlay_protocol": {
            "label_source": "P-Full v1 has no legal underlay label",
            "applicable": bool(valid_underlay_ids),
            "valid_underlay_ids": valid_underlay_ids,
            "raster_inference_forbidden": True,
        },
        "background": background,
        "_background_payload": background_payload,
        "source_artifacts": artifacts,
    }


def _load_background(sample: Dict[str, Any]) -> np.ndarray:
    canvas = sample["canvas"]
    width, height = int(canvas["width"]), int(canvas["height"])
    background = sample["background"]
    if background["kind"] == "blank_canvas":
        if tuple(background.get("rgb") or ()) != BLANK_BACKGROUND_RGB:
            raise A3EvaluationError(
                "blank background descriptor disagrees with the R3 renderer contract"
            )
        return np.full((height, width, 3), BLANK_BACKGROUND_RGB, dtype=np.uint8)
    try:
        payload = sample.get("_background_payload")
        if not isinstance(payload, bytes):
            raise A3EvaluationError("captured background bytes are unavailable")
        actual_sha = hashlib.sha256(payload).hexdigest()
        if actual_sha != background["asset_sha256"]:
            raise A3EvaluationError(
                f"background changed after extraction: {actual_sha} != "
                f"{background['asset_sha256']}"
            )
        with Image.open(io.BytesIO(payload)) as image:
            rgb = image.convert("RGB")
            if rgb.size != (width, height):
                rgb = rgb.resize((width, height), Image.LANCZOS)
            return np.asarray(rgb, dtype=np.uint8)
    except A3EvaluationError:
        raise
    except (OSError, ValueError) as exc:
        raise A3EvaluationError(
            f"cannot load background for {sample['run_id']}/{sample['sample_id']}: {exc}"
        ) from exc


def _metric(
    value: Optional[float], status: str = "ok", reason: Optional[str] = None
) -> Dict[str, Any]:
    return {"status": status, "value": None if value is None else float(value), "reason": reason}


def _validated_saliency_map(raw: Any, height: int, width: int) -> np.ndarray:
    """Enforce the frozen detector's exact 2D canvas-map contract."""
    try:
        saliency = np.asarray(raw, dtype=np.float32)
    except (TypeError, ValueError) as exc:
        raise A3EvaluationError(f"saliency output is not numeric: {exc}") from exc
    expected_shape = (height, width)
    if saliency.ndim != 2 or saliency.shape != expected_shape:
        raise A3EvaluationError(
            f"saliency output shape must be exactly {expected_shape}, got {saliency.shape}"
        )
    if not np.isfinite(saliency).all():
        raise A3EvaluationError("saliency output contains NaN or infinity")
    minimum = float(saliency.min())
    maximum = float(saliency.max())
    if minimum < 0.0 or maximum > 1.0:
        raise A3EvaluationError(
            f"saliency output must be within [0, 1], got [{minimum}, {maximum}]"
        )
    return saliency


def evaluate_sample(
    sample: Dict[str, Any],
    saliency_mode: str,
    saliency_fn: Optional[Callable[[np.ndarray, Optional[tuple]], np.ndarray]] = None,
) -> Dict[str, Any]:
    """Compute the six per-sample metrics from a validated extracted B0."""
    if saliency_mode not in (SALIENCY_MODE_FROZEN, SALIENCY_MODE_SKIP):
        raise A3EvaluationError(f"unsupported saliency mode: {saliency_mode}")
    layout = sample["layout"]
    width = float(sample["canvas"]["width"])
    height = float(sample["canvas"]["height"])
    background = _load_background(sample)
    # Eligibility is determined only after canonical clipping/filtering. A raw
    # cls=3 box that was removed by the 0.1% filter must not contribute zero.
    underlay_applicable = any(cls == CLS_UNDERLAY for cls, _ in layout)
    metrics = {
        "Ali": _metric(metric_alignment([layout], width, height)),
        "Ove": _metric(metric_overlay([layout])),
        "Und_l": (
            _metric(metric_underlay_loose([layout]))
            if underlay_applicable
            else _metric(None, "not_applicable", "no reliable explicit cls=3 label")
        ),
        "Und_s": (
            _metric(metric_underlay_strict([layout]))
            if underlay_applicable
            else _metric(None, "not_applicable", "no reliable explicit cls=3 label")
        ),
        "Rea": _metric(metric_readability([layout], [background], width, height)),
    }
    saliency_lineage: Dict[str, Any]
    if saliency_mode == SALIENCY_MODE_SKIP:
        metrics["Occ"] = _metric(None, "skipped", "explicit --saliency-mode skip")
        saliency_lineage = {"status": "skipped_explicit", "map_sha256": None}
    else:
        if saliency_fn is None:
            from metagpt.ext.agentlayout.evaluation.saliency_basnet_isnet import (
                basnet_isnet_saliency,
            )

            saliency_fn = basnet_isnet_saliency
        try:
            saliency = saliency_fn(background, (int(height), int(width)))
        except Exception as exc:  # noqa: BLE001
            raise A3EvaluationError(
                "frozen BASNet+ISNet failed closed for "
                f"{sample['run_id']}/{sample['sample_id']}: {type(exc).__name__}: {exc}"
            ) from exc
        saliency = _validated_saliency_map(saliency, int(height), int(width))
        metrics["Occ"] = _metric(metric_occlusion([layout], [saliency], width, height))
        saliency_lineage = {"status": "computed", "map_sha256": sha256_array(saliency)}
    result = {
        key: value
        for key, value in sample.items()
        if key not in {"layout", "_background_payload"}
    }
    result.update(
        {
            "status": "evaluated",
            "background_array_sha256": sha256_array(background),
            "metrics": metrics,
            "saliency": saliency_lineage,
        }
    )
    return result


def aggregate_metric_records(records: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Aggregate evaluated records while preserving eligibility/count lineage."""
    rows = list(records)
    source_skipped_n = sum(row.get("status") == "source_skipped" for row in rows)
    aggregate: Dict[str, Dict[str, Any]] = {}
    for key in METRIC_KEYS:
        entries = []
        for row in rows:
            if row.get("status") == "source_skipped":
                continue
            entry = row.get("metrics", {}).get(key)
            if not isinstance(entry, dict):
                raise A3EvaluationError(
                    f"record {row.get('run_id')}/{row.get('sample_id')} lacks metric {key}"
                )
            entries.append(entry)
        applicable = [entry for entry in entries if entry.get("status") != "not_applicable"]
        valid = [entry for entry in applicable if entry.get("status") == "ok"]
        values = [float(entry["value"]) for entry in valid if entry.get("value") is not None]
        metric_skipped = [entry for entry in applicable if entry.get("status") == "skipped"]
        aggregate[key] = {
            "value": float(statistics.mean(values)) if values else None,
            "applicable_n": len(applicable),
            "valid_n": len(values),
            "skipped_n": len(metric_skipped) + source_skipped_n,
            "metric_skipped_n": len(metric_skipped),
            "source_skipped_n": source_skipped_n,
            "not_applicable_n": len(entries) - len(applicable),
            "zero_contribution_n": sum(value == 0.0 for value in values),
        }
    return aggregate


def _expect_exact_keys(value: Dict[str, Any], expected: set, context: str) -> None:
    actual = set(value)
    if actual != expected:
        raise A3EvaluationError(
            f"{context} fields mismatch; missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _validate_per_sample_record(record: Dict[str, Any], mode: str) -> None:
    status = record.get("status")
    try:
        if status == "evaluated" and mode == "evaluate":
            EvaluatedSampleRecord.model_validate(record)
        elif status == "validated" and mode == "validate-only":
            ValidatedSampleRecord.model_validate(record)
        elif status == "source_skipped":
            SourceSkippedRecord.model_validate(record)
        elif status in {"evaluated", "validated"}:
            raise A3EvaluationError(
                f"per-sample status {status!r} is forbidden in mode {mode!r}"
            )
        else:
            raise A3EvaluationError(f"unknown per-sample status: {status!r}")
    except A3EvaluationError:
        raise
    except Exception as exc:
        raise A3EvaluationError(f"per-sample contract violation: {exc}") from exc
    _require_finite_tree(record, "per-sample record")


def validate_evaluation_bundle(
    manifest: Dict[str, Any],
    records: Sequence[Dict[str, Any]],
    aggregate: Dict[str, Any],
) -> None:
    """Validate the complete v1 sidecar contract before it can be published."""
    _require_finite_tree(manifest, "evaluation manifest")
    _require_finite_tree(aggregate, "aggregate")
    try:
        EvaluationManifestRecord.model_validate(manifest)
    except Exception as exc:
        raise A3EvaluationError(f"manifest contract violation: {exc}") from exc
    _expect_exact_keys(
        manifest,
        {
            "schema_version",
            "protocol_version",
            "evaluation_id",
            "created_at",
            "mode",
            "command",
            "command_argv",
            "source_runs",
            "matched_samples",
            "protocol_lineage",
            "write_policy",
            "cost",
        },
        "evaluation manifest",
    )
    _expect_exact_keys(
        aggregate,
        {"schema_version", "protocol_version", "evaluation_id", "runs"},
        "aggregate",
    )
    for container_name, container in (("manifest", manifest), ("aggregate", aggregate)):
        if container.get("schema_version") != SCHEMA_VERSION:
            raise A3EvaluationError(f"{container_name} has wrong schema_version")
        if container.get("protocol_version") != PROTOCOL_VERSION:
            raise A3EvaluationError(f"{container_name} has wrong protocol_version")
    if manifest.get("evaluation_id") != aggregate.get("evaluation_id"):
        raise A3EvaluationError("manifest/aggregate evaluation_id mismatch")
    if manifest.get("command") != shlex.join(manifest.get("command_argv", [])):
        raise A3EvaluationError("manifest command does not match command_argv")
    mode = manifest.get("mode")
    if mode not in {"evaluate", "validate-only"}:
        raise A3EvaluationError(f"unknown evaluation mode: {mode!r}")
    occlusion = manifest["protocol_lineage"]["occlusion"]
    if mode == "evaluate" and occlusion["mode"] == SALIENCY_MODE_FROZEN:
        detector = occlusion.get("detector")
        if not detector or not detector.get("runtime_identity"):
            raise A3EvaluationError(
                "frozen evaluation requires detector and non-null runtime identity"
            )
        runtime_identity = detector["runtime_identity"]
        basnet = detector["basnet"]
        basnet_runtime = runtime_identity["basnet"]
        if (
            basnet_runtime["requested_revision"] != basnet["revision"]
            or basnet_runtime["resolved_snapshot"] != basnet["snapshot_path"]
            or basnet_runtime["from_pretrained_path"]
            != basnet["load_contract"]["from_pretrained_path"]
        ):
            raise A3EvaluationError("BASNet runtime/static lineage mismatch")
        for filename in ("configuration_basnet.py", "modeling_basnet.py"):
            if (
                basnet_runtime["executed_code"][filename]["authoritative_sha256"]
                != basnet["artifacts"][filename]["sha256"]
            ):
                raise A3EvaluationError("BASNet executed-code lineage mismatch")
        if runtime_identity["isnet"]["verified_artifact"] != detector["isnet"]["artifact"]:
            raise A3EvaluationError("ISNet runtime/static artifact lineage mismatch")
    if occlusion["mode"] == SALIENCY_MODE_SKIP and occlusion.get("detector") is not None:
        raise A3EvaluationError("skip mode cannot carry detector lineage")
    source_runs = manifest.get("source_runs")
    if not isinstance(source_runs, list) or not source_runs:
        raise A3EvaluationError("manifest source_runs must be a non-empty array")
    runs = aggregate.get("runs")
    if not isinstance(runs, dict):
        raise A3EvaluationError("aggregate runs must be an object")
    run_ids = [source_run.get("run_id") for source_run in source_runs]
    if (
        not all(isinstance(run_id, str) and run_id for run_id in run_ids)
        or len(run_ids) != len(set(run_ids))
        or set(run_ids) != set(runs)
    ):
        raise A3EvaluationError("manifest and aggregate run IDs must match uniquely")
    matched = manifest.get("matched_samples")
    if not isinstance(matched, dict):
        raise A3EvaluationError("manifest matched_samples must be an object")
    _expect_exact_keys(
        matched,
        {"count", "ordered_sample_ids", "ordered_sample_ids_sha256"},
        "matched_samples",
    )
    matched_ids = matched.get("ordered_sample_ids")
    if not isinstance(matched_ids, list) or not all(
        isinstance(sample_id, str) and sample_id for sample_id in matched_ids
    ):
        raise A3EvaluationError("matched ordered_sample_ids must be strings")
    if len(matched_ids) != len(set(matched_ids)) or matched.get("count") != len(matched_ids):
        raise A3EvaluationError("matched sample ID count/uniqueness mismatch")
    encoded_ids = json.dumps(
        matched_ids, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    if hashlib.sha256(encoded_ids).hexdigest() != matched.get("ordered_sample_ids_sha256"):
        raise A3EvaluationError("matched sample ID hash mismatch")

    grouped: Dict[str, List[Dict[str, Any]]] = {run_id: [] for run_id in run_ids}
    seen_pairs = set()
    for record in records:
        if not isinstance(record, dict):
            raise A3EvaluationError("per-sample record must be an object")
        _validate_per_sample_record(record, mode)
        if record.get("status") == "evaluated":
            occ = record["metrics"]["Occ"]
            saliency = record["saliency"]
            if occlusion["mode"] == SALIENCY_MODE_FROZEN and (
                occ["status"] != "ok" or saliency["status"] != "computed"
            ):
                raise A3EvaluationError(
                    "frozen evaluation requires computed Occ and saliency hash"
                )
            if occlusion["mode"] == SALIENCY_MODE_SKIP and (
                occ["status"] != "skipped"
                or saliency["status"] != "skipped_explicit"
            ):
                raise A3EvaluationError("skip mode requires explicitly skipped Occ")
        pair = (record["run_id"], record["sample_id"])
        if pair in seen_pairs or record["run_id"] not in grouped:
            raise A3EvaluationError(f"duplicate or unknown per-sample identity: {pair}")
        seen_pairs.add(pair)
        grouped[record["run_id"]].append(record)

    for source_run in source_runs:
        run_id = source_run["run_id"]
        run_records = grouped[run_id]
        selected_n = source_run.get("selection", {}).get("selected_n")
        if selected_n != len(run_records):
            raise A3EvaluationError(f"{run_id} selected_n does not match records")
        if [record["sample_id"] for record in run_records] != matched_ids[:selected_n]:
            raise A3EvaluationError(f"{run_id} record order is not the matched order")
        actual_counts = {
            "selected_n": len(run_records),
            "source_valid_n": sum(
                record["status"] in {"validated", "evaluated"}
                for record in run_records
            ),
            "source_skipped_n": sum(
                record["status"] == "source_skipped" for record in run_records
            ),
            "evaluated_n": sum(record["status"] == "evaluated" for record in run_records),
            "validated_only_n": sum(
                record["status"] == "validated" for record in run_records
            ),
        }
        if source_run.get("observed_counts") != actual_counts:
            raise A3EvaluationError(f"{run_id} manifest count conservation failed")
        run_aggregate = runs[run_id]
        _expect_exact_keys(run_aggregate, {"sample_counts", "metrics"}, f"{run_id} aggregate")
        if run_aggregate.get("sample_counts") != actual_counts:
            raise A3EvaluationError(f"{run_id} aggregate sample counts mismatch")
        metric_aggregates = run_aggregate.get("metrics")
        if mode == "validate-only":
            if metric_aggregates != {}:
                raise A3EvaluationError("validate-only aggregate metrics must be empty")
            continue
        try:
            SixAggregates.model_validate(metric_aggregates)
        except Exception as exc:
            raise A3EvaluationError(
                f"{run_id} aggregate metric contract violation: {exc}"
            ) from exc
        expected_aggregates = aggregate_metric_records(run_records)
        if metric_aggregates != expected_aggregates:
            raise A3EvaluationError(f"{run_id} aggregate does not match per-sample rows")
        for metric in METRIC_KEYS:
            counts = metric_aggregates[metric]
            if (
                counts["applicable_n"]
                + counts["not_applicable_n"]
                + counts["source_skipped_n"]
                != len(run_records)
            ):
                raise A3EvaluationError(
                    f"{run_id}/{metric} aggregate count conservation failed"
                )


def frozen_detector_lineage() -> Dict[str, Any]:
    """Verify local frozen detector artifacts and return IDs plus exact hashes."""
    from metagpt.ext.agentlayout.evaluation.saliency_basnet_isnet import (
        _BASNET_REQUIRED_FILES,
        _resolve_basnet_snapshot,
        _verify_isnet_artifact,
    )

    try:
        revision, snapshot = _resolve_basnet_snapshot()
    except RuntimeError as exc:
        raise A3EvaluationError(str(exc)) from exc
    try:
        isnet_artifact = _verify_isnet_artifact()
    except RuntimeError as exc:
        raise A3EvaluationError(str(exc)) from exc
    saliency_source = Path(__file__).with_name("saliency_basnet_isnet.py")
    basnet_artifacts = {
        filename: _record_artifact(snapshot / filename)
        for filename in _BASNET_REQUIRED_FILES
    }
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
                "from_pretrained_path": str(snapshot.resolve()),
                "local_files_only": True,
                "trust_remote_code": True,
                "force_download": False,
            },
            "snapshot_path": str(snapshot.resolve()),
            "artifacts": basnet_artifacts,
        },
        "isnet": {
            "model_id": "rembg/isnet-general-use",
            "artifact": isnet_artifact,
            "provider": "CPUExecutionProvider",
            "download_path": "forbidden; direct verified ONNX bytes",
        },
        "pku_deviation": "ISNet replaces the PFPN branch used by PKU PosterLayout",
        "implementation_sha256": sha256_file(saliency_source),
    }


def evaluation_code_runtime_lineage(cli_source: Path) -> Dict[str, Any]:
    """Capture executable source hashes and dependency/runtime identities."""
    import cv2
    import onnxruntime
    import pooch
    import pydantic
    import rembg
    import torch
    import torchvision
    import transformers

    metrics_source = Path(__file__).with_name("sega_metrics.py")
    evaluator_source = Path(__file__)
    saliency_source = Path(__file__).with_name("saliency_basnet_isnet.py")
    sources = {
        "sega_metrics.py": _record_artifact(metrics_source),
        "a3_sega_evaluator.py": _record_artifact(evaluator_source),
        "evaluate_a3_sega.py": _record_artifact(cli_source),
        "saliency_basnet_isnet.py": _record_artifact(saliency_source),
    }
    return {
        "sources": sources,
        "runtime": {
            "python": {
                "version": platform.python_version(),
                "implementation": platform.python_implementation(),
                "executable": str(Path(sys.executable).resolve()),
            },
            "platform": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "platform": platform.platform(),
            },
            "numpy": np.__version__,
            "cv2": cv2.__version__,
            "Pillow": importlib.metadata.version("Pillow"),
            "torch": torch.__version__,
            "torchvision": torchvision.__version__,
            "transformers": transformers.__version__,
            "rembg": rembg.__version__,
            "onnxruntime": onnxruntime.__version__,
            "pydantic": pydantic.__version__,
            "pooch": pooch.__version__,
            "onnxruntime_available_providers": onnxruntime.get_available_providers(),
        },
    }


def protocol_lineage(
    saliency_mode: str,
    detector: Optional[Dict[str, Any]] = None,
    code_runtime: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return the frozen metric/preprocessing contract stored with every result."""
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "bbox_input": "A3 B0 left-top-width-height",
        "bbox_metric_frame": "xyxy, clipped to canvas",
        "validity_filter": {
            "minimum_canvas_fraction": VALID_AREA_FRACTION,
            "comparison": "area >= threshold",
        },
        "alignment": "PKU layout-wide-min aggregation quirk",
        "overlay_denominator": "non-underlay element count",
        "underlay": "P-Full v1 has no legal underlay class; current A3 is N/A",
        "readability": "background-only float64 Sobel; definition-aligned, not PKU bit-exact",
        "blank_background": {"renderer_contract": "R3", "rgb": list(BLANK_BACKGROUND_RGB)},
        "occlusion": {
            "mode": saliency_mode,
            "detector": detector,
            "sobel_fallback_forbidden": True,
        },
        "code_runtime_lineage": code_runtime,
    }


def load_run_summary(run_dir: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Load the authoritative A3 summary and return its ordered sample entries."""
    summary_path = run_dir / "a3_run_summary.json"
    manifest_path = run_dir / "run_manifest.json"
    summary, summary_artifact = _load_json_captured(summary_path)
    manifest, manifest_artifact = _load_json_captured(manifest_path)
    run_id = str(manifest.get("run_id") or "")
    if not run_id or run_id != run_dir.name:
        raise A3EvaluationError(
            f"manifest run_id must match directory name: {run_id!r} != {run_dir.name!r}"
        )
    entries = summary.get("samples")
    if not isinstance(entries, list):
        raise A3EvaluationError(f"summary samples must be a list: {summary_path}")
    if not all(isinstance(entry, dict) for entry in entries):
        raise A3EvaluationError(f"summary sample rows must be objects: {summary_path}")
    reported_total = _required_nonnegative_int(summary, "total", "summary")
    reported_failed = _required_nonnegative_int(summary, "failed", "summary")
    if reported_total != len(entries):
        raise A3EvaluationError(
            f"summary total does not match sample rows: {reported_total} != {len(entries)}"
        )
    sample_ids = [entry.get("sample_id") for entry in entries]
    if (
        not all(isinstance(sample_id, str) and sample_id for sample_id in sample_ids)
        or len(sample_ids) != len(set(sample_ids))
    ):
        raise A3EvaluationError("summary sample IDs must be non-empty and unique")
    failed_n = sum(entry.get("status") == "failed" for entry in entries)
    completed_n = sum(entry.get("status") == "completed" for entry in entries)
    other_status_n = len(entries) - failed_n - completed_n
    if reported_failed != failed_n:
        raise A3EvaluationError(
            f"summary failed count does not match rows: {reported_failed} != {failed_n}"
        )
    reported_completed: Optional[int] = None
    if "completed" in summary:
        reported_completed = _required_nonnegative_int(summary, "completed", "summary")
        if reported_completed != completed_n:
            raise A3EvaluationError(
                "summary completed count does not match rows: "
                f"{reported_completed} != {completed_n}"
            )

    snapshot = manifest.get("sample_ids_snapshot") or {}
    stored_ref = snapshot.get("stored_path")
    if not stored_ref:
        raise A3EvaluationError("manifest sample_ids_snapshot.stored_path is missing")
    sample_ids_path = _resolve_artifact_ref(str(stored_ref), run_dir)
    if not _is_relative_to(sample_ids_path, run_dir.resolve()):
        raise A3EvaluationError("sample ID snapshot must be stored inside the run directory")
    stored_ids, sample_ids_artifact = _load_json_list_captured(sample_ids_path)
    if (
        not all(isinstance(sample_id, str) and sample_id for sample_id in stored_ids)
        or len(stored_ids) != len(set(stored_ids))
    ):
        raise A3EvaluationError("sample ID snapshot must contain unique non-empty strings")
    expected_snapshot_sha = str(snapshot.get("sha256") or "")
    actual_snapshot_sha = sample_ids_artifact["sha256"]
    if expected_snapshot_sha != actual_snapshot_sha:
        raise A3EvaluationError(
            f"sample ID snapshot hash mismatch: {actual_snapshot_sha} != {expected_snapshot_sha}"
        )
    snapshot_count = _required_nonnegative_int(snapshot, "count", "sample ID snapshot")
    if snapshot_count != len(stored_ids):
        raise A3EvaluationError("sample ID snapshot count does not match stored rows")
    if stored_ids != sample_ids:
        raise A3EvaluationError("summary sample IDs do not match the manifest snapshot")

    formal_complete = other_status_n == 0 and completed_n + failed_n == reported_total
    return {
        "run_id": run_id,
        "run_dir": str(run_dir.resolve()),
        "summary": summary_artifact,
        "manifest": manifest_artifact,
        "sample_ids": sample_ids_artifact,
        "summary_counts": {
            "reported_total": reported_total,
            "reported_completed": reported_completed,
            "reported_failed": reported_failed,
            "reported_sample_n": len(entries),
            "completed_n": completed_n,
            "failed_n": failed_n,
            "other_status_n": other_status_n,
            "formal_complete": formal_complete,
        },
    }, entries


def verify_source_artifacts_unchanged(records: Iterable[Dict[str, Any]]) -> None:
    """Fail if any source artifact changed while evaluation was in progress."""
    seen: Dict[str, Tuple[str, int]] = {}
    for record in records:
        for artifact in record.get("source_artifacts", []):
            path = artifact["path"]
            captured_sha = artifact["sha256"]
            captured_size = artifact["size_bytes"]
            capture = (captured_sha, captured_size)
            if path in seen and seen[path] != capture:
                raise A3EvaluationError(
                    f"same source path captured with different hashes/sizes: {path}"
                )
            seen[path] = capture
    for raw_path, (expected_sha, expected_size) in seen.items():
        path = Path(raw_path)
        if (
            not path.is_file()
            or path.stat().st_size != expected_size
            or sha256_file(path) != expected_sha
        ):
            raise A3EvaluationError(f"source artifact changed during evaluation: {path}")


__all__ = [
    "A3EvaluationError",
    "METRIC_KEYS",
    "PROTOCOL_VERSION",
    "SALIENCY_MODE_FROZEN",
    "SALIENCY_MODE_SKIP",
    "SCHEMA_VERSION",
    "aggregate_metric_records",
    "assert_output_is_sidecar",
    "clip_and_filter_layout",
    "evaluate_sample",
    "extract_b0_sample",
    "frozen_detector_lineage",
    "load_run_summary",
    "protocol_lineage",
    "sha256_file",
    "verify_source_artifacts_unchanged",
]
