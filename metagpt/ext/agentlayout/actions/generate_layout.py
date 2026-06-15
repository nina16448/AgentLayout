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
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import base64

from PIL import Image
from pydantic import ValidationError

from metagpt.actions import Action
from metagpt.ext.agentlayout.schema import (
    AestheticFeedback,
    BackgroundAnalysis,
    CandidatesBatch,
    DesignSpec,
    LayoutTree,
    SemanticType,
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
          "font_family": "cursive",
          "font_size": 84,
          "font_weight": "bold",
          "color": "#C2547B",
          "text_align": "left"
        }
      ]
    }
  ]
}"""


# DELIBERATE PROMPT/QC ASYMMETRY (Step 67 audit, 2026-06-14):
# The "size reference" block below quotes 'prominent >=20%' and
# 'medium >=15%' as STRETCH TARGETS. The downstream QC acceptance floor
# (tools/quality_checker.py SIZE_HINT_LOWER_BOUND) is LOWER -- 0.10 and
# 0.08 respectively. Combined with the prompt's "aim for value .. value*1.2;
# do NOT exceed by huge margins" rule, this 1.5-2x gap counters the LLM
# size-timidity surfaced in Step 58/60 ([[project_step58_coverage_qc_live]],
# [[project_step60_photo_size_prior]]): anchoring the prompt to the QC
# floor would land actual outputs *below* the floor and crater acceptance.
# All Step 22..66 calibration was done against this gap; do NOT align the
# two numbers without re-running headline experiments.
PROMPT_TEMPLATE = """Role: You are a professional graphic layout designer.
Your goal is to arrange the given design elements on a canvas
by assigning precise pixel coordinates to each element.

# Context
Design Spec: {design_spec}
Safe zones: {safe_zones}
Dominant palette: {dominant_palette}
Recommended text color (default, override if needed): {recommended_text_color}
Feedback from previous round (if any): {feedback}

# Designer exemplars (Step 67, 2026-06-13; "None" when retrieval is off)
# Real human-designer layouts for structurally similar briefs, normalised to
# [0,1]. Study their composition language -- where they place text relative to
# photos, how large photos are, asymmetry -- and produce candidates in that
# language. Do NOT copy coordinates; they are a different brief.
{exemplars}

# Aesthetic objective (Step 33, 2026-06-09)
Every candidate you emit will be judged on these four axes (each 1-10, total
of 4 axes is the headline score). Treat them as design objectives, not as
post-hoc criteria. Push for 8+ on every axis when laying out elements.

A. Design and Layout
   Clean, balanced, consistent layout with a clear hierarchy. The Layout
   Tree's depth order tells you which element is most important; mirror that
   in visual weight (size, position, prominence). Avoid clutter, dead-space
   bands, or arbitrary placement. Maximize readability and visual flow.

B. Content Relevance and Effectiveness
   The layout must SERVE the brief and the design_spec. Every hard_constraint
   must be respected. Elements should be positioned so they communicate the
   brief's intent (e.g. headline dominates, CTA is prominent, supporting
   details are visually subordinate). A layout that ignores the brief is a
   guaranteed low score on this axis.

C. Typography and Color Scheme
   Font sizes must form a clear typographic hierarchy (title >> subtitle >>
   body). Colors must harmonize with `dominant_palette`; default text color
   to `recommended_text_color` unless a hard_constraint says otherwise.
   Avoid clashing colors, illegible size/contrast pairings, or two text
   elements competing at the same visual weight.

D. Innovation and Originality
   The 5 candidates MUST take distinctly different compositional approaches
   (different focal anchors, different alignments, different white-space
   strategies). Do not output 5 minor variations of the same composition.
   Avoid trend-following generic placement (everything centered, or
   everything top-aligned) unless the brief explicitly demands it.

# Previous Attempt (only act on this block when it is NOT "None")
{previous_attempt}

When this block is non-empty you are in REFINEMENT MODE, not cold-start mode.
Behaviour required in refinement mode:
  - Anchor every candidate to the previous best layout. Each element's
    (left, top, width, height) must stay within +/-10% of its previous value
    unless a structured_suggestion in `feedback` explicitly demands a larger
    change for that element id.
  - Reuse element ids verbatim from `prev_best_layout` (which equals the spec
    element ids). Do NOT rename or invent ids.
  - The 5 candidates may still explore distinct refinement directions
    (different elements emphasised, different drift orientations), but ALL
    candidates must remain in the neighbourhood of prev_best_layout. Do not
    treat refinement mode as an excuse to relocate elements to entirely new
    regions.
  - Use prev_best_subscores to prioritise which dimension to push:
    the lowest-scoring sub-dimension is the one your edits should improve.

