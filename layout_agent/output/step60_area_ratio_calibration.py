"""Step 60 (2026-06-11): GT element area-ratio calibration.

Motivation: Step 58 identified "size timidity" -- designer GT photos occupy
~75% of the canvas while Generator candidates sit around ~25%.  Steps 57-59
proved QC-gate feedback cannot fix Generator behaviour (retry never executes
actionable directives), so Step 60 changes the Generator INPUT instead:
a GT-calibrated per-class area-ratio hint injected into PROMPT_TEMPLATE.

This script computes the calibration table from ALL locally cached Crello
samples (crello_*/meta.json, ~1.9k designer ground truths, zero LLM):

  classes (mirroring how spec elements map at prompt-build time):
    photo        -- kind == "image"   (non-background photo/product asset)
    underlay     -- kind == "underlay" (pre-classified shape plate)
    title_text   -- per sample, the LARGEST kind=="text" element (title proxy;
                    GT meta has no semantic types, but designers' visible
                    title is reliably the biggest text block)
    other_text   -- remaining kind=="text" elements

  filters:
    - background_candidate kind skipped (full-canvas plates are background)
    - clipped area_ratio > 0.95 skipped (background-like, same rule as
      step59 calibration / Rea metric)
    - missing/degenerate geometry skipped
    - boxes clipped to canvas before area computation (GT elements may
      overhang the canvas; visible area is what matters)

Output: stdout table + step60_area_ratio_calibration.json
"""

from __future__ import annotations

import glob
import json
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parent


def clipped_area_ratio(el: dict, cw: int, ch: int):
    vals = [el.get(k) for k in ("left", "top", "width", "height")]
    if any(v is None for v in vals):
        return None
    left, top, width, height = map(float, vals)
    if width <= 0 or height <= 0:
        return None
    xl, yt = max(0.0, left), max(0.0, top)
    xr, yb = min(float(cw), left + width), min(float(ch), top + height)
    if xr <= xl or yb <= yt:
        return None
    return (xr - xl) * (yb - yt) / (cw * ch)


def main():
    buckets = {"photo": [], "underlay": [], "title_text": [], "other_text": []}
    n_samples = 0
    n_skipped_fullcanvas = 0

    for meta_path in sorted(glob.glob(str(OUT / "crello_*" / "meta.json"))):
        meta = json.load(open(meta_path))
        cw, ch = int(meta["canvas_width"]), int(meta["canvas_height"])
        if cw <= 0 or ch <= 0:
            continue
        n_samples += 1
        texts = []
        for el in meta.get("elements", []):
            kind = el.get("kind")
            if kind not in ("image", "underlay", "text"):
                continue
            ratio = clipped_area_ratio(el, cw, ch)
            if ratio is None:
                continue
            if ratio > 0.95:
                n_skipped_fullcanvas += 1
                continue
            if kind == "image":
                buckets["photo"].append(ratio)
            elif kind == "underlay":
                buckets["underlay"].append(ratio)
            else:
                texts.append(ratio)
        if texts:
            texts.sort(reverse=True)
            buckets["title_text"].append(texts[0])
            buckets["other_text"].extend(texts[1:])

    pcts = [10, 25, 50, 75, 90]
    result = {"n_samples": n_samples, "n_skipped_fullcanvas": n_skipped_fullcanvas, "classes": {}}
    header = f"{'class':<12} {'n':>6} {'mean':>7} " + " ".join(f"p{p:<4}" for p in pcts)
    print(header)
    print("-" * len(header))
    for cls, vals in buckets.items():
        arr = np.asarray(vals, dtype=float)
        if arr.size == 0:
            continue
        qs = np.percentile(arr, pcts)
        result["classes"][cls] = {
            "n": int(arr.size),
            "mean": round(float(arr.mean()), 4),
            **{f"p{p}": round(float(q), 4) for p, q in zip(pcts, qs)},
        }
        print(
            f"{cls:<12} {arr.size:>6} {arr.mean():>7.4f} "
            + " ".join(f"{q:>5.4f}" for q in qs)
        )

    out_json = OUT / "step60_area_ratio_calibration.json"
    out_json.write_text(json.dumps(result, indent=2))
    print(f"\nsamples={n_samples}  fullcanvas_skipped={n_skipped_fullcanvas}")
    print(f"wrote {out_json}")


if __name__ == "__main__":
    main()
