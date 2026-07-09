"""Composition Director -- imagine the layout BEFORE any pixel is placed.

"先想再畫" refactor (2026-06-25). The old monolithic ``GenerateLayout`` decided
the composition *and* emitted coordinates in one 400-line-prompt call. A 20-sample
demo proved its five "distinct candidates" were the same centred template shifted
on the y-axis (center_x == canvas_width / 2 for every text element). Root cause:
20+ hard constraints crammed into one prompt drove the LLM into survival mode --
the only layout that never trips a constraint is "centre everything".

This Action takes over the *thinking* half of that work. It outputs ONLY natural
language: 3 fundamentally different composition concepts, no bbox, no JSON layout.
Each concept is then handed to ``GenerateLayout`` (now the CoordinateMapper) which
turns a single concept into one candidate under a short, low-temperature prompt.

It deliberately replaces the Step 62 ``ComposeSketch``: that action picked a
template from a GT-calibrated menu and stored a structured ``CompositionDirective``
that became *yet another* constraint on the Generator prompt. The lesson there was
that menu-picking still routes the creative decision through the constraint-laden
Generator. Here the Director creates freely and the directive IS the concept.
"""

from __future__ import annotations

import json
from typing import List, Optional

from pydantic import ValidationError

from metagpt.actions import Action
from metagpt.ext.agentlayout.schema import (
    AestheticFeedback,
    BackgroundAnalysis,
    CompositionConcept,
    ConceptBatch,
    DesignSpec,
    SemanticType,
)
from metagpt.logs import logger
from metagpt.utils.common import CodeParser

MAX_RETRIES: int = 3
TARGET_CONCEPTS: int = 3

# Step 81 (2026-07-03): GT-calibrated text hierarchy. Computed over the 1,746
# cached Crello designer layouts with >= 2 text elements ("title" = the
# largest-area text). The Step 80 smoke showed the last remaining blind gap is
# text ARRANGEMENT (typography axis judged 0/9 even with pixel-identical
# fonts); these stats ground the Director's hierarchy instincts in data.
TEXT_HIERARCHY_GT = {
    "title_cy_p25": 0.349,   # dominant text center-y, fraction of canvas height
    "title_cy_p50": 0.475,
    "title_cy_p75": 0.570,
    "title_above_rate": 0.662,   # dominant text sits above supporting text
    "info_on_underlay_rate": 0.524,  # supporting text >= 50% on an underlay
}

# High temperature is the whole point: we WANT divergent ideas here, unlike the
# coordinate stage which wants deterministic JSON. Kept as a module constant so a
# live experiment can sweep it without touching the call sites.
COMPOSE_TEMPERATURE: float = 0.9


PROMPT_TEMPLATE = """Role: You are the art director of a poster design studio.
You are looking at a blank {canvas_width}x{canvas_height} canvas with a background
image attached (trust the image over any text description). You decide WHERE things
go -- not exact pixels, just the spatial concept a junior designer will execute.

# Elements to place
{element_list}

# Style direction
{style_keywords}
{underlay_block}
# Your task
Describe {n} fundamentally DIFFERENT composition concepts. For each, think like a
designer about: where the focal element sits, where the text group sits, how the
eye flows, how whitespace breathes, and the typography mood.

RULES:
- The {n} concepts MUST be spatially different: different quadrants, different
  alignments, different text-photo relationships. Do NOT give {n} variations of
  "everything centred".
- TEXT HIERARCHY (from 1,746 designer layouts): the DOMINANT text (the title)
  usually anchors the upper-middle band of the canvas and sits ABOVE the
  supporting text in 2 of 3 designs -- give it the most prominent spot your
  concept allows, and do not bury it below minor info lines. About half of all
  supporting/info text rides ON an underlay panel when one exists: prefer
  assigning info lines (not necessarily the title) to panels. Order
  text_assignments in semantic reading order: title first, then
  subtitle / body / CTA following your visual flow.
- At least 1 concept MUST be ASYMMETRIC: the text group's horizontal centre is
  clearly off the canvas midline (e.g. right-aligned column, left bleed).
- Think about the FEELING of the layout, not coordinates. Never output numbers.
- It is good to let text ride ON the photo when an underlay or strong contrast
  protects readability -- designers do this constantly.
- In text_assignments, assign EVERY text element id to a destination: use
  "panel N" when a pre-placed underlay panel (listed above, if any) fits the
  concept, otherwise a 3x3 region word like "bottom-left" or "middle-center".

# Output (JSON only, no commentary, no markdown fences)
A JSON array of exactly {n} objects, each with these keys:
[
  {{
    "name": "2-4 word concept name",
    "focal_element": "<one element id from the list above>",
    "focal_placement": "where the focal element goes (natural language)",
    "text_placement": "where the text group goes (natural language)",
    "visual_flow": "how the eye moves across the design",
    "whitespace": "whitespace / breathing-room strategy",
    "typography_mood": "font and colour direction",
    "text_photo_relation": "beside | overlay | above | below | mixed",
    "text_assignments": {{"<text element id>": "panel N | <row>-<col> region"}}
  }}
]
"""


