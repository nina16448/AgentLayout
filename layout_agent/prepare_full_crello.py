"""Prepare the pinned official Crello test split for batched A3 evaluation.

This tool has no OpenAI client and no paid execution path.  Network and
dataset-byte operations are separately gated:

* ``snapshot-ids --allow-network`` projects only the pinned test ``id`` column
  and atomically publishes the ordered-ID snapshot.
* ``materialize --allow-dataset-download`` streams the pinned test rows and
  atomically creates only missing cache directories while publishing missing
  text-bitmap sidecars without changing ``meta.json``.

``plan``, ``build-batches`` and ``verify-batches`` are local-only.  Every
directory artifact is published with Linux ``renameat2(RENAME_NOREPLACE)``;
no completed cache, snapshot, batch bundle, run, or evaluation may be replaced.
"""
from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import random
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from metagpt.ext.agentlayout.tools.pfull_preprocessor import (  # noqa: E402
    A3_TEXT_BITMAP_SIDECAR_VERSION as TEXT_BITMAP_SIDECAR_VERSION,
)

DATASET = "cyberagent/crello"
DATASET_REVISION = "7997e2f434ee4aa73cf4cdf22c5954cb175872e1"
DATASET_LAST_MODIFIED = "2026-02-27T02:45:00Z"
SPLIT = "test"
EXPECTED_SPLIT_COUNTS = {"train": 19479, "validation": 1852, "test": 1971}
EXPECTED_TEST_COUNT = EXPECTED_SPLIT_COUNTS[SPLIT]
TEST_PARQUET_BYTES = 1_551_056_855
MIN_FREE_BYTES = 80 * 1024**3
BATCH_SIZE = 100
BATCH_SEED = 42
EXPECTED_EXISTING_IDS_SHA256 = (
    "0e5401fb45cb83c573c82be458508e6ace003482b027b667556dfd876aed052c"
)

SNAPSHOT_SCHEMA_VERSION = "a3.crello-official-test-snapshot.v1"
BATCH_SCHEMA_VERSION = "a3.crello-official-test-batches.v1"
CACHE_PROVENANCE_VERSION = "a3.crello-cache-provenance.v1"
TEXT_BITMAP_SIDECAR_FILENAME = "a3_text_bitmaps.json"
CACHE_PROVENANCE_FILENAME = "a3_cache_provenance.json"

DEFAULT_CRELLO_ROOT = REPO_ROOT / "layout_agent" / "output"
DEFAULT_EXISTING_IDS = REPO_ROOT / "layout_agent" / "sample_ids" / "a3_general_n100.json"
DEFAULT_SNAPSHOT_DIR = (
    REPO_ROOT / "layout_agent" / "sample_ids" / "a3_crello_test_n1971_v1"
)
DEFAULT_BATCH_DIR = (
    REPO_ROOT / "layout_agent" / "sample_ids" / "a3_crello_test_batches_v1"
)
DEFAULT_CONFIG = REPO_ROOT / "layout_agent" / "configs" / "a3_crello_test_l0_v1.json"
DEFAULT_RUNS_ROOT = REPO_ROOT / "layout_agent" / "runs" / "a3"
DEFAULT_EVALUATIONS_ROOT = (
    REPO_ROOT / "layout_agent" / "evaluations" / "a3-sega" / "a3.sega-pku-protocol.v1"
)

COMPLETED_ARTIFACTS = {
    "sega_manifest": (
        REPO_ROOT
        / "layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/"
        "a3-general-n100-sega-v1/evaluation_manifest.json",
        "ee6f4d3284c91a0d8c5346b42d7e74f8640a63ddf77a97800931212a5d56086e",
    ),
    "sega_aggregate": (
        REPO_ROOT
        / "layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/"
        "a3-general-n100-sega-v1/aggregate.json",
        "dc5dfe2446933df717b21987258beb933d702add6a5416c4e3819f72c66bf5ae",
    ),
    "sega_per_sample": (
        REPO_ROOT
        / "layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/"
        "a3-general-n100-sega-v1/per_sample.jsonl",
        "a72c699ff4eac61022c8cb12d4705afb845a80699c76fbe4923465827e663f25",
    ),
}

SAMPLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
REQUIRED_ROW_ARRAYS = (
    "type",
    "image",
    "text",
    "left",
    "top",
    "width",
    "height",
)


class FullCrelloPreparationError(RuntimeError):
    """The pinned full-test preparation contract was violated."""


def _pretty_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strict_json(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON constant {value}")
        ),
    )


