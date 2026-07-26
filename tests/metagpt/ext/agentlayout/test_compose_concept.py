"""Unit tests for ComposeConcept -- the "先想再畫" Composition Director.

These tests do NOT hit a real LLM. They exercise the parse / fallback / prompt
helpers directly, plus ``run()`` with a fake llm installed onto the action the
same way ``test_generator_vision_channel.py`` does.

Run:
    pytest tests/metagpt/ext/agentlayout/test_compose_concept.py -v --no-cov
"""
from __future__ import annotations

import json
from typing import Any, List, Optional

import pytest

from metagpt.ext.agentlayout.actions.compose_concept import ComposeConcept
from metagpt.ext.agentlayout.schema import (
    Canvas,
    ConceptBatch,
    DesignSpec,
    Element,
    SemanticType,
    VisualType,
)


# ---------------------------------------------------------------- fixtures


def _el(eid: str, sem: SemanticType, vis: VisualType, content: Optional[str] = None) -> Element:
    return Element(
        id=eid,
        semantic_type=sem,
        visual_type=vis,
        content=content if vis == VisualType.TEXT else None,
        inferred=False,
        importance=3,
        semantic_relevance=0.8,
    )


def _spec() -> DesignSpec:
    return DesignSpec(
        canvas=Canvas(width=1080, height=1920),
        elements=[
            _el("bg_1", SemanticType.BACKGROUND_IMAGE, VisualType.IMAGE),
            _el("photo_1", SemanticType.PRODUCT_IMAGE, VisualType.IMAGE),
            _el("title_1", SemanticType.TITLE, VisualType.TEXT, "Big Sale Today"),
        ],
        hard_constraints=[],
        style_keywords=["bold", "modern"],
        language="en",
    )


def _concept_obj(name: str, focal: str = "photo_1") -> dict:
    return {
        "name": name,
        "focal_element": focal,
        "focal_placement": "left half bleeding to the edge",
        "text_placement": "right third, right-aligned",
        "visual_flow": "Z-pattern",
        "whitespace": "generous right margin",
        "typography_mood": "bold display, white on dark",
        "text_photo_relation": "beside",
    }


class _FakeLLM:
    """Returns a canned response for every aask; records prompts and images."""

    def __init__(self, supports_vision: bool, canned_response: str):
        self.supports_vision = supports_vision
        self.canned_response = canned_response
        self.calls: List[dict] = []

    def support_image_input(self) -> bool:
        return self.supports_vision

    async def aask(self, prompt: str, images: Optional[List[str]] = None, **kwargs: Any) -> str:
        self.calls.append({"prompt": prompt, "images": images, "kwargs": kwargs})
        return self.canned_response


# ---------------------------------------------------------------- _parse


def test_parse_bare_array():
    rsp = json.dumps([_concept_obj("A"), _concept_obj("B"), _concept_obj("C")])
    batch = ComposeConcept._parse(rsp, _spec())
    assert isinstance(batch, ConceptBatch)
    assert [c.name for c in batch.concepts] == ["A", "B", "C"]


def test_parse_strips_markdown_fences():
    rsp = "```json\n" + json.dumps([_concept_obj("A")]) + "\n```"
    batch = ComposeConcept._parse(rsp, _spec())
    assert len(batch.concepts) == 1
    assert batch.concepts[0].focal_element == "photo_1"


def test_parse_tolerates_dict_wrapper():
    rsp = json.dumps({"concepts": [_concept_obj("A"), _concept_obj("B")]})
    batch = ComposeConcept._parse(rsp, _spec())
    assert len(batch.concepts) == 2


def test_parse_clamps_to_five():
    rsp = json.dumps([_concept_obj(f"c{i}") for i in range(6)])
    batch = ComposeConcept._parse(rsp, _spec())
    assert len(batch.concepts) == 5  # 6 -> clamped, no hard fail


def test_parse_rejects_empty_and_nonlist():
    with pytest.raises(ValueError):
        ComposeConcept._parse("[]", _spec())
    with pytest.raises(ValueError):
        ComposeConcept._parse('"just a string"', _spec())


# ------------------------------------------------------------ _fallback_concept


def test_fallback_prefers_product_image():
    c = ComposeConcept._fallback_concept(_spec())
    assert c.focal_element == "photo_1"
    assert c.text_photo_relation == "below"


def test_fallback_skips_background_when_no_photo():
    spec = DesignSpec(
        canvas=Canvas(width=800, height=600),
        elements=[
            _el("bg_1", SemanticType.BACKGROUND_IMAGE, VisualType.IMAGE),
            _el("title_1", SemanticType.TITLE, VisualType.TEXT, "Hi"),
        ],
        hard_constraints=[],
        style_keywords=[],
        language="en",
    )
    c = ComposeConcept._fallback_concept(spec)
    assert c.focal_element == "title_1"  # not the background


# ---------------------------------------------------------------- _build_prompt


def test_build_prompt_excludes_background_and_names_n():
    prompt = ComposeConcept._build_prompt(_spec(), n=3)
    assert "bg_1" not in prompt  # background image is the attachment, not an element
    assert "photo_1" in prompt and "title_1" in prompt
    assert "3 fundamentally DIFFERENT" in prompt
    assert "bold, modern" in prompt  # style keywords joined


def test_build_prompt_without_bg_has_no_underlay_section():
    """Step 76: non-SEGA briefs keep the pre-Step-76 prompt shape."""
    prompt = ComposeConcept._build_prompt(_spec(), n=3)
    assert "Pre-placed underlay panels" not in prompt