class ComposeConcept(Action):
    """Agent 2.5 -- Composition Director (DesignSpec -> ConceptBatch).

    Output is purely natural-language composition concepts. No coordinates leave
    this Action; the CoordinateMapper (GenerateLayout) does the pixel work.
    """

    name: str = "ComposeConcept"
    desc: str = (
        "Imagine 3 spatially diverse composition concepts in natural language, "
        "before any pixel-level layout. Replaces the Step 62 template-menu sketch."
    )

    async def run(
        self,
        *,
        spec: DesignSpec,
        bg: Optional[BackgroundAnalysis] = None,
        n: int = TARGET_CONCEPTS,
        feedback: Optional["AestheticFeedback"] = None,
        prev_concepts: Optional[ConceptBatch] = None,
    ) -> ConceptBatch:
        """Build the prompt, call the LLM (with the background image if vision is
        available), and parse into a ``ConceptBatch``.

        Step 84: ``feedback`` + ``prev_concepts`` close the design-reject loop.
        The Step 83 trace showed rejected rounds regenerating near-identical
        concepts because the Director re-imagined BLIND -- the judge's concrete
        criticisms were discarded on the CompositionDirector routing path. Now
        the rejected concept and the judge's reasons enter the prompt and the
        Director must revise against them.

        Never raises on a bad LLM response: after ``MAX_RETRIES`` parse failures it
        falls back to a single safe concept so the pipeline can still proceed.
        """
        prompt = self._build_prompt(spec, n, bg, feedback, prev_concepts)

        images: List[str] = []
        if self.llm.support_image_input():
            # Reuse the Generator's background-render helper verbatim so the two
            # stages literally see the same downscaled PNG. (Same import pattern
            # the old ComposeSketch used.)
            from metagpt.ext.agentlayout.actions.generate_layout import GenerateLayout

            bg_b64 = GenerateLayout._render_bg_image(spec)
            if bg_b64 is not None:
                images = [bg_b64]

        last_err: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            rsp = await self._aask(prompt, images)
            try:
                batch = self._parse(rsp, spec)
                logger.info(
                    f"ComposeConcept: parsed {len(batch.concepts)} concept(s) "
                    f"on attempt {attempt}/{MAX_RETRIES}."
                )
                return batch
            except (ValueError, ValidationError) as err:
                last_err = err
                logger.warning(
                    f"ComposeConcept attempt {attempt}/{MAX_RETRIES} failed: {err}"
                )

        logger.warning(
            f"ComposeConcept: falling back to a single safe centred concept after "
            f"{MAX_RETRIES} failures ({last_err})."
        )
        return ConceptBatch(concepts=[self._fallback_concept(spec)])

    async def _aask(self, prompt: str, images: List[str]) -> str:
        """Single LLM call. Tries to pass the high compose temperature; degrades
        to a plain call if the provider's ``aask`` does not accept it."""
        try:
            if images:
                return await self.llm.aask(prompt, images=images, temperature=COMPOSE_TEMPERATURE)
            return await self.llm.aask(prompt, temperature=COMPOSE_TEMPERATURE)
        except TypeError:
            # Some providers' aask() signature has no temperature kwarg.
            if images:
                return await self.llm.aask(prompt, images=images)
            return await self.llm.aask(prompt)

    @staticmethod
    def _format_rejection_block(
        feedback: Optional["AestheticFeedback"],
        prev_concepts: Optional[ConceptBatch],
    ) -> str:
        """Step 84: show the Director WHAT was rejected and WHY.

        Without this the design-reject path regenerated the same concept every
        round (Step 83 trace R0-R3). The instruction asks for a REVISION that
        addresses each criticism -- not a random different concept, and not the
        same one again.
        """
        if feedback is None and prev_concepts is None:
            return ""
        parts = ["\n# Your previous concept was REJECTED"]
        if prev_concepts is not None and prev_concepts.concepts:
            prev = prev_concepts.concepts[0]
            parts.append(
                f"Rejected concept: '{prev.name}' -- focal: {prev.focal_placement}; "
                f"text: {prev.text_placement}; assignments: "
                f"{json.dumps(prev.text_assignments, ensure_ascii=False)}"
            )
        if feedback is not None:
            if feedback.common_issues:
                parts.append(f"Judge's overall issue: {feedback.common_issues}")
            for s in feedback.suggestions[:4]:
                parts.append(f"- {s}")
            for obs in feedback.visual_observations[:4]:
                parts.append(f"- [{obs.kind.value}] {obs.target_id}: {obs.note}")
            # Step 88: ledger targets are LOCKED -- the revised concept must
            # assign these elements consistently with them, or the mapper's
            # override will contradict the concept and waste the round.
            locked = [o for o in feedback.visual_observations if o.target_bbox]
            if locked:
                parts.append(
                    "LEDGER CONSTRAINTS (locked targets -- your revised concept's "
                    "text_assignments MUST place these elements consistently):"
                )
                for obs in locked[:4]:
                    parts.append(
                        f"  - {obs.target_id} must end up INSIDE bbox {obs.target_bbox}"
                    )
        parts.append(
            "Produce a REVISED concept that directly addresses EACH criticism "
            "above. Keep what was not criticised. Do NOT resubmit the same "
            "spatial arrangement under a new name."
        )
        return "\n".join(parts) + "\n"

    @staticmethod
    def _build_prompt(
        spec: DesignSpec,
        n: int,
        bg: Optional[BackgroundAnalysis] = None,
        feedback: Optional["AestheticFeedback"] = None,
        prev_concepts: Optional[ConceptBatch] = None,
    ) -> str:
        element_lines = []
        for el in spec.elements:
            if el.semantic_type == SemanticType.BACKGROUND_IMAGE:
                # The background is the attached image, not a placeable element.
                continue
            desc = f"- {el.id} ({el.semantic_type.value}/{el.visual_type.value})"
            if el.content:
                preview = el.content.strip().replace("\n", " ")
                desc += f': "{preview[:60]}" (~{len(el.content)} chars)'
            element_lines.append(desc)
        element_list = "\n".join(element_lines) if element_lines else "(no foreground elements)"
        style = ", ".join(spec.style_keywords) if spec.style_keywords else "(none specified)"
        return PROMPT_TEMPLATE.format(
            canvas_width=spec.canvas.width,
            canvas_height=spec.canvas.height,
            element_list=element_list,
            style_keywords=style,
            underlay_block=ComposeConcept._format_underlay_block(spec, bg),
            n=n,
        ) + ComposeConcept._format_rejection_block(feedback, prev_concepts)

    @staticmethod
    def _region_position_words(bbox: List[int], cw: int, ch: int) -> str:
        """Human words for a bbox centre on a 3x3 grid, e.g. 'middle-left'.

        The Director is told to never output numbers, so its input describes
        panel positions in the same natural language it thinks in; the exact
        bbox goes to the CoordinateMapper instead (Step 76 dual-form design).
        """
        cx = (bbox[0] + bbox[2]) / 2.0 / max(1, cw)
        cy = (bbox[1] + bbox[3]) / 2.0 / max(1, ch)
        col = "left" if cx < 1 / 3 else ("center" if cx < 2 / 3 else "right")
        row = "top" if cy < 1 / 3 else ("middle" if cy < 2 / 3 else "bottom")
        return f"{row}-{col}"

    @staticmethod
    def _format_underlay_block(spec: DesignSpec, bg: Optional[BackgroundAnalysis]) -> str:
        """Step 76 feed-forward: describe baked underlay panels in words only.

        Empty string when the SEGA preprocessor did not run, keeping the prompt
        byte-identical to the pre-Step-76 shape for non-Crello briefs. Panels
        are framed as invitations, not hard rules -- Step 62 showed that
        stacking a second binding constraint on the creative stage pushes the
        model back into survival mode.
        """
        if bg is None or not bg.underlay_regions:
            return ""
        cw, ch = spec.canvas.width, spec.canvas.height
        lines = []
        for i, region in enumerate(bg.underlay_regions, 1):
            w_pct = round(100.0 * (region.bbox[2] - region.bbox[0]) / max(1, cw))
            h_pct = round(100.0 * (region.bbox[3] - region.bbox[1]) / max(1, ch))
            pos = ComposeConcept._region_position_words(region.bbox, cw, ch)
            if region.panel_type == "frame":
                # Step 79: an outlined transparent box -- the backdrop behind
                # text is the background showing through, not a plate fill.
                desc = (
                    f"- panel {i}: a transparent outlined frame at the {pos} of "
                    f"the canvas (the background shows through it, backdrop "
                    f"~{region.dominant_color}), spanning about {w_pct}% of its "
                    f"width and {h_pct}% of its height"
                )
            else:
                desc = (
                    f"- panel {i}: a {region.dominant_color} panel at the {pos} "
                    f"of the canvas, spanning about {w_pct}% of its width and "
                    f"{h_pct}% of its height"
                )
            lines.append(
                f"{desc}; {region.recommended_text_color} text reads well on "
                f"it. Reference it as 'panel {i}' in text_assignments."
            )
        panels = "\n".join(lines)
        return (
            "\n# Pre-placed underlay panels\n"
            "The background image ALREADY contains solid panel(s) the original\n"
            "designer put there to hold text -- they are part of the attached\n"
            "image, not elements you control:\n"
            f"{panels}\n"
            "Treat these panels as strong invitations: at least one concept\n"
            "should anchor its text group ON a panel, in a colour that\n"
            "contrasts with the panel fill.\n"
        )

    @staticmethod
    def _parse(rsp: str, spec: DesignSpec) -> ConceptBatch:
        """Strip optional markdown fences, JSON-parse a list of concept objects,
        and validate against ``ConceptBatch``."""
        text = (rsp or "").strip()
        if "```" in text:
            try:
                text = CodeParser.parse_code(text=text, lang="json") or text
            except Exception:
                pass
        data = json.loads(text)
        if isinstance(data, dict):
            # Tolerate {"concepts": [...]} as well as a bare array.
            data = data.get("concepts", data)
        if not isinstance(data, list) or not data:
            raise ValueError(
                f"ComposeConcept expected a non-empty JSON array; got {type(data).__name__}"
            )
        # ConceptBatch enforces 1..5; clamp defensively so an over-eager model that
        # returns 6 ideas does not hard-fail the whole parse.
        return ConceptBatch(concepts=[CompositionConcept(**obj) for obj in data[:5]])

    @staticmethod
    def _fallback_concept(spec: DesignSpec) -> CompositionConcept:
        """A guaranteed-valid, deliberately conservative concept (centred symmetry,
        text below the focal image). Only used when every LLM attempt fails."""
        focal = next(
            (e.id for e in spec.elements if e.semantic_type == SemanticType.PRODUCT_IMAGE),
            None,
        )
        if focal is None:
            focal = next(
                (e.id for e in spec.elements if e.semantic_type != SemanticType.BACKGROUND_IMAGE),
                spec.elements[0].id if spec.elements else "element_1",
            )
        return CompositionConcept(
            name="Centred safe",
            focal_element=focal,
            focal_placement="centred in the upper half of the canvas",
            text_placement="centred below the focal element",
            visual_flow="top-to-bottom: focal image then text stack",
            whitespace="even margins on all sides",
            typography_mood="clean, legible, high contrast against the background",
            text_photo_relation="below",
        )
