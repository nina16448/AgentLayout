"""Analyst Action — Agent 1 of the AgentLayout pipeline.

Parses the user's natural-language design brief and the raw asset list into
a structured ``DesignSpec`` JSON. This is the only Action that converts free
text into the typed schema; every downstream Action consumes typed objects.

Pipeline position::

    user brief + raw assets  ->  AnalyzeBrief (THIS)  ->  DesignSpec
                                                          (importance / semantic_relevance
                                                           still None until Asset Analyzer)

The prompt is a verbatim port of ``layout_agent/analyst.md``; only the
runtime substitutions (``{user_brief}`` / ``{asset_list}`` / ``{feedback}`` /
``{format_example}``) are filled at call time.
"""
from __future__ import annotations

import json
from typing import List, Optional

from pydantic import BaseModel, ValidationError, model_validator

from metagpt.actions import Action
from metagpt.ext.agentlayout.schema import AestheticFeedback, DesignSpec
from metagpt.logs import logger
from metagpt.utils.common import CodeParser


# ============================================================
# Input model
# ============================================================


class AssetInput(BaseModel):
    """A single raw asset before Analyst processes it.

    At least one of ``asset_ref`` (image file path) or ``content`` (text
    string) must be set. Step 80 (2026-07-03) allows BOTH simultaneously for
    pre-rendered text assets (``*_text.png``): the image carries the
    designer's typography verbatim, while ``content`` carries the text string
    so the Director can still reason about semantics. The CLIP preprocessor
    independently produces an embedding key for each asset; that key is *not*
    surfaced to the Analyst — Analyst always outputs ``embedding_key: null``
    per the spec.
    """

    asset_ref: Optional[str] = None
    content: Optional[str] = None

    @model_validator(mode="after")
    def _at_least_one_payload(self) -> "AssetInput":
        has_ref = self.asset_ref is not None and self.asset_ref != ""
        has_content = self.content is not None and self.content != ""
        if not has_ref and not has_content:
            raise ValueError(
                "AssetInput must set at least one of 'asset_ref' or 'content'."
            )
        return self


# ============================================================
# Prompt template (verbatim port of layout_agent/analyst.md)
# ============================================================


FORMAT_EXAMPLE_JSON = """{
  "canvas": {
    "width": 1080,
    "height": 1920,
    "background_asset_ref": "bg.jpg",
    "background_embedding_key": null,
    "background_color": null
  },
  "elements": [
    {
      "id": "bg_1",
      "semantic_type": "background_image",
      "visual_type": "image",
      "content": null,
      "asset_ref": "bg.jpg",
      "embedding_key": null,
      "inferred": false
    },
    {
      "id": "headline_1",
      "semantic_type": "title",
      "visual_type": "text",
      "content": "夏日限定 5 折起",
      "asset_ref": null,
      "embedding_key": null,
      "inferred": false
    },
    {
      "id": "logo_1",
      "semantic_type": "logo",
      "visual_type": "image",
      "content": null,
      "asset_ref": "logo.png",
      "embedding_key": null,
      "inferred": false
    }
  ],
  "hard_constraints": [
    {
      "rule": "position_preference",
      "targets": ["logo_1"],
      "params": {"hint": "top_right"}
    },
    {
      "rule": "size_preference",
      "targets": ["headline_1"],
      "params": {"hint": "prominent"}
    },
    {
      "rule": "z_order",
      "targets": ["headline_1"],
      "params": {"hint": "above_background"}
    }
  ],
  "soft_constraints": [
    {"rule": "color_harmony", "weight": 0.9, "params": {}},
    {"rule": "visual_hierarchy", "weight": 1.0, "params": {}}
  ],
  "style_keywords": ["橙色調", "夏日", "促銷", "活潑"],
  "language": "zh-TW",
  "inferred_fields": {
    "canvas.width": true,
    "canvas.height": true,
    "elements.bg_1.semantic_type": true
  }
}"""


