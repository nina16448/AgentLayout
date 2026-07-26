"""Step 80 smoke -- text-as-image mode on the panel samples.

Same 9 samples / winner config as step79_smoke, ONE change: text elements
enter the pipeline as pre-rendered bitmaps (designer typography verbatim)
with their content strings attached, instead of raw strings the system must
typeset itself. Expected effects: typography quality = GT, size timidity
gone (natural size), render-parity gap (Step 54: 61-68% of blind gap)
eliminated for text.

Run (consumes real LLM tokens):
    conda activate meta
    python layout_agent/output2/step80_smoke.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import List, Tuple

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from layout_agent.output2.step76_ab_live import (  # noqa: E402
    OUTPUT1,
    SEGA_PRE,
    DEMO_IDS,
    _blind_pairwise,
)
from layout_agent.output2.step79_smoke import _compare_image  # noqa: E402
from metagpt.ext.agentlayout.actions.analyze_brief import AssetInput  # noqa: E402
from metagpt.ext.agentlayout.pipeline import (  # noqa: E402
    LayoutPipeline,
    PipelineConfig,
    PipelineError,
)
from metagpt.ext.agentlayout.schema import UnderlayRegion  # noqa: E402
from metagpt.ext.agentlayout.tools.renderer import render_to_file  # noqa: E402
from metagpt.llm import LLM  # noqa: E402

SMOKE_ROOT = HERE / "step80_smoke"


def _build_text_image_inputs(pre: dict) -> Tuple[str, List[AssetInput], List[UnderlayRegion]]:
    """Mode C: half-finished poster + pre-rendered text bitmaps."""
    cw, ch = pre["canvas_width"], pre["canvas_height"]
    bg_ref = pre["background_path"]
    user_brief = (
        f"Create a {cw}x{ch} marketing graphic for the theme '{pre['title']}'. "
        f"Canvas size is exactly {cw} pixels wide by {ch} pixels tall. "
        "The image asset provided below is a HALF-FINISHED poster: background, "
        "photos and decorative panels are already in their final positions. "
        f"Set canvas.background_asset_ref to exactly '{bg_ref}'. "
        "Every OTHER image asset ends with '_text.png': each is a PRE-RENDERED "
        "TEXT layer (designer typography, transparent background) whose text "
        "content is provided alongside it. Your job is ONLY to place these "
        "text layers on the poster. Do NOT create plain text elements and do "
        "NOT add any other imagery."
    )
    asset_list: List[AssetInput] = []
    # bg is referenced via the brief directive (same as mode B); asset_list
    # carries only the placeable text bitmaps.
    for ta in pre["text_assets"]:
        asset_list.append(AssetInput(asset_ref=ta["asset_ref"], content=ta["content"]))
    regions = [UnderlayRegion(**r) for r in pre["underlay_regions"]]
    return user_brief, asset_list, regions


async def main() -> int:
    global SMOKE_ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=str, default=None,
                        help="output folder name under output2/ (e.g. step81_smoke)")
    args = parser.parse_args()
    if args.out:
        SMOKE_ROOT = HERE / args.out

    os.environ.pop("AGENTLAYOUT_VISUAL_LOOP", None)  # winner config: loop OFF

    ids = []
    for sid in DEMO_IDS:
        p = SEGA_PRE / sid / "sega_input.json"
        if not p.exists():
            continue
        pre = json.loads(p.read_text())
        if pre["underlay_regions"] and pre["text_assets"]:
            ids.append(sid)

    print(f"runnable panel samples with text assets: {len(ids)}")
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
        user_brief, asset_list, regions = _build_text_image_inputs(pre)

        row = {"id": sid, "n_text_assets": len(pre["text_assets"])}
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
