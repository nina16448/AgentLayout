"""Unit tests for the GenerateLayout (CoordinateMapper) vision channel.

These tests do NOT hit a real LLM; they install a fake llm onto the action and
verify the image-payload plumbing and refusal fallback only.

"先想再畫" refactor (2026-06-25): ``run()`` now requires a ``concept`` and emits
ONE candidate. The Step 65 self-render channel (prev_render_path) was a negative
result and has been removed, so its tests are gone too.
"""
from __future__ import annotations

import base64
import json
from io import BytesIO
from typing import Any, List, Optional

import pytest
from PIL import Image

from metagpt.ext.agentlayout.actions.generate_layout import GenerateLayout
from metagpt.ext.agentlayout.pipeline import default_white_background
from metagpt.ext.agentlayout.schema import (
    Canvas,
    CompositionConcept,
    DesignSpec,
    Element,
    LayoutTree,
    LayoutTreeNode,
    SemanticType,
    VisualType,
)


def _bg_png_to_path(path) -> None:
    img = Image.new("RGB", (64, 96), color=(200, 220, 240))
    img.save(path, format="PNG")


def _make_spec(bg_path: Optional[str]) -> DesignSpec:
    el = Element(
        id="title_1",
        semantic_type=SemanticType.TITLE,
        visual_type=VisualType.TEXT,
        content="HELLO",
        inferred=False,
        importance=5,
        semantic_relevance=0.9,
    )
    return DesignSpec(
        canvas=Canvas(width=600, height=400, background_asset_ref=bg_path),
        elements=[el],
        hard_constraints=[],
        style_keywords=["bold"],
        language="en",
    )


def _flat_tree(spec: DesignSpec) -> LayoutTree:
    return LayoutTree(
        root=LayoutTreeNode(
            id="root",
            children=[LayoutTreeNode(id=el.id) for el in spec.foreground_elements()],
        )
    )


def _concept() -> CompositionConcept:
    return CompositionConcept(
        name="Centred",
        focal_element="title_1",
        focal_placement="centred",
        text_placement="centred",
        visual_flow="top-down",
        whitespace="even",
        typography_mood="bold",
    )


class _FakeLLM:
    """Records every aask call so the test can assert on (prompt, images)."""

    def __init__(self, *, supports_vision: bool, canned_response: str):
        self.supports_vision = supports_vision
        self.canned_response = canned_response
        self.calls: List[dict] = []
        self.model = "fake-vision-mock"

    def support_image_input(self) -> bool:
        return self.supports_vision

    async def aask(self, prompt: str, images: Optional[List[str]] = None, **kwargs: Any) -> str:
        self.calls.append(
            {"prompt_head": prompt[:80], "prompt": prompt, "images": images, "kwargs": kwargs}
        )
        return self.canned_response


_CANNED_BATCH_JSON = json.dumps(
    {
        "candidates": [
            {
                "candidate_id": "cand_01",
                "elements": [
                    {
                        "id": "title_1",
                        "left": 50,
                        "top": 50,
                        "width": 500,
                        "height": 100,
                        "angle": 0,
                        "z_index": 3,
                        "font_family": "sans-serif",
                        "font_size": 48,
                        "font_weight": "bold",
                        "color": "#111111",
                        "text_align": "center",
                    }
                ],
            }
        ]
    }
)


def test_render_bg_image_returns_none_when_no_ref():
    spec = _make_spec(bg_path=None)
    assert GenerateLayout._render_bg_image(spec) is None


def test_render_bg_image_returns_none_when_path_missing(tmp_path):
    spec = _make_spec(bg_path=str(tmp_path / "does_not_exist.png"))
    assert GenerateLayout._render_bg_image(spec) is None


def test_render_bg_image_returns_base64_for_existing_png(tmp_path):
    bg = tmp_path / "bg.png"
    _bg_png_to_path(bg)
    spec = _make_spec(bg_path=str(bg))
    b64 = GenerateLayout._render_bg_image(spec)
    assert isinstance(b64, str)
    assert len(b64) > 0
    raw = base64.b64decode(b64)
    decoded = Image.open(BytesIO(raw))
    decoded.verify()


def test_render_bg_image_caps_longest_edge_at_768(tmp_path):
    big = Image.new("RGB", (2000, 1500), color=(120, 90, 60))
    big_path = tmp_path / "big_bg.png"
    big.save(big_path, format="PNG")
    spec = _make_spec(bg_path=str(big_path))
    b64 = GenerateLayout._render_bg_image(spec)
    assert b64 is not None
    decoded = Image.open(BytesIO(base64.b64decode(b64)))
    assert max(decoded.size) == 768
    assert min(decoded.size) == 576  # 1500 * (768/2000) rounded


def test_load_image_b64_returns_none_for_missing_path(tmp_path):
    assert GenerateLayout._load_image_b64(tmp_path / "nope.png") is None


def test_load_image_b64_returns_none_for_unreadable_file(tmp_path):
    garbage = tmp_path / "garbage.png"
    garbage.write_bytes(b"not a png at all")
    assert GenerateLayout._load_image_b64(garbage) is None


