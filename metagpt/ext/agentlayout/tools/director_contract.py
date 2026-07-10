"""A3 Composition Director contract: exactly three distinct concepts.

Pure schemas, prompt builder, parser and validators; the LLM wiring lives in
``actions/compose_concept_a3.py``. Kept import-light so contract tests run
without the heavyweight ``metagpt.actions`` dependency chain.
"""
from __future__ import annotations

import hashlib
import json
from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from metagpt.ext.agentlayout.layout_tree_v3 import TreeCondition, condition_prompt_payload
from metagpt.ext.agentlayout.schema import CompositionConcept
from metagpt.ext.agentlayout.tools.analyst_vision import A3AnalystOutput


A3_CONCEPT_SET_VERSION = "a3.concept-set.v1"
A3_DIRECTOR_REQUEST_VERSION = "a3.director-request.v1"
A3_CONCEPT_COUNT = 3


class A3ConceptSet(BaseModel):
    """Exactly three composition concepts with distinct names."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["a3.concept-set.v1"] = A3_CONCEPT_SET_VERSION
    concepts: List[CompositionConcept] = Field(
        ..., min_length=A3_CONCEPT_COUNT, max_length=A3_CONCEPT_COUNT
    )

    @model_validator(mode="after")
    def _distinct_names(self) -> "A3ConceptSet":
        names = [concept.name.strip().lower() for concept in self.concepts]
        if len(set(names)) != len(names):
            raise ValueError("the three concepts must have distinct names")
        return self


class A3DirectorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["a3.director-request.v1"] = A3_DIRECTOR_REQUEST_VERSION
    prompt: str
    prompt_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    tree_arm: str


def validate_concepts_against_assets(
    concept_set: A3ConceptSet, condition: TreeCondition
) -> None:
    known = set(condition.asset_ids)
    unknown = [
        concept.focal_element
        for concept in concept_set.concepts
        if concept.focal_element not in known
    ]
    if unknown:
        raise ValueError(f"focal_element references unknown asset IDs: {unknown}")


def build_director_prompt(
    analyst: A3AnalystOutput, condition: TreeCondition, canvas: str
) -> str:
    payload = {
        "design_intent": analyst.design_intent,
        "background_summary": analyst.background_summary,
        "style_keywords": analyst.style_keywords,
        "assets": [asset.model_dump(mode="json") for asset in analyst.assets],
        **condition_prompt_payload(condition),
    }
    schema = A3ConceptSet.model_json_schema()
    return f"""Role: You are the Composition Director in AgentLayout A3.

The attached image is the base background canvas. Propose exactly
{A3_CONCEPT_COUNT} spatially DISTINCT composition concepts in natural
language; a separate Coordinate Mapper will turn each concept into exact
pixels afterwards.

# Canvas
{canvas}

# Semantic context
{json.dumps(payload, ensure_ascii=False, indent=2)}

# Rules
- The three concepts must place the focal element and the text group in
  clearly different canvas regions; do not emit three variations of one idea.
- focal_element must be one of the supplied asset IDs.
- Describe placements in natural language only. Do NOT output coordinates,
  bbox, x/y, width, height, font size or file paths.
- Respect the provided tree/grouping information when present; never invent
  assets or roles.

# Output JSON Schema
{json.dumps(schema, ensure_ascii=False, indent=2)}

Output one JSON object only, without markdown fences."""


def build_director_request(
    analyst: A3AnalystOutput, condition: TreeCondition, canvas: str
) -> A3DirectorRequest:
    prompt = build_director_prompt(analyst, condition, canvas)
    return A3DirectorRequest(
        prompt=prompt,
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        tree_arm=condition.arm,
    )


def parse_concept_set(response: str) -> A3ConceptSet:
    text = (response or "").strip()
    if not text.startswith("{") or "```" in text:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    return A3ConceptSet.model_validate(json.loads(text))
