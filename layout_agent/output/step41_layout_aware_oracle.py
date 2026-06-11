"""Step 41 -- Layout-aware structured pairwise GT oracle.

Building on Step 40 (flag-aware feedback): the pairwise judge now ALSO
receives the candidate's layout JSON (bbox + style per element). It can
therefore emit machine-actionable structured_suggestions ({target_id,
metric, op, value, rationale}) instead of relying on the Generator to
translate semantic flags into geometric edits.

Motivation (2026-06-09 conversation): the user observed that the
existing pairwise loop fails because Generator interprets feedback at a
surface level ("composition_unbalanced -> I'll move things a bit") with
no ability to evaluate the rendered result. If Judge can see both the
render AND the underlying bbox JSON, it can compute concrete target
values (e.g. "title_1.left set_to 260") that the Generator applies
deterministically -- closing the visual-feedback loop.

The Suggestion schema (metagpt.ext.agentlayout.schema.Suggestion) was
already wired through GenerateLayout's PROMPT_TEMPLATE (Step 14
structured_suggestions block, lines ~141-159), so the Generator side
needs no changes.

WARNING: This is a MAXIMALLY informed oracle ablation. The judge sees
both the GT image AND the candidate's coordinates. Frame in paper as
upper-bound experiment, not a deployable system.

Loop is the same as Step 34/37/40:
  Round 1: generate -> render -> judge (A vs GT, A wins/tie -> commit)
  Round 2+: same but reference = previously committed render
  Up to MAX_RETRY_PER_ROUND=3 retries per round on loss.

Cost: ~$0.04 per judge call (longer prompt + JSON output), ~$0.45 per
sample worst case (~$9 for N=20).

Run:  conda activate meta && python layout_agent/output/step41_layout_aware_oracle.py
      python layout_agent/output/step41_layout_aware_oracle.py --ids-file layout_agent/output/step13_drawn_ids.json
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import re
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from openai import AsyncOpenAI

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = str(_HERE.parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from run_role_team_live_crello import (  # noqa: E402
    build_pipeline_inputs,
    load_crello_sample,
)

from metagpt.ext.agentlayout.actions.analyze_brief import AnalyzeBrief  # noqa: E402
from metagpt.ext.agentlayout.actions.compose_sketch import ComposeSketch  # noqa: E402
from metagpt.ext.agentlayout.actions.generate_layout import GenerateLayout  # noqa: E402
from metagpt.ext.agentlayout.actions.plan_assets import PlanAssets  # noqa: E402
from metagpt.ext.agentlayout.schema import (  # noqa: E402
    AestheticFeedback,
    Candidate,
    DesignSpec,
    Suggestion,
    SuggestionKind,
)
from metagpt.ext.agentlayout.tools.asset_analyzer import AssetAnalyzer  # noqa: E402
from metagpt.ext.agentlayout.tools.background_analyzer import resolve_background  # noqa: E402
from metagpt.ext.agentlayout.tools.quality_checker import (  # noqa: E402
    ViolationType,
    check_candidate,
)
from metagpt.ext.agentlayout.tools.renderer import render_to_file  # noqa: E402

OUT = _HERE
CONFIG2 = Path.home() / ".metagpt" / "config2.yaml"
MODEL = "gpt-4o"

# Step 53 (2026-06-11) gate-off ablation knobs, set from argparse in main().
# Defaults reproduce the historical step41/48/49 behaviour exactly.
SAFE_ZONE_GATE = True
RENDER_PREFIX = "step41_layout_aware_oracle"
RESULTS_JSON = "step41_layout_aware_results.json"

DEFAULT_IDS = [
    "5928015095a7a863ddcd8e38",
    "5c94fa6085ea3c16f9ca91a2",
    "5e6a3440b04a3d1f19b1f59f",
    "5f56075fa637ee11e31044fa",
    "5e72455e4b3890eb07bcc0fa",
]

MAX_RETRY_PER_ROUND = 3
MAX_ROUNDS_AFTER_GT = 2

FLAG_CATALOGUE: Dict[str, List[str]] = {
    "design_layout": [
        "composition_unbalanced",
        "excessive_dead_space",
        "crowded_no_whitespace",
        "broken_visual_hierarchy",
        "misaligned_elements",
    ],
    "content_relevance": [
        "title_missing_or_swapped",
        "decoration_irrelevant_to_theme",
        "key_message_hidden_or_truncated",
        "audience_style_mismatch",
    ],
    "typography_color": [
        "low_contrast_text",
        "size_hierarchy_broken",
        "color_clash_or_disharmony",
        "font_inconsistency",
        "text_on_non_text_collision",
    ],
    "graphics_images": [
        "image_dominates_inappropriately",
        "image_too_small_to_serve_as_hero",
        "decoration_overused",
        "image_placement_awkward",
    ],
    "innovation_originality": [
        "generic_centered_only_composition",
        "trend_following_template_feel",
        "no_distinctive_compositional_choice",
    ],
}

_REQUIRED_AXES = (
    "design_layout",
    "content_relevance",
    "typography_color",
    "graphics_images",
    "innovation_originality",
)

ALLOWED_KINDS = {sk.value for sk in SuggestionKind}
KIND_METRIC_WHITELIST = {
    "place_in_bbox": {"bbox"},
    "resize": {"width", "height"},
    "move": {"left", "top"},
    "typography": {"font_size", "font_weight", "font_family", "text_align"},
    "color": {"color"},
    "zorder": {"z_index"},
}


PAIRWISE_PROMPT_TEMPLATE = """You are an expert graphic design critic comparing TWO designs.

