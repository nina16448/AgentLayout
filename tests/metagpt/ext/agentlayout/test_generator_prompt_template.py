"""Pinned-string regression tests for ``generate_layout.PROMPT_TEMPLATE``.

"先想再畫" refactor (2026-06-25): the v1 prompt's ATTENTION/coverage/refinement
blocks were intentionally removed when the LayoutGenerator was split into a
CompositionDirector (decides the spatial concept) and this CoordinateMapper
(turns ONE concept into pixels). These tests pin the *new* lean-prompt
invariants: the concept block, the single-candidate contract, the essential QC
rules, and that ``.format`` keys line up with ``_build_prompt``.

They do NOT call the LLM.

Run:
    pytest tests/metagpt/ext/agentlayout/test_generator_prompt_template.py -v --no-cov
"""
from __future__ import annotations


def test_prompt_is_coordinate_mapper_not_art_director():
    """The mapper realises the art director's concept; it must not re-invent it."""
    from metagpt.ext.agentlayout.actions.generate_layout import PROMPT_TEMPLATE

    assert "layout technician" in PROMPT_TEMPLATE
    assert "{concept_block}" in PROMPT_TEMPLATE
    # One candidate per concept, not the old "5 distinct candidates".
    assert "exactly ONE candidate" in PROMPT_TEMPLATE
    assert "5 candidates" not in PROMPT_TEMPLATE


def test_prompt_keeps_essential_qc_rules():
    """Hard constraints survive the cut: bounds, ids, z-order, contrast, reading order."""
    from metagpt.ext.agentlayout.actions.generate_layout import PROMPT_TEMPLATE

    assert "left+width <= {canvas_width}" in PROMPT_TEMPLATE
    assert "must appear exactly once" in PROMPT_TEMPLATE
    assert "LOWER z_index" in PROMPT_TEMPLATE
    assert "WCAG AA" in PROMPT_TEMPLATE


def test_prompt_allows_chain_of_thought_before_json():
    """The mapper writes reasoning first, then a single JSON candidate."""
    from metagpt.ext.agentlayout.actions.generate_layout import PROMPT_TEMPLATE

    assert "2-3 short sentences" in PROMPT_TEMPLATE
    assert "{format_example}" in PROMPT_TEMPLATE


def test_prompt_template_formats_with_new_substitutions():
    """Regression: PROMPT_TEMPLATE.format must not raise KeyError for the lean
    CoordinateMapper substitution set."""
    from metagpt.ext.agentlayout.actions.generate_layout import (
        FORMAT_EXAMPLE_JSON,
        PROMPT_TEMPLATE,
    )

    rendered = PROMPT_TEMPLATE.format(
        concept_block="CONCEPT",
        canvas_width=1080,
        canvas_height=1920,
        bg_color="#FFFFFF",
        element_list="- title_1 (title/text)",
        safe_zones="[]",
        dominant_palette="[]",
        recommended_text_color="#111111",
        underlay_panels="None.",
        text_area_prior="None",
        feedback_block="",
        format_example=FORMAT_EXAMPLE_JSON,
    )
    assert "CONCEPT" in rendered
    assert "cand_01" in rendered  # the format example landed in the prompt


def test_rule7_carries_gt_hierarchy_numbers():
    """Step 81: the reading-order rule cites the calibrated dominant-text band."""
    from metagpt.ext.agentlayout.actions.generate_layout import PROMPT_TEMPLATE

    assert "[0.35, 0.57]" in PROMPT_TEMPLATE
    assert "median 0.475" in PROMPT_TEMPLATE
    assert "ABOVE" in PROMPT_TEMPLATE


def test_prompt_template_has_underlay_panels_slot():
    """Step 76: the baked-underlay feed-forward block must be present."""
    from metagpt.ext.agentlayout.actions.generate_layout import PROMPT_TEMPLATE

    assert "{underlay_panels}" in PROMPT_TEMPLATE
    assert "Baked underlay panels" in PROMPT_TEMPLATE


def test_concept_block_resolves_panel_assignment_to_bbox():
    """Step 77: 'panel N' in text_assignments becomes that panel's exact bbox."""
    from metagpt.ext.agentlayout.actions.generate_layout import GenerateLayout
    from metagpt.ext.agentlayout.schema import (
        BackgroundAnalysis,
        CompositionConcept,
        UnderlayRegion,
    )

    concept = CompositionConcept(
        name="n", focal_element="photo_1", focal_placement="p",
        text_placement="t", visual_flow="v", whitespace="w", typography_mood="m",
        text_assignments={"title_1": "panel 1", "body_1": "bottom-left"},
    )
    bg = BackgroundAnalysis(
        underlay_regions=[
            UnderlayRegion(bbox=[36, 30, 1064, 1049], dominant_color="#090102",
                           recommended_text_color="#F4F4F4")
        ]
    )
    block = GenerateLayout._format_concept_block(concept, bg)
    assert "BINDING" in block
    assert "title_1 -> panel 1 = bbox [left=36, top=30, right=1064, bottom=1049]" in block
    assert "#F4F4F4" in block
    assert "body_1 -> bottom-left" in block  # non-panel destination passes through

    # No assignments -> block keeps the legacy shape (no BINDING section).
    legacy = CompositionConcept(
        name="n", focal_element="e", focal_placement="p", text_placement="t",
        visual_flow="v", whitespace="w", typography_mood="m",
    )
    assert "BINDING" not in GenerateLayout._format_concept_block(legacy, bg)


