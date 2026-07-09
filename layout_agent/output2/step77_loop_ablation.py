"""Step 77d -- visual-loop ablation on the SEGA arm (loop-off vs loop-on).

Same N=19 eval set, arm B inputs only (bg_composite + text-only + underlay
feed-forward + Step 77 text_assignments). Each sample runs the pipeline TWICE:

    off: AGENTLAYOUT_VISUAL_LOOP unset  -> judge feedback as in Step 76c
    on:  AGENTLAYOUT_VISUAL_LOOP=1      -> judge also emits closed-catalogue
                                           visual_observations; the pipeline
                                           records per-round COMPLIANCE

Both variants get a blind pairwise verdict vs the designer GT (identical
attachment order per sample, so the on/off comparison is order-matched).
Selection-effect-free: exhausted runs are rendered and judged too (Step 76b).

Outputs:
    layout_agent/output2/step77_loop_ablation/<id>/{off,on}/final_b.png
    layout_agent/output2/step77_loop_ablation/<id>/row.json
    layout_agent/output2/step77_loop_ablation/_summary.json

Run (consumes real LLM tokens, 19 samples x 2 variants):
    conda activate meta
    python layout_agent/output2/step77_loop_ablation.py [--limit N]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

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

# Default output folder; Step 78 reruns pass --out step78_decoupled so each
# loop design's results stay separate.
ABL_ROOT = HERE / "step77_loop_ablation"
FLAG = "AGENTLAYOUT_VISUAL_LOOP"


async def _run_variant(sample_id: str, variant: str, out_dir: Path, llm: LLM) -> dict:
    """One pipeline run on arm-B inputs; variant toggles the visual loop."""
    if variant == "on":
        os.environ[FLAG] = "1"
    else:
        os.environ.pop(FLAG, None)

    crello_dir = OUTPUT1 / f"crello_{sample_id}"
    meta = json.loads((crello_dir / "meta.json").read_text())
    pre = json.loads((SEGA_PRE / sample_id / "sega_input.json").read_text())
    user_brief, asset_list, regions = _build_sega_inputs(pre)

    pipe = LayoutPipeline(config=PipelineConfig(max_total_rounds=3))
    candidate = spec = None
    trace: Optional[list] = None
    row = {"variant": variant}
    try:
        result = await pipe.run(
            user_brief=user_brief, asset_list=asset_list,
            underlay_regions=regions or None,
        )
        candidate, spec, trace = result.accepted_candidate, result.spec, result.trace
        row.update(status="accepted", internal_accepted=True)
    except PipelineError as err:
        if err.best_candidate is None or err.spec is None:
            return {"variant": variant, "status": "pipeline_error", "error": str(err)}
        candidate, spec, trace = err.best_candidate, err.spec, err.trace
        row.update(status="exhausted_judged", internal_accepted=False, error=str(err))

    row["trace"] = [t.model_dump() for t in (trace or [])]
    # Aggregate the Step 77 compliance rows for this run.
    comp_rows = [t.compliance for t in (trace or []) if t.compliance is not None]
    row["n_observation_rounds"] = len(comp_rows)
    verifiable = sum(c["n_verifiable"] for c in comp_rows)
    satisfied = sum(c["n_satisfied"] for c in comp_rows)
    row["compliance"] = {
        "n_verifiable": verifiable,
        "n_satisfied": satisfied,
        "rate": (satisfied / verifiable) if verifiable else None,
    }

    variant_dir = out_dir / variant
    variant_dir.mkdir(parents=True, exist_ok=True)
    png = variant_dir / "final_b.png"
    render_to_file(candidate, spec, png)

    gt_path = crello_dir / "ground_truth_preview.jpg"
    if gt_path.exists():
        # arm label fixed to "B" so on/off share the same blind attachment
        # order per sample -- the comparison is order-matched.
        row["verdict"] = await _blind_pairwise(
            llm, sample_id, "B", meta.get("title", ""), png, gt_path
        )
    return row


def _tally(rows: List[dict], variant: str) -> dict:
    t = {"accepted": 0, "exhausted_judged": 0, "pipeline_error": 0, "crash": 0,
         "overall": {"cand": 0, "gt": 0, "tie": 0},
         "design_layout": {"cand": 0, "gt": 0, "tie": 0},
         "n_observation_rounds": 0, "n_verifiable": 0, "n_satisfied": 0}
    for r in rows:
        v = r.get(variant, {})
        status = v.get("status")
        if status in t:
            t[status] += 1
        verdict = v.get("verdict")
        if isinstance(verdict, dict):
            t["overall"][verdict["overall_winner"]] += 1
            t["design_layout"][verdict["design_layout"]] += 1
        comp = v.get("compliance") or {}
        t["n_observation_rounds"] += v.get("n_observation_rounds", 0)
        t["n_verifiable"] += comp.get("n_verifiable", 0)
        t["n_satisfied"] += comp.get("n_satisfied", 0)
    t["compliance_rate"] = (
        t["n_satisfied"] / t["n_verifiable"] if t["n_verifiable"] else None
    )
    return t


def _write_summary(rows: List[dict]) -> None:
    summary = {"n": len(rows), "off": _tally(rows, "off"), "on": _tally(rows, "on"),
               "rows": rows}
    (ABL_ROOT / "_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False)
    )


async def main() -> int:
    global ABL_ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", type=str, default=None,
                        help="output folder name under output2/ (e.g. step78_decoupled)")
    args = parser.parse_args()
    if args.out:
        ABL_ROOT = HERE / args.out

    ids = [i for i in DEMO_IDS if (OUTPUT1 / f"crello_{i}" / "meta.json").exists()
           and (SEGA_PRE / i / "sega_input.json").exists()]
    if args.limit:
        ids = ids[: args.limit]

    ABL_ROOT.mkdir(parents=True, exist_ok=True)
    llm = LLM()
    rows = []
    for n, sid in enumerate(ids, 1):
        print(f"\n=== [{n}/{len(ids)}] {sid} ===", flush=True)
        out_dir = ABL_ROOT / sid
        out_dir.mkdir(parents=True, exist_ok=True)
        row = {"id": sid}
        for variant in ("off", "on"):
            try:
                row[variant] = await _run_variant(sid, variant, out_dir, llm)
            except Exception as err:  # noqa: BLE001
                row[variant] = {"variant": variant, "status": "crash",
                                "error": f"{type(err).__name__}: {err}"}
            v = row[variant]
            verdict = v.get("verdict") or {}
            print(f"  {variant}: {v.get('status')}  overall={verdict.get('overall_winner')}  "
                  f"obs_rounds={v.get('n_observation_rounds')}  "
                  f"compliance={((v.get('compliance') or {}).get('rate'))}", flush=True)
        (out_dir / "row.json").write_text(json.dumps(row, indent=2, ensure_ascii=False))
        rows.append(row)
        _write_summary(rows)
    _write_summary(rows)
    print("\nDONE. Summary at", ABL_ROOT / "_summary.json", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
