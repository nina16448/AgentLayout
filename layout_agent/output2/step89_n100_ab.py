"""Step 89 -- N=100 two-arm comparison: deep-review stack vs pre-83 baseline.

Same text-as-image inputs (Step 80) + priors + assignments on BOTH arms; the
arms isolate the Steps 83-89 contribution:

    Arm A (baseline): 3 concepts, rounds<=3, visual loop OFF
                      (the step80/81 configuration)
    Arm B (deep):     1 concept, rounds<=5, visual loop ON
                      (anchored judge + per-element review + ledger +
                       override + regression watch + KEEP constraints)

Blind pairwise vs designer GT per arm, attachment order fixed per sample
(order-matched across arms). Selection-effect-free (exhausted runs are
rendered and judged too). RESUMABLE: samples with an existing row.json are
skipped, so a crashed/killed run continues where it stopped.

Run (LLM cost ~US$50-100, ~6-8 h):
    conda activate meta
    python layout_agent/output2/step89_n100_ab.py [--limit N]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import List

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from layout_agent.output2.provenance import capture as _prov_capture  # noqa: E402
from layout_agent.output2.step76_ab_live import OUTPUT1, SEGA_PRE, _blind_pairwise  # noqa: E402
from layout_agent.output2.step80_smoke import _build_text_image_inputs  # noqa: E402
from metagpt.ext.agentlayout.pipeline import (  # noqa: E402
    LayoutPipeline,
    PipelineConfig,
    PipelineError,
)
from metagpt.ext.agentlayout.tools.renderer import render_to_file  # noqa: E402
from metagpt.llm import LLM  # noqa: E402

N100_ROOT = HERE / "step89_n100"
IDS_FILE = HERE / "eval100_ids.json"
FLAG = "AGENTLAYOUT_VISUAL_LOOP"

ARMS = {
    "a": {"loop": False, "config": dict(max_total_rounds=3)},
    "b": {"loop": True, "config": dict(max_total_rounds=5, n_concepts=1)},
}


async def _run_arm(sample_id: str, arm: str, out_dir: Path, llm: LLM) -> dict:
    spec_cfg = ARMS[arm]
    if spec_cfg["loop"]:
        os.environ[FLAG] = "1"
    else:
        os.environ.pop(FLAG, None)

    crello_dir = OUTPUT1 / f"crello_{sample_id}"
    meta = json.loads((crello_dir / "meta.json").read_text())
    pre = json.loads((SEGA_PRE / sample_id / "sega_input.json").read_text())
    user_brief, asset_list, regions = _build_text_image_inputs(pre)

    row = {"arm": arm}
    arm_dir = out_dir / arm
    (arm_dir / "rounds").mkdir(parents=True, exist_ok=True)

    def _on_round(round_idx, kept, judgement, cb_spec):
        """Per-round snapshot -- fuels the '每輪進步多少' post-hoc curves:
        judge scores + best candidate bboxes per round, both arms."""
        best = next((c for c in kept
                     if c.candidate_id == judgement.best_candidate_id), None)
        best_ev = next((e for e in judgement.evaluations
                        if e.candidate_id == judgement.best_candidate_id), None)
        (arm_dir / "rounds" / f"round{round_idx}.json").write_text(json.dumps({
            "round": round_idx,
            "decision": judgement.decision.value,
            "total": best_ev.total if best_ev else None,
            "scores": best_ev.scores.model_dump() if best_ev else None,
            "best_elements": [e.model_dump() for e in best.elements] if best else None,
            "ledger_open": len(judgement.feedback.visual_observations),
        }, ensure_ascii=False))

    pipe = LayoutPipeline(config=PipelineConfig(**spec_cfg["config"]))
    try:
        result = await pipe.run(user_brief=user_brief, asset_list=asset_list,
                                underlay_regions=regions or None,
                                round_callback=_on_round)
        candidate, spec, trace = result.accepted_candidate, result.spec, result.trace
        row.update(status="accepted", internal_accepted=True)
    except PipelineError as err:
        if err.best_candidate is None or err.spec is None:
            row.update(status="pipeline_error", error=str(err))
            return row
        candidate, spec, trace = err.best_candidate, err.spec, err.trace
        row.update(status="exhausted_judged", internal_accepted=False)

    row["rounds"] = len(trace or [])
    comp = [t.compliance for t in (trace or []) if t.compliance is not None]
    row["compliance"] = {
        "n_verifiable": sum(c["n_verifiable"] for c in comp),
        "n_satisfied": sum(c["n_satisfied"] for c in comp),
    }

    png = arm_dir / "final.png"
    render_to_file(candidate, spec, png)
    (arm_dir / "candidate.json").write_text(candidate.model_dump_json())
    # Spec is required to rebuild Layouts for the post-hoc SEGA geometric
    # metrics (Ali/Ove/Und/Rea/Occ) -- user requirement: metrics on BOTH arms.
    (arm_dir / "spec.json").write_text(spec.model_dump_json())

    gt_path = crello_dir / "ground_truth_preview.jpg"
    if gt_path.exists():
        # arm label fixed to "B" so both arms share the per-sample blind
        # attachment order (order-matched comparison).
        row["verdict"] = await _blind_pairwise(
            llm, sample_id, "B", meta.get("title", ""), png, gt_path
        )
    return row


def _tally(rows: List[dict], arm: str) -> dict:
    t = {"accepted": 0, "exhausted_judged": 0, "pipeline_error": 0, "crash": 0,
         "overall": {"cand": 0, "gt": 0, "tie": 0},
         "design_layout": {"cand": 0, "gt": 0, "tie": 0},
         "typography_color": {"cand": 0, "gt": 0, "tie": 0},
         "overall_accepted_only": {"cand": 0, "gt": 0, "tie": 0},
         "rounds_total": 0, "n_verifiable": 0, "n_satisfied": 0}
    for r in rows:
        a = r.get(arm, {})
        status = a.get("status")
        if status in t:
            t[status] += 1
        t["rounds_total"] += a.get("rounds", 0)
        comp = a.get("compliance") or {}
        t["n_verifiable"] += comp.get("n_verifiable", 0)
        t["n_satisfied"] += comp.get("n_satisfied", 0)
        v = a.get("verdict")
        if isinstance(v, dict):
            t["overall"][v["overall_winner"]] += 1
            t["design_layout"][v["design_layout"]] += 1
            t["typography_color"][v["typography_color"]] += 1
            if a.get("internal_accepted"):
                t["overall_accepted_only"][v["overall_winner"]] += 1
    t["compliance_rate"] = (
        t["n_satisfied"] / t["n_verifiable"] if t["n_verifiable"] else None
    )
    return t


def _write_summary(rows: List[dict]) -> None:
    # provenance: the original 2026-07-03 run recorded neither commit nor model,
    # so its source had to be reconstructed from mtimes afterwards. Never again.
    summary = {"n": len(rows), "provenance": _prov_capture(),
               "arm_a": _tally(rows, "a"), "arm_b": _tally(rows, "b"),
               "rows": rows}
    (N100_ROOT / "_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False))


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    all_ids = json.loads(IDS_FILE.read_text())["ids"]
    ids = []
    for sid in all_ids:
        p = SEGA_PRE / sid / "sega_input.json"
        if p.exists() and json.loads(p.read_text())["text_assets"]:
            ids.append(sid)
    if args.limit:
        ids = ids[: args.limit]
    print(f"runnable: {len(ids)}/{len(all_ids)}", flush=True)

    N100_ROOT.mkdir(parents=True, exist_ok=True)
    llm = LLM()
    rows: List[dict] = []
    # Resume: reload rows for already-finished samples.
    for n, sid in enumerate(ids, 1):
        out_dir = N100_ROOT / sid
        row_file = out_dir / "row.json"
        if row_file.exists():
            rows.append(json.loads(row_file.read_text()))
            continue
        print(f"\n=== [{n}/{len(ids)}] {sid} ===", flush=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        row = {"id": sid}
        for arm in ("a", "b"):
            try:
                row[arm] = await _run_arm(sid, arm, out_dir, llm)
            except Exception as err:  # noqa: BLE001
                row[arm] = {"arm": arm, "status": "crash",
                            "error": f"{type(err).__name__}: {err}"}
            v = row[arm].get("verdict") or {}
            print(f"  {arm}: {row[arm].get('status')}  "
                  f"overall={v.get('overall_winner')}  design={v.get('design_layout')}",
                  flush=True)
        row_file.write_text(json.dumps(row, indent=2, ensure_ascii=False))
        rows.append(row)
        _write_summary(rows)
    _write_summary(rows)
    print("\nDONE. Summary at", N100_ROOT / "_summary.json", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
