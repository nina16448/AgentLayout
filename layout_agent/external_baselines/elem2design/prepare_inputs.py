#!/usr/bin/env python
"""Build Elem2Design test.json from frozen A3 P-Full/R3 inputs (e2d env).

Only R3 bitmaps, text contents, canvas size, and the official predicted
roles enter the model input.  GT geometry/roles and A3 artifacts never do;
a recursive forbidden-key scan and R3 hash checks enforce this fail-closed.

Run inside the `e2d` conda environment (needs the official `common.context`
module from `pip install -e dataset/src`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from common_protocol import (  # noqa: E402
    BASELINE_SEED,
    N_LAYERS,
    PROTOCOL_VERSION,
    assert_placeholder_gpt_turns,
    build_index_map,
    deterministic_layer_order,
    scan_forbidden_keys,
)

from common.context import ContextHandler  # noqa: E402  (official, e2d env)

CONTEXT_CONFIG = {"format": "default", "template": "index-content", "image_token": "<image>"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_sample(sample_id: str, source_run: Path, roles: dict) -> dict:
    pfull = json.loads(
        (source_run / "samples" / sample_id / "inputs" / "pfull" / "asset_manifest.json").read_text()
    )
    r3 = json.loads(
        (source_run / "samples" / sample_id / "inputs" / "r3" / "r3_asset_manifest.json").read_text()
    )
    r3_by_id = {a["asset_id"]: a for a in r3["assets"]}
    role_list = roles[sample_id]

    asset_ids, texts, images, roles_by_asset = [], {}, {}, {}
    input_hashes = {}
    for asset in pfull["assets"]:
        asset_id = asset["asset_id"]
        source_index = int(asset["source_index"])
        if source_index >= len(role_list):
            raise ValueError(f"{sample_id}: source_index {source_index} outside role list")
        r3_asset = r3_by_id[asset_id]
        bitmap = Path(r3_asset["asset_ref"])
        if not bitmap.is_file():
            raise FileNotFoundError(f"{sample_id}: missing R3 bitmap {bitmap}")
        actual = sha256_file(bitmap)
        if actual != r3_asset["sha256"]:
            raise ValueError(f"{sample_id}: R3 hash mismatch for {asset_id}")
        input_hashes[asset_id] = actual
        asset_ids.append(asset_id)
        is_text = asset["media_type"] == "text" or (asset.get("content") not in (None, "", "None"))
        texts[asset_id] = (asset.get("content") or "") if is_text else ""
        images[asset_id] = str(bitmap)
        roles_by_asset[asset_id] = int(role_list[source_index])

    ordered = deterministic_layer_order(asset_ids, roles_by_asset, sample_id)
    index_map = build_index_map(ordered)
    ordered_texts = [texts[a] for a in ordered]
    ordered_images = [images[a] for a in ordered]
    ordered_roles = [roles_by_asset[a] for a in ordered]

    handler = ContextHandler(config=CONTEXT_CONFIG, images=ordered_images, texts=ordered_texts)
    context, image_path = handler.construct_context()

    per_layer_counts = [ordered_roles.count(layer) for layer in range(N_LAYERS)]
    context_by_layer = []
    cnt = 0
    for layer, num in enumerate(per_layer_counts):
        layer_context, layer_images = handler.construct_context(
            start_index=cnt, end_index=cnt + num, index_offset=cnt
        )
        context_by_layer.append(layer_context)
        image_path.extend(layer_images)
        if layer < N_LAYERS - 1:
            image_path.append(f"{sample_id}/layer_{layer}.png")
        cnt += num

    canvas_w, canvas_h = int(pfull["canvas_width"]), int(pfull["canvas_height"])
    preamble = f"A poster of canvas width {canvas_w}px, canvas height {canvas_h}px. "
    conversations = [
        {
            "from": "human",
            "value": preamble + context
            + " Please predict step by step according to the semantics of the elements."
            + " After each prediction, there will be an intermediate rendering result"
            + " as a reference to better make the next prediction.\n\n\n"
            + f"Now predict the background elements: {context_by_layer[0]}",
        },
        {"from": "gpt", "value": "{}"},
    ]
    for layer, name in zip(range(1, N_LAYERS), ["underlay", "logo/image", "text", "embellishment"]):
        conversations.append(
            {
                "from": "human",
                "value": f"current canvas state: <image>. Now predict the {name} elements: {context_by_layer[layer]}",
            }
        )
        conversations.append({"from": "gpt", "value": "{}"})
    assert_placeholder_gpt_turns(conversations)

    annotation = {
        "id": sample_id,
        "image": image_path,
        "conversations": conversations,
        "render_image": ordered_images,
        "render_text": ordered_texts,
    }
    violations = scan_forbidden_keys(annotation)
    if violations:
        raise ValueError(f"{sample_id}: forbidden keys in test.json: {violations}")
    return {
        "annotation": annotation,
        "index_map": index_map,
        "roles": {a: roles_by_asset[a] for a in ordered},
        "texts": texts,
        "input_hashes": input_hashes,
        "canvas": {"canvas_width": canvas_w, "canvas_height": canvas_h},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-ids", required=True, type=Path)
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument("--role-pkl", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()

    sample_ids = json.loads(args.sample_ids.read_text())
    with args.role_pkl.open("rb") as handle:
        roles = pickle.load(handle)

    out = args.output_root
    out.mkdir(parents=True, exist_ok=True)
    for name in ("test.json", "input_manifest.json", "id_maps.json", "sample_ids.json"):
        if (out / name).exists():
            raise FileExistsError(f"refusing to overwrite {out / name}")

    annotations, id_maps, manifest_samples, prepare_failures = [], {}, {}, {}
    for sample_id in sample_ids:
        try:
            built = build_sample(sample_id, args.source_run.resolve(), roles)
        except (ValueError, FileNotFoundError, KeyError) as error:
            prepare_failures[sample_id] = str(error)
            continue
        annotations.append(built["annotation"])
        id_maps[sample_id] = {
            "index_map": built["index_map"],
            "roles": built["roles"],
            "texts": built["texts"],
            "canvas": built["canvas"],
        }
        manifest_samples[sample_id] = {
            "n_elements": len(built["index_map"]["index_to_asset"]),
            "input_hashes": built["input_hashes"],
        }

    (out / "test.json").write_text(json.dumps(annotations, ensure_ascii=False))
    (out / "id_maps.json").write_text(json.dumps(id_maps, ensure_ascii=False))
    (out / "sample_ids.json").write_text(json.dumps(sample_ids))
    manifest = {
        "schema_version": PROTOCOL_VERSION,
        "baseline": "elem2design",
        "seed": BASELINE_SEED,
        "source_run": str(args.source_run.resolve()),
        "role_pkl_sha256": sha256_file(args.role_pkl),
        "sample_ids_sha256": sha256_file(args.sample_ids),
        "n_samples": len(sample_ids),
        "n_prepared": len(annotations),
        "prepare_failures": prepare_failures,
        "samples": manifest_samples,
        "protocol_notes": [
            "per-sample deterministic shuffle keyed on (seed, sample_id), then stable role sort",
            "gpt turns are '{}' placeholders; no GT geometry/roles anywhere in test.json",
            "image_folder must be '/' — all image paths are absolute",
        ],
    }
    (out / "input_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    print(f"prepared {len(annotations)}/{len(sample_ids)} samples -> {out}")
    if prepare_failures:
        print(f"prepare failures: {prepare_failures}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
