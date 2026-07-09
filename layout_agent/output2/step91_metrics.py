"""Step 91 post-hoc -- paired gpt-4o vs o4-mini comparison on the same N=20.

Three signals, from weakest to strongest coupling with the LLM judge:

  1. geometry   -- SEGA rule-based axes (Ali/Ove/Rea/Occ + text_on_panel),
                   identical implementation to step89_metrics.py. Judge-free:
                   if o4-mini degrades the layouts, this moves regardless of
                   what any grader thinks.
  2. status     -- terminal pipeline status + judge rounds + empty-candidate
                   rounds. Exposes refusals and QC blowups.
  3. verdict    -- blind pairwise vs designer GT, PAIRED per sample. Both arms
                   were graded by gpt-4o with the same attachment order, so a
                   per-sample cross-tab is meaningful.

Sources:
    gt     -- designer text layout (meta.json, kind=text)
    gpt4o  -- step89_n100/<id>/a/  (Step 89 arm A; pipeline roles on gpt-4o)
    o4mini -- step91_model_ab/o4mini/<id>/  (this run)

Only ids present in BOTH arms are compared, so every number is paired.

Run (offline, no LLM):
    conda activate meta
    python layout_agent/output2/step91_metrics.py
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
    metric_alignment,
    metric_occlusion,
    metric_overlay,
    metric_readability,
)
import step20_sega_eval as s20  # noqa: E402
from step89_metrics import _cand_layout, _gt_layout, _on_panel_rate  # noqa: E402

N100 = HERE / "step89_n100"
O4MINI = HERE / "step91_model_ab" / "o4mini"
SEGA_PRE = OUTPUT1 / "sega_pre"
OUT = HERE / "step91_model_ab" / "comparison.json"

AXES = ["overall_winner", "design_layout", "typography_color",
        "graphics_images", "content_relevance", "innovation_originality"]


def _paired_ids() -> list:
    if not O4MINI.exists():
        raise SystemExit(f"missing {O4MINI} -- run step91_o4mini_ab.py first")
    out = []
    for d in sorted(O4MINI.iterdir()):
        if not d.is_dir() or not (d / "row.json").exists():
            continue
        if (N100 / d.name / "row.json").exists():
            out.append(d.name)
    return out


def main() -> int:
    ids = _paired_ids()
    if not ids:
        raise SystemExit("no paired samples")

    acc = {src: {"Ali": [], "Ove": [], "Rea": [], "Occ": [], "panel": []}
           for src in ("gt", "gpt4o", "o4mini")}
    status = {"gpt4o": {}, "o4mini": {}}
    rounds = {"gpt4o": [], "o4mini": []}
    empty_rounds = {"o4mini": 0}
    paired = {ax: {} for ax in AXES}
    per_sample = []
    n_geo = 0

    for sid in ids:
        pre_p = SEGA_PRE / sid / "sega_input.json"
        meta_p = OUTPUT1 / f"crello_{sid}" / "meta.json"
        row_g = json.loads((N100 / sid / "row.json").read_text())
        row_o = json.loads((O4MINI / sid / "row.json").read_text())

        arm_g = row_g.get("a", {})
        sg = arm_g.get("status", "?")
        so = row_o.get("status", "?")
        status["gpt4o"][sg] = status["gpt4o"].get(sg, 0) + 1
        status["o4mini"][so] = status["o4mini"].get(so, 0) + 1
        if arm_g.get("rounds") is not None:
            rounds["gpt4o"].append(arm_g["rounds"])
        if row_o.get("rounds") is not None:
            rounds["o4mini"].append(row_o["rounds"])
        for tr in row_o.get("trace") or []:
            if tr.get("candidate_count") == 0:
                empty_rounds["o4mini"] += 1

        # --- paired verdict cross-tab (gpt4o outcome -> o4mini outcome) ---
        vg, vo = arm_g.get("verdict"), row_o.get("verdict")
        if isinstance(vg, dict) and isinstance(vo, dict):
            for ax in AXES:
                key = f"{vg.get(ax)}->{vo.get(ax)}"
                paired[ax][key] = paired[ax].get(key, 0) + 1
            per_sample.append({"id": sid,
                               "gpt4o": {ax: vg.get(ax) for ax in AXES},
                               "o4mini": {ax: vo.get(ax) for ax in AXES}})

        # --- judge-free geometry ---
        if not (pre_p.exists() and meta_p.exists()):
            continue
        pre = json.loads(pre_p.read_text())
        meta = json.loads(meta_p.read_text())
        cw, ch = float(pre["canvas_width"]), float(pre["canvas_height"])
        bg = np.asarray(
            Image.open(pre["background_path"]).convert("RGB").resize((int(cw), int(ch))),
            dtype=np.uint8,
        )
        sal = s20._saliency_from_bg(bg)
        regions = pre["underlay_regions"]

        layouts = {"gt": _gt_layout(meta)}
        cp_g, cp_o = N100 / sid / "a" / "candidate.json", O4MINI / sid / "candidate.json"
        if cp_g.exists():
            layouts["gpt4o"] = _cand_layout(json.loads(cp_g.read_text()))
        if cp_o.exists():
            layouts["o4mini"] = _cand_layout(json.loads(cp_o.read_text()))

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
        n_geo += 1

    geometry = {}
    for src in ("gt", "gpt4o", "o4mini"):
        geometry[src] = {k: (round(sum(v) / len(v), 5) if v else None)
                         for k, v in acc[src].items()}
        geometry[src]["n"] = len(acc[src]["Ali"])

    result = {
        "n_paired": len(ids), "n_geometry": n_geo,
        "geometry": geometry,
        "status": status,
        "rounds_mean": {k: (round(sum(v) / len(v), 2) if v else None) for k, v in rounds.items()},
        "empty_candidate_rounds": empty_rounds,
        "verdict_paired": paired,
        "per_sample": per_sample,
    }

    print(f"n_paired={len(ids)}  n_geometry={n_geo}\n")
    print(f"{'':8s}{'Ali↓':>10s}{'Ove↓':>10s}{'Rea↓':>10s}{'Occ↓':>10s}{'panel↑':>10s}")
    for src in ("gt", "gpt4o", "o4mini"):
        g = geometry[src]

        def fmt(x):
            return f"{x:>10.5f}" if isinstance(x, float) else f"{'-':>10s}"

        print(f"{src:8s}{fmt(g['Ali'])}{fmt(g['Ove'])}{fmt(g['Rea'])}{fmt(g['Occ'])}"
              f"{fmt(g['panel'])}  (n={g['n']})")

    print("\nstatus:", json.dumps(status, ensure_ascii=False))
    print("rounds_mean:", result["rounds_mean"])
    print("empty_candidate_rounds (o4mini):", empty_rounds["o4mini"])
    print("\npaired verdict transitions (gpt4o -> o4mini):")
    for ax in AXES:
        flips = {k: v for k, v in sorted(paired[ax].items()) if v}
        print(f"  {ax:24s} {json.dumps(flips)}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
