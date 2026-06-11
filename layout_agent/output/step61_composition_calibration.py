"""Step 61 (2026-06-12): GT vs candidate coarse-composition calibration.

Motivation: eight experiments (Steps 49-60) converged on "Generator-bounded"
-- prompts, QC gates, and feedback cannot move composition verdicts.  The
remaining hypothesis is GRANULARITY: the Generator decides sketch-level
composition (where the photo goes, where the text mass goes) and pixel-level
detail in a single LLM call, with no thumbnail-sketch stage.  Before building
a sketch/composition Role, this script verifies the premise: is there a
measurable gap at the SKETCH level between designer GT and our candidates?

Coarse-composition signature per layout (squint-test representation):
  photo   = largest kind=="image" (GT) / semantic_type=="product_image" (cand)
            -> 3x3 grid cell of its center + size bucket
               (small <0.20 / medium 0.20-0.45 / large 0.45-0.80 / bleed >0.80)
  text    = area-weighted center of ALL text elements -> 3x3 grid cell
  relation (photo vs text mass):
    text-on-photo  -- text boxes overlap the photo by >=30% of text area
    stacked        -- |dy| dominates and exceeds 1/6 canvas height
    side-by-side   -- |dx| dominates and exceeds 1/6 canvas width
    centered-mix   -- neither offset exceeds the threshold

Sources:
  GT:        crello_*/meta.json (same filters as step60 calibration:
             clip to canvas, skip ratio>0.95 background-like, skip
             background_candidate kind)
  candidate: parse_log_samples over the five live N=20 logs
             (step56 / step58 / step58b / step59 / step60d), every candidate
             from every attempt; semantic types resolved from the spec echo.

Output: stdout tables + step61_composition_calibration.json
Zero LLM calls.
"""

from __future__ import annotations

import glob
import json
import math
import sys
from collections import Counter
from pathlib import Path

OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT))

from step59_text_gradient_calibration import parse_log_samples  # noqa: E402

CAND_LOGS = [
    "step56_live_N20.log",
    "step58_live_N20.log",
    "step58b_live_N20.log",
    "step59_live_N20.log",
    "step60d_live_N20.log",
]

H_LABEL = ["L", "C", "R"]  # left / center / right third
V_LABEL = ["T", "M", "B"]  # top / middle / bottom third


def clip_box(left, top, width, height, cw, ch):
    xl, yt = max(0.0, float(left)), max(0.0, float(top))
    xr = min(float(cw), float(left) + float(width))
    yb = min(float(ch), float(top) + float(height))
    if xr <= xl or yb <= yt:
        return None
    return (xl, yt, xr, yb)


def third(coord, total):
    return min(2, int(3 * coord / total))


def size_bucket(ratio):
    if ratio < 0.20:
        return "small"
    if ratio < 0.45:
        return "medium"
    if ratio < 0.80:
        return "large"
    return "bleed"


def overlap_area(a, b):
    xl, yt = max(a[0], b[0]), max(a[1], b[1])
    xr, yb = min(a[2], b[2]), min(a[3], b[3])
    if xr <= xl or yb <= yt:
        return 0.0
    return (xr - xl) * (yb - yt)