Image A (FIRST attached) is the CANDIDATE. Image B (SECOND attached) is
the designer GROUND-TRUTH reference. You ALSO receive Image A's layout
JSON below, so your suggestions can reference exact element ids and
coordinates -- not vague semantic descriptions.

CANDIDATE LAYOUT JSON (Image A):
{candidate_layout_json}

SAFE ZONES for this canvas (CV-derived background-saliency-LOW regions;
the background subject sits OUTSIDE these zones). Primary content
(title, subtitle, body_text, product_image, logo) MUST live inside one
of these zones. Suggestions that move a primary element to coordinates
OUTSIDE every safe_zone are FORBIDDEN -- the QC pipeline rejects them.

bbox format is [left, top, RIGHT, BOTTOM] (absolute pixel coords). To
check whether an element with (left, top, width, height) lies inside a
safe zone, test:
    left   >= safe_left   AND right  = left + width  <= safe_right
    top    >= safe_top    AND bottom = top  + height <= safe_bottom

SAFE ZONES JSON:
{safe_zones_json}

For EACH of the 5 axes you must:
  1. pick a winner: "A", "B", or "tie"
  2. list every failure flag (from the closed catalogue) on Image A   (a_flags)
  3. list every flag on Image B   (b_flags)
  4. write a ONE-LINE reason

Then output:
  - overall_winner ("A", "B", or "tie")
  - summary (one line)
  - structured_suggestions: a flat list. Each suggestion has the shape
      {{"kind": ..., "target_id": ..., "metric": ..., "op": ..., "value": ..., "rationale": ...}}
    target_id MUST be an actual element id from the CANDIDATE LAYOUT JSON
    above. The Generator will apply each suggestion deterministically, so
    `value` must be a concrete number (or hex string for colour), not a
    range. Use this concrete-value rule even on accept-side polishes.

  Allowed kind / metric pairs:
    kind=place_in_bbox  metric=bbox  value="[L,T,R,B]"  target_bbox=[L,T,R,B]
        ^^^ STRONGLY PREFERRED for ANY position/size fix. You are looking at
        Image A, and Image B (the GT) shows where elements SHOULD go. When
        you can identify the empty visual region in Image A that Image B
        uses, emit a place_in_bbox suggestion with absolute pixel
        target_bbox=[L,T,R,B] (L>=0, T>=0, R<=canvas_width, B<=canvas_height,
        R>L, B>T). The Generator obeys target_bbox verbatim and lifts its
        +/-10% drift cap for that element. ONLY emit place_in_bbox for
        elements whose semantic_type is NOT background_image (the canvas
        background must stay full-canvas).
        op MUST be "set_to" for this kind. ALL primary-element target_bbox
        values MUST overlap one of the safe_zones listed above (>=50% of
        target_bbox area inside a safe_zone) -- otherwise QC rejects the
        candidate before the next round.
    kind=resize     metric=width | height        value=int  (pixels)
    kind=move       metric=left  | top           value=int  (pixels)
    kind=typography metric=font_size                value=int  (pixels)
    kind=typography metric=font_weight              value=int (100-900) | str ("bold"|"regular"|...)
    kind=typography metric=font_family              value=str  (e.g. "serif", "Inter", "Playfair Display")
    kind=typography metric=text_align               value=str  in {{"left","center","right","justify"}}
        ^^^ Step 45: when Image B (GT) has clearly better typography than
        Image A (heavier weight, different family, different alignment, or
        meaningfully different size), emit ONE typography suggestion PER
        failing metric on the affected text element. Do not bundle multiple
        metrics into one suggestion. Typography fixes are how AL closes the
        visual gap to the designer GT once layout positions are right.
    kind=color      metric=color                 value="#RRGGBB"
    kind=zorder     metric=z_index               value=int
  op in {{">=", "<=", "==", "set_to", "increase_by", "decrease_by"}}