def test_build_prompt_with_underlay_regions_describes_panels_in_words():
    """Step 76: the Director gets panel positions in words + colours, no bbox."""
    from metagpt.ext.agentlayout.schema import BackgroundAnalysis, UnderlayRegion

    bg = BackgroundAnalysis(
        underlay_regions=[
            UnderlayRegion(
                # Canvas 1080x1920; centre (270, 960) -> middle-left.
                bbox=[0, 480, 540, 1440],
                dominant_color="#1A2B3C",
                recommended_text_color="#F4F4F4",
            )
        ]
    )
    prompt = ComposeConcept._build_prompt(_spec(), n=3, bg=bg)
    assert "Pre-placed underlay panels" in prompt
    assert "middle-left" in prompt
    assert "#1A2B3C" in prompt and "#F4F4F4" in prompt
    assert "invitation" in prompt  # soft guidance, not a hard rule (Step 62 lesson)
    assert "left=0" not in prompt  # exact bboxes go to the CoordinateMapper only


def test_prompt_requires_text_assignments_and_numbers_panels():
    """Step 77: the Director must assign every text to 'panel N' or a region."""
    from metagpt.ext.agentlayout.schema import BackgroundAnalysis, UnderlayRegion

    bg = BackgroundAnalysis(
        underlay_regions=[
            UnderlayRegion(bbox=[0, 480, 540, 1440], dominant_color="#1A2B3C",
                           recommended_text_color="#F4F4F4")
        ]
    )
    prompt = ComposeConcept._build_prompt(_spec(), n=3, bg=bg)
    assert "text_assignments" in prompt
    assert "panel 1:" in prompt  # panels are numbered for reference
    assert "Reference it as 'panel 1'" in prompt
    assert "assign EVERY text element" in prompt


def test_text_hierarchy_prior_in_prompt_and_constants_pinned():
    """Step 81: GT-calibrated hierarchy guidance must reach the Director.
    Re-run the calibration one-liner in IMPLEMENTATION_LOG Step 81 before
    changing the constants."""
    from metagpt.ext.agentlayout.actions.compose_concept import TEXT_HIERARCHY_GT

    assert TEXT_HIERARCHY_GT["title_cy_p50"] == 0.475
    assert TEXT_HIERARCHY_GT["title_above_rate"] == 0.662
    assert TEXT_HIERARCHY_GT["info_on_underlay_rate"] == 0.524

    prompt = ComposeConcept._build_prompt(_spec(), n=3)
    assert "TEXT HIERARCHY" in prompt
    assert "upper-middle band" in prompt
    assert "do not bury it below minor info lines" in prompt
    assert "semantic reading order" in prompt


def test_rejection_block_shows_prev_concept_and_criticisms():
    """Step 84: on a design reject, the Director must see WHAT was rejected
    and WHY, and be told to revise -- not regenerate blind."""
    from metagpt.ext.agentlayout.schema import (
        AestheticFeedback,
        CompositionConcept,
        ConceptBatch,
        VisualObservation,
        VisualObservationKind,
    )

    prev = ConceptBatch(concepts=[CompositionConcept(**_concept_obj("Old plan"))])
    fb = AestheticFeedback(
        common_issues="hierarchy unclear",
        suggestions=["group the subtitle with the title"],
        visual_observations=[VisualObservation(
            kind=VisualObservationKind.TEXT_OVERLAP, target_id="text_1",
            second_id="text_2", note="body lines collide")],
    )
    prompt = ComposeConcept._build_prompt(_spec(), n=1, feedback=fb, prev_concepts=prev)
    assert "previous concept was REJECTED" in prompt
    assert "Old plan" in prompt
    assert "hierarchy unclear" in prompt
    assert "group the subtitle with the title" in prompt
    assert "[text_overlap] text_1: body lines collide" in prompt
    assert "Do NOT resubmit the same" in prompt
    # Step 88: bbox-carrying observations become locked concept constraints.
    fb.visual_observations[0].target_bbox = [20, 276, 802, 373]
    prompt2 = ComposeConcept._build_prompt(_spec(), n=1, feedback=fb, prev_concepts=prev)
    assert "LEDGER CONSTRAINTS" in prompt2
    assert "text_1 must end up INSIDE bbox [20, 276, 802, 373]" in prompt2
    # cold start keeps the prompt clean
    assert "REJECTED" not in ComposeConcept._build_prompt(_spec(), n=1)


def test_region_position_words_grid():
    assert ComposeConcept._region_position_words([0, 0, 100, 100], 1000, 1000) == "top-left"
    assert ComposeConcept._region_position_words([450, 450, 550, 550], 1000, 1000) == "middle-center"
    assert ComposeConcept._region_position_words([900, 900, 1000, 1000], 1000, 1000) == "bottom-right"


# ---------------------------------------------------------------- run()


@pytest.mark.asyncio
async def test_run_parses_three_concepts():
    canned = json.dumps([_concept_obj("A"), _concept_obj("B"), _concept_obj("C")])
    action = ComposeConcept()
    fake = _FakeLLM(supports_vision=False, canned_response=canned)
    object.__setattr__(action, "llm", fake)
    batch = await action.run(spec=_spec())
    assert len(batch.concepts) == 3
    assert len(fake.calls) == 1  # parsed on first attempt, no retry


@pytest.mark.asyncio
async def test_run_falls_back_when_llm_unparseable():
    action = ComposeConcept()
    fake = _FakeLLM(supports_vision=False, canned_response="I cannot do that.")
    object.__setattr__(action, "llm", fake)
    batch = await action.run(spec=_spec())
    # No raise: graceful single-concept fallback after MAX_RETRIES attempts.
    assert len(batch.concepts) == 1
    assert batch.concepts[0].name == "Centred safe"
    assert len(fake.calls) == 3  # MAX_RETRIES exhausted
