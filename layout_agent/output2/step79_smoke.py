"""Step 79 smoke -- first live run on CORRECTED panel-type data (small batch).

Only the samples that actually have underlay regions (9 of 19) -- the Step 79
frame/solid fix affects exactly these. Winner config: SEGA arm, visual loop
OFF, text prior + assignments on.

Per-sample artifacts for eyeballing:
    layout_agent/output2/step79_smoke/<id>/final_b.png   -- candidate render
    layout_agent/output2/step79_smoke/<id>/compare.png   -- [candidate | GT]
    layout_agent/output2/step79_smoke/<id>/candidate.json -- winning bboxes
    layout_agent/output2/step79_smoke/_summary.json

Run (consumes real LLM tokens, ~9 pipeline runs + 9 blind verdicts):
    conda activate meta
    python layout_agent/output2/step79_smoke.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import List

from PIL import Image

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from layout_agent.output2.step76_ab_live import (  # noqa: E402
    OUTPUT1,
    SEGA_PRE,
    DEMO_IDS,
    _blind_pairwise,
    _build_sega_inputs,
)
from metagpt.ext.agentlayout.pipeline import (  # noqa: E402
    LayoutPipeline,
    PipelineConfig,
    PipelineError,
)
from metagpt.ext.agentlayout.tools.renderer import render_to_file  # noqa: E402
from metagpt.llm import LLM  # noqa: E402

SMOKE_ROOT = HERE / "step79_smoke"


def _compare_image(final_png: Path, gt_path: Path, out_path: Path) -> None:
    """[candidate | GT] side-by-side, heights matched."""
    cand = Image.open(final_png).convert("RGB")
    panes = [cand]
    if gt_path.exists():
        gt = Image.open(gt_path).convert("RGB")
        gt = gt.resize((int(gt.width * cand.height / gt.height), cand.height))
        panes.append(gt)
    gap = 12
    total_w = sum(p.width for p in panes) + gap * (len(panes) - 1)
    sheet = Image.new("RGB", (total_w, cand.height), (220, 220, 220))
    x = 0
    for p in panes:
        sheet.paste(p, (x, 0))
        x += p.width + gap
    sheet.save(out_path, format="PNG")


async def main() -> int:
    os.environ.pop("AGENTLAYOUT_VISUAL_LOOP", None)  # winner config: loop OFF

    ids = []
    for sid in DEMO_IDS:
        p = SEGA_PRE / sid / "sega_input.json"
        if p.exists() and json.loads(p.read_text())["underlay_regions"]:
            ids.append(sid)

    SMOKE_ROOT.mkdir(parents=True, exist_ok=True)
    llm = LLM()
    rows: List[dict] = []
    for n, sid in enumerate(ids, 1):
        print(f"\n=== [{n}/{len(ids)}] {sid} ===", flush=True)
        out_dir = SMOKE_ROOT / sid
        out_dir.mkdir(parents=True, exist_ok=True)
        crello_dir = OUTPUT1 / f"crello_{sid}"
        meta = json.loads((crello_dir / "meta.json").read_text())
        pre = json.loads((SEGA_PRE / sid / "sega_input.json").read_text())
        user_brief, asset_list, regions = _build_sega_inputs(pre)

        row = {"id": sid,
               "panel_types": [r.panel_type for r in regions]}
        pipe = LayoutPipeline(config=PipelineConfig(max_total_rounds=3))
        try:
            result = await pipe.run(user_brief=user_brief, asset_list=asset_list,
                                    underlay_regions=regions)
            candidate, spec = result.accepted_candidate, result.spec
            row.update(status="accepted", internal_accepted=True,
                       rounds=len(result.trace))
        except PipelineError as err:
            if err.best_candidate is None or err.spec is None:
                row.update(status="pipeline_error", error=str(err))
                rows.append(row)
                print(f"  {row['status']}", flush=True)
                continue
            candidate, spec = err.best_candidate, err.spec
            row.update(status="exhausted_judged", internal_accepted=False)

        png = out_dir / "final_b.png"
        render_to_file(candidate, spec, png)
        # Persist the winning layout bboxes -- the forensic gap noted in 76b.
        (out_dir / "candidate.json").write_text(candidate.model_dump_json(indent=2))
        gt_path = crello_dir / "ground_truth_preview.jpg"
        _compare_image(png, gt_path, out_dir / "compare.png")

        if gt_path.exists():
            row["verdict"] = await _blind_pairwise(
                llm, sid, "B", meta.get("title", ""), png, gt_path
            )
        rows.append(row)
        (out_dir / "row.json").write_text(json.dumps(row, indent=2, ensure_ascii=False))
        verdict = row.get("verdict") or {}
        print(f"  {row['status']}  overall={verdict.get('overall_winner')}  "
              f"design={verdict.get('design_layout')}", flush=True)

        summary = {
            "n": len(rows),
            "accepted": sum(1 for r in rows if r.get("status") == "accepted"),
            "overall": {k: sum(1 for r in rows
                               if (r.get("verdict") or {}).get("overall_winner") == k)
                        for k in ("cand", "gt", "tie")},
            "design_layout": {k: sum(1 for r in rows
                                     if (r.get("verdict") or {}).get("design_layout") == k)
                              for k in ("cand", "gt", "tie")},
            "rows": rows,
        }
        (SMOKE_ROOT / "_summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False))

    print("\nDONE. Eyeball the compare.png files in", SMOKE_ROOT, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