MEDIUM-STRICT tie-breaking rule (Step 43-R1, 2026-06-10): the prior
"default to Image B" rule was over-strict -- visual audit showed that
samples 5e8d (Nurse) and 5888 (Volunteering) produced AL renders that
human inspection rates as visually equivalent to GT, yet "B by default"
forced 100% B verdicts in pairwise. The new rule defaults to `tie` on
ambiguity, NOT to B:
  - Pick "A" only when you can name a SPECIFIC, OBJECTIVE way Image A
    improves on Image B (cleaner hierarchy, more readable typography,
    more distinctive composition).
  - Pick "B" only when you can name a SPECIFIC, OBJECTIVE way Image B
    improves on Image A (mirror of the above).
  - When both look comparable / either-or / "B has subtle edge" / "A
    has subtle edge" -> use `tie`.
This gives the loop a fair chance to commit on ambiguous near-equal
renders while still rejecting clearly worse Generator output.

CLOSED flag catalogue (use these EXACT strings; do not invent flags):

A. design_layout:        composition_unbalanced | excessive_dead_space | crowded_no_whitespace | broken_visual_hierarchy | misaligned_elements
B. content_relevance:    title_missing_or_swapped | decoration_irrelevant_to_theme | key_message_hidden_or_truncated | audience_style_mismatch
C. typography_color:     low_contrast_text | size_hierarchy_broken | color_clash_or_disharmony | font_inconsistency | text_on_non_text_collision
D. graphics_images:      image_dominates_inappropriately | image_too_small_to_serve_as_hero | decoration_overused | image_placement_awkward
E. innovation_originality: generic_centered_only_composition | trend_following_template_feel | no_distinctive_compositional_choice

Respond ONLY in JSON, no markdown fence:
{{
  "design_layout":         {{"winner":"A|B|tie","a_flags":[...],"b_flags":[...],"reason":"<one line>"}},
  "content_relevance":     {{"winner":"A|B|tie","a_flags":[...],"b_flags":[...],"reason":"<one line>"}},
  "typography_color":      {{"winner":"A|B|tie","a_flags":[...],"b_flags":[...],"reason":"<one line>"}},
  "graphics_images":       {{"winner":"A|B|tie","a_flags":[...],"b_flags":[...],"reason":"<one line>"}},
  "innovation_originality":{{"winner":"A|B|tie","a_flags":[...],"b_flags":[...],"reason":"<one line>"}},
  "overall_winner":        "A|B|tie",
  "summary":               "<one-line whole-image verdict>",
  "structured_suggestions":[
    {{"kind":"...","target_id":"...","metric":"...","op":"...","value":...,"target_bbox":[L,T,R,B],"rationale":"<one line>"}}
  ]
}}

