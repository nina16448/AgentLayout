"""Step 52 / Experiment B: run designer GROUND-TRUTH layouts through our own
QC safe-zone gate (2026-06-11). Offline, zero API cost.

Hypothesis: the Step 43 primary-in-safe-zone gate (>=50% overlap) constrains
candidate A into a placement subspace the human designers do NOT occupy. If
GT layouts violate the gate at a high rate, the gate is asymmetric: A must
satisfy a rule the designs it is judged against routinely break, which can
force the dead-space / unbalanced compositions the blind judge penalizes
(design_layout gt 38/40, Step 51).

Method:
  for each of the 20 step13_drawn_ids samples:
    1. bg_ref via the SAME path the live runner uses
       (run_role_team_live_crello._composite_background_plates, fallback to
       first kind=="image" descriptor);
    2. safe zones via resolve_background(Canvas(...)) -- identical CV path
       (occupancy mask) the pipeline gives QualityChecker;
    3. treat GT elements as QC primaries by approximation:
         kind=="text"                          -> text primary
         kind=="image" not promoted to bg      -> product_image primary
       (GT lacks semantic_type; caption/cta are not QC primaries, so the
        text mapping slightly OVER-counts violations -- bias is against the
        hypothesis, i.e. conservative);
    4. exact overlap math copied from quality_checker._check_primary_in_safe_zone
       (LTRB intersection / element area, threshold 0.50).

Report: element-level violation rate + sample-level "GT would be rejected by
our gate" rate (QC rejects on ANY primary violation).

Usage:
  conda activate meta && python layout_agent/output/step52_gt_qc_audit.py
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from run_role_team_live_crello import (  # noqa: E402
    _composite_background_plates,
    load_crello_sample,
)

from metagpt.ext.agentlayout.schema import Canvas  # noqa: E402
from metagpt.ext.agentlayout.tools.background_analyzer import (  # noqa: E402
    resolve_background,
)

THRESHOLD = 0.50  # quality_checker.PRIMARY_SAFE_ZONE_MIN_OVERLAP


def best_safe_zone_overlap(el: dict, safe_zones) -> float:
    """Exact math from quality_checker._check_primary_in_safe_zone."""
    el_left = float(el["left"])
    el_top = float(el["top"])
    el_right = el_left + float(el["width"])
    el_bottom = el_top + float(el["height"])
    elem_area = float(el["width"]) * float(el["height"])
    if elem_area <= 0:
        return 1.0  # zero-area: QC skips, count as pass
    best = 0.0
    for sz in safe_zones:
        sl, st, sr, sb = sz.bbox  # LTRB
        ix = max(0.0, min(el_right, float(sr)) - max(el_left, float(sl)))
        iy = max(0.0, min(el_bottom, float(sb)) - max(el_top, float(st)))
        best = max(best, (ix * iy) / elem_area)
    return best


def main():
    ids = json.load(open(HERE / "step13_drawn_ids.json"))["ids"]
    rows = []
    for sid in ids:
        sample_dir = HERE / f"crello_{sid}"
        if not sample_dir.is_dir():
            print(f"  [skip] {sid[:8]}: no cached dir")
            continue
        meta, descriptors = load_crello_sample(sample_dir)
        bg_ref = _composite_background_plates(meta, descriptors)
        promoted_idx = None
        if bg_ref is None:
            for d in descriptors:
                if d.get("kind") == "image" and d.get("asset_ref"):
                    bg_ref = d["asset_ref"]
                    promoted_idx = d.get("idx")
                    break
        canvas = Canvas(
            width=int(meta["canvas_width"]),
            height=int(meta["canvas_height"]),
            background_asset_ref=bg_ref,
        )
        bg = resolve_background(canvas)

        primaries = []
        for d in descriptors:
            kind = d.get("kind")
            if kind == "text":
                primaries.append((d, "text"))
            elif kind == "image" and d.get("idx") != promoted_idx:
                primaries.append((d, "image"))

        elems = []
        for d, role in primaries:
            ov = best_safe_zone_overlap(d, bg.safe_zones)
            elems.append(
                {
                    "idx": d.get("idx"),
                    "role": role,
                    "content": (d.get("content") or "")[:30],
                    "best_overlap": round(ov, 3),
                    "violation": ov < THRESHOLD,
                }
            )
        n_viol = sum(e["violation"] for e in elems)
        rows.append(
            {
                "id": sid,
                "bg_ref": Path(bg_ref).name if bg_ref else None,
                "n_safe_zones": len(bg.safe_zones),
                "n_primaries": len(elems),
                "n_violations": n_viol,
                "gate_rejects_gt": n_viol > 0,
                "elements": elems,
            }
        )
        flag = "REJECT" if n_viol > 0 else "pass  "
        print(
            f"  {sid[:8]} zones={len(bg.safe_zones):2d} "
            f"primaries={len(elems):2d} violations={n_viol:2d} -> {flag}"
        )

    n = len(rows)
    total_el = sum(r["n_primaries"] for r in rows)
    total_viol = sum(r["n_violations"] for r in rows)
    rejected = sum(r["gate_rejects_gt"] for r in rows)
    print(f"\n=== Step 52 / Exp B: designer GT vs our QC safe-zone gate (thr={THRESHOLD}) ===")
    print(f"  samples:            {n}")
    print(f"  element violations: {total_viol}/{total_el} ({100*total_viol/max(total_el,1):.1f}%)")
    print(f"  GT layouts our gate would REJECT: {rejected}/{n} ({100*rejected/max(n,1):.0f}%)")
    print("  (candidate-A QC rejection rate for reference: step48 47%, step49 41%)")

    by_role = {}
    for r in rows:
        for e in r["elements"]:
            t, v = by_role.setdefault(e["role"], [0, 0])
            by_role[e["role"]] = [t + 1, v + e["violation"]]
    for role, (t, v) in sorted(by_role.items()):
        print(f"  {role:6s}: {v}/{t} violations ({100*v/max(t,1):.1f}%)")

    out = HERE / "step52_gt_qc_audit.json"
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
