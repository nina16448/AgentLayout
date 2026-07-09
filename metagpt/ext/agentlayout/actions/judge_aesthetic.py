"""Aesthetic Judge Action — Agent 4 of the AgentLayout pipeline.

The only multi-modal LLM agent in the pipeline. Renders each candidate to a
PNG, encodes it as base64, and asks a vision-capable LLM to score the layouts
across the COLE 5-axis aesthetic rubric (each 1-10, total 5-50). See Step 30
migration (2026-06-09) for the rationale aligning this with the offline
Phase B COLE eval (layout_agent/output/step21_phaseb_eval.py).

Pipeline position::

    K_VALID candidates that passed Quality Checker
        |
        v
    JudgeAesthetic (THIS) -> AestheticJudgement
        |                       |
        |                       +-> decision == 'accept' (>= 35) -> stop, return best
        |                       +-> decision == 'reject'         -> feedback to
        |                                                            Generator (rounds 1..N)
        |                                                            or Analyst (round N+1+)

Validation layers:
1. Pydantic schema (``AestheticJudgement`` / ``Evaluation`` / ``JudgeScores``):
   - JudgeScores 5 COLE axes each 1-10
   - Evaluation total == sum(scores)  (model_validator)
   - accept <-> feedback null, reject <-> feedback non-null  (model_validator)
2. Action-level semantic check (``_validate_against_input``):
   - best_candidate_id appears in input candidates
   - evaluations id set == input candidates id set

The prompt deliberately shows TWO format examples (accept and reject) so the
LLM does not collapse onto one of the two output shapes by mimicry.
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple

from pydantic import ValidationError

from metagpt.actions import Action
from metagpt.ext.agentlayout.schema import (
    AestheticJudgement,
    BackgroundAnalysis,
    Candidate,
    DesignSpec,
    JudgeDecision,
    LayoutTree,
    VisualObservation,
)
from metagpt.ext.agentlayout.tools.renderer import image_to_base64, render
from metagpt.logs import logger
from metagpt.utils.common import CodeParser


# ============================================================
# Custom exception
# ============================================================


class _JudgementValidationError(ValueError):
    """Pydantic accepted the JSON, but best_candidate_id or evaluations don't match input."""


# ============================================================
# Prompt template (verbatim port of layout_agent/aesthetic_judge.md)
# ============================================================


FORMAT_EXAMPLE_ACCEPT = """{
  "decision": "accept",
  "best_candidate_id": "cand_02",
  "evaluations": [
    {
      "candidate_id": "cand_01",
      "total": 33,
      "scores": {
        "design_layout": 7,
        "content_relevance": 7,
        "typography_color": 6,
        "graphics_images": 6,
        "innovation_originality": 7
      },
      "strengths": "headline_1 position is clear, logo_1 in top-right matches the brief.",
      "weaknesses": "product_img_1 and headline_1 are too far apart, weakening their semantic link."
    },
    {
      "candidate_id": "cand_02",
      "total": 38,
      "scores": {
        "design_layout": 8,
        "content_relevance": 8,
        "typography_color": 7,
        "graphics_images": 7,
        "innovation_originality": 8
      },
      "strengths": "Generous whitespace, clear reading order, palette matches style_keywords.",
      "weaknesses": "price_1 is slightly small and could be more legible."
    }
  ],
  "feedback": {
    "common_issues": "Overall accepted, but price_1 readability is still slightly weak.",
    "suggestions": [
      "Slightly increase price_1 size by about 15% to strengthen legibility.",
      "Keep all other elements within +/-5% drift to preserve the accepted composition."
    ],
    "structured_suggestions": [
      {
        "kind": "resize",
        "target_id": "price_1",
        "metric": "width",
        "op": "increase_by",
        "value": 24,
        "rationale": "Small bump improves legibility without breaking the accepted design_layout."
      },
      {
        "kind": "resize",
        "target_id": "price_1",
        "metric": "height",
        "op": "increase_by",
        "value": 8,
        "rationale": "Pair the width bump with proportional height to preserve aspect."
      }
    ]
  }
}"""


