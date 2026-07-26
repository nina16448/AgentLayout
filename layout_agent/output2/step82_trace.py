"""Step 82 -- single-sample round-by-round render trace.

User request: run ONE sample and save the render of the first attempt AND
every subsequent loop round, to see how the layout evolves inside the
pipeline. Uses the Step 82 round_callback hook.

Config: text-as-image mode + hierarchy prior (current best), visual loop OFF,
sample 590afa87 (the forest poster the user has been eyeballing) unless
--sample is given.

Outputs (layout_agent/output2/step82_trace/<id>/):
    round{r}_cand{i}.png          -- every QC-kept candidate of round r
    round{r}_cand{i}_BEST.png     -- the one the internal judge picked
    round{r}_judgement.json       -- decision + per-candidate scores
    final.png                     -- the run's final output
    progress.png                  -- one sheet: rounds left-to-right + GT
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import List

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from layout_agent.output2.step76_ab_live import OUTPUT1, SEGA_PRE  # noqa: E402
from layout_agent.output2.step80_smoke import _build_text_image_inputs  # noqa: E402
from metagpt.ext.agentlayout.pipeline import (  # noqa: E402
    LayoutPipeline,
    PipelineConfig,
    PipelineError,
)
from metagpt.ext.agentlayout.tools.renderer import render_to_file  # noqa: E402

TRACE_ROOT = HERE / "step82_trace"
DEFAULT_SAMPLE = "590afa8795a7a863ddcd6e10"
THUMB_H = 420


def _sheet(out_dir: Path, gt_path: Path) -> None:
    """Stack per-round strips + GT into one labelled progress sheet."""
    rounds: List[List[Path]] = []
    r = 0
    while True:
        pngs = sorted(out_dir.glob(f"round{r}_cand*.png"))
        if not pngs:
            break
        rounds.append(pngs)
        r += 1
    panes: List[Image.Image] = []
    labels: List[str] = []
    for r, pngs in enumerate(rounds):
        for p in pngs:
            img = Image.open(p).convert("RGB")
            img = img.resize((int(img.width * THUMB_H / img.height), THUMB_H))
            panes.append(img)
            labels.append(f"R{r}" + (" BEST" if "_BEST" in p.name else ""))
    if gt_path.exists():
        gt = Image.open(gt_path).convert("RGB")
        gt = gt.resize((int(gt.width * THUMB_H / gt.height), THUMB_H))
        panes.append(gt)
        labels.append("GT")
    if not panes:
        return
    gap, band = 10, 26
    total_w = sum(p.width for p in panes) + gap * (len(panes) - 1)
    sheet = Image.new("RGB", (total_w, THUMB_H + band), (245, 245, 245))
    draw = ImageDraw.Draw(sheet)
    x = 0
    for img, label in zip(panes, labels):
        sheet.paste(img, (x, band))
        draw.text((x + 4, 5), label, fill=(180, 30, 30) if "BEST" in label or label == "GT"
                  else (60, 60, 60))
        x += img.width + gap
    sheet.save(out_dir / "progress.png", format="PNG")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=str, default=DEFAULT_SAMPLE)
    parser.add_argument("--n-concepts", type=int, default=None,
                        help="override concept count (1 = single-candidate deep review)")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--out", type=str, default=None,
                        help="output folder name under output2/ (default step82_trace)")
    parser.add_argument("--visual-loop", action="store_true",
                        help="enable the Step 85 rubric observer + issue ledger")
    args = parser.parse_args()
    sid = args.sample
    trace_root = HERE / args.out if args.out else TRACE_ROOT

    if args.visual_loop:
        os.environ["AGENTLAYOUT_VISUAL_LOOP"] = "1"
    else:
        os.environ.pop("AGENTLAYOUT_VISUAL_LOOP", None)
    out_dir = trace_root / sid
    out_dir.mkdir(parents=True, exist_ok=True)
    crello_dir = OUTPUT1 / f"crello_{sid}"
    pre = json.loads((SEGA_PRE / sid / "sega_input.json").read_text())
    user_brief, asset_list, regions = _build_text_image_inputs(pre)

    def on_round(round_idx, kept, judgement, spec):
        for i, cand in enumerate(kept):
            best = cand.candidate_id == judgement.best_candidate_id
            name = f"round{round_idx}_cand{i}{'_BEST' if best else ''}.png"
            render_to_file(cand, spec, out_dir / name)
        (out_dir / f"round{round_idx}_judgement.json").write_text(json.dumps({
            "decision": judgement.decision.value,
            "best": judgement.best_candidate_id,
            "evaluations": [
                {"id": ev.candidate_id, "total": ev.total,
                 "scores": ev.scores.model_dump(),
                 "weaknesses": ev.weaknesses}
                for ev in judgement.evaluations
            ],
            # Step 87 visibility fix: the per-element inspector output (ledger
            # view actually fed to the mapper) -- THIS carries the concrete
            # per-element verdicts + target bboxes; the `weaknesses` prose
            # above is the verdict call's summary and is not load-bearing.
            "common_issues": judgement.feedback.common_issues,
            "visual_observations_ledger_view": [
                o.model_dump() for o in judgement.feedback.visual_observations
            ],
        }, indent=2, ensure_ascii=False))
        print(f"  round {round_idx}: {judgement.decision.value}  "
              f"kept={len(kept)}  best={judgement.best_candidate_id}", flush=True)

    pipe = LayoutPipeline(config=PipelineConfig(
        max_total_rounds=args.rounds, n_concepts=args.n_concepts))
    status = "accepted"
    try:
        result = await pipe.run(user_brief=user_brief, asset_list=asset_list,
                                underlay_regions=regions, round_callback=on_round)
        candidate, spec = result.accepted_candidate, result.spec
    except PipelineError as err:
        if err.best_candidate is None or err.spec is None:
            print("pipeline_error:", err)
            return 1
        candidate, spec = err.best_candidate, err.spec
        status = "exhausted"

    render_to_file(candidate, spec, out_dir / "final.png")
    _sheet(out_dir, crello_dir / "ground_truth_preview.jpg")
    print(f"\nDONE ({status}). Artifacts in {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
