"""LLM-driven AssetPlannerRole corner pytest — opt-in via `-m requires_llm`.

Mirrors layout_agent/output/verify_planner_corner.py three cases. Default-skipped
by tests/metagpt/ext/agentlayout/conftest.py.

Cost when run: ~$0.15-0.20 (3 text-only gpt-4o calls; Case 2 retries 3x by design).
"""
from __future__ import annotations

from typing import List, Optional

import pytest
from pydantic import ValidationError

from metagpt.ext.agentlayout.actions.plan_assets import PlanAssets
from metagpt.ext.agentlayout.schema import (
    Canvas,
    DesignSpec,
    Element,
    LayoutTree,
    SemanticType,
    VisualType,
)


# ============================================================
# Fixture builders
# ============================================================


def _enriched_element(
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


def _collect_ids(node, out: List[str]) -> None:
    if node.id != "root":
        out.append(node.id)
    for ch in node.children:
        _collect_ids(ch, out)


def _depth(node, current: int = 0) -> int:
    if not node.children:
        return current
    return max(_depth(ch, current + 1) for ch in node.children)


# ============================================================
# Case 1 — Minimal single-element spec
# ============================================================


@pytest.mark.requires_llm
@pytest.mark.asyncio
async def test_planner_minimal_single_element_tree():
    spec = DesignSpec(
        canvas=Canvas(width=1080, height=1080),
        elements=[
            _enriched_element(
                "text_1",
                SemanticType.TITLE,
                VisualType.TEXT,
                content="Hello",
                importance=5,
                relevance=0.8,
            ),
        ],
        style_keywords=["clean"],
        language="en",
    )

    tree = await PlanAssets().run(spec=spec)

    assert isinstance(tree, LayoutTree)
    ids: List[str] = []
    _collect_ids(tree.root, ids)
    assert ids == ["text_1"], f"got {ids}"
    assert len(tree.root.children) == 1
    assert _depth(tree.root) == 1


# ============================================================
# Case 2 — Duplicate element ids (design-boundary probe)
# ============================================================


@pytest.mark.requires_llm
@pytest.mark.asyncio
async def test_planner_duplicate_ids_raises_value_error():
    """Pydantic accepts dup ids; PlanAssets internal _validate_against_spec
    must reject after MAX_RETRIES with a clear ValueError."""
    spec = DesignSpec(
        canvas=Canvas(width=1080, height=1080),
        elements=[
            _enriched_element(
                "text_1", SemanticType.TITLE, VisualType.TEXT,
                content="Welcome", importance=5, relevance=0.7,
            ),
            _enriched_element(
                "text_1",  # intentional dup
                SemanticType.SUBTITLE, VisualType.TEXT,
                content="Subtitle here", importance=3, relevance=0.5,
            ),
            _enriched_element(
                "image_1", SemanticType.PRODUCT_IMAGE, VisualType.IMAGE,
                asset_ref="/tmp/placeholder.png",
                importance=4, relevance=0.7,
            ),
        ],
        style_keywords=["clean"],
        language="en",
    )

    with pytest.raises((ValueError, ValidationError)) as exc_info:
        await PlanAssets().run(spec=spec)

    msg = str(exc_info.value).lower()
    assert "duplicate" in msg or "text_1" in msg or "attempts" in msg, (
        f"unhelpful error message: {exc_info.value}"
    )


# ============================================================
# Case 3 — Large spec triggers semantic grouping
# ============================================================


@pytest.mark.requires_llm
@pytest.mark.asyncio
async def test_planner_large_spec_groups_into_nested_tree():
    """10-element spec -> tree must cover all ids exactly once and be nested."""
    specs = [
        ("product_img_1", SemanticType.PRODUCT_IMAGE, VisualType.IMAGE, None, "/tmp/p1.png", 5, 0.9),
        ("logo_1", SemanticType.LOGO, VisualType.IMAGE, None, "/tmp/logo.png", 4, 0.4),
        ("headline_1", SemanticType.TITLE, VisualType.TEXT, "BIG SALE", None, 5, 0.85),
        ("subtitle_1", SemanticType.SUBTITLE, VisualType.TEXT, "This summer only", None, 4, 0.7),
        ("cta_1", SemanticType.CTA, VisualType.TEXT, "Shop Now", None, 4, 0.6),
        ("pricetag_1", SemanticType.PRICETAG, VisualType.TEXT, "$29.99", None, 4, 0.8),
        ("caption_1", SemanticType.CAPTION, VisualType.TEXT, "Free shipping over $50", None, 3, 0.5),
        ("caption_2", SemanticType.CAPTION, VisualType.TEXT, "30-day return", None, 3, 0.5),
        ("icon_1", SemanticType.ICON, VisualType.IMAGE, None, "/tmp/star.png", 2, 0.3),
        ("body_text_1", SemanticType.BODY_TEXT, VisualType.TEXT, "Limited stock left.", None, 3, 0.4),
    ]
    elements = [
        _enriched_element(eid, st, vt, content=content, asset_ref=asset_ref,
                          importance=imp, relevance=rel)
        for eid, st, vt, content, asset_ref, imp, rel in specs
    ]
    spec = DesignSpec(
        canvas=Canvas(width=1080, height=1920),
        elements=elements,
        style_keywords=["modern", "bold", "energetic"],
        language="en",
    )
    spec_ids = [e.id for e in elements]

    tree = await PlanAssets().run(spec=spec)

    assert isinstance(tree, LayoutTree)
    ids: List[str] = []
    _collect_ids(tree.root, ids)
    assert sorted(ids) == sorted(spec_ids), (
        f"missing={set(spec_ids)-set(ids)} extra={set(ids)-set(spec_ids)}"
    )
    assert len(ids) == len(set(ids)), f"dup in tree: {ids}"
    assert _depth(tree.root) >= 2, f"flat tree at depth {_depth(tree.root)}"
    assert len(tree.root.children) < 10, (
        f"root has {len(tree.root.children)} children, no grouping happened"
    )