FORMAT_EXAMPLE_REJECT = """{
  "decision": "reject",
  "best_candidate_id": "cand_03",
  "evaluations": [
    {
      "candidate_id": "cand_03",
      "total": 31,
      "scores": {
        "design_layout": 6,
        "content_relevance": 7,
        "typography_color": 6,
        "graphics_images": 6,
        "innovation_originality": 6
      },
      "strengths": "Palette aligns with style_keywords, background space is well used.",
      "weaknesses": "headline_1 is too small to dominate the layout, weakening design_layout hierarchy."
    }
  ],
  "feedback": {
    "common_issues": "All candidates fail to make headline_1 dominate visually. product_img_1 and headline_1 are too far apart.",
    "suggestions": [
      "Increase headline_1 size so it visibly dominates other text elements.",
      "Reduce the distance between product_img_1 and headline_1 so they form a visual group.",
      "Consider bolder whitespace to avoid a crowded layout."
    ],
    "structured_suggestions": [
      {
        "kind": "place_in_bbox",
        "target_id": "headline_1",
        "metric": "bbox",
        "op": "set_to",
        "value": "[80, 200, 720, 480]",
        "target_bbox": [80, 200, 720, 480],
        "rationale": "Upper-left negative-space band (sky region) is empty; move and enlarge headline_1 to occupy it so it visually anchors the design."
      },
      {
        "kind": "place_in_bbox",
        "target_id": "product_img_1",
        "metric": "bbox",
        "op": "set_to",
        "value": "[260, 540, 540, 820]",
        "target_bbox": [260, 540, 540, 820],
        "rationale": "Pull the product up so it sits ~40px below the headline, forming a tight semantic group within the same safe zone."
      },
      {
        "kind": "typography",
        "target_id": "headline_1",
        "metric": "font_size",
        "op": ">=",
        "value": 72,
        "rationale": "Currently ~32px, too small to anchor the design_layout hierarchy."
      }
    ]
  }
}"""