def _path_lexists(path: Path) -> bool:
    return os.path.lexists(os.fspath(path))


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _write_once_or_verify(path: Path, payload: bytes) -> str:
    """Publish one file without replacement; identical reruns are verification."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        _write_new(temp, payload)
        try:
            os.link(temp, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise FullCrelloPreparationError(
                    f"refusing to overwrite different artifact: {path}"
                )
            return "verified-existing"
        return "created"
    finally:
        temp.unlink(missing_ok=True)


def _rename_directory_noreplace(source: Path, destination: Path) -> None:
    """Atomically publish a directory without replacing any existing path."""
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise FullCrelloPreparationError(
            "safe directory publication requires renameat2(RENAME_NOREPLACE)"
        )
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        -100,
        os.fsencode(source),
        -100,
        os.fsencode(destination),
        1,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise FullCrelloPreparationError(f"destination already exists: {destination}")
    raise OSError(error_number, os.strerror(error_number), os.fspath(destination))


def _publish_directory(
    destination: Path,
    *,
    build: Callable[[Path], None],
    verify: Callable[[Path], Any],
) -> Tuple[str, Any]:
    """Build, round-trip verify, and atomically publish a complete directory."""
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if _path_lexists(destination):
        return "verified-existing", verify(destination)
    staging: Optional[Path] = None
    try:
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{destination.name}.staging-",
                dir=str(destination.parent),
            )
        )
        build(staging)
        result = verify(staging)
        _rename_directory_noreplace(staging, destination)
        staging = None
        return "created", verify(destination)
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)


def _validate_ids(ids: Sequence[str], *, expected_count: Optional[int] = None) -> List[str]:
    values = list(ids)
    if expected_count is not None and len(values) != expected_count:
        raise FullCrelloPreparationError(
            f"expected {expected_count} IDs, found {len(values)}"
        )
    if not values:
        raise FullCrelloPreparationError("ID snapshot cannot be empty")
    if len(values) != len(set(values)):
        raise FullCrelloPreparationError("ID snapshot contains duplicates")
    invalid = [
        value
        for value in values
        if not isinstance(value, str)
        or not SAMPLE_ID_RE.fullmatch(value)
        or value in {".", ".."}
    ]
    if invalid:
        raise FullCrelloPreparationError(f"unsafe sample IDs: {invalid[:5]}")
    return values


def _load_ids(path: Path, *, expected_count: Optional[int] = None) -> List[str]:
    payload = _strict_json(path)
    if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
        raise FullCrelloPreparationError(f"IDs file must be a JSON string array: {path}")
    return _validate_ids(payload, expected_count=expected_count)


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT))
    except ValueError:
        return str(resolved)


def verify_id_snapshot(
    snapshot_dir: Path,
    *,
    expected_count: int = EXPECTED_TEST_COUNT,
    revision: str = DATASET_REVISION,
) -> Dict[str, Any]:
    ids_path = snapshot_dir / "ordered_ids.json"
    provenance_path = snapshot_dir / "dataset_provenance.json"
    ids = _load_ids(ids_path, expected_count=expected_count)
    provenance = _strict_json(provenance_path)
    if not isinstance(provenance, dict):
        raise FullCrelloPreparationError("dataset provenance must be an object")
    expected = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "dataset": DATASET,
        "revision": revision,
        "split": SPLIT,
        "split_counts": EXPECTED_SPLIT_COUNTS,
        "count": expected_count,
        "ordered_ids_file_sha256": _sha256_file(ids_path),
        "ordered_ids_canonical_sha256": _sha256_bytes(_canonical_json_bytes(ids)),
    }
    for key, value in expected.items():
        if provenance.get(key) != value:
            raise FullCrelloPreparationError(
                f"snapshot provenance mismatch for {key}: {provenance.get(key)!r} != {value!r}"
            )
    return {"count": len(ids), "ids": ids, "provenance": provenance}


def snapshot_ordered_ids(
    snapshot_dir: Path,
    *,
    load_dataset_fn: Optional[Callable[..., Any]] = None,
    expected_count: int = EXPECTED_TEST_COUNT,
    revision: str = DATASET_REVISION,
) -> Tuple[str, Dict[str, Any]]:
    """Project and atomically freeze the ordered ID column at one source SHA."""
    if _path_lexists(snapshot_dir):
        return "verified-existing", verify_id_snapshot(
            snapshot_dir, expected_count=expected_count, revision=revision
        )
    if load_dataset_fn is None:
        from datasets import load_dataset as load_dataset_fn  # type: ignore[no-redef]

    dataset = load_dataset_fn(
        DATASET,
        split=SPLIT,
        streaming=True,
        revision=revision,
    )
    if not hasattr(dataset, "select_columns"):
        raise FullCrelloPreparationError("streaming dataset lacks select_columns")
    projected = dataset.select_columns(["id"])
    ids = _validate_ids(
        [str(row["id"]) for row in projected],
        expected_count=expected_count,
    )
    ids_payload = _pretty_json_bytes(ids)
    provenance = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "dataset": DATASET,
        "revision": revision,
        "repository_last_modified": DATASET_LAST_MODIFIED,
        "split": SPLIT,
        "split_counts": EXPECTED_SPLIT_COUNTS,
        "count": len(ids),
        "ordered_ids_file_sha256": _sha256_bytes(ids_payload),
        "ordered_ids_canonical_sha256": _sha256_bytes(_canonical_json_bytes(ids)),
        "network_projection": ["id"],
        "api_model_calls": 0,
        "paid_cost_usd": 0.0,
    }

    def build(staging: Path) -> None:
        _write_new(staging / "ordered_ids.json", ids_payload)
        _write_new(staging / "dataset_provenance.json", _pretty_json_bytes(provenance))

    return _publish_directory(
        snapshot_dir,
        build=build,
        verify=lambda path: verify_id_snapshot(
            path, expected_count=expected_count, revision=revision
        ),
    )


def _validate_row(row: Mapping[str, Any]) -> Tuple[str, int]:
    sample_id = str(row.get("id", ""))
    _validate_ids([sample_id])
    for name in REQUIRED_ROW_ARRAYS:
        if name not in row or not isinstance(row[name], Sequence):
            raise FullCrelloPreparationError(
                f"row {sample_id} missing sequence field {name}"
            )
    count = len(row["type"])
    if count <= 0:
        raise FullCrelloPreparationError(f"row {sample_id} has no elements")
    mismatched = {name: len(row[name]) for name in REQUIRED_ROW_ARRAYS if len(row[name]) != count}
    if mismatched:
        raise FullCrelloPreparationError(
            f"row {sample_id} array lengths differ from type={count}: {mismatched}"
        )
    if int(row.get("length", count)) != count:
        raise FullCrelloPreparationError(f"row {sample_id} length field mismatch")
    if int(row.get("canvas_width", 0)) <= 0 or int(row.get("canvas_height", 0)) <= 0:
        raise FullCrelloPreparationError(f"row {sample_id} has invalid canvas")
    return sample_id, count


def _save_image_new(image: Image.Image, path: Path, *, image_format: str) -> None:
    with path.open("xb") as handle:
        image.save(handle, format=image_format)
        handle.flush()
        os.fsync(handle.fileno())


def _tree_file_snapshot(tree: Path, *, exclude: Iterable[str] = ()) -> List[Dict[str, Any]]:
    excluded = set(exclude)
    rows: List[Dict[str, Any]] = []
    for path in sorted(tree.rglob("*")):
        if path.is_symlink():
            raise FullCrelloPreparationError(f"symlink not allowed in cache: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(tree).as_posix()
        if relative in excluded:
            continue
        rows.append(
            {"path": relative, "size": path.stat().st_size, "sha256": _sha256_file(path)}
        )
    return rows


def _verify_cache_tree(
    tree: Path,
    *,
    final_dir: Path,
    sample_id: str,
    require_provenance: bool,
) -> Dict[str, Any]:
    meta_path = tree / "meta.json"
    preview_path = tree / "ground_truth_preview.jpg"
    if not meta_path.is_file() or not preview_path.is_file():
        raise FullCrelloPreparationError(f"incomplete cache tree for {sample_id}")
    meta = _strict_json(meta_path)
    if not isinstance(meta, dict) or meta.get("id") != sample_id:
        raise FullCrelloPreparationError(f"cache meta ID mismatch for {sample_id}")
    elements = meta.get("elements")
    if not isinstance(elements, list) or meta.get("n_elements") != len(elements):
        raise FullCrelloPreparationError(f"cache element count mismatch for {sample_id}")
    for element in elements:
        if not isinstance(element, dict):
            raise FullCrelloPreparationError(f"non-object cache element for {sample_id}")
        asset_ref = element.get("asset_ref")
        if not asset_ref:
            continue
        expected = final_dir / Path(str(asset_ref)).name
        if Path(str(asset_ref)) != expected:
            raise FullCrelloPreparationError(
                f"asset_ref escapes or disagrees with final cache: {asset_ref}"
            )
        if not (tree / expected.name).is_file():
            raise FullCrelloPreparationError(f"missing cache asset {expected.name}")
    sidecar = _strict_json(tree / TEXT_BITMAP_SIDECAR_FILENAME)
    if sidecar.get("version") != TEXT_BITMAP_SIDECAR_VERSION or sidecar.get("sample_id") != sample_id:
        raise FullCrelloPreparationError(f"invalid text sidecar for {sample_id}")
    bitmaps = sidecar.get("bitmaps")
    if not isinstance(bitmaps, dict):
        raise FullCrelloPreparationError(f"invalid text bitmap map for {sample_id}")
    for filename in bitmaps.values():
        if not isinstance(filename, str) or not (tree / filename).is_file():
            raise FullCrelloPreparationError(f"missing text bitmap for {sample_id}: {filename}")
    provenance_path = tree / CACHE_PROVENANCE_FILENAME
    if require_provenance:
        provenance = _strict_json(provenance_path)
        if (
            provenance.get("schema_version") != CACHE_PROVENANCE_VERSION
            or provenance.get("sample_id") != sample_id
            or provenance.get("dataset_revision") != DATASET_REVISION
        ):
            raise FullCrelloPreparationError(f"invalid cache provenance for {sample_id}")
        actual_files = _tree_file_snapshot(
            tree, exclude=(CACHE_PROVENANCE_FILENAME,)
        )
        if provenance.get("files") != actual_files:
            raise FullCrelloPreparationError(
                f"cache file snapshot mismatch for {sample_id}"
            )
        if provenance.get("files_snapshot_sha256") != _sha256_bytes(
            _canonical_json_bytes(actual_files)
        ):
            raise FullCrelloPreparationError(
                f"cache file snapshot hash mismatch for {sample_id}"
            )
    return {
        "sample_id": sample_id,
        "meta_sha256": _sha256_file(meta_path),
        "file_count": len(_tree_file_snapshot(tree)),
    }


def _verify_existing_text_sidecar(sample_dir: Path, sample_id: str) -> None:
    sidecar_path = sample_dir / TEXT_BITMAP_SIDECAR_FILENAME
    sidecar = _strict_json(sidecar_path)
    if (
        not isinstance(sidecar, dict)
        or sidecar.get("version") != TEXT_BITMAP_SIDECAR_VERSION
        or sidecar.get("sample_id") != sample_id
        or not isinstance(sidecar.get("bitmaps"), dict)
    ):
        raise FullCrelloPreparationError(f"invalid text sidecar for {sample_id}")
    for filename in sidecar["bitmaps"].values():
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or not (sample_dir / filename).is_file()
        ):
            raise FullCrelloPreparationError(
                f"sidecar bitmap is missing or unsafe for {sample_id}: {filename}"
            )


def _build_cache_tree(row: Mapping[str, Any], staging: Path, final_dir: Path) -> None:
    sample_id, count = _validate_row(row)
    types = row["type"]
    images = row["image"]
    texts = row["text"]
    canvas_width = int(row["canvas_width"])
    canvas_height = int(row["canvas_height"])
    elements: List[Dict[str, Any]] = []
    text_bitmaps: Dict[str, str] = {}

    from layout_agent.output.step27_audit_underlay_assets import _classify_underlay

    for index in range(count):
        type_code = int(types[index])
        descriptor: Dict[str, Any] = {
            "idx": index,
            "type_code": type_code,
            "left": float(row["left"][index]),
            "top": float(row["top"][index]),
            "width": float(row["width"][index]),
            "height": float(row["height"][index]),
        }
        image = images[index]
        if type_code == 1:
            descriptor["content"] = texts[index]
            descriptor["kind"] = "text"
            if image is not None:
                filename = f"a3_text_{index:04d}.png"
                _save_image_new(image.convert("RGBA"), staging / filename, image_format="PNG")
                text_bitmaps[str(index)] = filename
        elif type_code in (0, 2, 3, 4):
            if image is None:
                descriptor["kind"] = f"type{type_code}_no_image"
            else:
                label, signals = _classify_underlay(
                    image,
                    descriptor["width"],
                    descriptor["height"],
                    canvas_width,
                    canvas_height,
                )
                descriptor["classifier_label"] = label
                descriptor["classifier_signals"] = signals
                if label == "shape":
                    filename = f"asset_{index:02d}_underlay.png"
                    descriptor["kind"] = "underlay"
                elif label == "full_canvas":
                    filename = f"asset_{index:02d}_background.png"
                    descriptor["kind"] = "background_candidate"
                else:
                    filename = f"asset_{index:02d}_image.png"
                    descriptor["kind"] = "image"
                _save_image_new(image.convert("RGBA"), staging / filename, image_format="PNG")
                descriptor["asset_ref"] = str(final_dir / filename)
        else:
            descriptor["kind"] = f"type{type_code}"
        elements.append(descriptor)

    preview = row.get("preview")
    if preview is None:
        raise FullCrelloPreparationError(f"row {sample_id} has no preview")
    _save_image_new(preview.convert("RGB"), staging / "ground_truth_preview.jpg", image_format="JPEG")
    meta = {
        "id": sample_id,
        "title": row.get("title", ""),
        "canvas_width": canvas_width,
        "canvas_height": canvas_height,
        "n_elements": count,
        "elements": elements,
    }
    _write_new(
        staging / "meta.json",
        json.dumps(meta, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"),
    )
    _write_new(
        staging / TEXT_BITMAP_SIDECAR_FILENAME,
        _canonical_json_bytes(
            {
                "version": TEXT_BITMAP_SIDECAR_VERSION,
                "sample_id": sample_id,
                "bitmaps": text_bitmaps,
            }
        ),
    )
    file_snapshot = _tree_file_snapshot(staging)
    provenance = {
        "schema_version": CACHE_PROVENANCE_VERSION,
        "dataset": DATASET,
        "dataset_revision": DATASET_REVISION,
        "split": SPLIT,
        "sample_id": sample_id,
        "files": file_snapshot,
        "files_snapshot_sha256": _sha256_bytes(_canonical_json_bytes(file_snapshot)),
        "api_model_calls": 0,
        "paid_cost_usd": 0.0,
    }
    _write_new(staging / CACHE_PROVENANCE_FILENAME, _pretty_json_bytes(provenance))


def materialize_cache_row(row: Mapping[str, Any], crello_root: Path) -> Dict[str, Any]:
    """Atomically materialize one absent cache directory; never replace a final."""
    sample_id, _ = _validate_row(row)
    crello_root = crello_root.resolve()
    final_dir = crello_root / f"crello_{sample_id}"
    crello_root.mkdir(parents=True, exist_ok=True)
    if _path_lexists(final_dir):
        raise FullCrelloPreparationError(f"cache destination already exists: {final_dir}")
    staging: Optional[Path] = None
    try:
        staging = Path(
            tempfile.mkdtemp(prefix=f".crello_{sample_id}.staging-", dir=str(crello_root))
        )
        _build_cache_tree(row, staging, final_dir)
        _verify_cache_tree(
            staging,
            final_dir=final_dir,
            sample_id=sample_id,
            require_provenance=True,
        )
        _rename_directory_noreplace(staging, final_dir)
        staging = None
        return _verify_cache_tree(
            final_dir,
            final_dir=final_dir,
            sample_id=sample_id,
            require_provenance=True,
        )
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)


def _publish_text_sidecar(row: Mapping[str, Any], sample_dir: Path) -> str:
    """Publish missing text PNGs and sidecar without changing existing meta bytes."""
    sample_id, count = _validate_row(row)
    final_sidecar = sample_dir / TEXT_BITMAP_SIDECAR_FILENAME
    meta_path = sample_dir / "meta.json"
    meta_before = meta_path.read_bytes()
    meta = _strict_json(meta_path)
    if meta.get("id") != sample_id or len(meta.get("elements", [])) != count:
        raise FullCrelloPreparationError(f"row/cache mismatch for {sample_id}")
    if final_sidecar.exists():
        _verify_existing_text_sidecar(sample_dir, sample_id)
        return "verified-existing"

    bitmaps: Dict[str, str] = {}
    for index, type_code in enumerate(row["type"]):
        if int(type_code) != 1:
            continue
        image = row["image"][index]
        if image is None:
            continue
        filename = f"a3_text_{index:04d}.png"
        final = sample_dir / filename
        temp = sample_dir / f".{filename}.{os.getpid()}.tmp"
        try:
            _save_image_new(image.convert("RGBA"), temp, image_format="PNG")
            try:
                os.link(temp, final)
            except FileExistsError:
                if final.read_bytes() != temp.read_bytes():
                    raise FullCrelloPreparationError(
                        f"refusing to overwrite different text bitmap: {final}"
                    )
        finally:
            temp.unlink(missing_ok=True)
        bitmaps[str(index)] = filename
    status = _write_once_or_verify(
        final_sidecar,
        _canonical_json_bytes(
            {
                "version": TEXT_BITMAP_SIDECAR_VERSION,
                "sample_id": sample_id,
                "bitmaps": bitmaps,
            }
        ),
    )
    if meta_path.read_bytes() != meta_before:
        raise FullCrelloPreparationError(f"meta.json changed during sidecar publication: {sample_id}")
    return status


def _cache_inventory(official_ids: Sequence[str], crello_root: Path) -> Dict[str, Any]:
    valid: List[Dict[str, str]] = []
    missing: List[str] = []
    missing_sidecars: List[str] = []
    for sample_id in official_ids:
        sample_dir = crello_root / f"crello_{sample_id}"
        meta_path = sample_dir / "meta.json"
        if not _path_lexists(sample_dir):
            missing.append(sample_id)
            continue
        if not sample_dir.is_dir() or not meta_path.is_file():
            raise FullCrelloPreparationError(f"partial cache path consumes ID {sample_id}")
        meta = _strict_json(meta_path)
        if not isinstance(meta, dict) or meta.get("id") != sample_id:
            raise FullCrelloPreparationError(f"cache ID mismatch for {sample_id}")
        valid.append({"sample_id": sample_id, "meta_sha256": _sha256_file(meta_path)})
        if not (sample_dir / TEXT_BITMAP_SIDECAR_FILENAME).is_file():
            missing_sidecars.append(sample_id)
        else:
            _verify_existing_text_sidecar(sample_dir, sample_id)
    return {
        "valid_count": len(valid),
        "missing_count": len(missing),
        "missing_ids": missing,
        "missing_sidecar_count": len(missing_sidecars),
        "missing_sidecar_ids": missing_sidecars,
        "meta_snapshot_sha256": _sha256_bytes(_canonical_json_bytes(valid)),
    }


def _completed_artifact_snapshot(
    completed_artifacts: Mapping[str, Tuple[Path, str]],
) -> Dict[str, Dict[str, Any]]:
    snapshot: Dict[str, Dict[str, Any]] = {}
    for name, (path, expected_sha256) in completed_artifacts.items():
        if not path.is_file():
            raise FullCrelloPreparationError(f"completed artifact is missing: {path}")
        actual = _sha256_file(path)
        if actual != expected_sha256:
            raise FullCrelloPreparationError(
                f"completed artifact hash mismatch for {path}: {actual}"
            )
        snapshot[name] = {
            "path": _display_path(path),
            "sha256": actual,
            "size": path.stat().st_size,
        }
    return snapshot


def verify_batch_bundle(
    bundle_dir: Path,
    *,
    snapshot_dir: Path,
    existing_ids_path: Path,
    expected_official_count: int = EXPECTED_TEST_COUNT,
) -> Dict[str, Any]:
    snapshot = verify_id_snapshot(snapshot_dir, expected_count=expected_official_count)
    official_ids = snapshot["ids"]
    existing_ids = _load_ids(existing_ids_path)
    manifest = _strict_json(bundle_dir / "manifest.json")
    if manifest.get("schema_version") != BATCH_SCHEMA_VERSION:
        raise FullCrelloPreparationError("batch manifest schema mismatch")
    if manifest.get("dataset", {}).get("revision") != DATASET_REVISION:
        raise FullCrelloPreparationError("batch manifest dataset revision mismatch")
    if manifest.get("coverage", {}).get("official_count") != expected_official_count:
        raise FullCrelloPreparationError("batch manifest official count mismatch")
    if manifest.get("dataset", {}).get("ordered_ids_file_sha256") != snapshot[
        "provenance"
    ]["ordered_ids_file_sha256"]:
        raise FullCrelloPreparationError("batch manifest ordered-ID hash mismatch")
    config_path = bundle_dir / "run_config.json"
    if manifest.get("protocol", {}).get("config_sha256") != _sha256_file(config_path):
        raise FullCrelloPreparationError("batch config snapshot hash mismatch")
    from metagpt.ext.agentlayout.a3_config import A3RunConfig

    config = A3RunConfig.model_validate_json(config_path.read_bytes())
    if config.dataset_split != "crello-official-test-n1971-batched-v1":
        raise FullCrelloPreparationError("batch config dataset_split mismatch")
    if manifest.get("completed_batch", {}).get("ids_sha256") != _sha256_file(
        existing_ids_path
    ):
        raise FullCrelloPreparationError("completed batch ID hash mismatch")
    if manifest.get("authorization", {}).get("paid_generation_authorized") is not False:
        raise FullCrelloPreparationError("batch bundle must remain paid-unauthorized")
    rows = manifest.get("new_batches")
    if not isinstance(rows, list) or not rows:
        raise FullCrelloPreparationError("batch manifest new_batches must be a non-empty list")
    flattened: List[str] = []
    run_ids: set[str] = set()
    evaluation_ids: set[str] = set()
    batch_size = manifest["coverage"].get("batch_size")
    if not isinstance(batch_size, int) or batch_size <= 0:
        raise FullCrelloPreparationError("batch manifest batch_size is invalid")
    for expected_index, row in enumerate(rows, start=1):
        if row.get("index") != expected_index:
            raise FullCrelloPreparationError("batch indices are not consecutive")
        ids_path = bundle_dir / row["ids_file"]
        ids = _load_ids(ids_path)
        if len(ids) != row.get("count") or _sha256_file(ids_path) != row.get("ids_sha256"):
            raise FullCrelloPreparationError(f"batch {expected_index} IDs mismatch")
        if expected_index < len(rows) and len(ids) != batch_size:
            raise FullCrelloPreparationError(f"batch {expected_index} is not full-sized")
        if expected_index == len(rows) and not (1 <= len(ids) <= batch_size):
            raise FullCrelloPreparationError("final batch size is invalid")
        if row.get("status") != "planned-not-authorized":
            raise FullCrelloPreparationError(f"batch {expected_index} status is not locked")
        run_id = row.get("run_id")
        evaluation_id = row.get("evaluation_id")
        if not isinstance(run_id, str) or run_id in run_ids:
            raise FullCrelloPreparationError("new run IDs are invalid or duplicated")
        if not isinstance(evaluation_id, str) or evaluation_id in evaluation_ids:
            raise FullCrelloPreparationError("new evaluation IDs are invalid or duplicated")
        run_ids.add(run_id)
        evaluation_ids.add(evaluation_id)
        flattened.extend(ids)
    if len(flattened) != len(set(flattened)):
        raise FullCrelloPreparationError("new batches overlap")
    if set(flattened) & set(existing_ids):
        raise FullCrelloPreparationError("new batches overlap completed N=100")
    if set(flattened) | set(existing_ids) != set(official_ids):
        raise FullCrelloPreparationError("completed plus new batches do not cover official test")
    expected_new = expected_official_count - len(existing_ids)
    if len(flattened) != expected_new or manifest["coverage"].get("new_count") != expected_new:
        raise FullCrelloPreparationError("new batch count mismatch")
    if manifest["coverage"].get("new_batch_count") != len(rows):
        raise FullCrelloPreparationError("new batch manifest count mismatch")
    last_batch_count = len(_load_ids(bundle_dir / rows[-1]["ids_file"]))
    if manifest["coverage"].get("final_batch_count") != last_batch_count:
        raise FullCrelloPreparationError("final batch count mismatch")
    return {
        "official_count": len(official_ids),
        "reused_count": len(existing_ids),
        "new_count": len(flattened),
        "new_batch_count": len(rows),
        "manifest_sha256": _sha256_file(bundle_dir / "manifest.json"),
    }


def build_batch_bundle(
    bundle_dir: Path,
    *,
    snapshot_dir: Path,
    existing_ids_path: Path,
    config_path: Path,
    crello_root: Path,
    runs_root: Path = DEFAULT_RUNS_ROOT,
    evaluations_root: Path = DEFAULT_EVALUATIONS_ROOT,
    batch_size: int = BATCH_SIZE,
    seed: int = BATCH_SEED,
    expected_official_count: int = EXPECTED_TEST_COUNT,
    expected_existing_ids_sha256: Optional[str] = EXPECTED_EXISTING_IDS_SHA256,
    completed_artifacts: Mapping[str, Tuple[Path, str]] = COMPLETED_ARTIFACTS,
) -> Tuple[str, Dict[str, Any]]:
    snapshot = verify_id_snapshot(snapshot_dir, expected_count=expected_official_count)
    official_ids = snapshot["ids"]
    existing_ids = _load_ids(existing_ids_path)
    existing_payload = existing_ids_path.read_bytes()
    if expected_existing_ids_sha256 is not None:
        actual = _sha256_bytes(existing_payload)
        if actual != expected_existing_ids_sha256:
            raise FullCrelloPreparationError(
                f"completed N=100 ID hash mismatch: {actual}"
            )
    if not set(existing_ids) <= set(official_ids):
        raise FullCrelloPreparationError("completed N=100 is not a subset of official test")
    if batch_size <= 0:
        raise FullCrelloPreparationError("batch_size must be positive")
    config_bytes = config_path.read_bytes()
    from metagpt.ext.agentlayout.a3_config import A3RunConfig

    config = A3RunConfig.model_validate_json(config_bytes)
    if config.dataset_split != "crello-official-test-n1971-batched-v1":
        raise FullCrelloPreparationError("full-test config dataset_split mismatch")
    remaining = sorted(set(official_ids) - set(existing_ids))
    random.Random(seed).shuffle(remaining)
    batches = [remaining[index : index + batch_size] for index in range(0, len(remaining), batch_size)]
    expected_batch_count = (len(remaining) + batch_size - 1) // batch_size
    if len(batches) != expected_batch_count:
        raise AssertionError("batch partition arithmetic changed")
    cache = _cache_inventory(official_ids, crello_root)
    if cache["missing_count"] or cache["missing_sidecar_count"]:
        raise FullCrelloPreparationError(
            "cache readiness incomplete: materialize all official caches and text sidecars first"
        )
    artifacts = _completed_artifact_snapshot(completed_artifacts)

    batch_rows: List[Dict[str, Any]] = []
    for index, ids in enumerate(batches, start=1):
        count = len(ids)
        suffix = f"{index:03d}-n{count}"
        run_id = f"a3-crello-test-batch-{suffix}-t2-l0-v1"
        evaluation_id = f"a3-crello-test-batch-{suffix}-sega-v1"
        run_dir = runs_root / run_id
        evaluation_dir = evaluations_root / evaluation_id
        if _path_lexists(run_dir) or _path_lexists(evaluation_dir):
            raise FullCrelloPreparationError(
                f"new write-once target already exists: {run_dir if _path_lexists(run_dir) else evaluation_dir}"
            )
        filename = f"batch_{index:03d}_n{count}.json"
        ids_payload = _pretty_json_bytes(ids)
        batch_rows.append(
            {
                "index": index,
                "batch_id": f"crello-test-{suffix}",
                "count": count,
                "ids_file": filename,
                "ids_sha256": _sha256_bytes(ids_payload),
                "run_id": run_id,
                "run_dir": _display_path(run_dir),
                "evaluation_id": evaluation_id,
                "evaluation_dir": _display_path(evaluation_dir),
                "tree_arm": "T2",
                "analyst_arm": "vision",
                "nominal_calls": 7 * count,
                "code_retry_max_calls": 21 * count,
                "operational_attempt_stop": 850 if count == 100 else 610,
                "usd_stop": 7.0 if count == 100 else 5.0,
                "input_token_ceiling": None,
                "output_token_ceiling": None,
                "status": "planned-not-authorized",
            }
        )

    manifest = {
        "schema_version": BATCH_SCHEMA_VERSION,
        "dataset": {
            "name": DATASET,
            "revision": DATASET_REVISION,
            "repository_last_modified": DATASET_LAST_MODIFIED,
            "split": SPLIT,
            "split_counts": EXPECTED_SPLIT_COUNTS,
            "ordered_ids_path": _display_path(snapshot_dir / "ordered_ids.json"),
            "ordered_ids_file_sha256": snapshot["provenance"]["ordered_ids_file_sha256"],
            "ordered_ids_canonical_sha256": snapshot["provenance"][
                "ordered_ids_canonical_sha256"
            ],
        },
        "coverage": {
            "official_count": len(official_ids),
            "reused_count": len(existing_ids),
            "new_count": len(remaining),
            "new_batch_count": len(batches),
            "batch_size": batch_size,
            "final_batch_count": len(batches[-1]),
            "seed": seed,
            "algorithm": "sorted-remaining-ids-shuffle-seed42-chunks.v1",
        },
        "protocol": {
            "config_source": _display_path(config_path),
            "config_file": "run_config.json",
            "config_sha256": _sha256_bytes(config_bytes),
            "model": "gpt-5.4-mini-2026-03-17",
            "foreground_protocol": "P-Full",
            "renderer": "R3",
            "loop": "L0",
            "tree_arm": "T2",
            "analyst_arm": "vision",
            "candidates_per_sample": 3,
        },
        "completed_batch": {
            "ids_path": _display_path(existing_ids_path),
            "ids_sha256": _sha256_bytes(existing_payload),
            "count": len(existing_ids),
            "run_id": "a3-general-n100-t2-l0-01",
            "evaluation_id": "a3-general-n100-sega-v1",
            "artifacts": artifacts,
            "immutable": True,
        },
        "cache_snapshot": cache,
        "new_batches": batch_rows,
        "budget": {
            "expected_new_usd_low": 75.0,
            "expected_new_usd_high": 85.0,
            "global_usd_stop": 120.0,
            "exact_token_ceilings_status": "pending-full-readiness-dry-run",
        },
        "authorization": {
            "paid_generation_authorized": False,
            "api_model_calls_performed_by_bundle": 0,
            "paid_cost_usd": 0.0,
        },
    }

    def build(staging: Path) -> None:
        _write_new(staging / "run_config.json", config_bytes)
        for ids, row in zip(batches, batch_rows):
            _write_new(staging / row["ids_file"], _pretty_json_bytes(ids))
        _write_new(staging / "manifest.json", _pretty_json_bytes(manifest))

    return _publish_directory(
        bundle_dir,
        build=build,
        verify=lambda path: verify_batch_bundle(
            path,
            snapshot_dir=snapshot_dir,
            existing_ids_path=existing_ids_path,
            expected_official_count=expected_official_count,
        ),
    )


def materialize_missing_and_sidecars(
    *,
    snapshot_dir: Path,
    crello_root: Path,
    load_dataset_fn: Optional[Callable[..., Any]] = None,
    min_free_bytes: int = MIN_FREE_BYTES,
) -> Dict[str, Any]:
    snapshot = verify_id_snapshot(snapshot_dir)
    official_ids = snapshot["ids"]
    inventory = _cache_inventory(official_ids, crello_root)
    available = shutil.disk_usage(crello_root).free
    if available < min_free_bytes:
        raise FullCrelloPreparationError(
            f"disk hard stop: {available} bytes available < {min_free_bytes}"
        )
    stale_staging = sorted(crello_root.glob(".crello_*.staging-*"))
    if stale_staging:
        raise FullCrelloPreparationError(
            f"stale cache staging directories must be audited first: {stale_staging[:3]}"
        )
    missing_cache = set(inventory["missing_ids"])
    missing_sidecar = set(inventory["missing_sidecar_ids"])
    targets = missing_cache | missing_sidecar
    if not targets:
        return {
            "scanned": 0,
            "cache_created": 0,
            "sidecars_created": 0,
            "remaining": [],
            "api_model_calls": 0,
            "paid_cost_usd": 0.0,
        }
    if load_dataset_fn is None:
        from datasets import load_dataset as load_dataset_fn  # type: ignore[no-redef]

    dataset = load_dataset_fn(
        DATASET,
        split=SPLIT,
        streaming=True,
        revision=DATASET_REVISION,
    )
    cache_created = 0
    sidecars_created = 0
    done: set[str] = set()
    scanned = 0
    for scanned, row in enumerate(dataset, start=1):
        sample_id = str(row.get("id", ""))
        if sample_id not in targets or sample_id in done:
            continue
        current_available = shutil.disk_usage(crello_root).free
        if current_available < min_free_bytes:
            raise FullCrelloPreparationError(
                f"disk hard stop during scan: {current_available} bytes < {min_free_bytes}"
            )
        if sample_id in missing_cache:
            materialize_cache_row(row, crello_root)
            cache_created += 1
        else:
            status = _publish_text_sidecar(row, crello_root / f"crello_{sample_id}")
            if status == "created":
                sidecars_created += 1
        done.add(sample_id)
        if done == targets:
            break
    remaining = sorted(targets - done)
    if remaining:
        raise FullCrelloPreparationError(
            f"pinned dataset scan ended with {len(remaining)} unresolved targets"
        )
    final_inventory = _cache_inventory(official_ids, crello_root)
    if final_inventory["missing_count"] or final_inventory["missing_sidecar_count"]:
        raise FullCrelloPreparationError("post-materialization cache inventory is incomplete")
    return {
        "scanned": scanned,
        "cache_created": cache_created,
        "sidecars_created": sidecars_created,
        "remaining": remaining,
        "available_bytes_before": available,
        "meta_snapshot_sha256": final_inventory["meta_snapshot_sha256"],
        "api_model_calls": 0,
        "paid_cost_usd": 0.0,
    }


def plan_state(
    *,
    snapshot_dir: Path,
    bundle_dir: Path,
    existing_ids_path: Path,
    crello_root: Path,
) -> Dict[str, Any]:
    existing_ids = _load_ids(existing_ids_path)
    cached_dirs = sum(1 for path in crello_root.glob("crello_*") if path.is_dir())
    available = shutil.disk_usage(crello_root).free
    return {
        "schema_version": "a3.crello-full-test-plan.v1",
        "dataset": DATASET,
        "revision": DATASET_REVISION,
        "split": SPLIT,
        "expected_test_count": EXPECTED_TEST_COUNT,
        "completed_ids_count": len(existing_ids),
        "local_cache_directory_count": cached_dirs,
        "snapshot_exists": _path_lexists(snapshot_dir),
        "bundle_exists": _path_lexists(bundle_dir),
        "available_bytes": available,
        "minimum_materialization_free_bytes": MIN_FREE_BYTES,
        "test_parquet_transfer_upper_bound_bytes": TEST_PARQUET_BYTES,
        "api_model_calls": 0,
        "paid_cost_usd": 0.0,
    }


def _add_common_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT_DIR)
    parser.add_argument("--bundle-dir", type=Path, default=DEFAULT_BATCH_DIR)
    parser.add_argument("--existing-ids", type=Path, default=DEFAULT_EXISTING_IDS)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--crello-root", type=Path, default=DEFAULT_CRELLO_ROOT)
    parser.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS_ROOT)
    parser.add_argument("--evaluations-root", type=Path, default=DEFAULT_EVALUATIONS_ROOT)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="Print local zero-cost readiness; no network or writes.")
    _add_common_paths(plan)
    snapshot = sub.add_parser(
        "snapshot-ids",
        help="Project pinned official test IDs and atomically publish their snapshot.",
    )
    _add_common_paths(snapshot)
    snapshot.add_argument("--allow-network", action="store_true")
    build = sub.add_parser(
        "build-batches", help="Build the deterministic 19-batch bundle from a frozen snapshot."
    )
    _add_common_paths(build)
    verify = sub.add_parser("verify-batches", help="Strictly reload and verify a batch bundle.")
    _add_common_paths(verify)
    materialize = sub.add_parser(
        "materialize",
        help="Stream pinned dataset rows to create missing caches and text sidecars.",
    )
    _add_common_paths(materialize)
    materialize.add_argument("--allow-dataset-download", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.command == "plan":
            result = plan_state(
                snapshot_dir=args.snapshot_dir,
                bundle_dir=args.bundle_dir,
                existing_ids_path=args.existing_ids,
                crello_root=args.crello_root,
            )
        elif args.command == "snapshot-ids":
            if not args.allow_network:
                print(
                    json.dumps(
                        {
                            "authorized": False,
                            "reason": "snapshot-ids requires --allow-network",
                            "api_model_calls": 0,
                            "paid_cost_usd": 0.0,
                        },
                        indent=2,
                    )
                )
                return 2
            status, verified = snapshot_ordered_ids(args.snapshot_dir)
            result = {"status": status, **{key: value for key, value in verified.items() if key != "ids"}}
        elif args.command == "build-batches":
            status, verified = build_batch_bundle(
                args.bundle_dir,
                snapshot_dir=args.snapshot_dir,
                existing_ids_path=args.existing_ids,
                config_path=args.config,
                crello_root=args.crello_root,
                runs_root=args.runs_root,
                evaluations_root=args.evaluations_root,
            )
            result = {"status": status, **verified, "api_model_calls": 0, "paid_cost_usd": 0.0}
        elif args.command == "verify-batches":
            result = {
                **verify_batch_bundle(
                    args.bundle_dir,
                    snapshot_dir=args.snapshot_dir,
                    existing_ids_path=args.existing_ids,
                ),
                "api_model_calls": 0,
                "paid_cost_usd": 0.0,
            }
        else:
            if not args.allow_dataset_download:
                print(
                    json.dumps(
                        {
                            "authorized": False,
                            "reason": "materialize requires --allow-dataset-download",
                            "transfer_upper_bound_bytes": TEST_PARQUET_BYTES,
                            "minimum_free_bytes": MIN_FREE_BYTES,
                            "api_model_calls": 0,
                            "paid_cost_usd": 0.0,
                        },
                        indent=2,
                    )
                )
                return 2
            result = materialize_missing_and_sidecars(
                snapshot_dir=args.snapshot_dir,
                crello_root=args.crello_root,
            )
    except (FullCrelloPreparationError, FileNotFoundError, ValueError) as error:
        print(f"ERROR: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
