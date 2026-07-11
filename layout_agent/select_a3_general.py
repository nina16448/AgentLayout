"""Freeze a model-blind random Crello-General sample before generation.

The selection universe is the locally cached Crello test snapshots.  No
semantic, element-count, asset-type, geometry, or model-output filter is used.
Only unreadable/missing metadata and cache-directory/metadata ID disagreement
are excluded as unavailable source records, and those exclusions are recorded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
from pathlib import Path
from typing import Dict, List, Tuple


SCHEMA_VERSION = "a3.general-selection.v1"
ALGORITHM = "sorted-local-cache-shuffle-prefix.v1"


class GeneralSelectionError(ValueError):
    """The frozen General selection cannot be created or reproduced."""


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def discover_cached_test_pool(
    crello_root: Path,
) -> Tuple[List[str], List[Dict[str, str]], Dict[str, int]]:
    """Return sorted IDs plus a frozen aggregate of their metadata bytes."""
    if not crello_root.is_dir():
        raise GeneralSelectionError(f"Crello root does not exist: {crello_root}")

    pool: List[str] = []
    meta_records: List[Dict[str, str]] = []
    exclusions = {
        "missing_meta": 0,
        "invalid_json": 0,
        "non_object_meta": 0,
        "id_mismatch": 0,
    }
    for sample_dir in sorted(crello_root.glob("crello_*")):
        if not sample_dir.is_dir():
            continue
        sample_id = sample_dir.name.removeprefix("crello_")
        meta_path = sample_dir / "meta.json"
        if not meta_path.is_file():
            exclusions["missing_meta"] += 1
            continue
        payload = meta_path.read_bytes()
        try:
            meta = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            exclusions["invalid_json"] += 1
            continue
        if not isinstance(meta, dict):
            exclusions["non_object_meta"] += 1
            continue
        if str(meta.get("id", "")) != sample_id:
            exclusions["id_mismatch"] += 1
            continue
        pool.append(sample_id)
        meta_records.append({"sample_id": sample_id, "meta_sha256": _sha256(payload)})

    if len(pool) != len(set(pool)):
        raise GeneralSelectionError("cached Crello sample IDs must be unique")
    return pool, meta_records, exclusions


def build_selection(
    crello_root: Path,
    *,
    count: int,
    seed: int,
    documented_raw_test_count: int,
) -> Tuple[List[str], Dict[str, object]]:
    """Build the deterministic selected IDs and their provenance record."""
    if count <= 0:
        raise GeneralSelectionError("count must be positive")
    pool, meta_records, exclusions = discover_cached_test_pool(crello_root)
    if len(pool) < count:
        raise GeneralSelectionError(
            f"requested {count} samples but only {len(pool)} cached records are available"
        )

    shuffled = list(pool)
    random.Random(seed).shuffle(shuffled)
    selected = shuffled[:count]
    selected_payload = _canonical_json(selected)
    provenance: Dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm": ALGORITHM,
        "dataset": "cyberagent/crello",
        "split": "test",
        "documented_raw_test_count": documented_raw_test_count,
        "selection_universe": "locally cached test snapshots with readable matching meta.json",
        "semantic_filter": None,
        "geometry_filter": None,
        "asset_count_filter": None,
        "seed": seed,
        "requested_count": count,
        "cached_directory_count": sum(1 for path in crello_root.glob("crello_*") if path.is_dir()),
        "candidate_pool_count": len(pool),
        "candidate_pool_sha256": _sha256(_canonical_json(pool)),
        "candidate_meta_snapshot_sha256": _sha256(_canonical_json(meta_records)),
        "exclusions": exclusions,
        "selected_ids_sha256": _sha256(selected_payload),
        "selected_count": len(selected),
        "notes": (
            "Selection is frozen before A3 generation and never inspects designer geometry, "
            "semantic richness, model output, candidate renders, or evaluation scores. The "
            "difference from the documented raw split is an explicit local-cache availability "
            "limitation."
        ),
    }
    return selected, provenance


def _write_once_or_verify(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        if path.read_bytes() != payload:
            raise GeneralSelectionError(f"refusing to overwrite different file: {path}")
        return "verified-existing"
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
    return "created"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crello-root", type=Path, required=True)
    parser.add_argument("--ids-output", type=Path, required=True)
    parser.add_argument("--provenance-output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--documented-raw-test-count", type=int, default=1971)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    selected, provenance = build_selection(
        args.crello_root,
        count=args.count,
        seed=args.seed,
        documented_raw_test_count=args.documented_raw_test_count,
    )
    ids_payload = _canonical_json(selected)
    if provenance["selected_ids_sha256"] != _sha256(ids_payload):
        raise AssertionError("selected ID payload hash changed before publication")
    ids_status = _write_once_or_verify(args.ids_output, ids_payload)
    provenance_status = _write_once_or_verify(
        args.provenance_output, _canonical_json(provenance)
    )
    print(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "selected_count": len(selected),
                "selected_ids_sha256": provenance["selected_ids_sha256"],
                "candidate_pool_count": provenance["candidate_pool_count"],
                "ids_output": str(args.ids_output.resolve()),
                "ids_status": ids_status,
                "provenance_output": str(args.provenance_output.resolve()),
                "provenance_status": provenance_status,
                "api_calls": 0,
                "paid_cost_usd": 0.0,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
