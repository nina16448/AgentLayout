"""Step 60 unit tests for the GT-calibrated photo size prior.

Calibration source: layout_agent/output/step60_area_ratio_calibration.py over
all 1,902 cached Crello GT layouts. Candidate photos cluster at area_ratio
0.111 (1/3 x 1/3 tile) vs GT p50 0.213 / p75 0.445 -- photos are the single
size-timid class, so the prior targets product_image ONLY.

No LLM calls; these tests cover prompt plumbing and the hint formatter.

Run:
    pytest tests/metagpt/ext/agentlayout/test_generator_area_prior.py -v --no-cov
"""
from __future__ import annotations

from typing import List

from metagpt.ext.agentlayout.actions.generate_layout import (
    PHOTO_AREA_GT,
    PHOTO_AREA_TARGET,
    PROMPT_TEMPLATE,
    GenerateLayout,
)
from metagpt.ext.agentlayout.schema import (
    Canvas,
    DesignSpec,
    Element,
    SemanticType,
    VisualType,
)


def _el(eid: str, sem: SemanticType, vis: VisualType) -> Element:
    return Element(
        id=eid,
        semantic_type=sem,
        visual_type=vis,
        content="x" if vis == VisualType.TEXT else None,
        inferred=False,
        importance=3,
        semantic_relevance=0.8,
    )


def _spec(elements: List[Element]) -> DesignSpec:
    return DesignSpec(
        canvas=Canvas(width=600, height=400),
        elements=elements,
        hard_constraints=[],
        style_keywords=["bold"],
        language="en",
    )


def test_prompt_template_has_photo_size_prior_slot():
    assert "{photo_size_prior}" in PROMPT_TEMPLATE
    assert "# GT-calibrated photo size prior (Step 60" in PROMPT_TEMPLATE


def test_calibration_constants_pinned():
    """Changing these requires re-running step60_area_ratio_calibration.py."""
    assert PHOTO_AREA_GT["p50"] == 0.213
    assert PHOTO_AREA_GT["p75"] == 0.445
    assert PHOTO_AREA_TARGET == (0.20, 0.45)


def test_hint_emitted_for_product_image():
    spec = _spec(
        [
            _el("title_1", SemanticType.TITLE, VisualType.TEXT),
            _el("product_image_1", SemanticType.PRODUCT_IMAGE, VisualType.IMAGE),
        ]
    )
    hint = GenerateLayout._format_area_hints(spec)
    assert "product_image_1" in hint
    assert "0.20-0.45" in hint
    assert "0.21" in hint  # GT median surfaces in the hint
    # Enlargement must stay compatible with the safe-zone rule (rule 6),
    # otherwise the QC gate kills the GT-style solution (Step 58 finding).
    assert "rule 6" in hint
    assert "LARGEST safe zone" in hint
    # The degenerate 1/3 x 1/3 habit is called out explicitly.
    assert "0.11" in hint


def test_hint_lists_every_photo_id():
    spec = _spec(
        [
            _el("product_image_1", SemanticType.PRODUCT_IMAGE, VisualType.IMAGE),
            _el("product_image_2", SemanticType.PRODUCT_IMAGE, VisualType.IMAGE),
        ]
    )
    hint = GenerateLayout._format_area_hints(spec)
    assert "- product_image_1" in hint
    assert "- product_image_2" in hint


def test_no_photo_yields_none():
    spec = _spec([_el("title_1", SemanticType.TITLE, VisualType.TEXT)])
    assert GenerateLayout._format_area_hints(spec) == "None"


