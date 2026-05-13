"""Layout Generator Action — Agent 3 of the AgentLayout pipeline.

Single most complex Action in the pipeline. Takes the full upstream context
(enriched DesignSpec, LayoutTree, BackgroundAnalysis, optional Aesthetic-Judge
feedback) and asks the LLM to produce a ``CandidatesBatch`` containing 5
fully-coordinatised layout proposals.

Pipeline position::

    enriched DesignSpec        \\
    LayoutTree                  \\___> GenerateLayout (THIS) -> CandidatesBatch
    BackgroundAnalysis          /                              (5 raw candidates,
    AestheticFeedback (opt'l)  /                                some may fail QC)

The K_valid = 5 top-up loop lives in the pipeline driver, *not* here. This
Action only emits one batch per call; the driver invokes it repeatedly,
filters with ``tools.quality_checker.filter_valid``, and re-invokes until the
valid pool reaches 5. This separation keeps the Action focused on a single
LLM call and lets the driver swap in different top-up strategies later.

Validation layers:
1. Pydantic schema (``CandidatesBatch`` / ``Candidate`` / ``LayoutElement``):
   - width / height > 0, z_index >= 0, etc.
2. Quality Checker (NOT done here): completeness, boundaries, hard constraints
3. Aesthetic Judge (NOT done here): visual / aesthetic dimensions

The Action keeps validation 1 only; layers 2/3 belong to downstream modules.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from pydantic import ValidationError

from metagpt.actions import Action
from metagpt.ext.agentlayout.schema import (
    AestheticFeedback,
    BackgroundAnalysis,
    CandidatesBatch,
    DesignSpec,
    LayoutTree,
)
from metagpt.logs import logger
from metagpt.utils.common import CodeParser


# ============================================================
# Prompt template (verbatim port of layout_agent/layout_generator.md)
# ============================================================


# Two candidates with deliberately different compositions, so the LLM
# understands "5 distinct compositional approaches" is the goal.
FORMAT_EXAMPLE_JSON = """{
  "candidates": [
    {
      "candidate_id": "cand_01",
      "elements": [
        {
          "id": "bg_1",
          "left": 0, "top": 0, "width": 1080, "height": 1920,
          "angle": 0, "z_index": 1
        },
        {
          "id": "logo_1",
          "left": 900, "top": 40, "width": 120, "height": 120,
          "angle": 0, "z_index": 4
        },
        {
          "id": "headline_1",
          "left": 100, "top": 800, "width": 880, "height": 600,
          "angle": 0, "z_index": 3,
          "font_family": "sans-serif",
          "font_size": 96,
          "font_weight": "bold",
          "color": "#1B3A6B",
          "text_align": "center"
        }
      ]
    },
    {
      "candidate_id": "cand_02",
      "elements": [
        {
          "id": "bg_1",
          "left": 0, "top": 0, "width": 1080, "height": 1920,
          "angle": 0, "z_index": 1
        },
        {
          "id": "logo_1",
          "left": 900, "top": 40, "width": 120, "height": 120,
          "angle": 0, "z_index": 4
        },
        {
          "id": "headline_1",
          "left": 80, "top": 1200, "width": 920, "height": 480,
          "angle": 0, "z_index": 3,
          "font_family": "serif",
          "font_size": 84,
          "font_weight": "bold",
          "color": "#FFFFFF",
          "text_align": "left"
        }
      ]
    }
  ]
}"""


PROMPT_TEMPLATE = """Role: You are a professional graphic layout designer.
Your goal is to arrange the given design elements on a canvas
by assigning precise pixel coordinates to each element.

# Context
Design Spec: {design_spec}
Safe zones: {safe_zones}
Dominant palette: {dominant_palette}
Recommended text color (default, override if needed): {recommended_text_color}
Feedback from previous round (if any): {feedback}

# How to read `feedback` (only when it is not "None")
The feedback object has two parts:
  - `suggestions`: free-text human notes; use them for *context* only.
  - `structured_suggestions`: a JSON list of typed constraints. PREFER these
    over the free text. Each entry has the shape
        {{"kind": ..., "target_id": ..., "metric": ..., "op": ..., "value": ...}}

Translate each structured suggestion into a concrete adjustment as follows:

  | kind         | what to change                                                  |
  | ------------ | --------------------------------------------------------------- |
  | resize       | the element's `width` and/or `height`                           |
  | move         | the element's `left` / `top` (or `right` / `bottom` derived)    |
  | spacing      | the gap between `target_id` and the element named in `metric`   |
  |              |   (metric format: 'gap_to:OTHER_ID')                            |
  | typography   | the element's `font_size` or `font_weight`                      |
  | color        | the element's `color` (use the exact hex string in `value`)     |
  | zorder       | the element's `z_index` (integer)                               |
  | other        | apply the operator/value to the named `metric` field            |

Operators:
  ">="     -> the field MUST be at least `value`. Aim for value to value*1.2;
              do NOT exceed by huge margins, that creates overlap and fails QC.
  "<="     -> the field MUST be at most `value`. Aim for value*0.8 to value.
  "=="     -> set the field to exactly `value`.
  "set_to" -> same as "==".
  "increase_by" / "decrease_by" -> shift the current value by that amount.

