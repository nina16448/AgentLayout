"""Immutable run-directory and provenance infrastructure for AgentLayout A3."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from metagpt.ext.agentlayout.a3_config import A3RunConfig


RUN_MANIFEST_SCHEMA_VERSION = "a3.run-manifest.v1"
SAMPLE_RECORD_SCHEMA_VERSION = "a3.sample-record.v1"
ERROR_RECORD_SCHEMA_VERSION = "a3.error-record.v1"
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SAMPLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
MANIFEST_FILENAME = "run_manifest.json"
SAMPLE_IDS_FILENAME = "sample_ids.json"
CONFIG_FILENAME = "run_config.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(repo_root: Path, *args: str) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _tracked_diff(repo_root: Path) -> bytes:
    out = _git(repo_root, "diff", "--binary", "HEAD", "--")
    return (out or "").encode("utf-8")


def _untracked_hashes(repo_root: Path, watched_roots: Iterable[str]) -> Dict[str, str]:
    hashes: Dict[str, str] = {}
    for watched in watched_roots:
        listing = _git(
            repo_root, "ls-files", "--others", "--exclude-standard", "--", watched
        )
        for relative in (listing or "").splitlines():
            path = repo_root / relative
            if path.is_file():
                hashes[relative] = sha256_file(path)
    return dict(sorted(hashes.items()))


def capture_provenance(
    repo_root: Path,
    *,
    watched_roots: Iterable[str] = (
        "metagpt/ext/agentlayout",
        "layout_agent/run_a3.py",
        "layout_agent/configs",
        "layout_agent/sample_ids",
    ),
) -> Dict[str, Any]:
    """Capture reconstructible source state without reading secret config values."""
    repo_root = repo_root.resolve()
    git_dir = _git(repo_root, "rev-parse", "--git-dir")
    head = _git(repo_root, "rev-parse", "HEAD")
    diff = _tracked_diff(repo_root)
    untracked = _untracked_hashes(repo_root, watched_roots)
    git_block: Dict[str, Any]
    if git_dir is None:
        git_block = {"available": False}
    else:
        git_block = {
            "available": True,
            "head": head.strip() if head else None,
            "branch": (_git(repo_root, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
            or None,
            "tracked_dirty": bool(diff),
            "tracked_diff_sha256": sha256_bytes(diff) if diff else "clean",
            "untracked_file_sha256": untracked,
        }
    return {
        "captured_at": utc_now(),
        "git": git_block,
        "runtime": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "executable": sys.executable,
        },
        "environment": {
            key: value
            for key, value in sorted(os.environ.items())
            if key.startswith("AGENTLAYOUT_")
        },
    }


class FileSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: str
    stored_path: str
    sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    count: Optional[int] = Field(default=None, ge=0)


class ErrorRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["a3.error-record.v1"] = ERROR_RECORD_SCHEMA_VERSION
    timestamp: str = Field(default_factory=utc_now)
    stage: str = Field(..., min_length=1)
    error_type: str = Field(..., min_length=1)
    message: str
    attempt: Optional[int] = Field(default=None, ge=1)
    retryable: bool = False
    details: Dict[str, Any] = Field(default_factory=dict)


class SampleRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["a3.sample-record.v1"] = SAMPLE_RECORD_SCHEMA_VERSION
    sample_id: str = Field(..., min_length=1)
    status: Literal["pending", "running", "completed", "failed", "skipped"] = "pending"
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    artifacts: Dict[str, str] = Field(default_factory=dict)
    cost: Dict[str, Any] = Field(default_factory=dict)
    errors: List[ErrorRecord] = Field(default_factory=list)


class RunManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["a3.run-manifest.v1"] = RUN_MANIFEST_SCHEMA_VERSION
    run_id: str
    created_at: str = Field(default_factory=utc_now)
    status: Literal["initialized", "running", "completed", "failed"] = "initialized"
    config: A3RunConfig
    config_snapshot: FileSnapshot
    sample_ids_snapshot: FileSnapshot
    provenance: Dict[str, Any]
    prompt_hashes: Dict[str, str] = Field(default_factory=dict)
    schema_versions: Dict[str, str] = Field(default_factory=dict)
    cost: Dict[str, Any] = Field(default_factory=dict)
    completion: Dict[str, int] = Field(default_factory=dict)
    errors: List[ErrorRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _valid_run_id(self) -> "RunManifest":
        validate_run_id(self.run_id)
        return self


def validate_run_id(run_id: str) -> str:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError(
            "run_id must be 1-128 characters using only letters, digits, '.', '_' or '-'"
        )
    if run_id in {".", ".."}:
        raise ValueError("run_id cannot be '.' or '..'")
    return run_id


def load_sample_ids(path: Path) -> List[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(isinstance(item, str) and item for item in data):
        raise ValueError("sample IDs file must be a JSON array of non-empty strings")
    if len(data) != len(set(data)):
        raise ValueError("sample IDs file contains duplicates")
    if not data:
        raise ValueError("sample IDs file cannot be empty")
    invalid = [item for item in data if not SAMPLE_ID_RE.fullmatch(item) or item in {".", ".."}]
    if invalid:
        raise ValueError(f"sample IDs contain unsafe path values: {invalid}")
    return data


def write_json_once(path: Path, value: Any) -> None:
    """Atomically publish JSON and refuse to replace an existing artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = canonical_json_bytes(value)
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


