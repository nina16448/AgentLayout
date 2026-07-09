"""Step 80 -- add pre-rendered TEXT element images to the demo-id cache.

The Crello dataset ships a rendered RGBA image for EVERY element, text
included (designer typography verbatim). The original cache pass only saved
non-text assets; this script incrementally adds, for each demo sample:

    crello_<id>/asset_{idx:02d}_text.png   (resized to the element's natural
                                            canvas size from meta.json)

and sets ``asset_ref`` on the matching meta.json text element. Nothing else
in meta.json is touched (the Step 28 classifier fields survive).

Run (network: streams the HF test split until all targets found):
    conda activate meta
    python layout_agent/output2/step80_snapshot_text_assets.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

OUTPUT1 = HERE.parent / "output"
DEMO_IDS = set(json.loads((HERE.parent / "demo_ids.json").read_text())["ids"])


def main() -> int:
    import argparse

    from datasets import load_dataset

    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", type=str, default=None,
                        help="path to a {'ids': [...]} JSON (default: demo_ids.json)")
    args = parser.parse_args()
    id_pool = (
        set(json.loads(Path(args.ids).read_text())["ids"]) if args.ids else DEMO_IDS
    )

    targets = {
        sid for sid in id_pool
        if (OUTPUT1 / f"crello_{sid}" / "meta.json").exists()
        # skip samples already snapshotted (incremental reruns stay cheap)
        and not any(
            e.get("kind") == "text" and e.get("asset_ref")
            for e in json.loads((OUTPUT1 / f"crello_{sid}" / "meta.json").read_text())["elements"]
        )
    }
    print(f"targets: {len(targets)} cached demo samples")

    ds = load_dataset("cyberagent/crello", split="test", streaming=True)
    done, patched_elements, mismatches = set(), 0, 0
    t0 = time.time()
    for i, sample in enumerate(ds):
        sid = sample["id"]
        if sid not in targets or sid in done:
            if len(done) == len(targets):
                break
            continue
        sample_dir = OUTPUT1 / f"crello_{sid}"
        meta = json.loads((sample_dir / "meta.json").read_text())
        images = sample["image"]
        types = sample["type"]
        for elem in meta["elements"]:
            idx = elem["idx"]
            if elem.get("kind") != "text":
                continue
            if idx >= len(images) or types[idx] != 1:
                mismatches += 1
                print(f"  [WARN] {sid} idx={idx}: dataset/meta type mismatch; skipped")
                continue
            w = max(1, int(round(float(elem["width"]))))
            h = max(1, int(round(float(elem["height"]))))
            out_png = sample_dir / f"asset_{idx:02d}_text.png"
            img = images[idx].convert("RGBA").resize((w, h))
            img.save(out_png, format="PNG")
            elem["asset_ref"] = str(out_png)
            patched_elements += 1
        (sample_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False))
        done.add(sid)
        print(f"  [{len(done)}/{len(targets)}] {sid}  "
              f"(scanned={i + 1}, elapsed={time.time() - t0:.0f}s)")
        if len(done) == len(targets):
            break

    print(f"\nDONE: {len(done)}/{len(targets)} samples, "
          f"{patched_elements} text images saved, {mismatches} mismatches.")
    missing = targets - done
    if missing:
        print("MISSING (not in test split scan):", sorted(missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
