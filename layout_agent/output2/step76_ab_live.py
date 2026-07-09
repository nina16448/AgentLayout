"""Step 76 A/B live -- raw-asset inputs vs SEGA-style preprocessed inputs.

Both arms run the SAME "先想再畫" LayoutPipeline on the N=20 eval set
(layout_agent/demo_ids.json); the only variable is the input form:

    Arm A (baseline): messy Crello asset list (build_pipeline_inputs, as in
                      every run up to Step 75).
    Arm B (SEGA):     bg_composite.png as the background plate, text-only
                      asset list, underlay_regions fed forward via
                      pipe.run(underlay_regions=...).

Each arm's accepted render is judged BLIND against the designer GT preview:
two images attached in a deterministic-random order (md5 of sample id + arm),
no identity labels in the prompt (Step 51: labelled pairwise is biased).

Outputs (user request 2026-07-02: new results live under output2/):
    layout_agent/output2/step76_ab/<id>/final_a.png / final_b.png
    layout_agent/output2/step76_ab/<id>/verdict_a.json / verdict_b.json
    layout_agent/output2/step76_ab/<id>/row.json
    layout_agent/output2/step76_ab/_summary.json

Run (consumes real LLM tokens, ~19 samples x 2 arms):
    conda activate meta
    python layout_agent/output2/step76_ab_live.py [--limit N]
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import io
import json
import sys
from pathlib import Path
from typing import List, Optional

from PIL import Image

HERE = Path(__file__).resolve().parent          # layout_agent/output2
REPO_ROOT = HERE.parents[1]
OUTPUT1 = HERE.parent / "output"                # legacy data lives here
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from layout_agent.output.run_role_team_live_crello import (  # noqa: E402
    build_pipeline_inputs,
    load_crello_sample,
)
from metagpt.ext.agentlayout.actions.analyze_brief import AssetInput  # noqa: E402
from metagpt.ext.agentlayout.pipeline import (  # noqa: E402
    LayoutPipeline,
    PipelineConfig,
    PipelineError,
)
from metagpt.ext.agentlayout.schema import UnderlayRegion  # noqa: E402
from metagpt.ext.agentlayout.tools.renderer import render_to_file  # noqa: E402
from metagpt.llm import LLM  # noqa: E402
from metagpt.logs import logger  # noqa: E402

# Run 2 (Step 76c): text-area prior added + selection-effect fix -- results go
# to a fresh folder so run 1 (step76_ab/) stays intact for comparison.
AB_ROOT = HERE / "step76_ab_run2"
SEGA_PRE = OUTPUT1 / "sega_pre"
DEMO_IDS = json.loads((HERE.parent / "demo_ids.json").read_text())["ids"]
MAX_JUDGE_RETRIES = 3
JUDGE_IMG_MAX_SIDE = 768

BLIND_PAIRWISE_PROMPT = """You are an expert graphic design judge. Two finished poster designs for the
SAME brief are attached, in random order. Theme of the brief: '{title}'.
Neither image is labelled -- judge purely on what you see.

For EACH axis pick exactly one of "1" (first image wins), "2" (second image
wins) or "tie":
  design_layout, content_relevance, typography_color, graphics_images,
  innovation_originality

Then pick overall_winner the same way and give a one-line reason.

