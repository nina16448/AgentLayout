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
import re
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import base64

from PIL import Image
from pydantic import ValidationError

from metagpt.actions import Action
from metagpt.ext.agentlayout.schema import (
    AestheticFeedback,
    BackgroundAnalysis,
    CandidatesBatch,
    CompositionConcept,
    DesignSpec,
    LayoutTree,
    SemanticType,
    VisualType,
)
from metagpt.logs import logger
from metagpt.utils.common import CodeParser

# Step 46 (2026-06-10): the Generator now attaches the canvas background to
# its LLM call so it can SEE the focal subject and empty space. We cap the
# longest edge at this size to keep per-call cost in check while preserving
# enough detail for the model to identify negative-space regions.
_BG_MAX_EDGE_PX: int = 768

# Step 60 (2026-06-11): GT-calibrated photo size prior. Calibration over all
# 1,902 locally cached Crello designer layouts (2,374 non-background photo
# elements, clipped-to-canvas area / canvas area; see
# layout_agent/output/step60_area_ratio_calibration.py). Candidate photos
# cluster at 0.111 (= 1/3 x 1/3 canvas) -- the "size timidity" failure mode
# identified in Step 58. The target range is GT p50..p75, deliberately NOT
# p90, to limit collisions with the safe-zone rule (Step 58: safe-zone gate
# kills oversized GT-style solutions).
PHOTO_AREA_GT = {"p25": 0.063, "p50": 0.213, "p75": 0.445, "p90": 0.619}
PHOTO_AREA_TARGET = (0.20, 0.45)

# Step 76c (2026-07-02): GT-calibrated TEXT union-coverage prior. Computed over
# all 1,902 cached Crello designer layouts (text elements only, same
# _union_coverage_ratio math as the Step 57 QC guardrail). The Step 76 A/B
# run showed the old size-timidity resurfacing on the text axis: 96% of
# SEGA-arm rounds fell below QC while designer text-only layouts pass 16/19.
TEXT_AREA_GT = {"p25": 0.103, "p50": 0.152, "p75": 0.213, "p90": 0.289}
# Target is GT p25-p75 shifted slightly up: the bias being corrected is
# downward (candidates cluster small), so aiming at the designer median-to-
# upper range lands them inside the distribution, not at its floor.
TEXT_AREA_TARGET = (0.12, 0.25)


# ============================================================
# Prompt template (CoordinateMapper -- "先想再畫" refactor 2026-06-25)
# ============================================================


# "先想再畫" refactor (2026-06-25): the CoordinateMapper emits exactly ONE
# candidate per call (one per composition concept). The example is deliberately
# ASYMMETRIC -- product image on the left half, text right-aligned in the right
# third -- so the model does not imitate the old "everything centred" pattern.
# The previous two-candidate example had every headline's left at
# (540 - width/2), i.e. perfectly centred; the LLM copied that bias.
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
          "id": "product_image_1",
          "left": 0, "top": 200, "width": 650, "height": 800,
          "angle": 0, "z_index": 2
        },
        {
          "id": "headline_1",
          "left": 680, "top": 300, "width": 360, "height": 200,
          "angle": 0, "z_index": 3,
          "font_family": "display",
          "font_size": 72,
          "font_weight": "bold",
          "color": "#1B3A6B",
          "text_align": "right"
        }
      ]
    }
  ]
}"""


PROMPT_TEMPLATE = """Role: You are a layout technician. An art director has already decided the
composition concept (below). Your job is to translate that ONE concept into
exact pixel coordinates for every element -- faithfully, not creatively. Do NOT
invent a different composition; realise the art director's intent.

# Composition concept from the art director
{concept_block}

# Canvas
{canvas_width} x {canvas_height} px. Background color: {bg_color}.

# Elements to place
{element_list}

