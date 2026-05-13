"""LLM-driven AnalystRole corner pytest — opt-in via `-m requires_llm`.

Mirrors layout_agent/output/verify_analyst_corner.py three cases. Default-skipped
by tests/metagpt/ext/agentlayout/conftest.py to keep CI deterministic and free.

Cost when run: ~$0.10-0.15 (3 text-only LLM calls, gpt-4o).

Run only this file:
    pytest tests/metagpt/ext/agentlayout/test_analyst_corner.py -m requires_llm -v --no-cov

Run all LLM-marked tests in this dir:
    pytest tests/metagpt/ext/agentlayout/ -m requires_llm -v --no-cov

Default invocation (no marker) — these 3 tests SKIP automatically:
    pytest tests/metagpt/ext/agentlayout/ -v --no-cov
"""
from __future__ import annotations

from pathlib import Path

import pytest

from metagpt.ext.agentlayout.actions.analyze_brief import AnalyzeBrief, AssetInput
from metagpt.ext.agentlayout.schema import DesignSpec
from metagpt.ext.agentlayout.tools.asset_analyzer import AssetAnalyzer


from metagpt.const import METAGPT_ROOT

SHARED_IMAGE = (
    METAGPT_ROOT / "layout_agent" / "output"
    / "crello_5efdd2dd499b85dcc75ba0bc"
    / "asset_00_image.png"
)


def _has_cjk(text: str) -> bool:
    if not text:
        return False
    for ch in text:
        code = ord(ch)
        if (
            0x4E00 <= code <= 0x9FFF
            or 0x3400 <= code <= 0x4DBF
            or 0x3040 <= code <= 0x30FF
            or 0x3100 <= code <= 0x312F
        ):
            return True
    return False


# ============================================================
# Case 1 — CJK / zh-TW input
# ============================================================


@pytest.mark.requires_llm
@pytest.mark.asyncio
async def test_analyst_cjk_input_preserves_chars_and_detects_language():
    """zh-TW brief + CJK title -> language!=en, CJK preserved, canvas correct."""
    if not SHARED_IMAGE.exists():
        pytest.skip(f"missing fixture: {SHARED_IMAGE}")

    user_brief = (
        "設計一張 1080x1080 的台灣中秋節宣傳海報，主題是月圓人團圓，"
        "需要月亮意象與中文 slogan。整體色調溫暖、布局乾淨。"
    )
    asset_list = [
        AssetInput(asset_ref=str(SHARED_IMAGE)),
        AssetInput(content="中秋快樂\n月圓人團圓"),
    ]

    spec = await AnalyzeBrief().run(
        user_brief=user_brief, asset_list=asset_list, feedback=None
    )
    AssetAnalyzer().run(spec)

    assert isinstance(spec, DesignSpec)
    assert spec.canvas.width > 0 and spec.canvas.height > 0
    text_elements = [e for e in spec.elements if e.visual_type.value == "text"]
    assert len(text_elements) >= 1
    assert any(_has_cjk(e.content or "") for e in text_elements)
    assert (spec.language or "").lower() != "en", f"got language={spec.language!r}"


# ============================================================
# Case 2 — Empty asset_list (graceful fallback)
# ============================================================


@pytest.mark.requires_llm
@pytest.mark.asyncio
async def test_analyst_empty_asset_list_returns_graceful_spec():
    """asset_list=[] -> DesignSpec with elements=[] or all-inferred elements."""
    user_brief = (
        "Design a 1200x800 promotional poster. Clean, modern aesthetic, "
        "light background. Style: minimal, professional."
    )
    spec = await AnalyzeBrief().run(
        user_brief=user_brief, asset_list=[], feedback=None
    )
    AssetAnalyzer().run(spec)

    assert isinstance(spec, DesignSpec)
    assert spec.canvas.width > 0 and spec.canvas.height > 0
    if spec.elements:
        assert all(e.inferred for e in spec.elements)
    else:
        assert len(spec.elements) == 0


# ============================================================
# Case 3 — Ambiguous brief (inferred_fields propagation)
# ============================================================


@pytest.mark.requires_llm
@pytest.mark.asyncio
async def test_analyst_ambiguous_brief_marks_inferred_fields():
    """'Make something nice' -> canvas dims and semantic_types marked inferred."""
    if not SHARED_IMAGE.exists():
        pytest.skip(f"missing fixture: {SHARED_IMAGE}")

    asset_list = [
        AssetInput(asset_ref=str(SHARED_IMAGE)),
        AssetInput(content="Welcome"),
    ]
    spec = await AnalyzeBrief().run(
        user_brief="Make something nice for me.",
        asset_list=asset_list,
        feedback=None,
    )
    AssetAnalyzer().run(spec)

    assert isinstance(spec, DesignSpec)
    assert len(spec.inferred_fields) >= 1
    assert any(
        k in spec.inferred_fields and spec.inferred_fields[k]
        for k in ("canvas.width", "canvas.height")
    ), f"inferred_fields={list(spec.inferred_fields)}"
    assert any(e.inferred for e in spec.elements)
    assert all(
        k.startswith(("canvas.", "elements.", "style_keywords", "language"))
        for k in spec.inferred_fields
    ), f"unknown keys: {list(spec.inferred_fields)}"
