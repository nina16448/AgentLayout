"""Step 57 — calibrate coverage / dead-space QC thresholds against designer GT.

Computes, for each of the 20 step13 designer GT layouts (foreground elements
only, same filtering as step54_render_parity.build_gt_spec_and_candidate):

  1. coverage      — union area of foreground bboxes / canvas area (grid raster)
  2. v_dead_band   — largest contiguous vertical gap (canvas-height fraction)
                     with NO foreground element (top/bottom margins included)
  3. h_dead_band   — same along the horizontal axis

QC thresholds must let ALL 20 designer layouts pass with margin; print min /
max / per-sample table so the threshold choice is auditable.

Usage:  conda activate meta && python step57_coverage_calibration.py
"""
from __future__ import annotations

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

GRID = 200


def foreground_boxes(meta: dict, descriptors: list) -> list:
    """Foreground bboxes with the same filtering as step54 parity script."""
    promoted_idx = None
    has_bg_plate = any(d.get("kind") == "background_candidate" for d in descriptors)
    if not has_bg_plate:
        for d in descriptors:
            if d.get("kind") == "image" and d.get("asset_ref"):
                promoted_idx = d.get("idx")
                break
    boxes = []
    for d in descriptors:
        kind = d.get("kind")
        if kind == "background_candidate" or d.get("idx") == promoted_idx:
            continue
        if kind not in ("text", "image", "underlay"):
            continue
        if kind in ("image", "underlay") and not d.get("asset_ref"):
            continue
        boxes.append(
            (float(d["left"]), float(d["top"]), float(d["width"]), float(d["height"]))
        )
    return boxes


def coverage_ratio(boxes: list, cw: float, ch: float) -> float:
    grid = [[False] * GRID for _ in range(GRID)]
    for left, top, w, h in boxes:
        x0 = max(0, int(left / cw * GRID))
        x1 = min(GRID, int((left + w) / cw * GRID + 0.9999))
        y0 = max(0, int(top / ch * GRID))
        y1 = min(GRID, int((top + h) / ch * GRID + 0.9999))
        for y in range(y0, y1):
            row = grid[y]
            for x in range(x0, x1):
                row[x] = True
    covered = sum(row.count(True) for row in grid)
    return covered / (GRID * GRID)


def max_dead_band(intervals: list, total: float) -> float:
    """Largest gap (fraction of total) not covered by any [start, end] interval."""
    if not intervals:
        return 1.0
    clipped = sorted(
        (max(0.0, s), min(total, e)) for s, e in intervals if e > 0 and s < total
    )
    best = clipped[0][0]  # leading margin
    cur_end = clipped[0][1]
    for s, e in clipped[1:]:
        if s > cur_end:
            best = max(best, s - cur_end)
        cur_end = max(cur_end, e)
    best = max(best, total - cur_end)  # trailing margin
    return best / total


def safe_zone_utilization(boxes: list, zones: list, cw: float, ch: float) -> float:
    """Fraction of total safe-zone (saliency-low) area covered by foreground.

    Saliency-aware dead-space signal: a band that looks 'empty' geometrically
    may be carried by a salient background subject (legitimate), while unused
    SAFE-ZONE area is genuinely blank background — the 5e8d966a failure mode.
    """
    if not zones:
        return 1.0  # no safe zones -> vacuously utilized
    zone_grid = [[False] * GRID for _ in range(GRID)]
    for sl, st, sr, sb in zones:
        x0 = max(0, int(sl / cw * GRID))
        x1 = min(GRID, int(sr / cw * GRID + 0.9999))
        y0 = max(0, int(st / ch * GRID))
        y1 = min(GRID, int(sb / ch * GRID + 0.9999))
        for y in range(y0, y1):
            row = zone_grid[y]
            for x in range(x0, x1):
                row[x] = True
    fg_grid = [[False] * GRID for _ in range(GRID)]
    for left, top, w, h in boxes:
        x0 = max(0, int(left / cw * GRID))
        x1 = min(GRID, int((left + w) / cw * GRID + 0.9999))
        y0 = max(0, int(top / ch * GRID))
        y1 = min(GRID, int((top + h) / ch * GRID + 0.9999))
        for y in range(y0, y1):
            row = fg_grid[y]
            for x in range(x0, x1):
                row[x] = True
    zone_total = covered = 0
    for y in range(GRID):
        for x in range(GRID):
            if zone_grid[y][x]:
                zone_total += 1
                if fg_grid[y][x]:
                    covered += 1
    return covered / zone_total if zone_total else 1.0