class A3RunStore:
    """Create a complete A3 run skeleton exactly once."""

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir.resolve()

    @classmethod
    def create(
        cls,
        *,
        runs_root: Path,
        run_id: str,
        config_path: Path,
        sample_ids_path: Path,
        repo_root: Path,
        prompt_hashes: Optional[Dict[str, str]] = None,
    ) -> "A3RunStore":
        validate_run_id(run_id)
        config_bytes = config_path.read_bytes()
        config = A3RunConfig.model_validate_json(config_bytes)
        sample_ids = load_sample_ids(sample_ids_path)

        runs_root.mkdir(parents=True, exist_ok=True)
        run_dir = runs_root / run_id
        run_dir.mkdir(exist_ok=False)
        store = cls(run_dir)
        try:
            for name in ("samples", "schemas", "prompts", "errors"):
                (run_dir / name).mkdir()

            schemas = {
                "run_config.schema.json": A3RunConfig.model_json_schema(),
                "run_manifest.schema.json": RunManifest.model_json_schema(),
                "sample_record.schema.json": SampleRecord.model_json_schema(),
                "error_record.schema.json": ErrorRecord.model_json_schema(),
            }
            for filename, schema in schemas.items():
                write_json_once(run_dir / "schemas" / filename, schema)

            config_payload = config.model_dump(mode="json")
            ids_payload = sample_ids
            write_json_once(run_dir / CONFIG_FILENAME, config_payload)
            write_json_once(run_dir / SAMPLE_IDS_FILENAME, ids_payload)

            config_snapshot = FileSnapshot(
                source_path=str(config_path.resolve()),
                stored_path=CONFIG_FILENAME,
                sha256=sha256_bytes(canonical_json_bytes(config_payload)),
            )
            ids_snapshot = FileSnapshot(
                source_path=str(sample_ids_path.resolve()),
                stored_path=SAMPLE_IDS_FILENAME,
                sha256=sha256_bytes(canonical_json_bytes(ids_payload)),
                count=len(sample_ids),
            )
            manifest = RunManifest(
                run_id=run_id,
                config=config,
                config_snapshot=config_snapshot,
                sample_ids_snapshot=ids_snapshot,
                provenance=capture_provenance(repo_root),
                prompt_hashes=prompt_hashes or {},
                schema_versions={
                    "run_manifest": RUN_MANIFEST_SCHEMA_VERSION,
                    "run_config": config.schema_version,
                    "sample_record": SAMPLE_RECORD_SCHEMA_VERSION,
                    "error_record": ERROR_RECORD_SCHEMA_VERSION,
                    **config.schema_versions,
                },
                completion={
                    "total": len(sample_ids),
                    "pending": len(sample_ids),
                    "completed": 0,
                    "failed": 0,
                    "skipped": 0,
                },
            )
            write_json_once(run_dir / MANIFEST_FILENAME, manifest.model_dump(mode="json"))
            for sample_id in sample_ids:
                sample_dir = run_dir / "samples" / sample_id
                sample_dir.mkdir()
                record = SampleRecord(sample_id=sample_id)
                write_json_once(sample_dir / "sample_record.json", record.model_dump(mode="json"))
        except Exception:
            # Keep the partial directory as forensic evidence.  Never erase it:
            # the run id remains consumed and cannot silently be reused.
            raise
        return store

    def manifest(self) -> RunManifest:
        return RunManifest.model_validate_json(
            (self.run_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
        )

    def record_run_error(self, error: ErrorRecord) -> Path:
        """Persist a uniquely named run error without mutating prior records."""
        error_dir = self.run_dir / "errors"
        index = len(list(error_dir.glob("error_*.json")))
        path = error_dir / f"error_{index:04d}.json"
        write_json_once(path, error.model_dump(mode="json"))
        return path