# How to read `feedback` (only when it is not "None")
The feedback object has two parts:
  - `suggestions`: free-text human notes; use them for *context* only.
  - `structured_suggestions`: a JSON list of typed constraints. PREFER these
    over the free text. Each entry has the shape
        {{"kind": ..., "target_id": ..., "metric": ..., "op": ..., "value": ...}}

Translate each structured suggestion into a concrete adjustment as follows:

  | kind           | what to change                                                       |
  | -------------- | -------------------------------------------------------------------- |
  | place_in_bbox  | OVERRIDE the element's (left,top,width,height) to                    |
  |                | (target_bbox[0], target_bbox[1],                                     |
  |                |  target_bbox[2]-target_bbox[0], target_bbox[3]-target_bbox[1]).      |
  |                | This kind BYPASSES the +/-10% refinement drift cap for target_id    |
  |                | because the Judge looked at the rendered image and decided the      |
  |                | exact region this element should occupy.                            |
  | resize         | the element's `width` and/or `height`                                |
  | move           | the element's `left` / `top` (or `right` / `bottom` derived)         |
  | spacing        | the gap between `target_id` and the element named in `metric`        |
  |                |   (metric format: 'gap_to:OTHER_ID')                                 |
  | typography     | One of FOUR text-style metrics on the target element:                |
  |                |   metric=font_size    -> set `font_size` (int pixels, e.g. 96)       |
  |                |   metric=font_weight  -> set `font_weight` (int 100-900 or named     |
  |                |                          string like "bold" / "regular")            |
  |                |   metric=font_family  -> set `font_family` (string e.g. "serif",    |
  |                |                          "Inter", "Playfair Display")               |
  |                |   metric=text_align   -> set `text_align` (one of "left" /          |
  |                |                          "center" / "right" / "justify")            |
  |                | Apply each typography suggestion verbatim; do NOT skip. These are   |
  |                | how the Judge closes the visual gap to the reference once layout    |
  |                | positions are correct. Apply to AT LEAST 4 of 5 candidates.         |
  | color          | the element's `color` (use the exact hex string in `value`)          |
  | zorder         | the element's `z_index` (integer)                                    |
  | other          | apply the operator/value to the named `metric` field                 |

Operators:
  ">="     -> the field MUST be at least `value`. Aim for value to value*1.2;
              do NOT exceed by huge margins, that creates overlap and fails QC.
  "<="     -> the field MUST be at most `value`. Aim for value*0.8 to value.
  "=="     -> set the field to exactly `value`.
  "set_to" -> same as "==".
  "increase_by" / "decrease_by" -> shift the current value by that amount.

If two structured suggestions conflict with each other or with a
hard_constraint, prefer the one that better serves design_layout
(the Layout Tree's depth order tells you which element is more important;
a clean, balanced, hierarchy-respecting design wins).

# Layout Tree
{layout_tree}

Elements in the same branch are semantically related.
Elements closer to the leaves have lower visual importance.

# size reference (element_area / canvas_area, must satisfy lower bound)
full-canvas: >=95%  |  hero: >=60%   |  large: >=30%
prominent:   >=20%  |  medium: >=15% |  small: >=8%   |  caption: >=3%
photo-prominent: >=20%  (GT-calibrated photo floor, Step 60)
(If hard_constraints contain a size_preference for a target with hint H,
 that target's width*height divided by canvas_width*canvas_height MUST be
 at or above the lower bound of H.)

# GT-calibrated photo size prior (Step 60, 2026-06-11)
{photo_size_prior}

# Composition directive (Step 62, 2026-06-12; "None" when no director ran)
{composition_directive}

# Layout constraints (Step 37 hard rules, 2026-06-09)
The Quality Checker downstream WILL reject candidates that violate any of
these. Generate candidates that already comply so retries are not wasted:

1. DECORATIVE elements (semantic_type = "decorative_image") MUST occupy
   STRICTLY LESS than 40% of the canvas area each. Underlays are accents,
   not the main visual. Full-canvas plates are background_image, not
   decorative_image.

2. TITLE elements (semantic_type = "title") MUST satisfy ALL of:
     a) area_ratio = title.width * title.height / canvas_area >= 0.025
        (titles must be large enough to read as the design's anchor)
     b) horizontal centre  cx = (left + width/2) / canvas_width
        must lie in [0.10, 0.90]
     c) vertical centre    cy = (top + height/2) / canvas_height
        must lie in [0.05, 0.85]
   Titles in corners or pinned to canvas edges are rejected.