def test_non_photo_image_classes_excluded():
    """Logos/icons must stay small; background/decorative are not photos.
    Calibration says titles/underlays are NOT size-timid -- no hint for them."""
    spec = _spec(
        [
            _el("background_1", SemanticType.BACKGROUND_IMAGE, VisualType.IMAGE),
            _el("underlay_1", SemanticType.DECORATIVE_IMAGE, VisualType.IMAGE),
            _el("logo_1", SemanticType.LOGO, VisualType.IMAGE),
            _el("icon_1", SemanticType.ICON, VisualType.IMAGE),
            _el("title_1", SemanticType.TITLE, VisualType.TEXT),
        ]
    )
    assert GenerateLayout._format_area_hints(spec) == "None"


def test_prompt_pins_photo_prominent_bucket_and_attention():
    """Step 60 second lever: the bucket appears in the size table AND as an
    end-of-prompt ATTENTION rule (highest-compliance prompt region)."""
    assert "photo-prominent: >=20%" in PROMPT_TEMPLATE
    assert "ATTENTION: Photo sizing (Step 60" in PROMPT_TEMPLATE
    assert "0.20 * canvas_width * canvas_height" in PROMPT_TEMPLATE


def test_qc_bucket_pinned():
    from metagpt.ext.agentlayout.tools.quality_checker import SIZE_HINT_LOWER_BOUND

    assert SIZE_HINT_LOWER_BOUND["photo-prominent"] == 0.20


def test_inject_photo_size_prior_appends_constraint():
    from metagpt.ext.agentlayout.actions.analyze_brief import inject_photo_size_prior
    from metagpt.ext.agentlayout.schema import HardConstraintRule

    spec = _spec(
        [
            _el("title_1", SemanticType.TITLE, VisualType.TEXT),
            _el("product_image_1", SemanticType.PRODUCT_IMAGE, VisualType.IMAGE),
            _el("logo_1", SemanticType.LOGO, VisualType.IMAGE),
        ]
    )
    inject_photo_size_prior(spec)
    assert len(spec.hard_constraints) == 1
    hc = spec.hard_constraints[0]
    assert hc.rule == HardConstraintRule.SIZE_PREFERENCE
    assert hc.targets == ["product_image_1"]  # logo excluded
    assert hc.params["hint"] == "photo-prominent"


def test_inject_photo_size_prior_respects_analyst_constraint():
    from metagpt.ext.agentlayout.actions.analyze_brief import inject_photo_size_prior
    from metagpt.ext.agentlayout.schema import HardConstraint, HardConstraintRule

    spec = _spec([_el("product_image_1", SemanticType.PRODUCT_IMAGE, VisualType.IMAGE)])
    spec.hard_constraints.append(
        HardConstraint(
            rule=HardConstraintRule.SIZE_PREFERENCE,
            targets=["product_image_1"],
            params={"hint": "hero"},
        )
    )
    inject_photo_size_prior(spec)
    # Analyst's own (stronger) preference wins; no duplicate appended.
    assert len(spec.hard_constraints) == 1
    assert spec.hard_constraints[0].params["hint"] == "hero"


def test_inject_photo_size_prior_noop_without_photo():
    from metagpt.ext.agentlayout.actions.analyze_brief import inject_photo_size_prior

    spec = _spec([_el("title_1", SemanticType.TITLE, VisualType.TEXT)])
    inject_photo_size_prior(spec)
    assert spec.hard_constraints == []


def test_prompt_template_formats_with_ten_substitutions():
    """Regression: PROMPT_TEMPLATE.format must not raise KeyError after the
    photo_size_prior (Step 60) and composition_directive (Step 62) slots were
    added (all literal braces stay escaped)."""
    rendered = PROMPT_TEMPLATE.format(
        design_spec="{}",
        safe_zones="[]",
        dominant_palette="[]",
        recommended_text_color="#111111",
        feedback="None",
        previous_attempt="None",
        layout_tree="{}",
        format_example="{}",
        photo_size_prior="None",
        composition_directive="None",
    )
    assert "# GT-calibrated photo size prior (Step 60" in rendered
    assert "{photo_size_prior}" not in rendered
    assert "# Composition directive (Step 62" in rendered
    assert "{composition_directive}" not in rendered
