"""LLM-driven LayoutGeneratorRole corner pytest — opt-in via `-m requires_llm`.

Mirrors layout_agent/output/verify_generator_corner.py three cases. Default-skipped
by tests/metagpt/ext/agentlayout/conftest.py.

Cost when run: ~$0.15-0.20 (3 text-only gpt-4o calls).
"""
from __future__ import annotations

from typing import Optional

import pytest

from metagpt.ext.agentlayout.actions.generate_layout import GenerateLayout
from metagpt.ext.agentlayout.pipeline import default_white_background
from metagpt.ext.agentlayout.schema import (
    Canvas,
    CandidatesBatch,
    DesignSpec,
    Element,
    HardConstraint,
    HardConstraintRule,
    LayoutTree,
    LayoutTreeNode,
    SemanticType,
    VisualType,
)
from metagpt.ext.agentlayout.tools.quality_checker import (
    POSITION_HINT_TO_BANDS,
    filter_valid,
)


# ============================================================
# Fixture helpers
# ============================================================


def _el(
    eid: str,
    semantic_type: SemanticType,
    visual_type: VisualType,
    *,
    content: Optional[str] = None,
    asset_ref: Optional[str] = None,
    importance: int,
    relevance: float,
) -> Element:
    return Element(
        id=eid,
        semantic_type=semantic_type,
        visual_type=visual_type,
        content=content,
        asset_ref=asset_ref,
        inferred=False,
        importance=importance,
        semantic_relevance=relevance,
    )


def _flat_tree(spec: DesignSpec) -> LayoutTree:
    return LayoutTree(
        root=LayoutTreeNode(
            id="root",
            children=[LayoutTreeNode(id=el.id) for el in spec.foreground_elements()],
        )
    )


def _band(coord: float, total: int) -> int:
    third = total / 3
    if coord < third:
        return 0
    if coord < 2 * third:
        return 1
    return 2


# ============================================================
# Case 1 — Tight canvas + QC keeps at least 1 valid candidate
# ============================================================


@pytest.mark.requires_llm
@pytest.mark.asyncio
async def test_generator_tight_canvas_keeps_at_least_one_valid_candidate():
    spec = DesignSpec(
        canvas=Canvas(width=600, height=400),
        elements=[
            _el("headline_1", SemanticType.TITLE, VisualType.TEXT,
                content="HELLO", importance=5, relevance=0.9),
            _el("body_text_1", SemanticType.BODY_TEXT, VisualType.TEXT,
                content="Lorem ipsum body text here",
                importance=3, relevance=0.5),
            _el("logo_1", SemanticType.LOGO, VisualType.IMAGE,
                asset_ref="/tmp/logo.png", importance=4, relevance=0.4),
        ],
        hard_constraints=[
            HardConstraint(
                rule=HardConstraintRule.SIZE_PREFERENCE,
                targets=["headline_1"],
                params={"hint": "prominent"},
            ),
            HardConstraint(
                rule=HardConstraintRule.NO_OVERLAP,
                targets=["headline_1", "body_text_1", "logo_1"],
                params={},
            ),
        ],
        style_keywords=["bold"],
        language="en",
    )
    tree = _flat_tree(spec)
    bg = default_white_background(spec.canvas)

    batch = await GenerateLayout().run(spec=spec, tree=tree, bg=bg, feedback=None)

    assert isinstance(batch, CandidatesBatch)
    assert len(batch.candidates) >= 3

    valid, reports = filter_valid(batch.candidates, spec)
    assert len(reports) == len(batch.candidates)
    assert len(valid) >= 1, (
        f"no valid candidates among {len(batch.candidates)}; "
        f"violations={[v.type.value for r in reports for v in r.violations]}"
    )


# ============================================================
# Case 2 — position_preference top_right honored majority of the time
# ============================================================


@pytest.mark.requires_llm
@pytest.mark.asyncio
async def test_generator_position_top_right_honored_at_least_half():
    spec = DesignSpec(
        canvas=Canvas(width=1080, height=1080),
        elements=[
            _el("headline_1", SemanticType.TITLE, VisualType.TEXT,
                content="Big Sale", importance=5, relevance=0.9),
            _el("body_text_1", SemanticType.BODY_TEXT, VisualType.TEXT,
                content="Limited time", importance=3, relevance=0.4),
            _el("logo_1", SemanticType.LOGO, VisualType.IMAGE,
                asset_ref="/tmp/logo.png", importance=4, relevance=0.4),
        ],
        hard_constraints=[
            HardConstraint(
                rule=HardConstraintRule.POSITION_PREFERENCE,
                targets=["logo_1"],
                params={"hint": "top_right"},
            ),
        ],
        style_keywords=["clean"],
        language="en",
    )
    tree = _flat_tree(spec)
    bg = default_white_background(spec.canvas)

    batch = await GenerateLayout().run(spec=spec, tree=tree, bg=bg, feedback=None)

    expected_band = POSITION_HINT_TO_BANDS["top_right"]
    cw, ch = spec.canvas.width, spec.canvas.height

    assert all("logo_1" in {e.id for e in c.elements} for c in batch.candidates)

    n_in_band = 0
    for c in batch.candidates:
        logo = next(e for e in c.elements if e.id == "logo_1")
        cx = logo.left + logo.width / 2
        cy = logo.top + logo.height / 2
        if (_band(cx, cw), _band(cy, ch)) == expected_band:
            n_in_band += 1

    assert n_in_band >= 1, "no candidate honored top_right hint"
    assert n_in_band / len(batch.candidates) >= 0.5, (
        f"honor ratio {n_in_band}/{len(batch.candidates)} < 0.5"
    )


# ============================================================
# Case 3 — z_index ordering (background_image z < foreground z)
# ============================================================


@pytest.mark.requires_llm
@pytest.mark.asyncio
async def test_generator_bg_z_below_foreground_z_majority():
    spec = DesignSpec(
        canvas=Canvas(
            width=1080,
            height=1080,
            background_asset_ref="/tmp/bg.jpg",
        ),
        elements=[
            _el("bg_1", SemanticType.BACKGROUND_IMAGE, VisualType.IMAGE,
                asset_ref="/tmp/bg.jpg", importance=2, relevance=0.5),
            _el("headline_1", SemanticType.TITLE, VisualType.TEXT,
                content="Welcome", importance=5, relevance=0.9),
            _el("logo_1", SemanticType.LOGO, VisualType.IMAGE,
                asset_ref="/tmp/logo.png", importance=4, relevance=0.4),
        ],
        hard_constraints=[],
        style_keywords=["modern"],
        language="en",
    )
    tree = _flat_tree(spec)  # excludes bg_1 per foreground_elements()
    bg = default_white_background(spec.canvas)

    batch = await GenerateLayout().run(spec=spec, tree=tree, bg=bg, feedback=None)

    fg_ids = {"headline_1", "logo_1"}
    assert all(
        {"bg_1", "headline_1", "logo_1"} == {e.id for e in c.elements}
        for c in batch.candidates
    )

    correct = 0
    for c in batch.candidates:
        z_by_id = {e.id: e.z_index for e in c.elements}
        if z_by_id["bg_1"] < min(z_by_id[i] for i in fg_ids):
            correct += 1

    assert correct >= 1
    assert correct / len(batch.candidates) >= 0.6, (
        f"bg_below_fg ratio {correct}/{len(batch.candidates)} < 0.6"
    )