3. TEXT elements (semantic_type in {{title, subtitle, body_text, caption}})
   MUST NOT have any non-text element with z_index GREATER THAN OR EQUAL
   to the text's z_index covering >= 20% of the text bbox. Place text
   ABOVE decorative shapes in z order, and avoid placing it directly on
   top of high-coverage image elements.

4. TEXT colours MUST have WCAG 2.1 AA contrast (>= 4.5) against the canvas
   background colour. White text on white bg / black text on black bg are
   rejected. When in doubt prefer the spec's recommended_text_color.

5. SEQUENTIAL text from the asset_list MUST be placed in top-to-bottom
   y-order MATCHING the asset_list sequence. If the asset list contains
   text snippets in the order [A, B, C], the rendered A must sit above B
   and B above C (smaller top values). This preserves the designer's
   reading-order intent. NEVER reverse a multi-line heading (e.g. do not
   render "RESOURCES" above "HUMAN" when the source had "HUMAN" before
   "RESOURCES").

6. PRIMARY ELEMENTS (semantic_type in title / subtitle / body_text /
   product_image / logo) MUST overlap >= 50% with at least one of the
   provided `safe_zones`. The safe_zones list is the CV-derived
   background-saliency-low regions; everything OUTSIDE them is occupied
   by the background subject (faces, products, focal imagery). Placing a
   primary element outside the safe_zones means obscuring the background
   subject AND making the element hard to read.

   bbox format inside `safe_zones` is [left, top, RIGHT, BOTTOM]
   (absolute pixel coords; NOT width/height). To verify rule 6, test:
       primary.left   >= safe_left   AND primary.left  + primary.width   <= safe_right
       primary.top    >= safe_top    AND primary.top   + primary.height  <= safe_bottom
   A primary fully inside a safe zone passes; partial overlap counts the
   intersection-over-element-area which must be >= 0.50.

   Decorative elements may straddle safe / unsafe boundaries since their
   job is to anchor text, but the primary text/photo MUST sit inside a
   safe zone. Use the provided safe zones; do not invent your own.

7. COVERAGE / DEAD SPACE (Step 57, 2026-06-11). Counting every element
   EXCEPT background_image as foreground:
     a) the union of foreground bounding boxes MUST cover >= 10% of the
        canvas area. Do not shrink all content into one small sliver.
     b) no contiguous blank band (a horizontal strip or vertical strip
        containing NO foreground element, canvas margins included) may
        exceed 60% of the canvas height or width. Do not stack every
        element in one third of the canvas and leave the rest empty.
   Distribute elements so the composition engages the whole canvas; use
   the safe_zones across the canvas, not just the first one.