PROMPT_TEMPLATE = """Role: You are a senior graphic designer and aesthetic evaluator.
Your goal is to evaluate each layout candidate and provide scores,
strengths, weaknesses, and actionable improvement suggestions.

# Context
Design Spec: {design_spec}
Layout Tree: {layout_tree}
Dominant palette: {dominant_palette}
Candidate IDs (in the same order as the attached images): {candidate_ids}

# Scoring rubric (each dimension 1-10 on the COLE scale, total 5-50)
Grading anchors (apply to EVERY axis):
  * 10 = flawless on this axis.
  * 7  = mediocre / acceptable.
  * 4  = clear shortcomings.
  * 1-2 = severely poor.

A. design_layout (1-10)
   Is the layout clean, balanced, and consistent? Does the organization of
   elements create clear paths for the eye and follow the importance
   hierarchy in the Layout Tree? A 10 maximizes readability and visual
   appeal; a 1 is a cluttered, confusing layout with no hierarchy or flow.

B. content_relevance (1-10)
   Does the layout fulfill the user's design goals, hard_constraints, AND
   resonate with the intended audience? This axis absorbs the legacy
   `requirement_alignment` semantics: brief fidelity is graded HERE. A 10
   means the content aligns with the design's purpose and the brief; a 1
   means it is irrelevant to the brief or to the audience.

C. typography_color (1-10)
   Do font selection, size, line spacing, color, placement, and the overall
   color scheme work together to enhance readability and harmonize with
   `style_keywords` and `dominant_palette`? A 10 is excellent legibility +
   on-style palette; a 1 is unreadable or clashing.

D. graphics_images (1-10)
   Do graphics/images enhance the design rather than distract? Are they
   high quality, relevant, and harmonious with other elements? A 10 means
   imagery enhances message; a 1 means low-quality, irrelevant, or
   distracting visuals. If only placeholder boxes are visible, score
   conservatively (5-7) on quality of placement/sizing.

E. innovation_originality (1-10)
   Does the design show an original, creative approach beyond following
   trends? A 10 stands out in originality; a 1 is generic or trend-
   following with no unique interpretation of the brief.

# Structured suggestions (REQUIRED on BOTH accept and reject)
You MUST always emit a non-null `feedback` object containing
`common_issues`, `suggestions` (free text), AND `structured_suggestions`
(machine-readable, verifiable). The downstream Layout Generator will only act
on `structured_suggestions`; vague free text gets ignored.

The semantics differ by decision:
  - decision="reject": list concrete fixes for the failing dimensions
    (>= 1 structured suggestion, 2-5 recommended).
  - decision="accept": list SMALL-STEP polish suggestions for the mandatory
    refinement round that follows acceptance. These should be conservative
    nudges (e.g. "+15% on one element's width") that preserve the accepted
    composition; do NOT propose composition-level changes on accept.
    Emit at least 1 structured suggestion; <= 2 is typical.

Each structured suggestion is a JSON object with these fields:

  - kind: one of
        "place_in_bbox" -> [STRONGLY PREFERRED for position/size fixes]
                           set the element to an absolute canvas bbox
                           [L, T, R, B] in pixels. Use this whenever you can
                           SEE the candidate image and know exactly which
                           region of the canvas the element should occupy.
                           This bypasses Generator drift caps and is the
                           highest-bandwidth way to convey a visual decision.
        "resize"      -> change an element's width or height (numeric pixels)
        "move"        -> change an element's top-left position (numeric pixels)
        "spacing"     -> change a gap between two elements (numeric pixels)
        "typography"  -> change font_size or font_weight (numeric)
        "color"       -> set a hex color like "#RRGGBB"
        "zorder"      -> set explicit z_index (integer)
        "other"       -> avoid; only use when none of the above fit
  - target_id: an element id that EXISTS in the Layout Tree above.
  - metric: REQUIRED to be a schema-native field name. The Layout schema only
    has `left`, `top`, `width`, `height`, `font_size`, `font_weight`, `color`,
    `z_index`. There is NO `right` or `bottom` field. The allowed metric
    string is determined by `kind`:
        kind=place_in_bbox -> "bbox"  (with op="set_to" and target_bbox=[L,T,R,B])
        kind=resize     -> "width" or "height"
        kind=move       -> "left" or "top"   (NOT "right" / "bottom" / "x" / "y";
                                              if you want the element pushed to
                                              the bottom-right, compute the
                                              target `left` and `top` yourself
                                              from canvas_width / canvas_height
                                              and emit TWO move suggestions)
        kind=spacing    -> "gap_to:OTHER_ID"  (OTHER_ID is an element id)
        kind=typography -> ONE of "font_size" | "font_weight" |
                                  "font_family" | "text_align"
                           Value typing per metric:
                             font_size   -> integer pixels (e.g. 96)
                             font_weight -> integer (e.g. 700) OR string
                                            ("regular" / "bold")
                             font_family -> string (e.g. "serif", "Inter",
                                            "Playfair Display")
                             text_align  -> one of "left" / "center" /
                                            "right" / "justify"
        kind=color      -> "color"
        kind=zorder     -> "z_index"
        kind=other      -> any string (use sparingly)
  - op: a comparator or action, one of ">=", "<=", "==", "set_to",
        "increase_by", "decrease_by".
        For kind=place_in_bbox, op MUST be "set_to".
  - value: the target value. MUST be numeric (int or float) when kind is
        resize / move / spacing / zorder. MUST be a hex string like "#FFFFFF"
        when kind is color. For typography, value type depends on metric (see
        per-metric typing in the kind=typography entry above). For
        place_in_bbox, value mirrors target_bbox as a string like
        "[L, T, R, B]" (the authoritative source is target_bbox; value is
        descriptive only).
  - target_bbox: REQUIRED iff kind=place_in_bbox. A list of 4 ints
        [L, T, R, B] in canvas pixel coords (L>=0, T>=0, R>L, B>T).
        Generator will set the element to (left=L, top=T, width=R-L, height=B-T).
        For all other kinds, omit this field.
  - rationale: optional one-line explanation.

Place-in-bbox example (PREFERRED when you can see the image):
  {{"kind":"place_in_bbox","target_id":"headline_1","metric":"bbox",
    "op":"set_to","value":"[80, 200, 720, 480]",
    "target_bbox":[80, 200, 720, 480],
    "rationale":"Upper-left sky region is empty -- anchor headline there."}}
Numeric example:    {{"kind":"resize","target_id":"headline_1","metric":"height","op":">=","value":80}}
Color example:      {{"kind":"color","target_id":"bg_1","metric":"color","op":"set_to","value":"#1A1A2E"}}
Typography family:  {{"kind":"typography","target_id":"headline_1","metric":"font_family","op":"set_to","value":"Playfair Display","rationale":"GT uses a serif display face that conveys editorial weight; AL's sans-serif looks generic."}}
Typography align:   {{"kind":"typography","target_id":"subtitle_1","metric":"text_align","op":"set_to","value":"center","rationale":"GT centres the subtitle for visual balance with the symmetric headline."}}
Typography weight:  {{"kind":"typography","target_id":"headline_1","metric":"font_weight","op":"set_to","value":"bold","rationale":"GT headline is visibly heavier; AL's regular weight reads as body text."}}

# Format examples (output one JSON matching whichever case applies)

Case A -- best score >= 35, accept (feedback contains polish-step suggestions):
{format_example_accept}

Case B -- best score < 35, reject with corrective feedback:
{format_example_reject}

# Instruction
ATTENTION: Evaluate ALL candidates listed above. Do not skip any.
ATTENTION: strengths and weaknesses must reference specific element IDs.
ATTENTION: feedback MUST always be present (never null) on BOTH accept and reject.
           Reject: structured_suggestions lists corrective fixes (>= 1 entry,
                   2-5 recommended) targeting failing dimensions.
           Accept: structured_suggestions lists conservative polish nudges
                   (>= 1 entry, <= 2 typical) that the mandatory next refinement
                   round will apply on top of the accepted layout. Do NOT
                   propose composition-level changes on accept.
ATTENTION: For numeric kinds (resize / move / spacing / typography / zorder),
           the `value` field must be a number, NOT a string like "bigger".
ATTENTION: `metric` MUST be from the per-kind whitelist above. NEVER emit
           `metric: "right"` or `metric: "bottom"` (the schema has no such
           fields). If you want an element flush to bottom-right, compute and
           emit TWO suggestions like
               {{"kind":"move","target_id":"logo_1","metric":"left","op":"set_to","value":700}}
               {{"kind":"move","target_id":"logo_1","metric":"top","op":"set_to","value":1150}}
ATTENTION: Hard-constraint `size_preference` with hint "prominent" is enforced
           downstream as `width * height >= 0.10 * canvas_width * canvas_height`
           ("medium" => 0.08, "balanced" => 0.05). Resizing only `width` while
           keeping `height` small often fails this area gate. When you ask to
           enlarge a prominent element, emit BOTH a width AND a height resize
           so the product clears the area threshold. Example for an 800x1200
           canvas where title_1 must be "prominent" (area >= 96000 px^2):
               {{"kind":"resize","target_id":"title_1","metric":"width","op":">=","value":600}}
               {{"kind":"resize","target_id":"title_1","metric":"height","op":">=","value":180}}
           600 * 180 = 108000 >= 96000, so QC will pass.
ATTENTION: Step 45 (2026-06-10) -- TYPOGRAPHY parity with the reference.
           place_in_bbox solves "is the element in the right region"; it does
           NOT fix "does the text read like the reference design". When you
           can see a candidate text element that LOSES on typography_color
           against the GT image (smaller weight, generic family, wrong
           alignment, awkward size), emit ONE OR MORE kind="typography"
           suggestions targeting the failing dimension(s). Inspect ALL FOUR
           typography metrics on every text element:
             1. font_size    -- is the title big enough to dominate?
             2. font_weight  -- does the GT look heavier or lighter?
             3. font_family  -- serif vs sans, display vs body face?
             4. text_align   -- does the GT centre / left-align differently?
           Emit one suggestion PER failing metric so the Generator can apply
           them independently. Do NOT bundle multiple metrics into one
           suggestion. Typography fixes are how AL closes the visual quality
           gap to the designer GT once layout positions are already right.
ATTENTION: For ANY suggestion that involves moving or resizing an element,
           prefer kind="place_in_bbox" with an explicit target_bbox=[L,T,R,B]
           over a chain of resize/move suggestions. You are looking at the
           candidate image -- you can identify the empty visual region better
           than the Generator can. Bbox values are absolute canvas pixels;
           confirm they fit (L>=0, T>=0, R<=canvas_width, B<=canvas_height,
           R>L, B>T). The Generator will obey target_bbox verbatim and skip
           its +/-10% drift cap for that element.
ATTENTION: Prefer kind != "other"; aim for at most one "other" per response.
ATTENTION: best_candidate_id must be the candidate with the highest total score.
ATTENTION: Each evaluation's "total" must equal the sum of its four scores.
Output a single JSON object, nothing else.
"""


