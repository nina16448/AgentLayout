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

    Exactly one of ``asset_ref`` (image file path) or ``content`` (text string)
    must be set. The CLIP preprocessor independently produces an embedding key
    for each asset; that key is *not* surfaced to the Analyst — Analyst always
    outputs ``embedding_key: null`` per the spec.
    """

    asset_ref: Optional[str] = None
    content: Optional[str] = None

    @model_validator(mode="after")
    def _exactly_one_payload(self) -> "AssetInput":
        has_ref = self.asset_ref is not None and self.asset_ref != ""
        has_content = self.content is not None and self.content != ""
        if has_ref == has_content:
            raise ValueError(
                "AssetInput must set exactly one of 'asset_ref' or 'content'."
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
    "background_embedding_key": null
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
Asset list (each item has asset_ref or content, and embedding_key): {asset_list}
Previous feedback from Aesthetic Judge (if any): {feedback}

# Constraint extraction rules
- Descriptions with clear geometric meaning -> hard_constraints (structured object)
  Supported rules: position_preference / no_overlap / z_order / size_preference
  ATTENTION: params values must be semantic hints (e.g. "top_right"), NOT pixel coordinates.
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
                return self._parse_response(rsp)
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
