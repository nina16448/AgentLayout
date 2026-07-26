"""A3 Coordinate Mapper contract: one concept to one pixel candidate.

Pure schemas, prompt builder, parser and validators; the LLM wiring lives in
``actions/generate_layout_a3.py``. The prompt exposes only normalized bitmap
aspect ratios — no original or normalized pixel sizes can leak (R3 rule).
The same contract carries the single L1 revision call via an optional
revision instruction plus the B0 elements as the editing base.
"""
from __future__ import annotations

import hashlib
import json
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from metagpt.ext.agentlayout.layout_tree_v3 import TreeCondition, condition_prompt_payload
from metagpt.ext.agentlayout.schema import Candidate, CompositionConcept
from metagpt.ext.agentlayout.tools.text_bitmap_normalizer import R3AssetManifest


A3_MAPPER_REQUEST_VERSION = "a3.mapper-request.v1"


class A3MapperRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["a3.mapper-request.v1"] = A3_MAPPER_REQUEST_VERSION
    prompt: str
    prompt_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    tree_arm: str
    mode: Literal["r0", "revision"]


def validate_candidate_coverage(candidate: Candidate, asset_ids: List[str]) -> None:
    expected = set(asset_ids)
    actual = [element.id for element in candidate.elements]
    if len(actual) != len(set(actual)):
        duplicates = sorted({asset_id for asset_id in actual if actual.count(asset_id) > 1})
        raise ValueError(
            f"candidate places at least one asset more than once: {duplicates}; "
            "visually identical assets are still distinct asset IDs"
        )
    if set(actual) != expected:
        raise ValueError(
            f"candidate coverage mismatch: missing={sorted(expected - set(actual))}, "
            f"extra={sorted(set(actual) - expected)}"
        )


def _prompt_assets(manifest: R3AssetManifest) -> List[Dict]:
    return [
        {
            "asset_id": asset.asset_id,
            "media_type": asset.media_type,
            "content": asset.content,
            "bitmap_aspect_ratio": round(asset.bitmap_aspect_ratio, 6),
        }
        for asset in manifest.foreground_assets()
    ]


def build_mapper_prompt(
    *,
    concept: CompositionConcept,
    condition: TreeCondition,
    manifest: R3AssetManifest,
    revision_instruction: Optional[str] = None,
    base_elements: Optional[List[Dict]] = None,
) -> str:
    schema = Candidate.model_json_schema()
    revision_block = ""
    if revision_instruction is not None:
        revision_block = f"""
# Revision mode
You are revising an already-selected layout. Start from the base elements
below and apply ONLY the requested change.

## Base elements (B0)
{json.dumps(base_elements or [], ensure_ascii=False, indent=2)}

## Revision instruction
{revision_instruction}
"""
    return f"""Role: You are the Coordinate Mapper in AgentLayout A3.

The attached image is the base background canvas. Translate the composition
concept into exact pixel coordinates for every foreground asset.

# Canvas (top-left origin)
{manifest.canvas_width}x{manifest.canvas_height}

# Composition concept
{json.dumps(concept.model_dump(mode="json"), ensure_ascii=False, indent=2)}

# Foreground assets
{json.dumps(_prompt_assets(manifest), ensure_ascii=False, indent=2)}

# Structure
{json.dumps(condition_prompt_payload(condition), ensure_ascii=False, indent=2)}
{revision_block}
# Rules
- Output one bbox per asset ID, each asset exactly once; never invent IDs.
- Visually similar or identical assets are still DISTINCT asset IDs: place
  every listed asset_id exactly once and never repeat an ID.
- Choose each element's size from the design context and the canvas; keep
  each bitmap's aspect ratio (width/height must match bitmap_aspect_ratio).
- Keep every bbox fully inside the canvas.
- Assign z_index so overlapping elements stack intentionally.
- candidate_id must be "candidate".

# Output JSON Schema
{json.dumps(schema, ensure_ascii=False, indent=2)}

Output one JSON object only, without markdown fences."""


def build_mapper_request(
    *,
    concept: CompositionConcept,
    condition: TreeCondition,
    manifest: R3AssetManifest,
    revision_instruction: Optional[str] = None,
    base_elements: Optional[List[Dict]] = None,
) -> A3MapperRequest:
    prompt = build_mapper_prompt(
        concept=concept,
        condition=condition,
        manifest=manifest,
        revision_instruction=revision_instruction,
        base_elements=base_elements,
    )
    return A3MapperRequest(
        prompt=prompt,
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        tree_arm=condition.arm,
        mode="revision" if revision_instruction is not None else "r0",
    )


def parse_candidate(response: str) -> Candidate:
    text = (response or "").strip()
    if not text.startswith("{") or "```" in text:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    return Candidate.model_validate(json.loads(text))