Output JSON only, no commentary, no markdown fences:
{{"design_layout": "...", "content_relevance": "...", "typography_color": "...",
 "graphics_images": "...", "innovation_originality": "...",
 "overall_winner": "...", "reason": "..."}}"""


def _b64_png(path: Path) -> str:
    """Load any raster file, downscale to JUDGE_IMG_MAX_SIDE, emit base64 PNG."""
    img = Image.open(path).convert("RGB")
    img.thumbnail((JUDGE_IMG_MAX_SIDE, JUDGE_IMG_MAX_SIDE), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _candidate_first(sample_id: str, arm: str) -> bool:
    """Deterministic-random blind order (reproducible across reruns)."""
    return int(hashlib.md5(f"{sample_id}:{arm}".encode()).hexdigest(), 16) % 2 == 0


def _parse_verdict(text: str) -> Optional[dict]:
    text = (text or "").strip()
    if "```" in text:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return None
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    keys = {
        "design_layout", "content_relevance", "typography_color",
        "graphics_images", "innovation_originality", "overall_winner",
    }
    if not keys.issubset(data):
        return None
    if any(data[k] not in ("1", "2", "tie") for k in keys):
        return None
    return data


async def _blind_pairwise(llm: LLM, sample_id: str, arm: str, title: str,
                          candidate_png: Path, gt_path: Path) -> Optional[dict]:
    """Judge candidate vs GT blind; return verdict remapped to cand/gt/tie."""
    cand_first = _candidate_first(sample_id, arm)
    imgs = [_b64_png(candidate_png), _b64_png(gt_path)]
    if not cand_first:
        imgs.reverse()
    prompt = BLIND_PAIRWISE_PROMPT.format(title=title)

    for attempt in range(1, MAX_JUDGE_RETRIES + 1):
        rsp = await llm.aask(prompt, images=imgs)
        verdict = _parse_verdict(rsp)
        if verdict is not None:
            cand_label = "1" if cand_first else "2"
            gt_label = "2" if cand_first else "1"
            remap = {cand_label: "cand", gt_label: "gt", "tie": "tie"}
            out = {k: remap[v] for k, v in verdict.items() if k != "reason"}
            out["reason"] = verdict.get("reason", "")
            out["candidate_was_first"] = cand_first
            return out
        logger.warning(f"blind judge parse fail {sample_id}/{arm} attempt {attempt}")
    return None


def _build_sega_inputs(pre: dict) -> tuple:
    """Arm B brief + assets: half-finished poster + text snippets only."""
    cw, ch = pre["canvas_width"], pre["canvas_height"]
    bg_ref = pre["background_path"]
    user_brief = (
        f"Create a {cw}x{ch} marketing graphic for the theme '{pre['title']}'. "
        f"Canvas size is exactly {cw} pixels wide by {ch} pixels tall. "
        "The image asset provided below is a HALF-FINISHED poster: background, "
        "photos and decorative panels are already in their final positions. "
        f"Set canvas.background_asset_ref to exactly '{bg_ref}' and do NOT add "
        "any image elements. Your job is ONLY to place the provided text "
        "snippets on top of it. The visible heading text MUST come from the "
        "text snippets in the asset list, NOT from this theme description."
    )
    asset_list = [AssetInput(content=t) for t in pre["text_contents"]]
    regions = [UnderlayRegion(**r) for r in pre["underlay_regions"]]
    return user_brief, asset_list, regions


async def _run_arm(sample_id: str, arm: str, out_dir: Path, llm: LLM) -> dict:
    """Run one pipeline arm end-to-end; returns the result row fragment."""
    crello_dir = OUTPUT1 / f"crello_{sample_id}"
    gt_path = crello_dir / "ground_truth_preview.jpg"
    meta = json.loads((crello_dir / "meta.json").read_text())

    if arm == "A":
        _, descriptors = load_crello_sample(crello_dir)
        user_brief, asset_list = build_pipeline_inputs(meta, descriptors)
        regions: List[UnderlayRegion] = []
    else:
        pre = json.loads((SEGA_PRE / sample_id / "sega_input.json").read_text())
        user_brief, asset_list, regions = _build_sega_inputs(pre)

    pipe = LayoutPipeline(config=PipelineConfig(max_total_rounds=3))
    candidate = spec = None
    row = {"arm": arm}
    try:
        result = await pipe.run(
            user_brief=user_brief, asset_list=asset_list,
            underlay_regions=regions or None,
        )
        candidate, spec = result.accepted_candidate, result.spec
        row.update(
            status="accepted",
            internal_accepted=True,
            rounds=len(result.trace),
            trace=[t.model_dump() for t in result.trace],
        )
    except PipelineError as err:
        # Step 76b selection-effect fix: an exhausted run still carries the
        # last round's judge-preferred candidate -- render and blind-judge it
        # so blind statistics cover ALL samples, not just internal accepts.
        if err.best_candidate is not None and err.spec is not None:
            candidate, spec = err.best_candidate, err.spec
            row.update(status="exhausted_judged", internal_accepted=False,
                       error=str(err))
        else:
            return {"arm": arm, "status": "pipeline_error", "error": str(err)}

    png = out_dir / f"final_{arm.lower()}.png"
    render_to_file(candidate, spec, png)

    if gt_path.exists():
        verdict = await _blind_pairwise(
            llm, sample_id, arm, meta.get("title", ""), png, gt_path
        )
        row["verdict"] = verdict
        (out_dir / f"verdict_{arm.lower()}.json").write_text(
            json.dumps(verdict, indent=2, ensure_ascii=False)
        )
    return row


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="run first N samples only")
    args = parser.parse_args()

    ids = [i for i in DEMO_IDS if (OUTPUT1 / f"crello_{i}" / "meta.json").exists()
           and (SEGA_PRE / i / "sega_input.json").exists()]
    if args.limit:
        ids = ids[: args.limit]

    AB_ROOT.mkdir(parents=True, exist_ok=True)
    llm = LLM()
    rows = []
    for n, sid in enumerate(ids, 1):
        print(f"\n=== [{n}/{len(ids)}] {sid} ===", flush=True)
        out_dir = AB_ROOT / sid
        out_dir.mkdir(parents=True, exist_ok=True)
        row = {"id": sid}
        for arm in ("A", "B"):
            try:
                row[arm] = await _run_arm(sid, arm, out_dir, llm)
            except Exception as err:  # noqa: BLE001
                row[arm] = {"arm": arm, "status": "crash",
                            "error": f"{type(err).__name__}: {err}"}
            verdict = row[arm].get("verdict")
            overall = verdict.get("overall_winner") if isinstance(verdict, dict) else None
            print(f"  arm {arm}: {row[arm].get('status')}  overall={overall}", flush=True)
        (out_dir / "row.json").write_text(json.dumps(row, indent=2, ensure_ascii=False))
        rows.append(row)
        _write_summary(rows)  # incremental: crash-safe progress
    _write_summary(rows)
    print("\nDONE. Summary at", AB_ROOT / "_summary.json", flush=True)
    return 0


def _write_summary(rows: List[dict]) -> None:
    def _tally(arm: str) -> dict:
        t = {"accepted": 0, "exhausted_judged": 0, "pipeline_error": 0, "crash": 0,
             # Step 76b: `overall`/`design_layout` cover ALL judged rows
             # (selection-free); the *_accepted_only pair reproduces the old
             # conditioned statistic for comparison against the first run.
             "overall": {"cand": 0, "gt": 0, "tie": 0},
             "design_layout": {"cand": 0, "gt": 0, "tie": 0},
             "overall_accepted_only": {"cand": 0, "gt": 0, "tie": 0},
             "design_layout_accepted_only": {"cand": 0, "gt": 0, "tie": 0}}
        for r in rows:
            a = r.get(arm, {})
            status = a.get("status")
            if status in t:
                t[status] += 1
            v = a.get("verdict")
            if isinstance(v, dict):
                for axis, key in (("overall_winner", "overall"),
                                  ("design_layout", "design_layout")):
                    t[key][v[axis]] += 1
                    if a.get("internal_accepted"):
                        t[f"{key}_accepted_only"][v[axis]] += 1
        return t

    summary = {"n": len(rows), "arm_A": _tally("A"), "arm_B": _tally("B"), "rows": rows}
    (AB_ROOT / "_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