NOTE: `target_bbox` is REQUIRED when kind="place_in_bbox" and FORBIDDEN
(omit the key entirely) for every other kind."""


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _load_client() -> AsyncOpenAI:
    cfg = yaml.safe_load(CONFIG2.read_text())
    llm = cfg.get("llm", {})
    key = llm.get("api_key")
    base = llm.get("base_url") or "https://api.openai.com/v1"
    if not key:
        sys.exit(f"no llm.api_key in {CONFIG2}")
    return AsyncOpenAI(api_key=key, base_url=base)


def _b64(p: Path) -> str:
    return base64.b64encode(p.read_bytes()).decode()


def _candidate_layout_json(cand: Candidate, spec: DesignSpec) -> str:
    elements = []
    spec_types = {e.id: e.semantic_type.value for e in spec.elements}
    for el in cand.elements:
        item: Dict[str, Any] = {
            "id": el.id,
            "semantic_type": spec_types.get(el.id, "unknown"),
            "left": int(el.left),
            "top": int(el.top),
            "width": int(el.width),
            "height": int(el.height),
            "z_index": el.z_index,
        }
        if el.font_size is not None:
            item["font_size"] = el.font_size
        if el.font_weight is not None:
            item["font_weight"] = el.font_weight
        if el.color is not None:
            item["color"] = el.color
        elements.append(item)
    payload = {
        "canvas": {"width": spec.canvas.width, "height": spec.canvas.height},
        "elements": elements,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _parse_pairwise(text: str, candidate_ids: set) -> Optional[Dict]:
    if not text:
        return None
    m = _JSON_FENCE.search(text)
    if m:
        text = m.group(1)
    try:
        obj = json.loads(text.strip())
    except json.JSONDecodeError:
        m2 = re.search(r"\{.*\}", text, re.DOTALL)
        if not m2:
            return None
        try:
            obj = json.loads(m2.group(0))
        except json.JSONDecodeError:
            return None
    for ax in _REQUIRED_AXES:
        v = obj.get(ax)
        if not isinstance(v, dict):
            return None
        if v.get("winner") not in ("A", "B", "tie"):
            return None
        if not isinstance(v.get("a_flags"), list):
            return None
        if not isinstance(v.get("b_flags"), list):
            return None
    if obj.get("overall_winner") not in ("A", "B", "tie"):
        return None
    ss = obj.get("structured_suggestions")
    if ss is None:
        obj["structured_suggestions"] = []
        ss = obj["structured_suggestions"]
    if not isinstance(ss, list):
        return None
    cleaned = []
    for s in ss:
        if not isinstance(s, dict):
            continue
        kind = s.get("kind")
        target = s.get("target_id")
        metric = s.get("metric")
        op = s.get("op")
        value = s.get("value")
        target_bbox = s.get("target_bbox")
        if kind not in ALLOWED_KINDS or not target or not metric or op is None:
            continue
        if target not in candidate_ids:
            continue
        whitelist = KIND_METRIC_WHITELIST.get(kind)
        if whitelist is not None and metric not in whitelist:
            continue
        if kind == "place_in_bbox":
            if (
                not isinstance(target_bbox, list)
                or len(target_bbox) != 4
                or not all(isinstance(v, int) for v in target_bbox)
            ):
                continue
            l, t, r, b = target_bbox
            if r <= l or b <= t or l < 0 or t < 0:
                continue
            if value is None:
                value = f"[{l}, {t}, {r}, {b}]"
        else:
            target_bbox = None
        cleaned.append(
            {
                "kind": kind,
                "target_id": target,
                "metric": metric,
                "op": op,
                "value": value,
                "target_bbox": target_bbox,
                "rationale": s.get("rationale"),
            }
        )
    obj["structured_suggestions"] = cleaned
    return obj


async def _pairwise_judge(
    client: AsyncOpenAI,
    cand_png: Path,
    ref_png: Path,
    candidate_layout: str,
    candidate_ids: set,
    safe_zones_json: str,
    max_retries: int = 2,
) -> Optional[Dict]:
    cand_b64 = _b64(cand_png)
    ref_b64 = _b64(ref_png)
    prompt = PAIRWISE_PROMPT_TEMPLATE.format(
        candidate_layout_json=candidate_layout,
        safe_zones_json=safe_zones_json,
    )
    last_err: Optional[str] = None
    for attempt in range(max_retries):
        try:
            resp = await client.chat.completions.create(
                model=MODEL,
                temperature=0.0,
                max_tokens=2200,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{cand_b64}"},
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{ref_b64}"},
                            },
                        ],
                    }
                ],
            )
            text = (resp.choices[0].message.content or "").strip()
            parsed = _parse_pairwise(text, candidate_ids)
            if parsed is not None:
                return parsed
            last_err = f"unparseable: {text[:160]!r}"
        except Exception as err:
            last_err = f"{type(err).__name__}: {err}"
        if attempt < max_retries - 1:
            await asyncio.sleep(1.0)
    print(f"    [warn] pairwise judge failed: {last_err}")
    return None


def _flags_to_fix(verdict: Dict) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for ax in _REQUIRED_AXES:
        v = verdict.get(ax, {})
        a_flags = list(v.get("a_flags", []))
        b_flags = set(v.get("b_flags", []))
        unique = [f for f in a_flags if f not in b_flags]
        if unique:
            out[ax] = unique
    return out


def _build_feedback(verdict: Dict, reference_label: str) -> AestheticFeedback:
    to_fix = _flags_to_fix(verdict)
    flag_lines: List[str] = []
    for ax, flags in to_fix.items():
        flag_lines.append(f"  [{ax}] flags-to-fix: {', '.join(flags)}")
    common_issues = (
        f"Pairwise judge: candidate LOST to the {reference_label}. "
        "The structured_suggestions list contains concrete element-level "
        "instructions you MUST apply on the next attempt.\n"
        + "\n".join(flag_lines)
    )
    free_text = []
    structured: List[Suggestion] = []
    for s in verdict.get("structured_suggestions", []):
        try:
            structured.append(
                Suggestion(
                    kind=SuggestionKind(s["kind"]),
                    target_id=s["target_id"],
                    metric=s["metric"],
                    op=s["op"],
                    value=s["value"],
                    target_bbox=s.get("target_bbox"),
                    rationale=s.get("rationale"),
                )
            )
            free_text.append(
                f"{s['target_id']}.{s['metric']} {s['op']} {s['value']} -- "
                f"{s.get('rationale') or ''}"
            )
        except Exception:
            continue
    return AestheticFeedback(
        common_issues=common_issues,
        suggestions=free_text,
        structured_suggestions=structured,
    )


async def _generate_render(
    sample_id: str,
    spec: DesignSpec,
    tree,
    bg,
    feedback: Optional[AestheticFeedback],
    out_png: Path,
) -> Optional[Candidate]:
    gen = GenerateLayout()
    try:
        batch = await gen.run(spec=spec, tree=tree, bg=bg, feedback=feedback)
    except Exception as err:
        print(f"    [warn] GenerateLayout crashed: {type(err).__name__}: {err}")
        return None
    if not batch.candidates:
        return None
    cand = batch.candidates[0]
    render_to_file(cand, spec, out_png)
    return cand


async def _process_sample(client: AsyncOpenAI, sample_id: str) -> Dict:
    crello_dir = OUT / f"crello_{sample_id}"
    gt_png = crello_dir / "ground_truth_preview.jpg"
    if not (crello_dir / "meta.json").exists() or not gt_png.exists():
        return {"id": sample_id, "status": "no_cache"}

    meta, descriptors = load_crello_sample(crello_dir)
    try:
        user_brief, asset_list = build_pipeline_inputs(meta, descriptors)
    except Exception as err:
        traceback.print_exc()
        return {"id": sample_id, "status": f"build_inputs_crash: {err}"}

    print("  Stage 1: Analyst")
    try:
        spec: DesignSpec = await AnalyzeBrief().run(user_brief=user_brief, asset_list=asset_list)
        AssetAnalyzer().run(spec)
        print("  Stage 2: PlanAssets")
        tree = await PlanAssets().run(spec=spec)
        bg = resolve_background(spec.canvas)
        # Step 62 (2026-06-12): Composition Director picks the GT-calibrated
        # sketch template BEFORE pixel layout; the directive rides
        # spec.composition into the Generator prompt and the QC contract.
        print("  Stage 2.5: ComposeSketch (Composition Director)")
        directive = await ComposeSketch().run(spec=spec, bg=bg)
        print(
            f"    directive: {directive.template_id} "
            f"(relation={directive.relation}, photo_size={directive.photo_size})"
        )
    except Exception as err:
        traceback.print_exc()
        return {"id": sample_id, "status": f"setup_crash: {err}"}

    # Step 43: serialize safe_zones once per sample so the judge sees the
    # same saliency information the Generator now consumes (Step 42 rule 6).
    safe_zones_json = json.dumps(
        {
            "canvas": {"width": spec.canvas.width, "height": spec.canvas.height},
            "safe_zones": [
                {
                    "region": sz.region,
                    "bbox": list(sz.bbox),
                    "confidence": round(sz.confidence, 3),
                }
                for sz in bg.safe_zones
            ],
        },
        ensure_ascii=False,
        indent=2,
    )

    rounds_log: List[Dict] = []
    committed_render: Optional[Path] = None
    committed_round: int = 0
    feedback: Optional[AestheticFeedback] = None
    round_idx = 1
    reference_png = gt_png
    reference_label = "GT (Crello designer reference)"
    won_this_round = False

    for attempt in range(1, MAX_RETRY_PER_ROUND + 1):
        cand_png = OUT / f"{RENDER_PREFIX}_crello_{sample_id}_r{round_idx}a{attempt}.png"
        print(f"  Round {round_idx} attempt {attempt}: generating...")
        cand = await _generate_render(sample_id, spec, tree, bg, feedback, cand_png)
        if cand is None:
            rounds_log.append({"round": round_idx, "attempt": attempt, "status": "generate_failed"})
            continue
        # Step 43 (2026-06-10): QC gate -- if any primary element falls
        # outside every safe_zone, skip the judge and rebuild feedback so
        # the next attempt fixes the spatial placement first.
        qc = check_candidate(cand, spec, bg=bg)
        sz_viols = [v for v in qc.violations if v.type == ViolationType.PRIMARY_OUTSIDE_SAFE_ZONE]
        # Step 58b (2026-06-11): the original Step 43 filter above silently
        # dropped every other violation type, so the Step 57 coverage /
        # dead-band guardrails never gated step58. They join the gate here;
        # SAFE_ZONE_GATE (Step 53 ablation flag) keeps affecting only the
        # safe-zone violations.
        # Step 59 (2026-06-11): TEXT_ON_BUSY_TEXTURE joins the gate alongside
        # the Step 57 guardrails (GT-calibrated T=0.065, detail directs the
        # Generator toward underlay shielding -- the designer-GT solution).
        # Step 62 (2026-06-12): COMPOSITION_MISMATCH / TEXT_ON_PHOTO_NO_UNDERLAY
        # join the gate (Step 60 lesson: violation types not whitelisted here
        # are computed then silently discarded).
        cov_viols = [
            v
            for v in qc.violations
            if v.type
            in (
                ViolationType.CANVAS_COVERAGE_LOW,
                ViolationType.DEAD_BAND_EXCESSIVE,
                ViolationType.TEXT_ON_BUSY_TEXTURE,
                ViolationType.COMPOSITION_MISMATCH,
                ViolationType.TEXT_ON_PHOTO_NO_UNDERLAY,
            )
        ]
        if sz_viols and not SAFE_ZONE_GATE:
            # Step 53 ablation: log the would-be rejection, judge anyway.
            print(f"    QC (gate OFF): {len(sz_viols)} violation(s) logged, judging anyway")
            rounds_log.append(
                {
                    "round": round_idx,
                    "attempt": attempt,
                    "status": "qc_violations_logged_gate_off",
                    "qc_violations": [v.model_dump() for v in sz_viols],
                }
            )
            sz_viols = []
        gate_viols = sz_viols + cov_viols
        if gate_viols:
            kinds = sorted({v.type.value for v in gate_viols})
            print(f"    QC: {len(gate_viols)} violation(s) ({', '.join(kinds)}) -> retry without judging")
            feedback = AestheticFeedback(
                common_issues=(
                    "QC rejected this candidate BEFORE the pairwise judge. "
                    "Fix every violation listed below before anything else. "
                    "The safe_zones JSON below the layout JSON in the next "
                    "Generator prompt tells you exactly where the saliency-"
                    "low regions are.\n"
                    + "\n".join(f"  * {v.detail}" for v in gate_viols)
                ),
                suggestions=[v.detail for v in gate_viols],
            )
            rounds_log.append(
                {
                    "round": round_idx,
                    "attempt": attempt,
                    "status": "qc_rejected_" + "+".join(kinds),
                    "qc_violations": [v.model_dump() for v in gate_viols],
                }
            )
            continue
        cand_layout = _candidate_layout_json(cand, spec)
        cand_ids = {el.id for el in cand.elements}
        print(f"    judging vs {reference_label}...")
        verdict = await _pairwise_judge(
            client, cand_png, reference_png, cand_layout, cand_ids, safe_zones_json
        )
        if verdict is None:
            rounds_log.append({"round": round_idx, "attempt": attempt, "status": "judge_failed"})
            continue
        overall = verdict["overall_winner"]
        n_struct = len(verdict.get("structured_suggestions", []))
        print(
            f"    overall_winner={overall}  "
            f"#structured_suggestions={n_struct}  "
            f"summary={verdict.get('summary','')[:80]}"
        )
        rounds_log.append(
            {
                "round": round_idx,
                "attempt": attempt,
                "overall_winner": overall,
                "axes": {k: verdict[k] for k in _REQUIRED_AXES},
                "summary": verdict.get("summary"),
                "committed": overall in ("A", "tie"),
                "flags_unique_to_A": _flags_to_fix(verdict),
                "structured_suggestions": verdict.get("structured_suggestions", []),
            }
        )
        if overall in ("A", "tie"):
            committed_render = cand_png
            committed_round = round_idx
            won_this_round = True
            break
        feedback = _build_feedback(verdict, reference_label)

    if not won_this_round:
        return {
            "id": sample_id,
            "status": "round1_exhausted",
            "rounds": rounds_log,
            "final_render": None,
        }

    for _ in range(MAX_ROUNDS_AFTER_GT):
        round_idx += 1
        reference_png = committed_render
        reference_label = f"committed render from Round {committed_round}"
        cand_png = OUT / f"{RENDER_PREFIX}_crello_{sample_id}_r{round_idx}a1.png"
        print(f"  Round {round_idx}: generating against {reference_label}...")
        cand = await _generate_render(sample_id, spec, tree, bg, feedback, cand_png)
        if cand is None:
            rounds_log.append({"round": round_idx, "attempt": 1, "status": "generate_failed"})
            break
        cand_layout = _candidate_layout_json(cand, spec)
        cand_ids = {el.id for el in cand.elements}
        verdict = await _pairwise_judge(
            client, cand_png, reference_png, cand_layout, cand_ids, safe_zones_json
        )
        if verdict is None:
            rounds_log.append({"round": round_idx, "attempt": 1, "status": "judge_failed"})
            break
        overall = verdict["overall_winner"]
        n_struct = len(verdict.get("structured_suggestions", []))
        print(
            f"    overall_winner={overall}  "
            f"#structured_suggestions={n_struct}  "
            f"summary={verdict.get('summary','')[:80]}"
        )
        committed = overall == "A"
        rounds_log.append(
            {
                "round": round_idx,
                "attempt": 1,
                "overall_winner": overall,
                "axes": {k: verdict[k] for k in _REQUIRED_AXES},
                "summary": verdict.get("summary"),
                "committed": committed,
                "flags_unique_to_A": _flags_to_fix(verdict),
                "structured_suggestions": verdict.get("structured_suggestions", []),
            }
        )
        if not committed:
            break
        committed_render = cand_png
        committed_round = round_idx
        feedback = None

    final_path = OUT / f"{RENDER_PREFIX}_crello_{sample_id}_render.png"
    final_path.write_bytes(committed_render.read_bytes())
    return {
        "id": sample_id,
        "status": "ok",
        "n_rounds_total": round_idx,
        "n_rounds_committed": committed_round,
        "final_render": final_path.name,
        "rounds": rounds_log,
    }


async def main() -> int:
    global SAFE_ZONE_GATE, RENDER_PREFIX, RESULTS_JSON
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max-samples", type=int, default=None)
    parser.add_argument("--ids-file", default=None)
    parser.add_argument(
        "--no-safe-zone-gate",
        action="store_true",
        help="Step 53 ablation: log primary_outside_safe_zone violations but never reject",
    )
    parser.add_argument("--render-prefix", default=RENDER_PREFIX)
    parser.add_argument("--results-json", default=RESULTS_JSON)
    args = parser.parse_args()

    SAFE_ZONE_GATE = not args.no_safe_zone_gate
    RENDER_PREFIX = args.render_prefix
    RESULTS_JSON = args.results_json
    print(f"safe_zone_gate={'ON' if SAFE_ZONE_GATE else 'OFF'}  prefix={RENDER_PREFIX}")

    if args.ids_file:
        raw = json.loads(Path(args.ids_file).read_text())
        loaded = raw.get("ids") if isinstance(raw, dict) and "ids" in raw else raw
        ids = list(loaded.keys()) if isinstance(loaded, dict) else list(loaded)
    else:
        ids = list(DEFAULT_IDS)
    if args.max_samples is not None:
        ids = ids[: args.max_samples]

    client = _load_client()
    out_rows: List[Dict] = []
    for idx, sid in enumerate(ids, 1):
        print(f"\n[{idx}/{len(ids)}] {sid}")
        try:
            row = await _process_sample(client, sid)
        except Exception as err:
            traceback.print_exc()
            row = {"id": sid, "status": f"driver_crash: {err}"}
        print(f"  -> status={row.get('status')}  final_round={row.get('n_rounds_committed')}")
        out_rows.append(row)

    n_ok = sum(1 for r in out_rows if r.get("status") == "ok")
    n_exhausted = sum(1 for r in out_rows if r.get("status") == "round1_exhausted")
    # Step 49c (2026-06-10): aggregate per-axis winners across every judge
    # call so each upgrade (49a typography, 49b balance) can be attributed
    # to the axis it targets instead of only the binary acceptance rate.
    axis_summary: Dict[str, Dict[str, int]] = {}
    for r in out_rows:
        for rnd in r.get("rounds", []):
            for ax, v in (rnd.get("axes") or {}).items():
                bucket = axis_summary.setdefault(ax, {"A": 0, "B": 0, "tie": 0})
                bucket[v.get("winner", "tie")] = bucket.get(v.get("winner", "tie"), 0) + 1
    print()
    print("=" * 80)
    print(f"STEP 41 -- N={len(ids)}  ok={n_ok}  round1_exhausted={n_exhausted}")
    print("-" * 80)
    print("  per-axis winners across all judge calls:")
    for ax, c in axis_summary.items():
        print(f"    {ax:25s} A={c['A']:3d}  B={c['B']:3d}  tie={c['tie']:3d}")
    print("=" * 80)
    for r in out_rows:
        print(
            f"  {r['id'][:12]}  status={r.get('status')}  "
            f"rounds_committed={r.get('n_rounds_committed','?')}"
        )
    out_json = OUT / RESULTS_JSON
    out_json.write_text(
        json.dumps(
            {
                "scope": (
                    "Step 41 -- layout-aware structured pairwise GT oracle. "
                    "Judge sees candidate PNG + GT PNG + candidate layout JSON; "
                    "emits per-axis flags + machine-actionable "
                    "structured_suggestions ({kind, target_id, metric, op, "
                    "value, rationale}). Reject feedback populates "
                    "AestheticFeedback.structured_suggestions which the "
                    "existing Generator prompt knows how to apply. Compare "
                    "against Step 37 (free-text reason) and Step 40 (flag-name "
                    "feedback) on the same ids."
                ),
                "n": len(ids),
                "axis_summary": axis_summary,
                "samples": out_rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"\n[done] wrote {out_json.name}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