PROMPT_TEMPLATE = """Role: You are a professional graphic design analyst.
Your goal is to parse a user's design request and asset list,
and output a structured Design Spec JSON for downstream layout agents.

# Context
User brief: {user_brief}
Asset list (each item has asset_ref, content, or BOTH for pre-rendered text): {asset_list}
Previous feedback from Aesthetic Judge (if any): {feedback}

# Constraint extraction rules
- Descriptions with clear geometric meaning -> hard_constraints (structured object)
  Supported rules: position_preference / no_overlap / z_order / size_preference
  ATTENTION: params values must be semantic hints (e.g. "top_right"), NOT pixel coordinates.
  ATTENTION: position_preference params.hint MUST be EXACTLY one of these 9
    region values (case-sensitive): top_left | top_center | top_right |
    middle_left | center | middle_right | bottom_left | bottom_center |
    bottom_right
    Express a RELATIVE intent as the nearest region: "below the title" ->
    "bottom_center" (or "center"); "to the left of the image" -> "middle_left".
    Do NOT invent relational hints like "below_title" / "above_logo" /
    "left_of_image" -- the Quality Checker only knows the 9 fixed regions and
    will reject every candidate (hard pipeline failure) otherwise.
  For z_order, the params hint must be the string "above_background" when an element must sit above the background image.
- Style and feeling descriptions -> style_keywords list (free-form strings, e.g. "minimal", "modern")
- Soft preferences -> soft_constraints
  ATTENTION: soft_constraints[*].rule MUST be EXACTLY one of these 5 values (case-sensitive):
    visual_hierarchy | whitespace | balance | color_harmony | readability
  Do NOT invent new rule names like "minimalism" or "modern_style" -- those belong in
  style_keywords (free-form), NOT in soft_constraints (closed enum).

# Inference rules
- If canvas size is not specified, infer from context (e.g. "poster" -> 1080x1920).
- If semantic_type of an element is unclear, infer from asset content and user brief.
- semantic_type MUST be EXACTLY one of these 12 values (case-sensitive):
  title | subtitle | body_text | caption | logo | product_image |
  background_image | decorative_image | icon | cta | pricetag | other
  (Do NOT invent new values like "headline", "header", "tagline" -- use "title" or "subtitle".)
- visual_type MUST be exactly "image" or "text".
- Mark all inferred fields in inferred_fields with true.
- If feedback is provided, adjust only the inferred fields, never override explicit user requirements.

# Background color inference (canvas.background_color)
ATTENTION: A 3-element design (product + title + logo) on a bare-white canvas
plateaus at visual_coherence ~17/25 and layout_balance ~17/25 -- the bottom
third stays empty and the layout looks "floating". A solid pleasant color
fixes this without adding decorative elements.

Rule of thumb:
- If asset_list contains a background image asset, set canvas.background_asset_ref
  to that asset and leave canvas.background_color null.
- If no background image asset is supplied AND the user did NOT explicitly demand
  a pure-white / blank background, set canvas.background_color to a pleasant
  6-digit hex (e.g. "#F5E6D3", "#1B2B4A") matched to style_keywords. AVOID
  emitting "#FFFFFF" by default -- pure white is the renderer fallback and
  produces visually flat posters.
- Mark "canvas.background_color" in inferred_fields with true when you set it
  without explicit user input.

Palette suggestions (choose one entry matching style_keywords; do NOT copy
verbatim if the brief implies a different mood):
  warm / autumn / festival / 中秋 / 溫暖    -> "#F5E6D3", "#FFE4B5", "#E8B873"
  cool / minimal / tech / 簡約 / 科技       -> "#E8F1F8", "#D6E4F0", "#1B2B4A"
  vibrant / promo / energetic / 活潑 / 促銷 -> "#FFE5B4", "#FFD7E3", "#FFC4A8"
  dark / luxe / serif / 高級 / 黑金         -> "#1A1A2E", "#0F3460", "#16213E"
  nature / fresh / organic / 自然 / 清新    -> "#E8F0E3", "#D6E5C4", "#A8C99A"
- If the brief explicitly asks for white ("white background", "blank canvas",
  "minimalist white"), you MAY emit "#FFFFFF" -- but then keep
  inferred_fields["canvas.background_color"]=false because it was user-specified.

# Underlay assets (asset filename heuristic)
ATTENTION: Any asset whose `asset_ref` ends with the suffix `_underlay.png`
has been pre-classified by the pipeline as a placeable decorative shape
(low colour complexity or transparent edges, NOT a photo and NOT a full
canvas plate). These plates are designed to sit BEHIND a text or product
element and visually anchor it.

Rules for `_underlay.png` assets:
- The element you emit MUST have:
    semantic_type: "decorative_image"
    visual_type:   "image"
    asset_ref:     <the exact `_underlay.png` path from asset_list>
- Do NOT mark these as `background_image` -- the full-canvas plate is a
  separate asset (suffix `_background.png` if present).
- Do NOT mark these as `product_image` / `logo` / `icon` -- they are
  semantic-free decorative shapes by classifier construction.
- The underlay should usually be paired with the text or product element it
  visually supports. When the pairing is implied by the brief you MAY add a
  `z_order` hard_constraint with hint "above_background" on the underlay
  so the Layout Generator stacks it correctly.

# Pre-rendered text assets (asset filename heuristic, Step 80)
ATTENTION: Any asset whose `asset_ref` ends with the suffix `_text.png` is a
PRE-RENDERED TEXT layer: the designer's typography (font, colour, effects)
already baked into a transparent PNG. Its `content` field carries the text
string it displays.

Rules for `_text.png` assets:
- The element you emit MUST have:
    visual_type:   "image"      (it is placed as a bitmap, never re-typeset)
    asset_ref:     <the exact `_text.png` path from asset_list>
    content:       <the exact text string provided with the asset>
    semantic_type: judged from the CONTENT string -- "title" for the main
                   heading, "subtitle" / "body_text" / "cta" / "caption" as
                   appropriate (same judgement as for plain text snippets).
- Do NOT additionally emit a plain text element for the same string -- the
  bitmap IS the text.
- Do NOT mark these as `decorative_image` / `product_image` -- they carry the
  design's message.

# Format example
{format_example}

# Instruction
ATTENTION: Do NOT output any geometry (left/top/width/height/angle/z_index).
ATTENTION: Do NOT output importance, text_hints, or image_hints -- these belong to Agent 2.
ATTENTION: embedding_key must always be null.
ATTENTION: hard_constraints params must use semantic hints, not pixel values.
Output carefully referenced "format example" in JSON format, nothing else.
"""