def parse_log_candidates(log_path: Path):
    """Yield (sample_id, canvas_w, canvas_h, bg_asset_ref, candidate_dict).

    The step56 live log interleaves '[k/20] <id>' headers, an Analyst spec
    fenced block (first ```json with "canvas"), and Generator output fenced
    blocks containing {"candidates": [...]}.
    """
    import re

    text = log_path.read_text()
    sections = re.split(r"^\[\d+/\d+\] ([0-9a-f]{24})\s*$", text, flags=re.M)
    for i in range(1, len(sections), 2):
        sid, body = sections[i], sections[i + 1]
        blocks = re.findall(r"```json\s*(\{.*?\})\s*```", body, flags=re.S)
        cw = ch = None
        bg_ref = None
        for blk in blocks:
            try:
                obj = json.loads(blk)
            except json.JSONDecodeError:
                continue
            if "canvas" in obj and cw is None:
                cw = float(obj["canvas"]["width"])
                ch = float(obj["canvas"]["height"])
                bg_ref = obj["canvas"].get("background_asset_ref")
            elif "candidates" in obj and cw is not None:
                for cand in obj["candidates"]:
                    yield sid, cw, ch, bg_ref, cand


def candidate_mode(log_path: Path):
    rows = []
    zones_cache = {}
    for sid, cw, ch, bg_ref, cand in parse_log_candidates(log_path):
        boxes = [
            (float(e["left"]), float(e["top"]), float(e["width"]), float(e["height"]))
            for e in cand["elements"]
            if not str(e["id"]).startswith("bg_")
        ]
        cov = coverage_ratio(boxes, cw, ch)
        v_dead = max_dead_band([(t, t + h) for _, t, _, h in boxes], ch)
        h_dead = max_dead_band([(l, l + w) for l, _, w, _ in boxes], cw)
        if sid not in zones_cache:
            canvas = Canvas(width=int(cw), height=int(ch), background_asset_ref=bg_ref)
            zones_cache[sid] = [tuple(z.bbox) for z in resolve_background(canvas).safe_zones]
        szu = safe_zone_utilization(boxes, zones_cache[sid], cw, ch)
        rows.append((sid[:8], cand.get("candidate_id", "?"), len(boxes), cov, v_dead, h_dead, szu))
        print(
            f"  {sid[:8]} {cand.get('candidate_id', '?'):8s} fg={len(boxes):2d} "
            f"coverage={cov:.3f} v_dead={v_dead:.3f} h_dead={h_dead:.3f} sz_util={szu:.3f}"
        )
    if not rows:
        return
    covs = [r[3] for r in rows]
    vds = [r[4] for r in rows]
    hds = [r[5] for r in rows]
    szus = [r[6] for r in rows]
    print(f"\nN={len(rows)} generated candidates")
    print(f"  coverage   min={min(covs):.3f} max={max(covs):.3f}")
    print(f"  v_dead     min={min(vds):.3f} max={max(vds):.3f}")
    print(f"  h_dead     min={min(hds):.3f} max={max(hds):.3f}")
    print(f"  sz_util    min={min(szus):.3f} max={max(szus):.3f}")


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--step56-log":
        candidate_mode(Path(sys.argv[2]))
        return
    ids = json.load(open(HERE / "step13_drawn_ids.json"))["ids"]
    rows = []
    for sid in ids:
        sample_dir = HERE / f"crello_{sid}"
        if not (sample_dir / "meta.json").exists():
            print(f"  [skip] {sid[:8]}: no cache")
            continue
        meta, descriptors = load_crello_sample(sample_dir)
        cw, ch = float(meta["canvas_width"]), float(meta["canvas_height"])
        boxes = foreground_boxes(meta, descriptors)
        cov = coverage_ratio(boxes, cw, ch)
        v_dead = max_dead_band([(t, t + h) for _, t, _, h in boxes], ch)
        h_dead = max_dead_band([(l, l + w) for l, _, w, _ in boxes], cw)
        bg_ref = _composite_background_plates(meta, descriptors)
        if bg_ref is None:
            for d in descriptors:
                if d.get("kind") == "image" and d.get("asset_ref"):
                    bg_ref = d["asset_ref"]
                    break
        canvas = Canvas(width=int(cw), height=int(ch), background_asset_ref=bg_ref)
        bg = resolve_background(canvas)
        zones = [tuple(z.bbox) for z in bg.safe_zones]
        szu = safe_zone_utilization(boxes, zones, cw, ch)
        rows.append((sid[:8], len(boxes), cov, v_dead, h_dead, szu, len(zones)))
        print(
            f"  {sid[:8]} fg={len(boxes):2d} coverage={cov:.3f} "
            f"v_dead={v_dead:.3f} h_dead={h_dead:.3f} "
            f"sz_util={szu:.3f} (zones={len(zones)})"
        )
    if not rows:
        return
    covs = [r[2] for r in rows]
    vds = [r[3] for r in rows]
    hds = [r[4] for r in rows]
    szus = [r[5] for r in rows]
    print(f"\nN={len(rows)} designer GT layouts")
    print(f"  coverage   min={min(covs):.3f} max={max(covs):.3f}")
    print(f"  v_dead     min={min(vds):.3f} max={max(vds):.3f}")
    print(f"  h_dead     min={min(hds):.3f} max={max(hds):.3f}")
    print(f"  sz_util    min={min(szus):.3f} max={max(szus):.3f}")
    print("\nthreshold guidance: CANVAS_COVERAGE_MIN < min(coverage); "
          "DEAD_BAND_MAX > max(v_dead, h_dead); SZ_UTIL_MIN < min(sz_util)")


if __name__ == "__main__":
    main()