def signature(photo_box, text_boxes, cw, ch):
    """Return (pattern_str, marginals dict) or None if no photo or no text."""
    if photo_box is None or not text_boxes:
        return None
    px = (photo_box[0] + photo_box[2]) / 2
    py = (photo_box[1] + photo_box[3]) / 2
    p_area = (photo_box[2] - photo_box[0]) * (photo_box[3] - photo_box[1])
    p_cell = V_LABEL[third(py, ch)] + H_LABEL[third(px, cw)]
    p_size = size_bucket(p_area / (cw * ch))

    t_area_sum = 0.0
    tx = ty = 0.0
    olap = 0.0
    for tb in text_boxes:
        a = (tb[2] - tb[0]) * (tb[3] - tb[1])
        t_area_sum += a
        tx += a * (tb[0] + tb[2]) / 2
        ty += a * (tb[1] + tb[3]) / 2
        olap += overlap_area(tb, photo_box)
    tx /= t_area_sum
    ty /= t_area_sum
    t_cell = V_LABEL[third(ty, ch)] + H_LABEL[third(tx, cw)]

    if olap / t_area_sum >= 0.30:
        rel = "text-on-photo"
    else:
        dx, dy = abs(tx - px) / cw, abs(ty - py) / ch
        if max(dx, dy) < 1 / 6:
            rel = "centered-mix"
        elif dy >= dx:
            rel = "stacked"
        else:
            rel = "side-by-side"

    pattern = f"photo@{p_cell}/{p_size} text@{t_cell} {rel}"
    return pattern, {"photo_cell": p_cell, "photo_size": p_size,
                     "text_cell": t_cell, "relation": rel}


def collect_gt():
    sigs, marg = [], {"photo_cell": Counter(), "photo_size": Counter(),
                      "text_cell": Counter(), "relation": Counter()}
    n_scanned = n_with_photo_text = 0
    for meta_path in sorted(glob.glob(str(OUT / "crello_*" / "meta.json"))):
        meta = json.load(open(meta_path))
        cw, ch = int(meta["canvas_width"]), int(meta["canvas_height"])
        if cw <= 0 or ch <= 0:
            continue
        n_scanned += 1
        photo_box, photo_area = None, 0.0
        text_boxes = []
        for el in meta.get("elements", []):
            kind = el.get("kind")
            if kind not in ("image", "text"):
                continue
            vals = [el.get(k) for k in ("left", "top", "width", "height")]
            if any(v is None for v in vals):
                continue
            box = clip_box(*vals, cw, ch)
            if box is None:
                continue
            area = (box[2] - box[0]) * (box[3] - box[1])
            if area / (cw * ch) > 0.95:
                continue
            if kind == "image":
                if area > photo_area:
                    photo_box, photo_area = box, area
            else:
                text_boxes.append(box)
        sig = signature(photo_box, text_boxes, cw, ch)
        if sig is None:
            continue
        n_with_photo_text += 1
        sigs.append(sig[0])
        for k, v in sig[1].items():
            marg[k][v] += 1
    return sigs, marg, n_scanned, n_with_photo_text


def collect_candidates():
    sigs, marg = [], {"photo_cell": Counter(), "photo_size": Counter(),
                      "text_cell": Counter(), "relation": Counter()}
    n_cands = n_with_photo_text = 0
    per_log = Counter()
    for log_name in CAND_LOGS:
        log_path = OUT / log_name
        if not log_path.exists():
            print(f"  [warn] missing log {log_name}, skipped")
            continue
        for sid, spec_dict, cands in parse_log_samples(log_path):
            canvas = spec_dict.get("canvas") or {}
            cw, ch = int(canvas.get("width", 0)), int(canvas.get("height", 0))
            if cw <= 0 or ch <= 0:
                continue
            photo_ids = set()
            text_ids = set()
            for el in spec_dict.get("elements", []):
                if el.get("semantic_type") == "product_image":
                    photo_ids.add(el.get("id"))
                elif el.get("visual_type") == "text":
                    text_ids.add(el.get("id"))
            for cand in cands:
                n_cands += 1
                photo_box, photo_area = None, 0.0
                text_boxes = []
                for el in cand.get("elements", []):
                    vals = [el.get(k) for k in ("left", "top", "width", "height")]
                    if any(v is None for v in vals):
                        continue
                    box = clip_box(*vals, cw, ch)
                    if box is None:
                        continue
                    area = (box[2] - box[0]) * (box[3] - box[1])
                    if el.get("id") in photo_ids:
                        if area > photo_area:
                            photo_box, photo_area = box, area
                    elif el.get("id") in text_ids:
                        text_boxes.append(box)
                sig = signature(photo_box, text_boxes, cw, ch)
                if sig is None:
                    continue
                n_with_photo_text += 1
                per_log[log_name] += 1
                sigs.append(sig[0])
                for k, v in sig[1].items():
                    marg[k][v] += 1
    return sigs, marg, n_cands, n_with_photo_text, per_log


