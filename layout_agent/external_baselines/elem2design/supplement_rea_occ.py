#!/usr/bin/env python
"""Close the deferred Rea/Occ axes for the Elem2Design comparison (meta env).

PKU-convention Rea/Occ are functions of the *background image* plus element
boxes — never of the rendered candidate — and both arms share the exact same
per-sample backgrounds. So no candidate rendering is needed:

* A3-T2 per-sample Rea/Occ come verbatim from the frozen formal bundle
  (``a3-relation-n100-t0-t2-t3-sega-v1``), including its background
  descriptors;
* E2D values are computed here on the identical background / BASNet+ISNet
  saliency stack with E2D's boxes (same ``drop_invalid`` preprocessing).

The output also recomputes Holm over the now-complete geometry family
{Ali, Ove, Rea, Occ} (4 tests), superseding the interim 2-test Holm in the
main compare bundle for the geometry family. Zero paid API.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PIL import Image  # noqa: E402

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
    metric_occlusion,
    metric_readability,
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

SCHEMA_VERSION = "a3.external-baseline-supplement.v1"


def load_background(row: dict, t2_run_dir: Path) -> np.ndarray:
    canvas = row["canvas"]
    width, height = int(canvas["width"]), int(canvas["height"])
    background = row["background"]
    if background["kind"] == "blank_canvas":
        return np.full((height, width, 3), tuple(background["rgb"]), dtype=np.uint8)
    r3_manifest = json.loads(
        (t2_run_dir / "samples" / row["sample_id"] / "inputs" / "r3"
         / "r3_asset_manifest.json").read_text()
    )
    asset = next(
        a for a in r3_manifest["assets"] if a["asset_id"] == background["asset_id"]
    )
    path = Path(asset["asset_ref"])
    if sha256_file(path) != background["asset_sha256"]:
        raise ValueError(f"{row['sample_id']}: background hash changed since freeze")
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        if rgb.size != (width, height):
            rgb = rgb.resize((width, height), Image.LANCZOS)
        return np.asarray(rgb, dtype=np.uint8)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--external-run-dir", required=True, type=Path)
    parser.add_argument("--frozen-sega-bundle", required=True, type=Path)
    parser.add_argument("--t2-run-dir", required=True, type=Path)
    parser.add_argument("--compare-bundle", required=True, type=Path,
                        help="main compare bundle (for Ali/Ove raw p, Holm(4) refresh)")
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()

    from metagpt.ext.agentlayout.evaluation.saliency_basnet_isnet import (  # noqa: E402
        basnet_isnet_saliency,
    )

    t2_rows = {
        r["sample_id"]: r
        for r in (
            json.loads(line)
            for line in (args.frozen_sega_bundle / "per_sample.jsonl").read_text().splitlines()
        )
        if "t2" in r.get("run_id", "") and r["status"] == "evaluated"
    }
    id_maps = json.loads((args.external_run_dir / "id_maps.json").read_text())
    sample_ids = json.loads((args.external_run_dir / "sample_ids.json").read_text())

    a3_arm, e2d_arm = [], []
    for sample_id in sample_ids:
        frozen = t2_rows.get(sample_id)
        a3_arm.append({
            "sample_id": sample_id, "arm": "A3-T2",
            "status": "completed" if frozen else "generation_failure",
            "rea": frozen["metrics"]["Rea"]["value"] if frozen else None,
            "occ": frozen["metrics"]["Occ"]["value"] if frozen else None,
        })
        candidate_path = args.external_run_dir / "samples" / sample_id / "candidate.json"
        row = {"schema_version": SCHEMA_VERSION, "sample_id": sample_id,
               "arm": "elem2design"}
        if not candidate_path.exists() or frozen is None:
            row.update(status="failed" if not candidate_path.exists() else "completed",
                       rea=None, occ=None)
            e2d_arm.append(row)
            continue
        candidate = Candidate.model_validate_json(candidate_path.read_text())
        texts = id_maps[sample_id]["texts"]
        canvas = frozen["canvas"]
        width, height = int(canvas["width"]), int(canvas["height"])
        layout = [
            (CLS_TEXT if texts.get(el.id) else CLS_IMAGE_LOGO,
             to_xyxy(el.left, el.top, el.width, el.height))
            for el in candidate.elements
        ]
        layout = drop_invalid_elements(layout, width, height)
        background = load_background(frozen, args.t2_run_dir.resolve())
        saliency = basnet_isnet_saliency(background)
        row.update(
            status="completed",
            rea=metric_readability([layout], [background], width, height),
            occ=metric_occlusion([layout], [saliency], width, height),
            background_kind=frozen["background"]["kind"],
        )
        e2d_arm.append(row)
        print(f"{sample_id} rea={row['rea']:.6f} occ={row['occ']:.6f}", flush=True)

    comparisons = []
    for metric in ("rea", "occ"):
        entry = compare_arms(a3_arm, e2d_arm, metric)
        entry["comparison"] = "A3-T2_vs_elem2design"
        entry["direction"] = "A3-T2 - elem2design (lower is better)"
        comparisons.append(entry)

    main_compare = json.loads((args.compare_bundle / "aggregate.json").read_text())
    geometry_raw = {
        e["metric"]: e["sign_test_p_raw"]
        for e in main_compare["comparisons"] if e["metric"] in ("ali", "ove")
    }
    family = [
        {"metric": "ali", "sign_test_p_raw": geometry_raw["ali"]},
        {"metric": "ove", "sign_test_p_raw": geometry_raw["ove"]},
        *[{"metric": e["metric"], "sign_test_p_raw": e["sign_test_p_raw"]}
          for e in comparisons],
    ]
    for member, holm in zip(family, holm_adjust([m["sign_test_p_raw"] for m in family])):
        member["sign_test_p_holm4"] = holm
    for entry in comparisons:
        entry["sign_test_p_holm4"] = next(
            m["sign_test_p_holm4"] for m in family if m["metric"] == entry["metric"]
        )

    def arm_means(rows):
        completed = [r for r in rows if r["status"] == "completed"]
        return {
            m: {"mean": (lambda v: sum(v) / len(v) if v else None)(
                    [r[m] for r in completed if r.get(m) is not None]),
                "n": len([r for r in completed if r.get(m) is not None])}
            for m in ("rea", "occ")
        }

    aggregate = {
        "schema_version": SCHEMA_VERSION,
        "evaluation_id": args.evaluation_id,
        "note": (
            "Rea/Occ supplement on identical per-sample backgrounds and the "
            "frozen BASNet+ISNet stack; A3-T2 values verbatim from the frozen "
            "formal bundle. geometry_family_holm4 supersedes the interim "
            "2-test Holm for the geometry family."
        ),
        "arm_metric_means": {"A3-T2": arm_means(a3_arm), "elem2design": arm_means(e2d_arm)},
        "comparisons": comparisons,
        "geometry_family_holm4": family,
        "bootstrap": {"seed": BOOTSTRAP_SEED, "resamples": BOOTSTRAP_RESAMPLES},
    }

    out = args.output_root / SCHEMA_VERSION / args.evaluation_id
    per_sample_bytes = b"".join(canonical_json_bytes(r) for r in a3_arm + e2d_arm)
    aggregate_bytes = canonical_json_bytes(aggregate)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "evaluation_id": args.evaluation_id,
        "created_at": utc_now(),
        "frozen_sega_per_sample_sha256": sha256_file(
            args.frozen_sega_bundle / "per_sample.jsonl"
        ),
        "compare_aggregate_sha256": sha256_file(args.compare_bundle / "aggregate.json"),
        "code_sha256": {"supplement_rea_occ.py": sha256_file(Path(__file__))},
        "artifact_sha256": {
            "aggregate.json": sha256_bytes(aggregate_bytes),
            "per_sample.jsonl": sha256_bytes(per_sample_bytes),
        },
        "write_once": True,
    }
    write_bytes_once(out / "aggregate.json", aggregate_bytes)
    write_bytes_once(out / "per_sample.jsonl", per_sample_bytes)
    write_json_once(out / "evaluation_manifest.json", manifest)
    print(json.dumps({"comparisons": comparisons, "geometry_family_holm4": family},
                     indent=1, default=str))
    print(f"bundle -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