If two structured suggestions conflict with each other or with a
hard_constraint, prefer the one that better serves info_hierarchy
(the Layout Tree's depth order tells you which element is more important).

# Layout Tree
{layout_tree}

Elements in the same branch are semantically related.
Elements closer to the leaves have lower visual importance.

# size reference (element_area / canvas_area, must satisfy lower bound)
full-canvas: >=95%  |  hero: >=60%   |  large: >=30%
prominent:   >=20%  |  medium: >=15% |  small: >=8%   |  caption: >=3%
(If hard_constraints contain a size_preference for a target with hint H,
 that target's width*height divided by canvas_width*canvas_height MUST be
 at or above the lower bound of H.)

# Format example
{format_example}

# Instruction
ATTENTION: Output exactly 5 candidates, each containing ALL element IDs from the spec.
ATTENTION: Use element IDs EXACTLY as they appear in the spec -- do NOT rename or
           translate them. If the spec says id='headline_1', output id='headline_1'
           (not 'title_1', not 'header_1'). The set of ids in your output MUST
           equal the set of ids in spec.elements.
ATTENTION: All coordinates must satisfy:
           left >= 0, top >= 0,
           left + width <= canvas_width,
           top + height <= canvas_height.
ATTENTION: Strictly obey all hard_constraints.
ATTENTION: For text elements, also output font_family, font_size, font_weight, color, text_align.
ATTENTION: For image elements, output geometry only -- no visual style fields needed.
ATTENTION: Each candidate must take a distinctly different compositional approach.
           Do not repeat similar layouts across candidates.
ATTENTION: If feedback is provided, satisfy every structured_suggestion in at
           least 4 of 5 candidates. Use the suggestions[] free text only as
           supplementary context. Do not ignore the structured list, but also
           do not over-apply: a ">=" constraint is a LOWER bound, not a target
           you must exceed by 2x.
Output carefully referenced "format example" in JSON format, nothing else.
"""


MAX_RETRIES: int = 3


# ============================================================
# Action
# ============================================================


class GenerateLayout(Action):
    """Agent 3 -- assign concrete pixel geometry to every element."""

    name: str = "GenerateLayout"
    desc: str = (
        "Arrange every design element on the canvas with concrete pixel "
        "coordinates and (for text elements) visual style. Produce 5 "
        "compositionally distinct candidates per call."
    )

    async def run(
        self,
        *,
        spec: DesignSpec,
        tree: LayoutTree,
        bg: BackgroundAnalysis,
        feedback: Optional[AestheticFeedback] = None,
    ) -> CandidatesBatch:
        """Build prompt, call LLM, parse and validate.

        Pre-condition: ``spec`` must be enriched (Asset Analyzer ran).
        Returns one batch of *raw* candidates -- the K_valid = 5 top-up loop
        is the pipeline driver's responsibility, not this Action's.
        """
        spec.assert_enriched()
        prompt = self._build_prompt(spec, tree, bg, feedback)

        last_err: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            rsp = await self.llm.aask(prompt)
            try:
                return self._parse_response(rsp)
            except (ValueError, ValidationError) as err:
                last_err = err
                logger.warning(
                    f"GenerateLayout attempt {attempt}/{MAX_RETRIES} failed: {err}"
                )

        raise ValueError(
            f"GenerateLayout: could not produce a valid CandidatesBatch after "
            f"{MAX_RETRIES} attempts. Last error: {last_err}"
        )

    def _build_prompt(
        self,
        spec: DesignSpec,
        tree: LayoutTree,
        bg: BackgroundAnalysis,
        feedback: Optional[AestheticFeedback],
    ) -> str:
        """Render PROMPT_TEMPLATE with all 7 substitutions."""
        spec_str = json.dumps(spec.model_dump(), indent=2, ensure_ascii=False)
        # Wrap tree as {"layout_tree": ...} so the LLM sees the same shape it
        # produced as Asset Planner output.
        tree_dump: Dict[str, Any] = {"layout_tree": tree.root.model_dump()}
        tree_str = json.dumps(tree_dump, indent=2, ensure_ascii=False)
        safe_zones_str = json.dumps(
            [sz.model_dump() for sz in bg.safe_zones], indent=2, ensure_ascii=False
        )
        palette_str = json.dumps(bg.dominant_palette, ensure_ascii=False)
        feedback_str = (
            "None"
            if feedback is None
            else json.dumps(feedback.model_dump(), indent=2, ensure_ascii=False)
        )
        return PROMPT_TEMPLATE.format(
            design_spec=spec_str,
            safe_zones=safe_zones_str,
            dominant_palette=palette_str,
            recommended_text_color=bg.recommended_text_color,
            feedback=feedback_str,
            layout_tree=tree_str,
            format_example=FORMAT_EXAMPLE_JSON,
        )

    @staticmethod
    def _parse_response(rsp: str) -> CandidatesBatch:
        """Strip markdown fences if present, then validate against CandidatesBatch."""
        text = rsp.strip()
        if "```" in text:
            try:
                text = CodeParser.parse_code(text=text, lang="json") or text
            except Exception:
                pass
        return CandidatesBatch.model_validate_json(text)