MAX_RETRIES: int = 3

# Step 78 decoupled observer: parse retries for the reject-only inspector
# call. Kept small -- a failed observer degrades to "no observations", it
# must never burn the main judgement budget.
_OBSERVE_MAX_RETRIES: int = 2

# Step 83: anchored scoring + concreteness contract. Step 82's trace showed
# unanchored absolute scores collapse into a 34-39 band with "minor
# adjustments needed" boilerplate; anchors + a cite-your-evidence rule force
# discrimination. Appended AFTER the template so pinned-string tests on
# PROMPT_TEMPLATE stay valid.
_SCORING_ANCHORS: str = """

# Scoring anchors (calibrate every 1-10 axis score to these)
  9-10 = professional designer quality; could ship as-is
  7-8  = competent but generic; visible non-blocking flaws
  5-6  = obvious amateur errors (broken grouping, orphaned elements, dead zones)
  1-4  = structurally broken
Spread your scores: candidates with visible differences must NOT all land on
the same total. Every `weaknesses` entry MUST cite at least one element id AND
one concrete measured fact (from the geometry block below or exact pixel
evidence you can see in the render). Generic phrases like "minor adjustments
needed" or "could be more prominent" are INVALID weaknesses."""


# ============================================================
# Action
# ============================================================


class JudgeAesthetic(Action):
    """Agent 4 -- score candidate layouts and emit feedback when none qualify."""

    name: str = "JudgeAesthetic"
    desc: str = (
        "Evaluate rendered layout candidates across four aesthetic dimensions; "
        "either accept the highest-scoring one or emit actionable feedback for "
        "the next iteration."
    )

    async def run(
        self,
        *,
        candidates: List[Candidate],
        spec: DesignSpec,
        tree: LayoutTree,
        bg: BackgroundAnalysis,
    ) -> AestheticJudgement:
        """Render candidates, build multi-modal prompt, call vision LLM, validate.

        Pre-condition: ``spec`` must be enriched (Asset Analyzer ran).
        Each candidate is rendered to PNG and base64-encoded; the order in the
        ``images`` list matches the order of ``candidates`` so the LLM can
        match images to ``candidate_ids``.
        """
        spec.assert_enriched()
        if not candidates:
            raise ValueError("JudgeAesthetic requires at least one candidate.")

        if not self.llm.support_image_input():
            logger.warning(
                f"JudgeAesthetic: LLM model '{getattr(self.llm, 'model', '?')}' does "
                f"not support image input. The 'images' arg will be silently dropped "
                f"and scores will be based on text context only."
            )

        prompt = self._build_prompt(candidates, spec, tree, bg)
        images = self._render_images(candidates, spec)

        last_err: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            rsp = await self.llm.aask(prompt, images=images)
            try:
                judgement = self._parse_response(rsp)
                self._validate_against_input(judgement, candidates)
                self._attach_best_candidate_layout(judgement, candidates)
                # Step 78: reject-only decoupled observer. The verdict above
                # was produced by the unmodified prompt (no contamination);
                # only AFTER it exists do we ask a separate call what defects
                # are visible on the best candidate's render.
                from metagpt.ext.agentlayout.feature_flags import visual_loop_enabled

                if visual_loop_enabled() and judgement.decision == JudgeDecision.REJECT:
                    judgement.feedback.visual_observations = await self._observe(
                        judgement, candidates, spec, bg
                    )
                return judgement
            except (ValueError, ValidationError) as err:
                last_err = err
                logger.warning(
                    f"JudgeAesthetic attempt {attempt}/{MAX_RETRIES} failed: {err}"
                )

        raise ValueError(
            f"JudgeAesthetic: could not produce a valid AestheticJudgement after "
            f"{MAX_RETRIES} attempts. Last error: {last_err}"
        )

    @staticmethod
    def _attach_best_candidate_layout(
        judgement: AestheticJudgement,
        candidates: List[Candidate],
    ) -> None:
        """Populate ``judgement.best_candidate_layout`` from input candidates.

        Refinement Loop (2026-05-20): the downstream LayoutGenerator needs the
        winning candidate's bbox dict to anchor the next refinement round.
        We look it up here (verdict only carries best_candidate_id; the bbox
        lives in the Candidate object that was the JudgeAesthetic input).
        """
        best = next(
            (c for c in candidates if c.candidate_id == judgement.best_candidate_id),
            None,
        )
        if best is None:  # _validate_against_input already raised if missing
            return
        bbox_dict: Dict[str, Tuple[float, float, float, float]] = {}
        for el in best.elements:
            bbox_dict[el.id] = (
                float(el.left),
                float(el.top),
                float(el.width),
                float(el.height),
            )
        judgement.best_candidate_layout = bbox_dict

    def _build_prompt(
        self,
        candidates: List[Candidate],
        spec: DesignSpec,
        tree: LayoutTree,
        bg: BackgroundAnalysis,
    ) -> str:
        """Render PROMPT_TEMPLATE; the actual images go via ``llm.aask(images=...)``."""
        spec_str = json.dumps(spec.model_dump(), indent=2, ensure_ascii=False)
        tree_dump = {"layout_tree": tree.root.model_dump()}
        tree_str = json.dumps(tree_dump, indent=2, ensure_ascii=False)
        palette_str = json.dumps(bg.dominant_palette, ensure_ascii=False)
        cand_ids_str = json.dumps(
            [c.candidate_id for c in candidates], ensure_ascii=False
        )
        return PROMPT_TEMPLATE.format(
            design_spec=spec_str,
            layout_tree=tree_str,
            dominant_palette=palette_str,
            candidate_ids=cand_ids_str,
            format_example_accept=FORMAT_EXAMPLE_ACCEPT,
            format_example_reject=FORMAT_EXAMPLE_REJECT,
        ) + _SCORING_ANCHORS + self._geometry_facts_block(candidates, spec, bg) + (
            "\n\n# Canvas resources (panels + placeable background regions)\n"
            + self._canvas_resources_block(bg)
        )

    @staticmethod
    def _canvas_resources_block(bg: BackgroundAnalysis) -> str:
        """Step 86 (user): panels + CV-derived placeable regions for the judge.

        The observer estimates target_bbox by eye; these are the terrain
        facts that ground the estimate -- exact panel bboxes (with the Step 79
        frame/solid distinction) and the background's calm regions. The main
        judgement call gets the same block so an unused panel or an ignored
        calm area is visible to the verdict, not just to the repair loop.
        """
        lines = []
        if bg.underlay_regions:
            lines.append("Baked panels (designer-placed text holders, exact bboxes):")
            for i, region in enumerate(bg.underlay_regions, 1):
                left, top, right, bottom = region.bbox
                surface = (
                    f"transparent frame, backdrop ~{region.dominant_color}"
                    if region.panel_type == "frame"
                    else f"solid fill {region.dominant_color}"
                )
                lines.append(
                    f"  panel {i}: bbox [{left}, {top}, {right}, {bottom}], "
                    f"{surface}, readable text colour {region.recommended_text_color}"
                )
        if bg.safe_zones:
            lines.append(
                "Background placeable regions (CV-derived calm areas -- good "
                "anchors for target_bbox estimates):"
            )
            for zone in bg.safe_zones[:5]:
                lines.append(
                    f"  {zone.region}: bbox {zone.bbox} (confidence {zone.confidence:.2f})"
                )
        if bg.low_saliency_regions:
            lines.append("Quietest background rectangles (lowest visual noise):")
            for zone in bg.low_saliency_regions[:5]:
                lines.append(f"  {zone.region}: bbox {zone.bbox}")
        return "\n".join(lines) if lines else "(no panel / region data available)"

    @staticmethod
    def _geometry_facts_block(candidates: List[Candidate], spec: DesignSpec,
                              bg: BackgroundAnalysis) -> str:
        """Step 83: neutral machine measurements per candidate.

        The judge cannot resolve 20-200px geometry on downscaled renders;
        these descriptors give it the evidence. They are explicitly neutral
        (e.g. full centering is a legitimate style in ~22% of designer
        layouts) -- the verdict stays with the judge.
        """
        from metagpt.ext.agentlayout.tools.layout_metrics import (
            format_metrics_block,
            measure_layout,
        )

        parts = ["\n\n# Machine-measured geometry (neutral descriptors, per candidate)"]
        for cand in candidates:
            try:
                block = format_metrics_block(
                    measure_layout(cand, spec, bg.underlay_regions or None)
                )
            except Exception:  # noqa: BLE001 -- measurement must never kill judging
                continue
            parts.append(f"\nCandidate {cand.candidate_id}:\n{block}")
        return "\n".join(parts) if len(parts) > 1 else ""

    # ------------------------------------------------------------------
    # Step 78: decoupled visual observer (second pass, reject-only)
    # ------------------------------------------------------------------
    # Step 77d post-mortem: putting the defect catalogue INSIDE the judgement
    # prompt contaminated the verdict itself -- round-0 acceptance collapsed
    # 7 -> 1 on identical candidate distributions (observer effect), while
    # compliance proved execution works (88.9%). The fix: the judgement
    # prompt stays byte-identical, and observations come from a SEPARATE
    # small call made only after a reject verdict already exists.

    @staticmethod
    def _build_observe_prompt(candidate: Candidate, spec: DesignSpec,
                              bg: BackgroundAnalysis) -> str:
        """Prompt for the reject-only render-inspector call (Step 78).

        Step 86: the panel list grew into the full canvas-resources block
        (panels with frame/solid semantics + CV placeable regions), and each
        text element line carries its natural bitmap size -- the terrain the
        observer needs to estimate target_bbox with, instead of pure eyeball.
        """
        panel_lines = JudgeAesthetic._canvas_resources_block(bg)
        spec_by_id = {el.id: el for el in spec.elements}
        layout_rows = []
        for el in candidate.elements:
            row = (
                f"  {el.id}: bbox [{el.left}, {el.top}, {el.left + el.width}, "
                f"{el.top + el.height}], color {getattr(el, 'color', None)}, "
                f"angle {el.angle}"
            )
            spec_el = spec_by_id.get(el.id)
            if spec_el and (spec_el.asset_ref or "").endswith("_text.png"):
                try:
                    from PIL import Image as _PILImage

                    with _PILImage.open(spec_el.asset_ref) as img:
                        row += f" (pre-rendered text, natural size {img.size[0]}x{img.size[1]}px)"
                except (OSError, IOError):
                    pass
            layout_rows.append(row)
        layout_lines = "\n".join(layout_rows)
        from metagpt.ext.agentlayout.tools.layout_metrics import (
            format_metrics_block,
            measure_layout,
        )

        try:
            metrics_block = format_metrics_block(
                measure_layout(candidate, spec, bg.underlay_regions or None)
            )
        except Exception:  # noqa: BLE001
            metrics_block = "(measurement unavailable)"
        return f"""Role: You are a render inspector. The attached image is a poster candidate
that was ALREADY rejected by a separate quality review -- your job is NOT to
judge it, only to report concrete visible defects so the next attempt can fix
them.

# Candidate layout (element id -> bbox [L, T, R, B] in canvas pixels)
Canvas: {spec.canvas.width}x{spec.canvas.height}.
{layout_lines}

# Canvas resources (panels + placeable background regions)
{panel_lines}

# Machine-measured geometry (neutral descriptors)
{metrics_block}

# Per-element review (Step 87 -- one entry for EVERY element above)
Review each element IN THE ORDER LISTED while LOOKING at the render. No
element may be skipped. For each, give a verdict:
  - "ok" when the element is well placed and legible. 'ok' is a valid and
    COMMON verdict -- do NOT invent defects to fill space.
  - otherwise the element's PRIMARY issue, classified by this priority rubric:
    1. "title_misplaced": the DOMINANT text is in the wrong spot for THIS
       background (designer prior: center-y in [0.35, 0.57] of canvas height
       -- but judge by eye). REQUIRED: target_bbox = the range it SHOULD occupy.
    2. "lockup_broken": secondary/subtitle text drifted from the dominant text
       (designer gap p90 = 0.122 of canvas height). REQUIRED: target_bbox.
    3. "text_off_panel": this info text should sit ON a baked panel but does
       not. REQUIRED: target_bbox = that panel's bbox (exact values above).
    4. "text_overlap": collides with another element. REQUIRED: second_id.
    5. "text_too_small" / "text_too_large": REQUIRED: target_area_px (int px^2).
    6. "text_illegible": unreadable. REQUIRED: target_color (concrete hex)
       AND/OR target_bbox (a calmer region).
    ("text_tilted": unintended rotation, any position.)
Fixes must be CONCRETE: every positional fix is a bbox range the element
should move INTO. If your instinct is "move it AWAY from X", express it as
the concrete range it should occupy instead. target_bbox values are YOUR
visual estimates for THIS image -- the references are priors, not rules.

# Output (JSON array only, ONE object per element, no commentary, no fences)
[{{"element_id": "...", "verdict": "ok" | "<issue kind>", "second_id": null,
"target_bbox": [L, T, R, B] | null, "target_color": "#RRGGBB" | null,
"target_area_px": 12345 | null, "comment": "one line"}}]"""

    @staticmethod
    def _parse_observations(
        rsp: str, expected_ids: Optional[set] = None
    ) -> List[VisualObservation]:
        """Parse the inspector response; invalid entries are dropped, never fatal.

        Step 87 per-element form: entries carry ``element_id`` + ``verdict``
        (+ ``comment``); verdict "ok" entries are coverage confirmations and
        produce no observation. The pre-87 form (``kind``/``target_id``/
        ``note``) still parses. When ``expected_ids`` is given, elements the
        inspector failed to review are logged -- vague coverage is visible.
        """
        text = (rsp or "").strip()
        if "```" in text:
            start = text.find("[")
            end = text.rfind("]")
            if start == -1 or end <= start:
                return []
            text = text[start:end + 1]
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        if isinstance(data, dict):
            data = data.get("visual_observations", data.get("reviews", []))
        if not isinstance(data, list):
            return []
        out: List[VisualObservation] = []
        reviewed: set = set()
        for item in data[:16]:
            if not isinstance(item, dict):
                continue
            target_id = item.get("element_id") or item.get("target_id")
            if target_id:
                reviewed.add(target_id)
            verdict = item.get("verdict", item.get("kind"))
            if verdict is None or str(verdict).lower() == "ok":
                continue
            try:
                out.append(VisualObservation(
                    kind=verdict,
                    target_id=target_id,
                    second_id=item.get("second_id"),
                    target_bbox=item.get("target_bbox"),
                    target_color=item.get("target_color"),
                    target_area_px=item.get("target_area_px"),
                    note=item.get("comment") or item.get("note") or "",
                ))
            except (ValidationError, TypeError):
                continue
        if expected_ids:
            missing = set(expected_ids) - reviewed
            if missing:
                logger.warning(
                    f"JudgeAesthetic observer skipped {len(missing)} element(s): "
                    f"{sorted(missing)}"
                )
        return out[:8]

    async def _observe(self, judgement: AestheticJudgement,
                       candidates: List[Candidate], spec: DesignSpec,
                       bg: BackgroundAnalysis) -> List[VisualObservation]:
        """Second-pass inspector call on the reject verdict's best candidate."""
        best = next(
            (c for c in candidates if c.candidate_id == judgement.best_candidate_id),
            None,
        )
        if best is None:
            return []
        prompt = self._build_observe_prompt(best, spec, bg)
        images = self._render_images([best], spec)
        for attempt in range(1, _OBSERVE_MAX_RETRIES + 1):
            try:
                rsp = await self.llm.aask(prompt, images=images)
            except Exception as err:  # noqa: BLE001 -- observer must never kill the run
                logger.warning(f"JudgeAesthetic observer call failed: {err}")
                return []
            obs = self._parse_observations(
                rsp, expected_ids={el.id for el in best.elements}
            )
            if obs or (rsp or "").strip().startswith("["):
                # A parseable (possibly all-ok) array is a final answer.
                return obs
            logger.warning(
                f"JudgeAesthetic observer parse failed (attempt {attempt}/"
                f"{_OBSERVE_MAX_RETRIES})."
            )
        return []

    @staticmethod
    def _render_images(candidates: List[Candidate], spec: DesignSpec) -> List[str]:
        """Render each candidate to a base64 PNG string, preserving order."""
        return [image_to_base64(render(c, spec)) for c in candidates]

    @staticmethod
    def _parse_response(rsp: str) -> AestheticJudgement:
        """Strip markdown fences if present, then validate against AestheticJudgement."""
        text = rsp.strip()
        if "```" in text:
            try:
                text = CodeParser.parse_code(text=text, lang="json") or text
            except Exception:
                pass
        return AestheticJudgement.model_validate_json(text)

    @staticmethod
    def _validate_against_input(
        judgement: AestheticJudgement, candidates: List[Candidate]
    ) -> None:
        """Raise if best_candidate_id or evaluations don't match the input candidates."""
        cand_ids = {c.candidate_id for c in candidates}
        eval_ids = {e.candidate_id for e in judgement.evaluations}

        if judgement.best_candidate_id not in cand_ids:
            raise _JudgementValidationError(
                f"best_candidate_id '{judgement.best_candidate_id}' "
                f"not found in input candidate ids {sorted(cand_ids)}."
            )

        missing = sorted(cand_ids - eval_ids)
        extra = sorted(eval_ids - cand_ids)
        if missing or extra:
            raise _JudgementValidationError(
                f"AestheticJudgement evaluations id set does not match input. "
                f"missing={missing}, extra={extra}"
            )
