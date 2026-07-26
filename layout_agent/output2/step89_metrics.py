"""Step 89 post-hoc -- SEGA geometric metrics for the N=100 two-arm run.

Computes the paper's rule-based axes (same implementation as steps 20/22:
``metagpt.ext.agentlayout.evaluation.sega_metrics``) for THREE sources on
identical backgrounds:

    gt     -- designer text layout (from meta.json, kind=text bboxes)
    arm_a  -- baseline config final candidates
    arm_b  -- deep-review config final candidates

Axes: Ali (lower better), Ove (lower), Rea (lower), Occ (lower). Und_l/Und_s
are 0 by construction in text-as-image SEGA mode (no placeable underlays --
both sides) and are replaced by the protocol-appropriate substitute
``text_on_panel`` (fraction of text elements >= 50% inside a baked panel).

Run (offline, no LLM):
    conda activate meta
    python layout_agent/output2/step89_metrics.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
OUTPUT1 = HERE.parent / "output"
for p in (str(REPO_ROOT), str(OUTPUT1)):
    if p not in sys.path:
        sys.path.insert(0, p)

from metagpt.ext.agentlayout.evaluation.sega_metrics import (  # noqa: E402
    CLS_TEXT,
    metric_alignment,
    metric_occlusion,
    metric_overlay,
    metric_readability,
    to_xyxy,
)
import step20_sega_eval as s20  # noqa: E402  (saliency helper reuse)

N100 = HERE / "step89_n100"
SEGA_PRE = OUTPUT1 / "sega_pre"


def _gt_layout(meta):
    out = []
    for e in meta["elements"]:
        if e.get("kind") == "text" and (e.get("content") or "").strip():
            out.append((CLS_TEXT, to_xyxy(float(e["left"]), float(e["top"]),
                                          float(e["width"]), float(e["height"]))))
    return out


def _cand_layout(cand):
    return [(CLS_TEXT, to_xyxy(el["left"], el["top"], el["width"], el["height"]))
            for el in cand["elements"]]


def _on_panel_rate(layout, regions):
    if not layout or not regions:
        return None
    hits = 0
    for _, (x1, y1, x2, y2) in layout:
        area = max(1.0, (x2 - x1) * (y2 - y1))
        best = 0.0
        for r in regions:
            rl, rt, rr, rb = r["bbox"]
            ix = max(0.0, min(x2, rr) - max(x1, rl))
            iy = max(0.0, min(y2, rb) - max(y1, rt))
            best = max(best, ix * iy / area)
        if best >= 0.5:
            hits += 1
    return hits / len(layout)


def main() -> int:
    acc = {src: {"Ali": [], "Ove": [], "Rea": [], "Occ": [], "panel": []}
           for src in ("gt", "arm_a", "arm_b")}
    n_done = 0
    for d in sorted(N100.iterdir()):
        if not d.is_dir():
            continue
        sid = d.name
        pre_p = SEGA_PRE / sid / "sega_input.json"
        meta_p = OUTPUT1 / f"crello_{sid}" / "meta.json"
        if not pre_p.exists() or not meta_p.exists():
            continue
        pre = json.loads(pre_p.read_text())
        meta = json.loads(meta_p.read_text())
        cw, ch = float(pre["canvas_width"]), float(pre["canvas_height"])
        bg = np.asarray(
            Image.open(pre["background_path"]).convert("RGB").resize(
                (int(cw), int(ch))),
            dtype=np.uint8,
        )
        sal = s20._saliency_from_bg(bg)
        regions = pre["underlay_regions"]

        layouts = {"gt": _gt_layout(meta)}
        for arm in ("a", "b"):
            cp = d / arm / "candidate.json"
            if cp.exists():
                layouts[f"arm_{arm}"] = _cand_layout(json.loads(cp.read_text()))

        for src, layout in layouts.items():
            if not layout:
                continue
            acc[src]["Ali"].append(metric_alignment([layout], cw, ch))
            acc[src]["Ove"].append(metric_overlay([layout]))
            acc[src]["Rea"].append(metric_readability([layout], [bg], cw, ch))
            acc[src]["Occ"].append(metric_occlusion([layout], [sal], cw, ch))
            rate = _on_panel_rate(layout, regions)
            if rate is not None:
                acc[src]["panel"].append(rate)
        n_done += 1

    result = {"n_samples": n_done}
    print(f"n_samples={n_done}")
    print(f"{'':8s}{'Ali↓':>10s}{'Ove↓':>10s}{'Rea↓':>10s}{'Occ↓':>10s}{'text_on_panel↑':>16s}")
    for src in ("gt", "arm_a", "arm_b"):
        row = {}
        for k, vals in acc[src].items():
            row[k] = (sum(vals) / len(vals)) if vals else None
        result[src] = {**row, "n": len(acc[src]["Ali"])}
        print(f"{src:8s}"
              f"{row['Ali']:>10.5f}{row['Ove']:>10.5f}{row['Rea']:>10.5f}"
              f"{row['Occ']:>10.5f}"
              f"{(row['panel'] if row['panel'] is not None else float('nan')):>16.3f}"
              f"   (n={len(acc[src]['Ali'])})")
    (N100 / "metrics.json").write_text(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