# Reasoning checklist (Step 42, 2026-06-10): walk through these steps
# mentally BEFORE you emit the JSON. The checklist is your scratchpad --
# do NOT include the reasoning in your output, just produce the
# candidates that satisfy what you concluded.

  Step 1 -- SAFE-ZONE PLAN:
    Read `safe_zones`. For each safe zone note: region label, bbox
    (left, top, width, height), area_ratio = w*h / (canvas_w * canvas_h).
    Decide which safe_zone will host the title, which will host the body
    text, which will host any logo / product image. The hero element
    goes in the largest safe zone.

  Step 2 -- ASSET INVENTORY:
    From the spec, list every element id + semantic_type + the asset
    behind it (text content for text elements, image asset_ref for image
    elements). Decide which element is THE focal point of this
    composition.

  Step 3 -- HIERARCHY:
    Set sizes so the importance ordering is visually obvious:
      title.font_size >= 1.5 * subtitle.font_size
      title area_ratio >= 0.025  (rule 2 above)
      decorative_image area_ratio < 0.40  (rule 1 above)

  Step 4 -- COLOR:
    Default text color = `recommended_text_color`. Override only if a
    hard_constraint demands it OR if dominant_palette suggests a clearly
    better choice. Mentally check WCAG AA contrast vs canvas
    background_color.

  Step 5 -- FEEDBACK APPLICATION (only when feedback != None):
    For every structured_suggestion, write down (mentally) the EXACT
    target_id and the EXACT new value you will apply. Do not paraphrase
    suggestions; apply them verbatim.

  Step 6 -- DIVERSITY CHECK:
    Plan 5 candidates that each anchor the title in a different
    safe_zone (or vary the focal element's safe_zone if only one
    text exists). Different safe_zone anchors = "5 distinct
    compositional approaches" as required below.

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
ATTENTION: Typography direction (Step 49a, 2026-06-10). Designer ground truths
           almost never use default black sans-serif titles; a layout that does
           loses the typography_color axis automatically. Choose font_family
           DELIBERATELY per text element:
             - The renderer supports four families; pick via these tokens:
                 "sans-serif" | "serif" | "cursive" (flowing script) |
                 "display" (heavy decorative headline face)
             - Map the design mood (style_keywords + the attached background
               image) to a TITLE family:
                 festive / floral / feminine / wedding / thank-you -> "cursive"
                 promo / sale / sporty / loud / youthful          -> "display"
                 editorial / luxury / classic / formal            -> "serif"
                 corporate / tech / minimal / clean               -> "sans-serif"
             - Body/caption text stays "sans-serif" or "serif" for legibility;
               reserve "cursive"/"display" for title / subtitle / cta.
             - Title color MUST come from the design's palette: pick a dominant
               or complementary hue from the background image / dominant
               palette. Use near-black (#000000-#222222) ONLY when the mood is
               corporate/minimal AND the background is a light neutral.
           Across the 5 candidates use at least TWO different (title
           font_family, title color) combinations -- five identical black
           sans-serif titles is an automatic fail.
ATTENTION: For image elements, output geometry only -- no visual style fields needed.
ATTENTION: Photo sizing (Step 60, 2026-06-11). Every element under a
           `size_preference: photo-prominent` hard constraint MUST have
           width * height >= 0.20 * canvas_width * canvas_height. This floor
           is the designer-ground-truth MEDIAN photo size -- producing a
           1/3 x 1/3 tile (area_ratio 0.11) or smaller is the single most
           common amateur tell and fails QC immediately. Compute the math
           per photo BEFORE emitting JSON: on a 1080x1920 canvas the photo
           needs >= 414,720 px^2 (e.g. 720x576, 648x640, 1080x384). Anchor
           the enlarged photo in the LARGEST safe zone; do NOT shrink it
           below the floor to dodge other constraints.
ATTENTION: Composition directive (Step 62, 2026-06-12). When the
           "# Composition directive" block above is not "None", it is the
           art director's decision and OUTRANKS your own compositional
           taste. ALL 5 candidates MUST satisfy its numeric contract
           (photo-center cell, photo area range, text-mass cell, photo-text
           relation) -- the Quality Checker verifies every bound and rejects
           violators immediately. Compute the math per candidate BEFORE
           emitting JSON. The "distinctly different approaches" rule applies
           WITHIN the directive: vary alignment, typography, exact positions
           and spacing -- never the coarse composition itself.
ATTENTION: Each candidate must take a distinctly different compositional approach.
           Do not repeat similar layouts across candidates.
ATTENTION: Canvas vertical coverage. The layout MUST occupy the full canvas
           height -- a poster with the bottom 30% empty looks unfinished and
           gets penalised on design_layout / typography_color. Concretely:
               max(top + height) across all elements >= 0.85 * canvas_height
               min(top)                              <= 0.10 * canvas_height
           Worked example for an 800x1200 canvas: the lowest element's bottom
           edge MUST reach y >= 1020, and at least one element MUST start at
           y <= 120. If you only have 3 elements and the natural total height
           is short, distribute them with larger inter-element gaps so the
           bottom edge still hits 0.85 of canvas_height -- do NOT cluster
           everything in the top half and leave a giant white band below.
ATTENTION: Horizontal balance / dead-space (Step 49b, 2026-06-11). Vertical
           coverage alone is not enough -- a layout where all elements hug
           one half of the canvas and leave a full-height empty band on the
           other side reads as unbalanced dead space and loses design_layout.
           Either:
             a) the union of non-background elements spans most of the width
                (min(left) <= 0.15 * canvas_width AND
                 max(left + width) >= 0.85 * canvas_width), OR
             b) you deliberately build a single text column beside the
                background's focal subject (photo / product). In that case
                centre the column inside its safe zone and keep the column's
                own left/right margins within 2x of each other -- do NOT
                push an off-centre cluster against one edge while a wide
                empty band sits next to it.