def diversity(sigs):
    c = Counter(sigs)
    n = len(sigs)
    top3 = sum(v for _, v in c.most_common(3)) / n if n else 0.0
    entropy = -sum((v / n) * math.log2(v / n) for v in c.values()) if n else 0.0
    return {"n": n, "distinct": len(c), "top3_share": round(top3, 3),
            "entropy_bits": round(entropy, 3)}


def print_side(title, gt_counter, cand_counter, gt_n, cand_n, keys=None):
    print(f"\n## {title}")
    print(f"{'value':<16} {'GT %':>8} {'cand %':>8}")
    all_keys = keys or sorted(set(gt_counter) | set(cand_counter),
                              key=lambda k: -gt_counter.get(k, 0))
    for k in all_keys:
        g = 100 * gt_counter.get(k, 0) / gt_n if gt_n else 0
        c = 100 * cand_counter.get(k, 0) / cand_n if cand_n else 0
        print(f"{k:<16} {g:>7.1f}% {c:>7.1f}%")


def main():
    print("collecting GT compositions...")
    gt_sigs, gt_marg, gt_scanned, gt_used = collect_gt()
    print(f"  GT scanned={gt_scanned}  with photo+text={gt_used}")

    print("collecting candidate compositions from live logs...")
    c_sigs, c_marg, c_total, c_used, per_log = collect_candidates()
    print(f"  candidates parsed={c_total}  with photo+text={c_used}")
    for k, v in per_log.items():
        print(f"    {k}: {v}")

    gt_div, c_div = diversity(gt_sigs), diversity(c_sigs)
    print("\n## Diversity (full sketch pattern)")
    print(f"{'':<14} {'GT':>10} {'candidate':>10}")
    for key in ("n", "distinct", "top3_share", "entropy_bits"):
        print(f"{key:<14} {gt_div[key]:>10} {c_div[key]:>10}")

    print_side("Photo grid cell (3x3, row+col)", gt_marg["photo_cell"],
               c_marg["photo_cell"], gt_used, c_used,
               keys=[v + h for v in V_LABEL for h in H_LABEL])
    print_side("Photo size bucket", gt_marg["photo_size"], c_marg["photo_size"],
               gt_used, c_used, keys=["small", "medium", "large", "bleed"])
    print_side("Text-mass grid cell", gt_marg["text_cell"], c_marg["text_cell"],
               gt_used, c_used, keys=[v + h for v in V_LABEL for h in H_LABEL])
    print_side("Photo-text relation", gt_marg["relation"], c_marg["relation"],
               gt_used, c_used,
               keys=["text-on-photo", "stacked", "side-by-side", "centered-mix"])

    print("\n## Top-10 GT sketch patterns")
    for pat, cnt in Counter(gt_sigs).most_common(10):
        print(f"  {100*cnt/gt_used:>5.1f}%  {pat}")
    print("\n## Top-10 candidate sketch patterns")
    for pat, cnt in Counter(c_sigs).most_common(10):
        print(f"  {100*cnt/c_used:>5.1f}%  {pat}")

    result = {
        "gt": {"scanned": gt_scanned, "used": gt_used, "diversity": gt_div,
               "marginals": {k: dict(v) for k, v in gt_marg.items()},
               "top_patterns": Counter(gt_sigs).most_common(20)},
        "candidate": {"parsed": c_total, "used": c_used, "diversity": c_div,
                      "per_log": dict(per_log),
                      "marginals": {k: dict(v) for k, v in c_marg.items()},
                      "top_patterns": Counter(c_sigs).most_common(20)},
    }
    out_json = OUT / "step61_composition_calibration.json"
    out_json.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\nwrote {out_json}")


if __name__ == "__main__":
    main()