MAX_RETRIES: int = 3


def inject_photo_size_prior(spec: DesignSpec) -> None:
    """Step 60 (2026-06-11): GT-calibrated photo size floor as spec INPUT.

    Designer GT photos have area_ratio p50 = 0.213 while Generator candidates
    cluster at 0.083-0.111 ("size timidity", Step 58); the descriptive prompt
    hint alone moved nothing (Step 60 smoke). This appends a hard constraint
    `size_preference: photo-prominent` (lower bound 0.20, see
    quality_checker.SIZE_HINT_LOWER_BOUND) for every product_image element,
    so the floor rides the strongest prompt channel ("Strictly obey all
    hard_constraints") AND is QC-enforced. Runs deterministically after the
    Analyst LLM call -- every pipeline driver gets it for free. Photos the
    Analyst already covered with its own size_preference are left alone.
    """
    from metagpt.ext.agentlayout.schema import (
        HardConstraint,
        HardConstraintRule,
        SemanticType,
    )

    covered = {
        tid
        for hc in spec.hard_constraints
        if hc.rule == HardConstraintRule.SIZE_PREFERENCE
        for tid in hc.targets
    }
    photo_ids = [
        el.id
        for el in spec.elements
        if el.semantic_type == SemanticType.PRODUCT_IMAGE and el.id not in covered
    ]
    if photo_ids:
        spec.hard_constraints.append(
            HardConstraint(
                rule=HardConstraintRule.SIZE_PREFERENCE,
                targets=photo_ids,
                params={"hint": "photo-prominent"},
            )
        )


# ============================================================
# Action
# ============================================================


class AnalyzeBrief(Action):
    """Agent 1 -- turn user brief + raw assets into a DesignSpec."""

    name: str = "AnalyzeBrief"
    desc: str = (
        "Parse the user's design request and asset list into a structured "
        "Design Spec JSON for downstream layout agents."
    )

    async def run(
        self,
        *,
        user_brief: str,
        asset_list: List[AssetInput],
        feedback: Optional[AestheticFeedback] = None,
    ) -> DesignSpec:
        """Build the prompt, call the LLM, parse and validate the response.

        Retries up to ``MAX_RETRIES`` times if the LLM output cannot be
        parsed as a valid ``DesignSpec``. Each retry re-prompts the LLM
        with the same prompt; future versions may inject the previous
        validation error to make retries error-aware.
        """
        prompt = self._build_prompt(user_brief, asset_list, feedback)

        last_err: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            rsp = await self.llm.aask(prompt)
            try:
                spec = self._parse_response(rsp)
                inject_photo_size_prior(spec)
                return spec
            except (ValueError, ValidationError) as err:
                last_err = err
                logger.warning(
                    f"AnalyzeBrief attempt {attempt}/{MAX_RETRIES} failed: {err}"
                )

        raise ValueError(
            f"AnalyzeBrief: could not produce a valid DesignSpec after "
            f"{MAX_RETRIES} attempts. Last error: {last_err}"
        )

    def _build_prompt(
        self,
        user_brief: str,
        asset_list: List[AssetInput],
        feedback: Optional[AestheticFeedback],
    ) -> str:
        """Render PROMPT_TEMPLATE with runtime substitutions."""
        asset_payload = [
            a.model_dump(exclude_none=True, exclude_defaults=False) for a in asset_list
        ]
        feedback_str = (
            "None"
            if feedback is None
            else json.dumps(feedback.model_dump(), indent=2, ensure_ascii=False)
        )
        return PROMPT_TEMPLATE.format(
            user_brief=user_brief,
            asset_list=json.dumps(asset_payload, indent=2, ensure_ascii=False),
            feedback=feedback_str,
            format_example=FORMAT_EXAMPLE_JSON,
        )

    @staticmethod
    def _parse_response(rsp: str) -> DesignSpec:
        """Strip markdown fences if present, then validate against DesignSpec."""
        text = rsp.strip()
        if "```" in text:
            try:
                text = CodeParser.parse_code(text=text, lang="json") or text
            except Exception:
                pass
        return DesignSpec.model_validate_json(text)
