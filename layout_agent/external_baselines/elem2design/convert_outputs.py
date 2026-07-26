#!/usr/bin/env python
"""Convert raw Elem2Design predictions into per-sample A3-style candidates.

Pure JSON transformation (no model, no torch): reads the shard JSONL files
plus ``id_maps.json``, maps official element ``index`` back to A3
``asset_id``, validates geometry fail-closed, and writes one
``samples/<sample_id>/candidate.json`` (or ``error.json``) per sample plus a
``run_summary.json``.  Runs in any Python 3.9+ environment.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from common_protocol import (  # noqa: E402
    PROTOCOL_VERSION,
    convert_sample,
    parse_elements,
    split_prediction_turns,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir

    id_maps = json.loads((run_dir / "id_maps.json").read_text())
    sample_ids = json.loads((run_dir / "sample_ids.json").read_text())

    records = {}
    for shard in sorted(run_dir.glob("raw_predictions_shard*.jsonl")):
        for line in shard.read_text().splitlines():
            record = json.loads(line)
            records[record["id"]] = record

    samples_dir = run_dir / "samples"
    summary = {"schema_version": PROTOCOL_VERSION, "samples": [], "completed": 0, "failed": 0}
    for sample_id in sample_ids:
        sample_dir = samples_dir / sample_id
        sample_dir.mkdir(parents=True, exist_ok=True)
        entry = {"sample_id": sample_id}

        def fail(reason: str, errors=None) -> None:
            entry.update(status="failed", reason=reason)
            (sample_dir / "error.json").write_text(
                json.dumps({"reason": reason, "errors": errors or []}, ensure_ascii=False)
            )

        record = records.get(sample_id)
        mapping = id_maps.get(sample_id)
        if mapping is None:
            fail("prepare_failure: sample absent from id_maps.json")
        elif record is None:
            fail("inference_missing: no raw prediction record")
        else:
            turns_text = split_prediction_turns(record["predictions"])
            turns = [parse_elements(t) for t in turns_text]
            elements, errors = convert_sample(
                turns, mapping["index_map"]["index_to_asset"], mapping["texts"]
            )
            if errors:
                fail("conversion_errors", errors)
            else:
                candidate = {
                    "candidate_id": "elem2design",
                    "elements": elements,
                }
                (sample_dir / "candidate.json").write_text(
                    json.dumps(candidate, ensure_ascii=False)
                )
                entry.update(
                    status="completed",
                    n_elements=len(elements),
                    turn_errors=len(record.get("turn_errors", {})),
                    elapsed_s=record.get("elapsed_s"),
                )
        summary["samples"].append(entry)
        summary["completed" if entry.get("status") == "completed" else "failed"] += 1

    (run_dir / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"completed {summary['completed']}/{len(sample_ids)}, failed {summary['failed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