def test_element_list_annotates_text_bitmap_natural_size(tmp_path):
    """Step 80: *_text.png elements carry their natural size + no-restyle rule."""
    from PIL import Image as PILImage

    from metagpt.ext.agentlayout.actions.generate_layout import GenerateLayout
    from metagpt.ext.agentlayout.schema import (
        Canvas,
        DesignSpec,
        Element,
        SemanticType,
        VisualType,
    )

    png = tmp_path / "asset_08_text.png"
    PILImage.new("RGBA", (640, 180), (10, 10, 10, 255)).save(png)
    spec = DesignSpec(
        canvas=Canvas(width=1080, height=1080),
        elements=[
            Element(id="title_1", semantic_type=SemanticType.TITLE,
                    visual_type=VisualType.IMAGE, content="CLEAN UP",
                    asset_ref=str(png), inferred=False, importance=5,
                    semantic_relevance=0.5),
            Element(id="photo_1", semantic_type=SemanticType.PRODUCT_IMAGE,
                    visual_type=VisualType.IMAGE, asset_ref="/nonexistent/p.png",
                    inferred=False, importance=3, semantic_relevance=0.5),
        ],
        hard_constraints=[], style_keywords=[], language="en",
    )
    listing = GenerateLayout._format_element_list(spec)
    assert "natural size 640x180px" in listing
    assert "KEEP the" in listing and "aspect ratio" in listing
    assert "omit" in listing  # no re-typesetting instruction
    # non-text image without a readable file gets no annotation and no crash
    assert "photo_1 (product_image/image)" in listing


def test_ledger_target_overrides_concept_assignment():
    """Step 88 precedence: an open ledger target replaces the concept's
    BINDING assignment for that element (deadlock fix from the 87 trace)."""
    from metagpt.ext.agentlayout.actions.generate_layout import GenerateLayout
    from metagpt.ext.agentlayout.schema import (
        AestheticFeedback,
        BackgroundAnalysis,
        CompositionConcept,
        VisualObservation,
        VisualObservationKind,
    )

    concept = CompositionConcept(
        name="n", focal_element="text_6", focal_placement="p", text_placement="t",
        visual_flow="v", whitespace="w", typography_mood="m",
        text_assignments={"text_6": "top-left", "text_1": "bottom-left"},
    )
    fb = AestheticFeedback(
        common_issues="x",
        visual_observations=[VisualObservation(
            kind=VisualObservationKind.TITLE_MISPLACED, target_id="text_6",
            target_bbox=[20, 276, 802, 373])],
    )
    block = GenerateLayout._format_concept_block(concept, BackgroundAnalysis(), fb)
    assert "text_6 -> LEDGER OVERRIDE (title_misplaced)" in block
    assert "INSIDE bbox [left=20, top=276, right=802, bottom=373]" in block
    assert "SUPERSEDES the concept's placement" in block
    assert "text_6 -> top-left" not in block          # 原指派被覆蓋
    assert "text_1 -> bottom-left" in block           # 無帳本目標者不受影響
    assert "LEDGER OVERRIDE lines outrank everything else" in block
    # 無 feedback 時輸出照舊
    legacy = GenerateLayout._format_concept_block(concept, BackgroundAnalysis())
    assert "LEDGER OVERRIDE" not in legacy


def test_keep_constraints_rendered_as_do_not_undo():
    """Step 89: retired targets ride the feedback block as KEEP constraints."""
    from metagpt.ext.agentlayout.actions.generate_layout import GenerateLayout
    from metagpt.ext.agentlayout.schema import (
        AestheticFeedback,
        VisualObservation,
        VisualObservationKind,
    )

    fb = AestheticFeedback(
        common_issues="x",
        keep_constraints=[VisualObservation(
            kind=VisualObservationKind.TITLE_MISPLACED, target_id="text_6",
            target_bbox=[20, 276, 802, 373])],
    )
    block = GenerateLayout()._format_feedback_block(fb, None, None, None)
    assert "KEEP constraints" in block
    assert "text_6 must STAY INSIDE bbox [20, 276, 802, 373]" in block
    assert "do NOT undo" in block
    # keep_constraints 不重複出現在 feedback JSON dump 裡
    assert block.count("[20, 276, 802, 373]") == 1


def test_format_underlay_panels_renders_bbox_and_colors():
    """Step 76: exact pixel bbox + contrasting colour for each region."""
    from metagpt.ext.agentlayout.actions.generate_layout import GenerateLayout
    from metagpt.ext.agentlayout.schema import BackgroundAnalysis, UnderlayRegion

    bg = BackgroundAnalysis(
        underlay_regions=[
            UnderlayRegion(
                bbox=[100, 50, 400, 170],
                dominant_color="#1A2B3C",
                recommended_text_color="#F4F4F4",
            )
        ]
    )
    block = GenerateLayout._format_underlay_panels(bg)
    assert "left=100" in block and "bottom=170" in block
    assert "#1A2B3C" in block
    assert "#F4F4F4" in block
    assert "INSIDE" in block

    assert GenerateLayout._format_underlay_panels(BackgroundAnalysis()) == "None."


def test_format_example_is_single_asymmetric_candidate():
    """The example must be one candidate and NOT perfectly centred (anti-imitation)."""
    import json

    from metagpt.ext.agentlayout.actions.generate_layout import FORMAT_EXAMPLE_JSON

    data = json.loads(FORMAT_EXAMPLE_JSON)
    assert len(data["candidates"]) == 1
    headline = next(e for e in data["candidates"][0]["elements"] if e["id"] == "headline_1")
    # Right-aligned, left at 680 on a 1080 canvas -> clearly off-centre.
    assert headline["text_align"] == "right"
    assert headline["left"] > 540