# Background analysis
Safe zones (calm, low-saliency regions -- PREFER text here, but this is a
preference, not a hard rule; follow the art director's concept first):
{safe_zones}
Dominant background palette: {dominant_palette}
Recommended text color for contrast: {recommended_text_color}
Baked underlay panels (Step 76 preprocessing -- solid panels the original
designer placed to hold text, ALREADY part of the background image):
{underlay_panels}

# Rules (only the essentials -- everything else is the art director's call)
1. Coordinates stay on canvas: left >= 0, top >= 0, left+width <= {canvas_width},
   top+height <= {canvas_height}.
2. Every element id from the list above must appear exactly once.
3. Text elements MUST include font_family, font_size, font_weight, color,
   text_align.
4. A decorative underlay that protects a text element must have a LOWER z_index
   than that text (photo < underlay < text).
5. The title's area must be >= 2.5% of the canvas area (it is the focal text).
6. Text color must contrast with whatever sits behind it (WCAG AA, ratio >=
   4.5). If text rides a busy photo, either put a decorative underlay behind it
   or choose a high-contrast color.
7. Elements with a natural reading order (title -> subtitle -> body -> CTA)
   should flow top to bottom: an earlier element's top < a later element's top.
   GT calibration (N=1,746 designer layouts): the DOMINANT text's center-y
   falls in [0.35, 0.57] of canvas height (median 0.475) and it sits ABOVE
   the supporting text in 66% of designs -- do not bury the title below
   minor info lines.

# Typography
font_family must be one of: display, serif, sans-serif, script.
Size text by role: the title is the largest, body text the smallest. Match the
art director's typography_mood.

# GT-calibrated text size prior
{text_area_prior}

{feedback_block}

# How to respond
First write 2-3 short sentences describing how you will turn THIS concept into
coordinates -- which element goes where, and how the concept's asymmetry / flow
is preserved. Then output exactly ONE candidate as JSON inside a ```json fenced
block, following this shape exactly (one candidate, real element ids):

{format_example}"""


MAX_RETRIES: int = 3

# Step 64 (2026-06-12): vision-channel safety refusals ("I'm sorry, I can't
# assist with that.") have been background noise in every live run since
# step58 (74/80/59/118/140 lines) and killed whole samples in step63. They
# only occur on this action's long prompt + photographic backgrounds
# (ComposeSketch and the Judge attach the same images and never refuse), and
# resending the identical payload usually refuses again. Detection is
# deliberately conservative: it only runs AFTER JSON parsing failed (a valid
# batch can never be misclassified), and only the first _REFUSAL_MAX_LEN
# characters are scanned -- refusals open with the apology, while candidate
# text content sits deep inside thousands of characters of JSON.
_REFUSAL_MARKERS: Tuple[str, ...] = (
    "i'm sorry",
    "i am sorry",
    "i can't assist",
    "i cannot assist",
    "can't help with",
    "cannot help with",
    # Step 65 smoke: gpt-4o also refuses with "I'm unable to assist with
    # this request." and long "I'm unable to provide ..." narratives --
    # undetected, they burned full retry budgets 5x/N=5.
    "i'm unable to",
    "i am unable to",
    "unable to assist",
    "unable to help",
)
_REFUSAL_MAX_LEN: int = 200

# Step 65 (2026-06-12): close the visual feedback loop. Step 59 proved the
# Generator ignores even explicit, executable TEXT repair instructions on
# retry; but it has never SEEN its own failed attempt -- feedback always
# arrived as JSON + prose while the render stayed on disk. This note rides
# the prompt only when the previous attempt's render is attached as the last
# image, telling the model to self-critique visually before regenerating.
_SELF_RENDER_NOTE: str = """\
ATTENTION: Step 65 (2026-06-12) -- THE LAST ATTACHED IMAGE IS YOUR OWN
           PREVIOUS ATTEMPT, ALREADY RENDERED. It is exactly what the Quality
           Checker / Judge saw when producing the `feedback` above. Before
           emitting new candidates:
             1. LOOK at that render and name its 2-3 worst visual flaws
                (text drowning in busy texture, timid undersized photo, large
                dead bands, colliding elements, illegible contrast, layout
                that ignores the composition directive).
             2. Cross-check every `feedback` item against what you SEE --
                the feedback refers to THIS image, not to an abstraction.
             3. Every new candidate must VISIBLY fix the flaws you named.
                If a new layout would render nearly identical to the attached
                attempt, it is WRONG -- do not resubmit the same geometry.
                Keep only what already looks good."""


# ============================================================
# Action
# ============================================================


class GenerateLayout(Action):
    """Agent 3 -- assign concrete pixel geometry to every element."""

    name: str = "GenerateLayout"
    desc: str = (
        "CoordinateMapper: translate ONE art-director composition concept into "
        "exact pixel coordinates for a single layout candidate."
    )

    async def run(
        self,
        *,
        spec: DesignSpec,
        tree: LayoutTree,
        bg: BackgroundAnalysis,
        concept: CompositionConcept,
        feedback: Optional[AestheticFeedback] = None,
        prev_best_layout: Optional[Dict[str, Tuple[float, float, float, float]]] = None,
        prev_best_subscores: Optional[Dict[str, int]] = None,
        exemplars: Optional[str] = None,
        revision: bool = False,
    ) -> CandidatesBatch:
        """Translate ONE art-director concept into a single coordinatised candidate.

        "先想再畫" refactor (2026-06-25): the composition decision now lives in
        ``concept`` (a CompositionConcept from ComposeConcept). This Action no
        longer invents the layout -- it faithfully maps the concept to pixels and
        returns a one-candidate batch. The pipeline calls it once per concept.

        ``feedback`` / ``prev_best_layout`` / ``prev_best_subscores`` stay for the
        typography/colour micro-adjust retry path: when the Judge's worst axis is
        typography/colour it routes feedback to the CoordinateMapper without
        asking the Director to re-imagine the composition.

        Pre-condition: ``spec`` must be enriched (Asset Analyzer ran).
        """
        spec.assert_enriched()

        # Attach the canvas background so the LLM sees the focal subject and the
        # real empty regions. Step 65's self-render channel is intentionally NOT
        # re-introduced here -- it was a negative result (doubled refusal rate).
        images: List[str] = []
        if self.llm.support_image_input():
            bg_b64 = self._render_bg_image(spec)
            if bg_b64 is not None:
                images.append(bg_b64)
            else:
                logger.debug("GenerateLayout: no usable background image; text-only call.")
        else:
            logger.debug(
                f"GenerateLayout: LLM '{getattr(self.llm, 'model', '?')}' lacks vision; text-only."
            )

        prompt = self._build_prompt(
            spec,
            tree,
            bg,
            concept,
            feedback=feedback,
            prev_best_layout=prev_best_layout,
            prev_best_subscores=prev_best_subscores,
            exemplars=exemplars,
            revision=revision,
        )

        if images:
            logger.info(f"GenerateLayout: attaching {len(images)} background image(s).")

        last_err: Optional[Exception] = None
        attempt = 0
        budget = MAX_RETRIES
        while attempt < budget:
            attempt += 1
            if images:
                rsp = await self.llm.aask(prompt, images=images)
            else:
                rsp = await self.llm.aask(prompt)
            try:
                return self._parse_response(rsp)
            except (ValueError, ValidationError) as err:
                # Refusal detection runs AFTER the parse attempt so a parseable
                # batch is never misclassified as a refusal.
                if images and self._looks_like_refusal(rsp):
                    # Informed degradation: drop the image and retry text-only
                    # (the `images` guard makes this fire at most once).
                    logger.warning(
                        f"GenerateLayout attempt {attempt}/{budget}: vision refusal "
                        f"({rsp.strip()[:60]!r}); retrying without image."
                    )
                    images = []
                    budget += 1
                    last_err = ValueError(f"vision refusal: {rsp.strip()[:120]}")
                    continue
                last_err = err
                logger.warning(f"GenerateLayout attempt {attempt}/{budget} failed: {err}")

        raise ValueError(
            f"GenerateLayout: could not produce a valid CandidatesBatch after "
            f"{attempt} attempts. Last error: {last_err}"
        )

    @staticmethod
    def _looks_like_refusal(rsp: str) -> bool:
        # Step 65: scan only the head -- long "I'm unable to provide ...,
        # however here is guidance ..." narratives are refusals too, but a
        # marker buried deep in candidate text content must not match.
        s = (rsp or "").strip().lower()[:_REFUSAL_MAX_LEN]
        if not s:
            return False
        return any(marker in s for marker in _REFUSAL_MARKERS)

    @staticmethod
    def _render_bg_image(spec: DesignSpec) -> Optional[str]:
        """Load spec.canvas.background_asset_ref, downscale, return base64 PNG.

        Returns None when no usable image exists (path missing, corrupt file,
        no background_asset_ref set). The caller falls back to text-only.
        Longest edge is capped at ``_BG_MAX_EDGE_PX`` to keep token cost low
        while preserving enough detail for the LLM to locate the focal
        subject and identify negative-space bands.
        """
        bg_ref = spec.canvas.background_asset_ref
        if not bg_ref:
            return None
        return GenerateLayout._load_image_b64(Path(bg_ref))

    @staticmethod
    def _load_image_b64(path: Path) -> Optional[str]:
        """Load any raster image, downscale to ``_BG_MAX_EDGE_PX``, base64 PNG.

        Shared by the background channel (Step 46) and the self-render
        channel (Step 65). Returns None when the file is missing or
        unreadable; callers degrade gracefully.
        """
        if not path.exists():
            return None
        try:
            img = Image.open(path).convert("RGB")
        except (OSError, IOError) as err:
            logger.warning(
                f"GenerateLayout._load_image_b64: cannot open {str(path)!r}: {err}"
            )
            return None
        w, h = img.size
        longest = max(w, h)
        if longest > _BG_MAX_EDGE_PX:
            scale = _BG_MAX_EDGE_PX / float(longest)
            new_size = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))
            img = img.resize(new_size, Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")

    def _build_prompt(
        self,
        spec: DesignSpec,
        tree: LayoutTree,
        bg: BackgroundAnalysis,
        concept: CompositionConcept,
        feedback: Optional[AestheticFeedback] = None,
        prev_best_layout: Optional[Dict[str, Tuple[float, float, float, float]]] = None,
        prev_best_subscores: Optional[Dict[str, int]] = None,
        exemplars: Optional[str] = None,
        revision: bool = False,
    ) -> str:
        """Render the lean CoordinateMapper PROMPT_TEMPLATE.

        "先想再畫" refactor: the prompt is built around ONE ``concept`` (the
        art-director's intent) plus the minimal context the coordinate stage
        needs. ``tree`` is accepted for signature compatibility with the
        pipeline but no longer injected verbatim -- the concept + element list
        carry the structure now.
        """
        safe_zones_str = json.dumps(
            [sz.model_dump() for sz in bg.safe_zones], ensure_ascii=False
        )
        palette_str = json.dumps(bg.dominant_palette, ensure_ascii=False)
        bg_color = spec.canvas.background_color or "(image background; see attached)"
        return PROMPT_TEMPLATE.format(
            concept_block=self._format_concept_block(concept, bg, feedback),
            canvas_width=spec.canvas.width,
            canvas_height=spec.canvas.height,
            bg_color=bg_color,
            element_list=self._format_element_list(spec),
            safe_zones=safe_zones_str,
            dominant_palette=palette_str,
            recommended_text_color=bg.recommended_text_color,
            underlay_panels=self._format_underlay_panels(bg),
            text_area_prior=self._format_text_area_prior(spec),
            feedback_block=self._format_feedback_block(
                feedback, prev_best_layout, prev_best_subscores, exemplars,
                revision=revision,
            ),
            format_example=FORMAT_EXAMPLE_JSON,
        )

    @staticmethod
    def _format_concept_block(
        concept: CompositionConcept,
        bg: Optional[BackgroundAnalysis] = None,
        feedback: Optional[AestheticFeedback] = None,
    ) -> str:
        """Render the art-director concept as the binding design brief.

        Step 77: when the concept carries ``text_assignments``, each one is
        rendered as a BINDING per-element destination; 'panel N' references
        resolve to that panel's exact bbox from ``bg.underlay_regions`` so the
        mapper gets concrete pixels, not prose.

        Step 88 precedence rule: an OPEN LEDGER TARGET (a judge observation
        with a target_bbox) OVERRIDES the concept's assignment for that
        element. The Step 87 trace showed the mapper obeying the concept's
        BINDING line over the ledger for three straight rounds -- the loop
        deadlocks unless the ledger outranks the concept.
        """
        block = (
            f"Name: {concept.name}\n"
            f"Focal element: {concept.focal_element} -> {concept.focal_placement}\n"
            f"Text placement: {concept.text_placement}\n"
            f"Visual flow: {concept.visual_flow}\n"
            f"Whitespace: {concept.whitespace}\n"
            f"Typography mood: {concept.typography_mood}\n"
            f"Text-photo relation: {concept.text_photo_relation}\n"
        )
        overrides = {}
        if feedback is not None:
            for obs in feedback.visual_observations:
                if obs.target_bbox is not None:
                    overrides.setdefault(obs.target_id, obs)

        def _override_line(elem_id: str) -> str:
            obs = overrides[elem_id]
            left, top, right, bottom = obs.target_bbox
            return (
                f"  - {elem_id} -> LEDGER OVERRIDE ({obs.kind.value}): place "
                f"{elem_id} INSIDE bbox [left={left}, top={top}, right={right}, "
                f"bottom={bottom}]. This target comes from the judge's review "
                f"and SUPERSEDES the concept's placement for this element."
            )

        if concept.text_assignments or overrides:
            lines = []
            regions = bg.underlay_regions if bg is not None else []
            for elem_id, dest in concept.text_assignments.items():
                if elem_id in overrides:
                    lines.append(_override_line(elem_id))
                    continue
                resolved = dest
                m = re.match(r"panel\s*(\d+)", dest.strip(), re.IGNORECASE)
                if m and 1 <= int(m.group(1)) <= len(regions):
                    region = regions[int(m.group(1)) - 1]
                    left, top, right, bottom = region.bbox
                    surface = (
                        f"transparent frame over ~{region.dominant_color} backdrop"
                        if region.panel_type == "frame"
                        else f"fill {region.dominant_color}"
                    )
                    resolved = (
                        f"{dest} = bbox [left={left}, top={top}, right={right}, "
                        f"bottom={bottom}], {surface}: place "
                        f"{elem_id} INSIDE this bbox, colour ~"
                        f"{region.recommended_text_color}"
                    )
                lines.append(f"  - {elem_id} -> {resolved}")
            for elem_id in overrides:
                if elem_id not in concept.text_assignments:
                    lines.append(_override_line(elem_id))
            header = "Text assignments (BINDING -- realise each destination exactly"
            if overrides:
                header += "; LEDGER OVERRIDE lines outrank everything else"
            block += header + "):\n" + "\n".join(lines) + "\n"
        return block + (
            "\nTranslate THIS concept into exact pixels. If the concept is "
            "asymmetric, your coordinates must be asymmetric too -- do not "
            "silently re-centre everything."
        )

    @staticmethod
    def _format_element_list(spec: DesignSpec) -> str:
        """Compact one-line-per-element listing (id, semantic/visual type, text).

        Step 80: elements backed by a pre-rendered text bitmap (asset_ref
        ``*_text.png``) get their NATURAL size appended -- the designer's own
        typography at its intended scale. The mapper should place them at that
        size (mild rescale allowed, aspect locked), which removes both the
        font-fidelity gap and the size-timidity failure in one move.
        """
        lines = []
        for el in spec.elements:
            desc = f"- {el.id} ({el.semantic_type.value}/{el.visual_type.value})"
            if el.content:
                preview = el.content.strip().replace("\n", " ")
                desc += f': "{preview[:80]}"'
            from metagpt.ext.agentlayout.tools.text_bitmap_normalizer import (
                is_r3_text_bitmap,
                r3_prompt_descriptor,
            )

            if is_r3_text_bitmap(el.asset_ref):
                desc += r3_prompt_descriptor(el.asset_ref)
            elif el.asset_ref and el.asset_ref.endswith("_text.png"):
                try:
                    with Image.open(el.asset_ref) as img:
                        nat_w, nat_h = img.size
                    desc += (
                        f" [pre-rendered text bitmap, natural size {nat_w}x{nat_h}px:"
                        f" place at this size (0.8x-1.2x rescale allowed, KEEP the"
                        f" aspect ratio); its font/colour are final -- omit"
                        f" font_family/font_size/color for this element]"
                    )
                except (OSError, IOError):
                    pass
            lines.append(desc)
        return "\n".join(lines) if lines else "(no elements)"

    @staticmethod
    def _format_text_area_prior(spec: DesignSpec) -> str:
        """Render the `# GT-calibrated text size prior` block (Step 76c).

        Anti-timidity prior for TEXT, mirroring the Step 60 photo prior that
        fixed the same bias on the photo axis. Tail-end position + concrete
        per-canvas pixel math + an executable instruction is the pattern
        Step 60 measured as effective. Returns "None" when the spec has no
        text elements so non-text briefs keep the prompt shape unchanged.
        """
        text_ids = [
            el.id for el in spec.elements if el.visual_type == VisualType.TEXT
        ]
        if not text_ids:
            return "None"
        lo, hi = TEXT_AREA_TARGET
        canvas_px = spec.canvas.width * spec.canvas.height
        lo_px = int(canvas_px * lo)
        hi_px = int(canvas_px * hi)
        id_lines = "\n".join(f"  - {tid}" for tid in text_ids)
        return (
            f"ATTENTION: designer ground truths (N=1,902 Crello layouts) place TEXT\n"
            f"so its combined union covers median {TEXT_AREA_GT['p50']:.0%} of the canvas "
            f"(p25 {TEXT_AREA_GT['p25']:.0%},\np75 {TEXT_AREA_GT['p75']:.0%}, "
            f"p90 {TEXT_AREA_GT['p90']:.0%}). Machine layouts that cluster small text\n"
            f"(union < 10%) read as TIMID and fail both QC and the design_layout axis.\n"
            f"For THIS {spec.canvas.width}x{spec.canvas.height} canvas, the text "
            f"elements below must together\ncover {lo:.0%}-{hi:.0%} of the canvas = "
            f"{lo_px:,}-{hi_px:,} px^2 total:\n"
            f"{id_lines}\n"
            f"Reach the target by ENLARGING font sizes (the title takes the largest\n"
            f"share and may exceed 10% alone), NOT by stretching boxes around tiny\n"
            f"text or overlapping text blocks."
        )

    @staticmethod
    def _format_underlay_panels(bg: BackgroundAnalysis) -> str:
        """Render the Step 76 baked-underlay feed-forward block.

        Exact pixel bboxes on purpose: the CoordinateMapper responds to
        explicit math-and-constraint instructions (Step 60), unlike the
        Director which gets the same panels described in words only.
        Returns "None." when the preprocessor did not run (non-SEGA inputs).
        """
        if not bg.underlay_regions:
            return "None."
        lines = []
        for i, region in enumerate(bg.underlay_regions, 1):
            left, top, right, bottom = region.bbox
            if region.panel_type == "frame":
                surface = (
                    f"a transparent outlined frame; the backdrop showing "
                    f"through is ~{region.dominant_color}"
                )
            else:
                surface = f"fill {region.dominant_color}"
            lines.append(
                f"- panel {i}: bbox [left={left}, top={top}, right={right}, "
                f"bottom={bottom}], {surface}. Text placed on "
                f"this panel must fit INSIDE the bbox and use a contrasting "
                f"colour (recommended {region.recommended_text_color})."
            )
        return "\n".join(lines)

    def _format_feedback_block(
        self,
        feedback: Optional[AestheticFeedback],
        prev_best_layout: Optional[Dict[str, Tuple[float, float, float, float]]],
        prev_best_subscores: Optional[Dict[str, int]],
        exemplars: Optional[str],
        revision: bool = False,
    ) -> str:
        """Render the optional retry block.

        Empty string on the cold first pass. Two modes (Step 84):
          * micro-adjust (``revision=False``): typography/colour feedback --
            keep the composition, nudge what the judge flagged.
          * revision (``revision=True``): the concept was re-imagined after a
            design reject. The previous layout is included as the REJECTED
            baseline so the mapper has a contrast reference -- the user's
            observation: without seeing the old coordinates, "fix it" has
            nothing to fix against.
        """
        if feedback is None and not prev_best_layout and not exemplars:
            return ""
        if revision:
            parts = [
                "# Previous REJECTED layout (contrast reference -- do NOT keep it)\n"
                "The concept above is a REVISION written against the judge's "
                "criticisms below. The coordinates below are the rejected "
                "attempt: your NEW layout must VISIBLY differ wherever a "
                "criticism points at it. Re-submitting near-identical geometry "
                "is a failure."
            ]
        else:
            parts = ["# Adjust the previous attempt (keep the composition; fix what the judge flagged)"]
        if feedback is not None:
            if feedback.keep_constraints:
                keep_lines = "\n".join(
                    f"  - {obs.target_id} must STAY INSIDE bbox {obs.target_bbox}"
                    f" ({obs.kind.value} -- already fixed)"
                    for obs in feedback.keep_constraints
                    if obs.target_bbox is not None
                )
                if keep_lines:
                    parts.append(
                        "KEEP constraints (already satisfied by the previous "
                        "round -- do NOT undo these while adjusting anything "
                        "else):\n" + keep_lines
                    )
            parts.append(json.dumps(
                feedback.model_dump(exclude={"keep_constraints"}),
                indent=2, ensure_ascii=False,
            ))
        prev = self._format_previous_attempt(prev_best_layout, prev_best_subscores)
        if prev != "None":
            parts.append(prev)
        if exemplars:
            parts.append(f"Reference exemplars:\n{exemplars}")
        return "\n".join(parts)

    @staticmethod
    def _format_saliency_landscape(bg: BackgroundAnalysis) -> str:
        """Render the F2 saliency block.

        Gated by ``feature_flags.f2_saliency_enabled()`` (env var
        ``AGENTLAYOUT_F2_SALIENCY``, default OFF). When disabled, returns
        "None" regardless of bg so the Generator sees the pre-F2 prompt
        shape. See feature_flags.py for the default-OFF rationale (Step
        72 N=100 net negative aesthetic impact).

        Returns "None" when the BackgroundAnalysis has no saliency data
        (solid-color stub path or pre-F2 cached pickles).
        """
        from metagpt.ext.agentlayout.feature_flags import f2_saliency_enabled

        if not f2_saliency_enabled():
            return "None (F2 saliency-aware prompting disabled; see Step 72)."
        if not bg.saliency_histogram and not bg.low_saliency_regions:
            return "None (no real background image; ignore)."

        hist = bg.saliency_histogram or []
        cells = ["TL", "TM", "TR", "ML", "MM", "MR", "BL", "BM", "BR"]
        if len(hist) == len(cells):
            hist_line = ", ".join(f"{c}={v:.2f}" for c, v in zip(cells, hist))
        else:
            hist_line = json.dumps(hist)

        regions_lines = []
        for i, sz in enumerate(bg.low_saliency_regions, 1):
            regions_lines.append(
                f"  {i}. bbox={sz.bbox} confidence={sz.confidence} "
                f"(calmness=high; prefer text here)"
            )
        regions_block = "\n".join(regions_lines) if regions_lines else "  (none)"

        return (
            "Continuous-saliency summary (higher value = busier background "
            "region; saliency-aware placement policy: prefer LOW-saliency "
            "areas for TEXT, allow HIGH-saliency for hero images).\n"
            f"3x3 grid mean (row-major): {hist_line}\n"
            f"Top-{len(bg.low_saliency_regions)} low-saliency rectangles "
            "(canvas-pixel bbox=[left,top,right,bottom]):\n"
            f"{regions_block}"
        )

    @staticmethod
    def _format_composition_directive(spec: DesignSpec) -> str:
        """Render the `# Composition directive` block with per-canvas pixel math.

        The Composition Director (Step 62) stores its template choice on
        ``spec.composition``; this turns the abstract sketch (grid cells,
        size bucket, relation) into concrete numeric bounds the LLM can obey
        and the Quality Checker re-verifies. Returns "None" when no director
        ran, keeping every pre-Step-62 caller unchanged.
        """
        from metagpt.ext.agentlayout.tools.composition_templates import (
            SIZE_BUCKET_RANGES,
            cell_bounds,
        )

        comp = spec.composition
        if comp is None:
            return "None"
        cw, ch = spec.canvas.width, spec.canvas.height
        lines = [
            f"Template '{comp.template_id}' chosen by the art director"
            + (f" -- {comp.rationale}" if comp.rationale else "")
            + ". HARD numeric contract (QC-enforced):"
        ]
        if comp.photo_cell and comp.photo_size:
            xl, yt, xr, yb = cell_bounds(comp.photo_cell, cw, ch)
            lo, hi = SIZE_BUCKET_RANGES[comp.photo_size]
            lines.append(
                f"  - focal photo center (left + width/2, top + height/2) must fall in "
                f"x in [{xl:.0f}, {xr:.0f}], y in [{yt:.0f}, {yb:.0f}] (grid cell {comp.photo_cell})."
            )
            lines.append(
                f"  - focal photo area: width*height in [{lo:.2f}, {hi:.2f}] of canvas area "
                f"= [{lo * cw * ch:,.0f} .. {hi * cw * ch:,.0f}] px^2 ('{comp.photo_size}')."
            )
            # Step 64: step63 hero candidates stalled at area 0.33, just under
            # the 'large' floor (0.45) -- abstract bounds alone do not move
            # the model far enough, but a copyable bbox does (step60 lesson:
            # concrete math beats narrative hints). Mid-bucket area, canvas
            # aspect, centered on the target cell; emitted only when the
            # clamped example still satisfies its own contract.
            mid = (lo + hi) / 2.0
            scale = mid**0.5
            ex_w = round(cw * scale)
            ex_h = round(ch * scale)
            cell_cx = (xl + xr) / 2.0
            cell_cy = (yt + yb) / 2.0
            ex_left = int(round(min(max(cell_cx - ex_w / 2.0, 0), cw - ex_w)))
            ex_top = int(round(min(max(cell_cy - ex_h / 2.0, 0), ch - ex_h)))
            ex_cx = ex_left + ex_w / 2.0
            ex_cy = ex_top + ex_h / 2.0
            if xl <= ex_cx <= xr and yt <= ex_cy <= yb:
                lines.append(
                    f"  - WORKED EXAMPLE satisfying both bounds: left={ex_left}, "
                    f"top={ex_top}, width={ex_w}, height={ex_h} (center in "
                    f"{comp.photo_cell}, area {ex_w * ex_h / (cw * ch):.2f} of "
                    f"canvas). Start from this shape; vary the rest of the "
                    f"layout, not the photo's coarse size."
                )
        if comp.text_cell:
            xl, yt, xr, yb = cell_bounds(comp.text_cell, cw, ch)
            lines.append(
                f"  - the AREA-WEIGHTED center of ALL text elements combined must fall in "
                f"x in [{xl:.0f}, {xr:.0f}], y in [{yt:.0f}, {yb:.0f}] (grid cell {comp.text_cell})."
            )
        # Step 64 third fix: the old narrative hint ("protect readability with
        # an underlay or strong contrast") never moved the model -- smoke
        # showed it parking the spec's underlay in an empty corner while text
        # rode the photo bare, failing text_on_photo_no_underlay every
        # attempt. Step 59 proved retry feedback is a dead path for underlay
        # instructions; step 60 proved generation-time concrete recipes work,
        # so the contract is spelled out here, with the spec's actual
        # decorative_image ids named.
        underlay_ids = [
            el.id
            for el in spec.elements
            if el.semantic_type == SemanticType.DECORATIVE_IMAGE
        ]
        text_on_photo_rule = (
            "  - relation 'text-on-photo': text boxes must overlap the focal photo by "
            ">= 30% of total text area. Place text ON the photo like designers do."
        )
        if underlay_ids:
            ids = ", ".join(underlay_ids)
            text_on_photo_rule += (
                f"\n  - underlay contract (QC-enforced): every text riding the photo must "
                f"sit on a decorative_image underlay covering >= 80% of that text's bbox. "
                f"This spec provides: {ids}. Recipe: give the underlay the SAME bbox as "
                f"the riding text expanded 10-20% per side, z_index between the photo and "
                f"the text (photo < underlay < text). Do NOT park the underlay in an "
                f"empty corner away from the text -- that fails QC."
            )
        else:
            text_on_photo_rule += (
                "\n  - this spec has no decorative_image underlay, so protect readability "
                "with strong text/photo contrast and keep each riding text FULLY inside "
                "the photo bbox -- text pixels hanging off the photo onto the busy "
                "background fail QC."
            )
        relation_rules = {
            "text-on-photo": text_on_photo_rule,
            "stacked": (
                f"  - relation 'stacked': the text mass and the photo center must be "
                f"vertically separated by > {ch / 6:.0f}px (1/6 canvas height), with "
                f"vertical offset dominating horizontal."
            ),
            "side-by-side": (
                f"  - relation 'side-by-side': the text mass and the photo center must be "
                f"horizontally separated by > {cw / 6:.0f}px (1/6 canvas width), with "
                f"horizontal offset dominating vertical."
            ),
            "centered-mix": (
                f"  - relation 'centered-mix': keep photo-center vs text-mass offsets "
                f"below {cw / 6:.0f}px horizontally and {ch / 6:.0f}px vertically."
            ),
        }
        if comp.relation in relation_rules:
            lines.append(relation_rules[comp.relation])
        return "\n".join(lines)

    @staticmethod
    def _format_area_hints(spec: DesignSpec) -> str:
        """Render the `# GT-calibrated photo size prior` block content.

        Targets ONLY semantic_type == product_image: calibration showed photos
        are the single size-timid class (candidate p50 0.111 vs GT p50 0.213 /
        p75 0.445), while titles are already GT-aligned and underlays/other
        text already run larger than GT -- hinting those would push the wrong
        direction. Logos and icons are deliberately excluded (they should
        stay small). Returns "None" when the spec has no photo element.
        """
        photo_ids = [
            el.id
            for el in spec.elements
            if el.semantic_type == SemanticType.PRODUCT_IMAGE
        ]
        if not photo_ids:
            return "None"
        id_lines = "\n".join(f"  - {pid}" for pid in photo_ids)
        comp = spec.composition
        if comp is not None and comp.photo_size:
            # Step 64: the art director's size bucket is the binding range.
            # Emitting the historical 0.20-0.45 prior alongside a 'large'
            # directive (0.45-0.80) gave the LLM two contradictory area
            # signals -- step63 hero candidates landed on 0.33, the
            # compromise point just below the bucket floor -- so the prior
            # defers entirely to the directive.
            from metagpt.ext.agentlayout.tools.composition_templates import (
                SIZE_BUCKET_RANGES,
            )

            lo, hi = SIZE_BUCKET_RANGES[comp.photo_size]
            return (
                f"The composition directive OVERRIDES the historical prior: the "
                f"focal photo's\narea_ratio MUST land in [{lo:.2f}, {hi:.2f}] "
                f"('{comp.photo_size}', QC-enforced).\nDo NOT default to a "
                f"1/3 x 1/3 tile (0.11) or a 0.33 block -- both fail the\n"
                f"directive. Photo elements:\n"
                f"{id_lines}"
            )
        lo, hi = PHOTO_AREA_TARGET
        return (
            f"Designer ground truths (N=1,902 Crello layouts) size non-background\n"
            f"photos at area_ratio median {PHOTO_AREA_GT['p50']:.2f}, upper quartile "
            f"{PHOTO_AREA_GT['p75']:.2f}\n"
            f"(p90 {PHOTO_AREA_GT['p90']:.2f}). Layouts whose photos sit near 0.11 "
            f"(a 1/3 x 1/3 tile)\nread as TIMID and lose the design_layout axis. "
            f"For each photo element\nbelow, target area_ratio {lo:.2f}-{hi:.2f} of "
            f"the canvas (the focal photo may go\nlarger):\n"
            f"{id_lines}\n"
            f"While enlarging, KEEP rule 6 satisfied: the photo must still overlap\n"
            f"a safe zone by >= 50% of its own area -- anchor the enlarged photo in\n"
            f"the LARGEST safe zone instead of shrinking it back down."
        )

    @staticmethod
    def _format_previous_attempt(
        prev_best_layout: Optional[Dict[str, Tuple[float, float, float, float]]],
        prev_best_subscores: Optional[Dict[str, int]],
    ) -> str:
        """Render the `# Previous Attempt` block content.

        Returns "None" on cold-start (no prev layout) so the conditional
        instruction reading "only act on this block when it is NOT None" naturally
        suppresses refinement mode. In refinement mode emits a compact dict per
        element plus the four subscores.
        """
        if not prev_best_layout:
            return "None"
        bbox_lines = []
        for elem_id, bbox in prev_best_layout.items():
            left, top, width, height = bbox
            bbox_lines.append(
                f'  "{elem_id}": [{int(round(left))}, {int(round(top))}, '
                f'{int(round(width))}, {int(round(height))}]'
            )
        bbox_block = "{\n" + ",\n".join(bbox_lines) + "\n}"
        subscores = prev_best_subscores or {}
        scores_line = (
            f"design_layout={subscores.get('design_layout', '?')}  "
            f"content_relevance={subscores.get('content_relevance', '?')}  "
            f"typography_color={subscores.get('typography_color', '?')}  "
            f"graphics_images={subscores.get('graphics_images', '?')}  "
            f"innovation_originality={subscores.get('innovation_originality', '?')}"
        )
        return (
            "prev_best_layout (element_id -> [left, top, width, height], pixels):\n"
            f"{bbox_block}\n"
            f"prev_best_subscores (COLE 5-axis, 1-10 each):\n  {scores_line}"
        )

    @staticmethod
    def _parse_response(rsp: str) -> CandidatesBatch:
        """Extract the candidate JSON and validate against CandidatesBatch.

        The CoordinateMapper prompt asks the model to write 2-3 sentences of
        reasoning before the JSON. Three extraction layers handle that:
          1. a ```json fenced block (the requested format) -> CodeParser;
          2. otherwise, the substring from the first '{' to the last '}'
             (strips a bare reasoning prefix/suffix around raw JSON);
          3. otherwise, the whole stripped text.
        """
        text = rsp.strip()
        if "```" in text:
            try:
                fenced = CodeParser.parse_code(text=text, lang="json")
                if fenced:
                    text = fenced
            except Exception:
                pass
        if not text.lstrip().startswith("{"):
            lo = text.find("{")
            hi = text.rfind("}")
            if lo != -1 and hi != -1 and hi > lo:
                text = text[lo : hi + 1]
        return CandidatesBatch.model_validate_json(text)
