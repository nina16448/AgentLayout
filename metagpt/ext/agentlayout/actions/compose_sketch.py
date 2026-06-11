"""Composition Director -- pick a sketch-level composition before layout.

Step 62 (2026-06-12). Step 61 quantified a sketch-level gap between designer
ground truths and Generator candidates (photo large+bleed 45% vs 0%;
text-on-photo 43% vs 4%): the Generator decides thumbnail composition and
pixel detail in a single call and systematically picks amateur sketches.
This Action splits the work the way a human studio does -- an art director
chooses the coarse composition (which GT-calibrated template), then the
Layout Generator executes it under hard numeric bounds.
"""

from __future__ import annotations

import json
from typing import Optional

from pydantic import ValidationError

from metagpt.actions import Action
from metagpt.ext.agentlayout.schema import (
    BackgroundAnalysis,
    CompositionDirective,
    DesignSpec,
    SemanticType,
)
from metagpt.ext.agentlayout.tools.composition_templates import (
    SIZE_BUCKET_RANGES,
    aspect_bucket,
    template_by_id,
    template_menu,
)
from metagpt.logs import logger
from metagpt.utils.common import CodeParser

MAX_RETRIES: int = 3

PROMPT_TEMPLATE = """Role: You are the art director of a design studio.
Before any pixel-level layout happens, you decide the COARSE COMPOSITION of
the poster: where the focal photo goes (3x3 grid cell), how large it is
(area-ratio bucket), where the text mass goes, and whether text sits ON the
photo or beside/under it. You choose ONE template from the menu below.

# Canvas
width={canvas_width}px height={canvas_height}px aspect={aspect}

# Elements to place
{elements}

# Background safe zones (coarse saliency summary; trust the attached image over the numbers)
{safe_zones}

# Template menu (calibrated on 1,902 designer layouts; gt_share = how often
# designers use it, relation_prior = how often this photo-text relation is
# used on a {aspect} canvas)
{menu}

# Decision rules
- Default to the highest-prior template unless the background image or the
  element mix argues otherwise (e.g. the background already contains a focal
  subject occupying the center -> prefer a side column template).
- "text-on-photo" templates are the designer norm (43% of ground truths);
  do NOT avoid them out of caution -- the pipeline will protect text
  readability with underlays.
- photo_size override: you may override the template's photo size bucket
  with one of small | medium | large | bleed when the asset mix demands it,
  but remember designers use large or bleed 45% of the time.

# Output (JSON only, no commentary)
{{"template_id": "<one id from the menu>", "photo_size": "<bucket or null>",
  "rationale": "<one sentence>"}}
"""


class ComposeSketch(Action):
    """Agent 2.5 -- Composition Director (DesignSpec -> CompositionDirective)."""

    name: str = "ComposeSketch"
    desc: str = (
        "Pick the sketch-level composition template (photo cell + size, "
        "text-mass cell, photo-text relation) before pixel layout."
    )

    async def run(
        self,
        *,
        spec: DesignSpec,
        bg: Optional[BackgroundAnalysis] = None,
    ) -> CompositionDirective:
        """Choose a template, store it on ``spec.composition`` and return it.

        Falls back to the highest-prior menu template when the LLM output
        cannot be parsed after ``MAX_RETRIES`` attempts -- the pipeline must
        never die at the sketch stage.
        """
        has_photo = any(
            el.semantic_type == SemanticType.PRODUCT_IMAGE for el in spec.elements
        )
        aspect = aspect_bucket(spec.canvas.width, spec.canvas.height)
        menu = template_menu(has_photo, aspect)
        prompt = self._build_prompt(spec, bg, aspect, menu)

        images = []
        if self.llm.support_image_input():
            from metagpt.ext.agentlayout.actions.generate_layout import GenerateLayout

            bg_b64 = GenerateLayout._render_bg_image(spec)
            if bg_b64 is not None:
                images = [bg_b64]

        directive: Optional[CompositionDirective] = None
        last_err: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            rsp = await self.llm.aask(prompt, images=images) if images else await self.llm.aask(prompt)
            try:
                directive = self._parse_response(rsp, menu)
                break
            except (ValueError, ValidationError) as err:
                last_err = err
                logger.warning(
                    f"ComposeSketch attempt {attempt}/{MAX_RETRIES} failed: {err}"
                )
        if directive is None:
            top = menu[0]
            logger.warning(
                f"ComposeSketch: falling back to top-prior template "
                f"'{top['id']}' after {MAX_RETRIES} failures ({last_err})"
            )
            directive = self._directive_from_template(top, photo_size=None, rationale="fallback: highest GT prior")

        spec.composition = directive
        return directive

    @staticmethod
    def _build_prompt(
        spec: DesignSpec,
        bg: Optional[BackgroundAnalysis],
        aspect: str,
        menu: list,
    ) -> str:
        elements = "\n".join(
            f"- {el.id} ({el.semantic_type.value}/{el.visual_type.value})"
            + (f': "{el.content[:60]}"' if el.content else "")
            for el in spec.elements
        )
        safe_zones = (
            json.dumps([sz.model_dump() for sz in bg.safe_zones], ensure_ascii=False)
            if bg is not None and bg.safe_zones
            else "None"
        )
        menu_str = json.dumps(menu, indent=2, ensure_ascii=False)
        return PROMPT_TEMPLATE.format(
            canvas_width=spec.canvas.width,
            canvas_height=spec.canvas.height,
            aspect=aspect,
            elements=elements,
            safe_zones=safe_zones,
            menu=menu_str,
        )

    @classmethod
    def _parse_response(cls, rsp: str, menu: list) -> CompositionDirective:
        text = rsp.strip()
        if "```" in text:
            try:
                text = CodeParser.parse_code(text=text, lang="json") or text
            except Exception:
                pass
        data = json.loads(text)
        template_id = data.get("template_id")
        allowed = {tpl["id"] for tpl in menu}
        if template_id not in allowed:
            raise ValueError(
                f"template_id {template_id!r} is not on the menu {sorted(allowed)}"
            )
        tpl = template_by_id(template_id)
        photo_size = data.get("photo_size")
        if photo_size in (None, "null", ""):
            photo_size = None
        elif photo_size not in SIZE_BUCKET_RANGES:
            raise ValueError(
                f"photo_size {photo_size!r} not in {sorted(SIZE_BUCKET_RANGES)}"
            )
        if tpl["photo_size"] is None:
            photo_size = None  # text-only template: no photo to size
        return cls._directive_from_template(tpl, photo_size, data.get("rationale"))

    @staticmethod
    def _directive_from_template(
        tpl: dict, photo_size: Optional[str], rationale: Optional[str]
    ) -> CompositionDirective:
        return CompositionDirective(
            template_id=tpl["id"],
            relation=tpl["relation"],
            photo_cell=tpl["photo_cell"],
            photo_size=photo_size or tpl["photo_size"],
            text_cell=tpl["text_cell"],
            rationale=rationale,
        )