ATTENTION: Decorative-image underlays. An element with
           semantic_type=="decorative_image" is a pre-classified shape plate
           (low colour complexity / transparent edges, not a photo). Treat
           it as a middle stacking layer:
             - z_index MUST be strictly LESS THAN the z_index of every title,
               subtitle, body_text, caption, product_image, logo, icon, cta
               and pricetag element. A typical good assignment is
               background_image=1, decorative_image=2, image/logo/text=3+.
             - PAIRING IS MANDATORY (Step 49b, 2026-06-11): every
               decorative_image MUST fully contain the bbox of at least one
               text element (title / subtitle / body_text / caption / cta),
               with the underlay extending 10-20% beyond that text on each
               side (so the underlay frames the text, not the reverse).
               A free-floating plate with no text on top of it reads as
               random clutter and loses design_layout.
             - Do NOT make decorative_image cover >=95% of the canvas; that is
               background territory. Keep its area below 60% of canvas.
ATTENTION: If feedback is provided, satisfy every structured_suggestion in at
           least 4 of 5 candidates. Use the suggestions[] free text only as
           supplementary context. Do not ignore the structured list, but also
           do not over-apply: a ">=" constraint is a LOWER bound, not a target
           you must exceed by 2x.
ATTENTION: Step 46 (2026-06-10) -- ATTACHED IMAGE IS THE CANVAS BACKGROUND.
           The FIRST attached image is the literal background PNG
           the renderer will composite your layout on top of. The numeric
           `safe_zones` you see in this prompt are a COARSE summary computed
           from that image. WHEN YOUR EYE AND THE NUMBERS DISAGREE, BELIEVE
           THE IMAGE. Look at the image and decide:
             - where is the focal subject (face / product / hero element)?
             - which negative-space regions are actually empty?
             - what colour band sits behind your candidate text positions
               (this affects WCAG contrast)?
             - is there a vertical / horizontal axis the composition naturally
               wants you to align to?
           Use these visual observations to pick concrete (left, top, width,
           height) values. HOWEVER (Step 49b clarification, 2026-06-11): the
           automated Quality Checker enforces rule 6 NUMERICALLY against the
           listed safe_zones -- a primary element overlapping < 50% with
           every listed safe_zone is rejected no matter how good it looks
           visually. So use the image to decide WHICH listed safe_zone hosts
           each primary element and to fine-position WITHIN it, NOT as a
           licence to abandon the listed zones. Only decorative / secondary
           elements may occupy image-revealed empty regions outside the
           listed safe_zones. If the image shows a face or focal element
           inside what the safe_zones call "safe", pick a different listed
           safe_zone for your primary -- do NOT cover the face.
{self_render}
ATTENTION: If the "# Previous Attempt" block is non-empty (refinement mode),
           every element's (left, top, width, height) must stay within +/-10%
           of its previous value unless a structured_suggestion explicitly
           demands a larger change for that element id. Element ids must be
           reused verbatim. The 5 candidates must remain anchored to
           prev_best_layout; do NOT relocate elements to entirely new regions.
           EXCEPTION: any element appearing in a kind="place_in_bbox"
           structured_suggestion has its drift cap LIFTED for that element
           only. Set its (left,top,width,height) directly from target_bbox
           verbatim, even if the move exceeds +/-10%. The Judge saw the
           image and made this call deliberately; do not partially apply.
