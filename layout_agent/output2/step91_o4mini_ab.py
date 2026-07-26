"""Step 91 -- what changes in the system when the pipeline roles run on o4-mini?

Only ONE thing differs from the Step 89 arm-A reference: the model driving the
pipeline roles (Analyst / CompositionDirector / CoordinateMapper / internal
JudgeAesthetic). Everything else -- samples, pipeline config, prompts, blind
evaluator -- is held fixed.

    reference : Step 89 arm A rows, pipeline roles on gpt-4o (already on disk)
    this run  : same samples + same config, pipeline roles on o4-mini

The blind pairwise-vs-designer-GT evaluator is PINNED to gpt-4o. Swapping the
evaluator alongside the system would confound "did the layouts get worse" with
"did the grader change", making the win rates uninterpretable.

Pipeline config = Step 89 arm A (3 concepts, max_total_rounds=3, visual loop
OFF), so the existing step89_n100/<id>/a/ rows are a valid paired reference.
No gpt-4o arm is re-run.

Deliberately NOT instrumented: token/cost accounting. MetaGPT's streamed path
never yields a real usage block (OpenAI omits it without
stream_options.include_usage, and the _calc_usage fallback throws on vision
messages and is swallowed), so any number here would be fake. Read the billing
dashboard instead. Leaving the streamed path untouched also keeps this run's
call shape identical to Step 89's.

Captured per sample: terminal status, judge rounds, the full pipeline trace
(per-round decision / candidate_count / qc_filtered_count -- these expose
refusals and QC blowups), and the blind verdict on four axes.

RESUMABLE: samples with an existing row.json are skipped.

Run (model override must land before any Action is constructed, hence one
process per model):
    conda activate meta
    python layout_agent/output2/step91_o4mini_ab.py --model o4-mini --limit 1  # smoke
    python layout_agent/output2/step91_o4mini_ab.py --model o4-mini
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Model override MUST happen before any Action/LLM is constructed.
_ARGP = argparse.ArgumentParser()
_ARGP.add_argument("--model", required=True, choices=["o4-mini", "gpt-4o"])
_ARGP.add_argument("--limit", type=int, default=20)
_ARGP.add_argument("--out", default="step91_model_ab")
ARGS = _ARGP.parse_args()

from metagpt.config2 import config  # noqa: E402

config.llm.model = ARGS.model

from layout_agent.output2.step76_ab_live import OUTPUT1, SEGA_PRE, _blind_pairwise  # noqa: E402
from layout_agent.output2.step80_smoke import _build_text_image_inputs  # noqa: E402
from metagpt.ext.agentlayout.pipeline import (  # noqa: E402
    LayoutPipeline,
    PipelineConfig,
    PipelineError,
)
from metagpt.ext.agentlayout.tools.renderer import render_to_file  # noqa: E402
from metagpt.llm import LLM  # noqa: E402
from metagpt.provider.base_llm import BaseLLM  # noqa: E402

OUT_ROOT = HERE / ARGS.out / ARGS.model.replace("-", "")
IDS_FILE = HERE / "eval100_ids.json"
EVALUATOR_MODEL = "gpt-4o"  # pinned; see module docstring
ARM_CONFIG = dict(max_total_rounds=3)  # Step 89 arm A
FLAG = "AGENTLAYOUT_VISUAL_LOOP"


def _trace_rows(trace) -> list:
    """Per-round pipeline behaviour: refusals show up as candidate_count 0,
    QC blowups as qc_filtered_count == candidate_count + filtered."""
    out = []
    for t in trace or []:
        out.append({
            "round": t.round_idx,
            "decision": t.decision,
            "feedback_target": t.feedback_target,
            "candidate_count": t.candidate_count,
            "qc_filtered_count": t.qc_filtered_count,
        })
    return out


async def _run_one(sample_id: str, out_dir: Path, evaluator: BaseLLM) -> dict:
    os.environ.pop(FLAG, None)  # visual loop OFF (arm A)

    crello_dir = OUTPUT1 / f"crello_{sample_id}"
    meta = json.loads((crello_dir / "meta.json").read_text())
    pre = json.loads((SEGA_PRE / sample_id / "sega_input.json").read_text())
    user_brief, asset_list, regions = _build_text_image_inputs(pre)

    row = {"id": sample_id, "model": ARGS.model}
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()

    pipe = LayoutPipeline(config=PipelineConfig(**ARM_CONFIG))
    try:
        result = await pipe.run(
            user_brief=user_brief, asset_list=asset_list, underlay_regions=regions or None
        )
        candidate, spec, trace = result.accepted_candidate, result.spec, result.trace
        row.update(status="accepted", internal_accepted=True)
    except PipelineError as err:
        if err.best_candidate is None or err.spec is None:
            row.update(status="pipeline_error", error=str(err),
                       wall_s=round(time.monotonic() - t0, 1))
            return row
        candidate, spec, trace = err.best_candidate, err.spec, err.trace
        row.update(status="exhausted_judged", internal_accepted=False)

    row["rounds"] = len(trace or [])
    row["trace"] = _trace_rows(trace)
    row["wall_s"] = round(time.monotonic() - t0, 1)

    png = out_dir / "final.png"
    render_to_file(candidate, spec, png)
    # spec + candidate are what the post-hoc SEGA geometric metrics rebuild
    # Layouts from -- Step 89 arm A stores the same two files.
    (out_dir / "candidate.json").write_text(candidate.model_dump_json())
    (out_dir / "spec.json").write_text(spec.model_dump_json())

    gt_path = crello_dir / "ground_truth_preview.jpg"
    if gt_path.exists():
        # arm label fixed to "B" -> same blind attachment order as Step 89.
        row["verdict"] = await _blind_pairwise(
            evaluator, sample_id, "B", meta.get("title", ""), png, gt_path
        )
    return row


def _tally(rows: list) -> dict:
    axes = ["overall_winner", "design_layout", "typography_color",
            "graphics_images", "content_relevance", "innovation_originality"]
    t = {"n": len(rows), "accepted": 0, "exhausted_judged": 0, "pipeline_error": 0,
         "crash": 0, "no_verdict": 0, "rounds_total": 0, "wall_s_total": 0.0,
         "empty_candidate_rounds": 0, "qc_filtered_total": 0}
    for ax in axes:
        t[ax] = {"cand": 0, "gt": 0, "tie": 0}
    for r in rows:
        if r.get("status") in t:
            t[r["status"]] += 1
        t["rounds_total"] += r.get("rounds", 0)
        t["wall_s_total"] += r.get("wall_s", 0.0)
        for tr in r.get("trace") or []:
            if tr.get("candidate_count") == 0:
                t["empty_candidate_rounds"] += 1
            t["qc_filtered_total"] += tr.get("qc_filtered_count", 0)
        v = r.get("verdict")
        if isinstance(v, dict):
            for ax in axes:
                if v.get(ax) in t[ax]:
                    t[ax][v[ax]] += 1
        else:
            t["no_verdict"] += 1
    t["wall_s_total"] = round(t["wall_s_total"], 1)
    return t


async def main() -> int:
    all_ids = json.loads(IDS_FILE.read_text())["ids"]
    ids = []
    for sid in all_ids:
        p = SEGA_PRE / sid / "sega_input.json"
        if p.exists() and json.loads(p.read_text())["text_assets"]:
            ids.append(sid)
    ids = ids[: ARGS.limit]
    print(f"model={ARGS.model} evaluator={EVALUATOR_MODEL} n={len(ids)}", flush=True)

    evaluator = LLM(llm_config=config.llm.model_copy(update={"model": EVALUATOR_MODEL}))

    rows = []
    for i, sid in enumerate(ids, 1):
        out_dir = OUT_ROOT / sid
        row_path = out_dir / "row.json"
        if row_path.exists():
            rows.append(json.loads(row_path.read_text()))
            print(f"[{i}/{len(ids)}] {sid} cached", flush=True)
            continue
        try:
            row = await _run_one(sid, out_dir, evaluator)
        except Exception as exc:  # noqa: BLE001 -- one bad sample must not kill the run
            row = {"id": sid, "model": ARGS.model, "status": "crash", "error": repr(exc)}
        out_dir.mkdir(parents=True, exist_ok=True)
        row_path.write_text(json.dumps(row, ensure_ascii=False, indent=1))
        rows.append(row)
        v = (row.get("verdict") or {}).get("overall_winner", "-")
        print(f"[{i}/{len(ids)}] {sid} {row.get('status')} rounds={row.get('rounds','-')} "
              f"overall={v} {row.get('wall_s','?')}s", flush=True)

    summary = {"model": ARGS.model, "evaluator": EVALUATOR_MODEL,
               "arm_config": ARM_CONFIG, "tally": _tally(rows), "rows": rows}
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    (OUT_ROOT / "_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps(summary["tally"], indent=1, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