@pytest.mark.asyncio
async def test_run_attaches_image_when_llm_supports_vision_and_bg_exists(tmp_path):
    bg = tmp_path / "bg.png"
    _bg_png_to_path(bg)
    spec = _make_spec(bg_path=str(bg))
    tree = _flat_tree(spec)
    bg_analysis = default_white_background(spec.canvas)

    fake = _FakeLLM(supports_vision=True, canned_response=_CANNED_BATCH_JSON)
    gen = GenerateLayout()
    object.__setattr__(gen, "llm", fake)  # bypass pydantic frozen field

    batch = await gen.run(spec=spec, tree=tree, bg=bg_analysis, concept=_concept())
    assert len(batch.candidates) == 1
    assert len(fake.calls) == 1
    assert isinstance(fake.calls[0]["images"], list)
    assert len(fake.calls[0]["images"]) == 1
    assert isinstance(fake.calls[0]["images"][0], str)


@pytest.mark.asyncio
async def test_run_skips_image_when_llm_lacks_vision_support(tmp_path):
    bg = tmp_path / "bg.png"
    _bg_png_to_path(bg)
    spec = _make_spec(bg_path=str(bg))
    tree = _flat_tree(spec)
    bg_analysis = default_white_background(spec.canvas)

    fake = _FakeLLM(supports_vision=False, canned_response=_CANNED_BATCH_JSON)
    gen = GenerateLayout()
    object.__setattr__(gen, "llm", fake)

    await gen.run(spec=spec, tree=tree, bg=bg_analysis, concept=_concept())
    assert len(fake.calls) == 1
    assert fake.calls[0]["images"] is None


@pytest.mark.asyncio
async def test_run_skips_image_when_bg_unavailable_even_with_vision_llm(tmp_path):
    spec = _make_spec(bg_path=None)
    tree = _flat_tree(spec)
    bg_analysis = default_white_background(spec.canvas)

    fake = _FakeLLM(supports_vision=True, canned_response=_CANNED_BATCH_JSON)
    gen = GenerateLayout()
    object.__setattr__(gen, "llm", fake)

    await gen.run(spec=spec, tree=tree, bg=bg_analysis, concept=_concept())
    assert len(fake.calls) == 1
    assert fake.calls[0]["images"] is None


# ============================================================
# Vision refusal fallback (carried over from Step 64)
# ============================================================


class _RefusingVisionLLM(_FakeLLM):
    """Refuses whenever an image is attached; answers normally text-only."""

    async def aask(self, prompt: str, images: Optional[List[str]] = None, **kwargs: Any) -> str:
        self.calls.append(
            {"prompt_head": prompt[:80], "prompt": prompt, "images": images, "kwargs": kwargs}
        )
        if images:
            return "I'm sorry, I can't assist with that."
        return self.canned_response


class _AlwaysRefusingLLM(_FakeLLM):
    """Refuses every call, with or without an image."""

    async def aask(self, prompt: str, images: Optional[List[str]] = None, **kwargs: Any) -> str:
        self.calls.append(
            {"prompt_head": prompt[:80], "prompt": prompt, "images": images, "kwargs": kwargs}
        )
        return "I'm sorry, I can't assist with that."


def test_looks_like_refusal_detection():
    f = GenerateLayout._looks_like_refusal
    assert f("I'm sorry, I can't assist with that.")
    assert f("  I cannot assist with that request. ")
    assert f("I'm unable to assist with this request.")
    assert f(
        "I'm unable to provide specific coordinates for this layout. "
        "However, I can offer general guidance on layout principles: " + "x" * 300
    )
    assert not f(_CANNED_BATCH_JSON)
    assert not f("")
    # head scan: a refusal phrase buried deep in a long response is not a refusal
    assert not f("x" * 300 + " i'm sorry")


@pytest.mark.asyncio
async def test_run_drops_image_after_vision_refusal(tmp_path):
    bg = tmp_path / "bg.png"
    _bg_png_to_path(bg)
    spec = _make_spec(bg_path=str(bg))
    tree = _flat_tree(spec)
    bg_analysis = default_white_background(spec.canvas)

    fake = _RefusingVisionLLM(supports_vision=True, canned_response=_CANNED_BATCH_JSON)
    gen = GenerateLayout()
    object.__setattr__(gen, "llm", fake)

    batch = await gen.run(spec=spec, tree=tree, bg=bg_analysis, concept=_concept())
    assert len(batch.candidates) == 1
    assert len(fake.calls) == 2
    assert fake.calls[0]["images"], "first call must attach the background image"
    assert not fake.calls[1]["images"], "fallback call must be text-only"


@pytest.mark.asyncio
async def test_run_raises_when_text_only_fallback_also_refuses(tmp_path):
    bg = tmp_path / "bg.png"
    _bg_png_to_path(bg)
    spec = _make_spec(bg_path=str(bg))
    tree = _flat_tree(spec)
    bg_analysis = default_white_background(spec.canvas)

    fake = _AlwaysRefusingLLM(supports_vision=True, canned_response=_CANNED_BATCH_JSON)
    gen = GenerateLayout()
    object.__setattr__(gen, "llm", fake)

    with pytest.raises(ValueError, match="could not produce a valid CandidatesBatch"):
        await gen.run(spec=spec, tree=tree, bg=bg_analysis, concept=_concept())
    # 1 vision refusal (grants +1 budget) + 3 text-only attempts = 4 calls
    assert len(fake.calls) == 4
    assert fake.calls[0]["images"]
    assert all(not c["images"] for c in fake.calls[1:])