Output carefully referenced "format example" in JSON format, nothing else.
"""


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
        prev_best_layout: Optional[Dict[str, Tuple[float, float, float, float]]] = None,
        prev_best_subscores: Optional[Dict[str, int]] = None,
        prev_render_path: Optional[Path] = None,
        exemplars: Optional[str] = None,
    ) -> CandidatesBatch:
        """Build prompt, call LLM, parse and validate.

        Pre-condition: ``spec`` must be enriched (Asset Analyzer ran).
        Returns one batch of *raw* candidates -- the K_valid = 5 top-up loop
        is the pipeline driver's responsibility, not this Action's.

        Refinement Loop (2026-05-20): when ``prev_best_layout`` is non-empty
        the prompt activates the ``# Previous Attempt`` block, switching the
        Generator from cold-start to anchored refinement mode (+/-10% drift
        per element unless a structured_suggestion demands a larger edit).

        Visual self-correction (Step 65, 2026-06-12): when
        ``prev_render_path`` points at the previous attempt's rendered PNG,
        it is attached as the LAST image and the prompt gains the
        self-render ATTENTION block. Callers that never render between
        retries simply omit it and get the pre-Step-65 behaviour.
        """
        spec.assert_enriched()

        # Step 46: attach the canvas background image so the LLM can see the
        # focal subject and the real empty regions, not just the coarse
        # safe_zones summary. Silently fall back to text-only when the model
        # lacks vision support or the background asset is missing/unreadable.
        # Step 65: optionally attach the previous attempt's render as the
        # LAST image. Images are collected BEFORE the prompt is built because
        # the prompt must only describe images that are actually attached.
        images: List[str] = []
        self_render_attached = False
        if self.llm.support_image_input():
            bg_b64 = self._render_bg_image(spec)
            if bg_b64 is not None:
                images.append(bg_b64)
            else:
                logger.debug(
                    "GenerateLayout: no usable background image; using text-only call."
                )
            if prev_render_path is not None:
                pr_b64 = self._load_image_b64(Path(prev_render_path))
                if pr_b64 is not None:
                    images.append(pr_b64)
                    self_render_attached = True
                else:
                    logger.debug(
                        f"GenerateLayout: previous render {prev_render_path!r} "
                        f"unreadable; proceeding without self-render."
                    )
        else:
            logger.debug(
                f"GenerateLayout: LLM '{getattr(self.llm, 'model', '?')}' lacks "
                f"vision support; using text-only call."
            )

        prompt = self._build_prompt(
            spec,
            tree,
            bg,
            feedback,
            prev_best_layout,
            prev_best_subscores,
            self_render_attached=self_render_attached,
            exemplars=exemplars,
        )

        if images:
            # INFO so live-run logs can decompose refusal rates by payload:
            # bg-only calls vs calls that also carry the self-render.
            logger.info(
                f"GenerateLayout: attaching {len(images)} image(s) "
                f"(self_render={self_render_attached})."
            )

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
                # Step 65: refusal detection moved AFTER the parse attempt --
                # a parseable batch is always accepted, so head-scanning for
                # refusal markers can never discard a good response.
                if images and self._looks_like_refusal(rsp):
                    # Step 64: informed degradation -- drop the images and
                    # fall back to the pre-step46 text-only mode (the numeric
                    # safe_zones summary is still in the prompt). Grant one
                    # replacement attempt so a refusal cannot burn the whole
                    # budget. The `images and` guard makes this fire at most
                    # once. Step 65: the prompt is rebuilt so it no longer
                    # claims a self-render is attached.
                    logger.warning(
                        f"GenerateLayout attempt {attempt}/{budget}: vision "
                        f"refusal detected ({rsp.strip()[:60]!r}); retrying "
                        f"without image(s)."
                    )
                    images = []
                    if self_render_attached:
                        self_render_attached = False
                        prompt = self._build_prompt(
                            spec,
                            tree,
                            bg,
                            feedback,
                            prev_best_layout,
                            prev_best_subscores,
                            self_render_attached=False,
                        )
                    budget += 1
                    last_err = ValueError(f"vision refusal: {rsp.strip()[:120]}")
                    continue
                last_err = err
                logger.warning(
                    f"GenerateLayout attempt {attempt}/{budget} failed: {err}"
                )

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
        feedback: Optional[AestheticFeedback],
        prev_best_layout: Optional[Dict[str, Tuple[float, float, float, float]]] = None,
        prev_best_subscores: Optional[Dict[str, int]] = None,
        self_render_attached: bool = False,
        exemplars: Optional[str] = None,
    ) -> str:
        """Render PROMPT_TEMPLATE with all 11 substitutions.

        ``previous_attempt`` is the new Refinement Loop block. It is "None"
        on cold-start (Round 0) and a compact JSON-ish description in
        refinement mode (Round 1+). ``self_render_attached`` (Step 65)
        activates the self-render ATTENTION block and must be True only when
        the previous attempt's render is actually in the image payload.
        """
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
        previous_attempt_str = self._format_previous_attempt(
            prev_best_layout, prev_best_subscores
        )
        return PROMPT_TEMPLATE.format(
            design_spec=spec_str,
            safe_zones=safe_zones_str,
            dominant_palette=palette_str,
            recommended_text_color=bg.recommended_text_color,
            feedback=feedback_str,
            previous_attempt=previous_attempt_str,
            layout_tree=tree_str,
            format_example=FORMAT_EXAMPLE_JSON,
            photo_size_prior=self._format_area_hints(spec),
            composition_directive=self._format_composition_directive(spec),
            self_render=_SELF_RENDER_NOTE if self_render_attached else "None",
            exemplars=exemplars or "None",
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
        """Strip markdown fences if present, then validate against CandidatesBatch."""
        text = rsp.strip()
        if "```" in text:
            try:
                text = CodeParser.parse_code(text=text, lang="json") or text
            except Exception:
                pass
        return CandidatesBatch.model_validate_json(text)
