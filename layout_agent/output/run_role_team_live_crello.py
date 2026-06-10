"""Live LLM run of the Role-driven AgentLayout team on a real Crello sample.

Goal: directly test the **spec sparsity hypothesis** left open by steps 7 & 8 --
the 3-element synthetic poster used by ``run_role_team_live.py`` has plateaued
at score 68 (== Crello GT baseline) regardless of background color (step 7) or
text contrast (step 8 reverted). If sparsity is the true bottleneck, a real
5-element Crello brief should score notably above 68 in the same pipeline.

Step 9b (2026-05-14) extends Live #7's N=1 result to N=3+ by accepting any
cached Crello sample id via ``--sample-id``. Output paths suffix with the
sample id to avoid clobbering prior runs.

Cost ceiling: ``max_total_rounds=3`` caps reject cycles. Worst-case spend
per run: ~$0.30-0.50.

Output: layout_agent/output/role_live_crello_<sample_id>_*.{png,json}
Run:    conda activate meta && \\
        python layout_agent/output/run_role_team_live_crello.py [--sample-id ID]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import traceback
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image

from metagpt.utils.common import any_to_str

from metagpt.ext.agentlayout.actions.analyze_brief import AssetInput
from metagpt.ext.agentlayout.actions.generate_layout import GenerateLayout
from metagpt.ext.agentlayout.actions.judge_aesthetic import JudgeAesthetic
from metagpt.ext.agentlayout.roles.iteration_state import (
    RetryAnalyst,
    RetryGeneration,
)
from metagpt.ext.agentlayout.schema import (
    AestheticJudgement,
    Candidate,
    CandidatesBatch,
    DesignSpec,
    JudgeDecision,
)
from metagpt.ext.agentlayout.team import LayoutBrief, build_team
from metagpt.ext.agentlayout.tools.renderer import render_to_file


OUTPUT_DIR = Path("/home/hui0705/MetaGPT/layout_agent/output")
DEFAULT_SAMPLE_ID = "5c6c0cba85ea3c16f964a15d"
BASELINE_3_ELEMENT_SCORE = 68  # Live #5 best (3-element shoe poster)


def load_crello_sample(sample_dir: Path) -> Tuple[dict, List[dict]]:
    """Load meta.json from a cached Crello sample dir."""
    meta = json.loads((sample_dir / "meta.json").read_text())
    return meta, meta["elements"]


def _composite_background_plates(meta: dict, descriptors: List[dict]) -> Optional[str]:
    """Flatten ALL kind=background_candidate plates into one canvas-size PNG.

    Step 47 (2026-06-10) bug fix: 17.3% of samples (329/1902) carry >=2
    background_candidate plates -- typically a solid base colour plus a
    full-canvas decorative frame with alpha (e.g. the Mother's Day floral
    border). The old code kept only the FIRST plate and silently dropped the
    rest, so the layer holding most of the design's visual richness never
    reached the Analyst / Generator / renderer. Compositing here (z-order =
    element list order, same as the designer's stacking) keeps every
    downstream component unchanged: BackgroundAnalyzer derives safe_zones
    from the real composite, and the Step 46 vision channel shows the LLM
    the background it will actually be rendered on.

    Returns the bg_ref to use, or None when no background_candidate exists.
    """
    cands = [
        d for d in descriptors
        if d.get("kind") == "background_candidate" and d.get("asset_ref")
    ]
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]["asset_ref"]

    cw = int(meta["canvas_width"])
    ch = int(meta["canvas_height"])
    canvas = Image.new("RGBA", (cw, ch), (255, 255, 255, 255))
    for d in cands:
        try:
            plate = Image.open(d["asset_ref"]).convert("RGBA")
        except (OSError, IOError):
            continue
        w = max(1, int(round(float(d.get("width", cw)))))
        h = max(1, int(round(float(d.get("height", ch)))))
        left = int(round(float(d.get("left", 0.0))))
        top = int(round(float(d.get("top", 0.0))))
        plate = plate.resize((w, h), Image.LANCZOS)
        # paste via a transparent full-canvas layer so negative offsets
        # (plates slightly larger than the canvas) crop correctly.
        layer = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        layer.paste(plate, (left, top), plate)
        canvas = Image.alpha_composite(canvas, layer)

    out_path = Path(cands[0]["asset_ref"]).parent / "asset_bg_composite.png"
    canvas.convert("RGB").save(out_path, format="PNG")
    return str(out_path)


def build_pipeline_inputs(meta: dict, descriptors: List[dict]) -> Tuple[str, List[AssetInput]]:
    """Mirror of ``run_iou_eval.build_pipeline_inputs`` (kept inline so the two
    drivers can drift independently if the Crello schema evolves).

    2026-05-27 redesign (Step 28): kind values produced by save_sample now
    include ``image`` (photo), ``underlay`` (pre-classified shape plate),
    ``background_candidate`` (full-canvas plate), and ``text``. Background
    plate detection prefers explicit kind=background_candidate; falls back
    to first kind=image for backward compatibility with old meta.json.
    Underlay assets are emitted into asset_list so the Analyst recognises
    them via filename suffix and assigns semantic_type=decorative_image.

    Step 47 (2026-06-10): multiple background_candidate plates are now
    flattened into asset_bg_composite.png instead of dropping all but the
    first (see _composite_background_plates).
    """
    cw = int(meta["canvas_width"])
    ch = int(meta["canvas_height"])
    title = meta["title"]
    # Background plate detection: composite of every kind=background_candidate
    # plate (classifier said "full-canvas"); falls back to first kind=image
    # for backward compatibility with old meta.json.
    bg_ref = _composite_background_plates(meta, descriptors)
    if bg_ref is None:
        bg_ref = next(
            (d["asset_ref"] for d in descriptors
             if d.get("kind") == "image" and d.get("asset_ref")),
            None,
        )
    # Step 36b (2026-06-09): the Crello dataset's `title` field is the
    # catalog/SEO description (e.g. "Quarantine concept with Man by open
    # Window"), NOT the visible title text the designer placed on the canvas.
    # Earlier wording "marketing graphic titled '{title}'" caused the Analyst
    # to surface the catalog description as the spec's TITLE element (Step 34
    # N=20 audit: 3/17 failures, samples 5e72, 5f98, 5bbc partial). Rephrase
    # to use it only as theme context; the visible title comes from the text
    # snippets in asset_list.
    user_brief = (
        f"Create a {cw}x{ch} marketing graphic for the theme '{title}'. "
        f"Canvas size is exactly {cw} pixels wide by {ch} pixels tall. "
        "Use the provided images and text snippets. The visible heading text "
        "MUST come from the text snippets in the asset list, NOT from this "
        "theme description. Aim for a clean, balanced composition appropriate "
        "for the theme."
    )
    if bg_ref is not None:
        user_brief += (
            f" The image asset '{bg_ref}' is the full-canvas BACKGROUND plate: "
            "set canvas.background_asset_ref to exactly that path and place all "
            "other elements on top of it."
        )
    asset_list: List[AssetInput] = []
    for d in descriptors:
        kind = d.get("kind")
        if kind == "image" and d.get("asset_ref"):
            # Skip the descriptor we already promoted to background plate
            # (avoid double-counting the bg as both bg and an asset_list item).
            if d["asset_ref"] == bg_ref:
                continue
            asset_list.append(AssetInput(asset_ref=d["asset_ref"]))
        elif kind == "underlay" and d.get("asset_ref"):
            # Step 28: Analyst PROMPT_TEMPLATE recognises the _underlay.png
            # filename suffix and assigns semantic_type=decorative_image.
            asset_list.append(AssetInput(asset_ref=d["asset_ref"]))
        elif kind == "text" and d.get("content"):
            asset_list.append(AssetInput(content=d["content"]))
    return user_brief, asset_list


def find_final_judgement(history) -> Optional[AestheticJudgement]:
    cause = any_to_str(JudgeAesthetic)
    for m in reversed(history):
        if m.cause_by == cause and isinstance(m.instruct_content, AestheticJudgement):
            return m.instruct_content
    return None


def find_candidates_for_id(history, target_id: str) -> Optional[Candidate]:
    cause = any_to_str(GenerateLayout)
    for m in reversed(history):
        if m.cause_by == cause and isinstance(m.instruct_content, CandidatesBatch):
            for c in m.instruct_content.candidates:
                if c.candidate_id == target_id:
                    return c
    return None


def find_latest_spec(history) -> Optional[DesignSpec]:
    from metagpt.ext.agentlayout.actions.analyze_brief import AnalyzeBrief

    cause = any_to_str(AnalyzeBrief)
    for m in reversed(history):
        if m.cause_by == cause and isinstance(m.instruct_content, DesignSpec):
            return m.instruct_content
    return None


def summarize_history(history) -> List[dict]:
    trace = []
    for i, m in enumerate(history):
        cb_short = m.cause_by.split(".")[-1] if m.cause_by else "<none>"
        ic_type = type(m.instruct_content).__name__ if m.instruct_content else "None"
        trace.append({"idx": i, "role": m.role, "cause_by": cb_short, "instruct_content_type": ic_type})
    return trace


async def main(sample_id: str) -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    sample_dir = OUTPUT_DIR / f"crello_{sample_id}"
    if not sample_dir.is_dir():
        print(f"[ERROR] Crello sample dir missing: {sample_dir}")
        return 4

    meta, descriptors = load_crello_sample(sample_dir)
    user_brief, asset_list = build_pipeline_inputs(meta, descriptors)

    print(f"[setup] Crello sample: {meta['id']}")
    print(f"        title         : {meta['title']!r}")
    print(f"        canvas        : {meta['canvas_width']}x{meta['canvas_height']}")
    print(f"        n_elements    : {meta['n_elements']}")
    print(f"        asset_list    : {len(asset_list)} (image + text mix)")
    print(f"        baseline score: {BASELINE_3_ELEMENT_SCORE} (3-element shoe poster, Live #5)")
    print()

    print("[run] dispatching to run_team() with REAL Aesthetic Judge...")
    print("      Roles: Analyst, AssetPlanner, LayoutGenerator,")
    print("             AestheticJudge, IterationState (5 hired)")
    print("      n_round=14, max_total_rounds=3 (cost cap ~$0.30)")
    print()

    try:
        team = build_team(n_round=14, max_total_rounds=3)
        from metagpt.actions import UserRequirement
        from metagpt.schema import Message

        brief = LayoutBrief(user_brief=user_brief, asset_list=asset_list)
        kickoff = Message(
            content=user_brief,
            instruct_content=brief,
            role="User",
            cause_by=UserRequirement,
        )
        team.env.publish_message(kickoff)
        await team.run(n_round=14)
    except Exception as err:
        print(f"[ERROR] run_team failed: {type(err).__name__}: {err}")
        traceback.print_exc()
        return 1

    history = team.env.history.get()
    iter_role = team.env.roles.get("IterationState")
    state = iter_role.state if iter_role else None

    print()
    print("=" * 60)
    print("RUN COMPLETE -- ENV HISTORY TRACE")
    print("=" * 60)
    for entry in summarize_history(history):
        print(
            f"  [{entry['idx']:2d}] role={entry['role']!r:24s}  "
            f"cause_by={entry['cause_by']:18s}  ic={entry['instruct_content_type']}"
        )

    rg_count = sum(1 for m in history if m.cause_by == any_to_str(RetryGeneration))
    ra_count = sum(1 for m in history if m.cause_by == any_to_str(RetryAnalyst))
    judge_count = sum(1 for m in history if m.cause_by == any_to_str(JudgeAesthetic))

    print()
    print("=" * 60)
    print("ITERATION STATE")
    print("=" * 60)
    if state is not None:
        print(f"  iteration count : {state.iteration}")
        print(
            f"  last target     : "
            f"{state.feedback_target.value if state.feedback_target else None}"
        )
        print(f"  last_feedback?  : {'yes' if state.last_feedback else 'no'}")
        if state.last_feedback:
            issues = state.last_feedback.common_issues
            print(f"    issues sample : {issues[:200]}")
    print()
    print(f"  RetryGeneration messages : {rg_count}")
    print(f"  RetryAnalyst messages    : {ra_count}")
    print(f"  JudgeAesthetic messages  : {judge_count}")

    print()
    print("=" * 60)
    print("FINAL JUDGEMENT")
    print("=" * 60)
    final = find_final_judgement(history)
    if final is None:
        print("  [WARN] no AestheticJudgement found in history -- aborting render.")
        return 2

    print(f"  decision           : {final.decision.value}")
    print(f"  best_candidate_id  : {final.best_candidate_id}")
    print(f"  evaluations:")
    best_total: Optional[int] = None
    for ev in final.evaluations:
        marker = "  *" if ev.candidate_id == final.best_candidate_id else "   "
        s = ev.scores
        print(
            f"  {marker} {ev.candidate_id}: total={ev.total} "
            f"(DL={s.design_layout} "
            f"CR={s.content_relevance} "
            f"TV={s.typography_color} "
            f"GI={s.graphics_images} "
            f"IO={s.innovation_originality})"
        )
        if ev.candidate_id == final.best_candidate_id:
            best_total = ev.total

    print()
    print("=" * 60)
    print("SPARSITY HYPOTHESIS RESULT")
    print("=" * 60)
    if best_total is not None:
        delta = best_total - BASELINE_3_ELEMENT_SCORE
        if delta >= 4:
            verdict = "CONFIRMED -- 5-element notably above 3-element; sparsity drives the plateau"
        elif -3 <= delta < 4:
            verdict = "INCONCLUSIVE -- 5-element within +/-3 of baseline; sparsity not the driver"
        else:
            verdict = "REFUTED -- 5-element below baseline; sparsity is not the cause"
        print(f"  best 5-element score : {best_total}")
        print(f"  baseline (3-element) : {BASELINE_3_ELEMENT_SCORE}")
        print(f"  delta                : {delta:+d}")
        print(f"  hypothesis           : {verdict}")

    spec = find_latest_spec(history)
    candidate = find_candidates_for_id(history, final.best_candidate_id)
    if spec is None or candidate is None:
        print("  [WARN] could not locate spec or candidate for rendering.")
        return 3

    out_png = OUTPUT_DIR / (
        f"role_live_crello_{sample_id}_accepted.png"
        if final.decision == JudgeDecision.ACCEPT
        else f"role_live_crello_{sample_id}_last_reject.png"
    )
    render_to_file(candidate, spec, str(out_png))
    img = Image.open(out_png)
    print()
    print(f"[render] wrote {out_png}  ({img.size[0]}x{img.size[1]} PNG)")

    (OUTPUT_DIR / f"role_live_crello_{sample_id}_trace.json").write_text(
        json.dumps(
            {
                "sample_id": meta["id"],
                "sample_n_elements": meta["n_elements"],
                "history_summary": summarize_history(history),
                "iteration_state": (
                    {
                        "iteration": state.iteration,
                        "feedback_target": (
                            state.feedback_target.value if state.feedback_target else None
                        ),
                        # Step 31 (2026-06-09): best-so-far guard exposure.
                        # `best_total_score` further down is the LAST round's
                        # best (Judge's final verdict). `best_so_far_total` is
                        # the MAX across all rounds — these differ whenever the
                        # loop regressed after an early high-water mark.
                        "best_so_far_total": state.best_so_far_total,
                        "best_so_far_subscores": state.best_so_far_subscores,
                    }
                    if state
                    else None
                ),
                "routing_counts": {
                    "RetryGeneration": rg_count,
                    "RetryAnalyst": ra_count,
                    "JudgeAesthetic": judge_count,
                },
                "final_decision": final.decision.value,
                "best_candidate_id": final.best_candidate_id,
                "best_total_score": best_total,
                "baseline_3_element_score": BASELINE_3_ELEMENT_SCORE,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    (OUTPUT_DIR / f"role_live_crello_{sample_id}_spec.json").write_text(
        json.dumps(spec.model_dump(), ensure_ascii=False, indent=2)
    )
    print(
        f"[debug] role_live_crello_{sample_id}_trace.json, "
        f"role_live_crello_{sample_id}_spec.json"
    )
    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--sample-id",
        default=DEFAULT_SAMPLE_ID,
        help=f"Crello sample directory id (default: {DEFAULT_SAMPLE_ID})",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    sys.exit(asyncio.run(main(args.sample_id)))
