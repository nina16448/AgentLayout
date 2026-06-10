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
    LayoutTree,
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
        )

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
