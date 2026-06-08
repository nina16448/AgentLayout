"""Asset Planner Action — Agent 2 of the AgentLayout pipeline.

Takes an *enriched* DesignSpec (Asset Analyzer must have filled
``importance`` and ``semantic_relevance``) and produces a ``LayoutTree``
that captures semantic parent-child relationships among foreground
elements.

Pipeline position::

    enriched DesignSpec  ->  PlanAssets (THIS)  ->  LayoutTree
                                                    (no background, single 'root',
                                                     all foreground ids exactly once)

The prompt is a verbatim port of ``layout_agent/asset_planner.md``.
Two layers of validation:

1. Pydantic schema (``LayoutTree`` / ``LayoutTreeNode``):
   - root.id == 'root'
   - extra='forbid' on nodes (only id / children allowed)
2. Action-level semantic check (``_validate_against_spec``):
   - tree element ids equal spec.foreground_elements() ids
   - no duplicate id appears in the tree
   (orphan-node check is implicit: tree structure forbids orphans by construction)
"""
from __future__ import annotations

import json
from typing import List, Optional

from pydantic import ValidationError

from metagpt.actions import Action
from metagpt.ext.agentlayout.schema import DesignSpec, LayoutTree
from metagpt.logs import logger
from metagpt.utils.common import CodeParser


# ============================================================
# Custom exception
# ============================================================


class _LayoutTreeValidationError(ValueError):
    """Pydantic accepted the JSON, but tree contents disagree with the DesignSpec.

    Raised when element ids are missing / extra / duplicated. Caught by the
    retry loop alongside ``ValueError`` and ``ValidationError``.
    """


# ============================================================
# Prompt template (verbatim port of layout_agent/asset_planner.md)
# ============================================================


FORMAT_EXAMPLE_JSON = """{
  "layout_tree": {
    "id": "root",
    "children": [
      {
        "id": "product_img_1",
        "children": [
          {
            "id": "headline_1",
            "children": [
              {"id": "price_1", "children": []}
            ]
          }
        ]
      },
      {
        "id": "date_1",
        "children": [
          {"id": "location_1", "children": []}
        ]
      },
      {"id": "logo_1", "children": []}
    ]
  }
}"""


PROMPT_TEMPLATE = """Role: You are a graphic design content analyst.
Your goal is to analyze the semantic relationships between design elements
and organize them into a hierarchical Layout Tree.
The tree structure tells the layout generator which elements are related
and should be placed near each other.

# Context
Elements (id / semantic_type / importance / semantic_relevance):
{elements_summary}

# Tree building rules
- Parent-child relationship means: "this element describes or supplements its parent"
  (e.g. a title describing a product image -> title is child of product_img)
  (e.g. a location supplementing a date -> location is child of date)
- Elements with no clear relationship to others -> direct children of root
- importance score is a reference: higher importance tends to be closer to root,
  but semantic relationship takes priority over importance
- Do NOT include background image (semantic_type: background_image) in the tree
- DO INCLUDE decorative_image elements (underlay assets) as leaves in the tree
  -- they are foreground stacking layers placed BEHIND text/product elements
  but ABOVE the background, NOT background themselves. A decorative_image
  paired with the text/product it visually anchors should sit as a sibling
  (or child, if the pairing is explicit) of that element.
- The tree can have multiple levels of depth -- do not limit to two levels

# Format example
{format_example}

# Instruction
ATTENTION: Every foreground element id from the Design Spec must appear exactly once.
ATTENTION: Do NOT include background image (semantic_type: background_image) in the tree.
ATTENTION: decorative_image (underlay) elements MUST appear in the tree as leaves.
           They are foreground decorative shapes, NOT background. Omitting them
           triggers a hard validation error and aborts the pipeline.
ATTENTION: Do NOT create virtual group nodes -- every node must be a real element id.
ATTENTION: Nodes only have two fields: "id" and "children". No other fields allowed.
ATTENTION: Leaf nodes must have an empty children list [].
ATTENTION: Semantic relationship takes priority over importance score.
Output carefully referenced "format example" in JSON format, nothing else.
"""


MAX_RETRIES: int = 3


# ============================================================
# Action
# ============================================================


class PlanAssets(Action):
    """Agent 2 -- group foreground elements into a semantic Layout Tree."""

    name: str = "PlanAssets"
    desc: str = (
        "Analyze the semantic relationships between foreground elements and "
        "organize them into a hierarchical Layout Tree."
    )

    async def run(self, *, spec: DesignSpec) -> LayoutTree:
        """Build prompt, call LLM, parse JSON, validate against the spec.

        Pre-condition: ``spec`` must be enriched by Asset Analyzer (i.e.
        every element has ``importance`` and ``semantic_relevance`` set).
        Retries up to ``MAX_RETRIES`` times if the LLM output cannot be
        parsed as a valid LayoutTree, or fails the semantic check.
        """
        spec.assert_enriched()
        prompt = self._build_prompt(spec)

        last_err: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            rsp = await self.llm.aask(prompt)
            try:
                tree = self._parse_response(rsp)
                self._validate_against_spec(tree, spec)
                return tree
            except (ValueError, ValidationError) as err:
                last_err = err
                logger.warning(
                    f"PlanAssets attempt {attempt}/{MAX_RETRIES} failed: {err}"
                )

        raise ValueError(
            f"PlanAssets: could not produce a valid LayoutTree after "
            f"{MAX_RETRIES} attempts. Last error: {last_err}"
        )

    def _build_prompt(self, spec: DesignSpec) -> str:
        """Render PROMPT_TEMPLATE; only foreground elements appear in the summary."""
        summary_rows = [
            {
                "id": e.id,
                "semantic_type": e.semantic_type.value,
                "importance": e.importance,
                "semantic_relevance": e.semantic_relevance,
            }
            for e in spec.foreground_elements()
        ]
        return PROMPT_TEMPLATE.format(
            elements_summary=json.dumps(summary_rows, indent=2, ensure_ascii=False),
            format_example=FORMAT_EXAMPLE_JSON,
        )

    @staticmethod
    def _parse_response(rsp: str) -> LayoutTree:
        """Strip markdown fences if present, then validate structurally.

        The LLM is told to output ``{"layout_tree": {...}}`` (per the format
        example). We unwrap the outer ``layout_tree`` key here so the result
        is a clean ``LayoutTree`` instance.
        """
        text = rsp.strip()
        if "```" in text:
            try:
                text = CodeParser.parse_code(text=text, lang="json") or text
            except Exception:
                pass
        payload = json.loads(text)
        if isinstance(payload, dict) and "layout_tree" in payload:
            payload = {"root": payload["layout_tree"]}
        return LayoutTree.model_validate(payload)

    @staticmethod
    def _validate_against_spec(tree: LayoutTree, spec: DesignSpec) -> None:
        """Raise if tree element ids disagree with the spec's foreground set."""
        foreground_ids = {e.id for e in spec.foreground_elements()}
        tree_ids_list: List[str] = tree.all_element_ids()
        tree_ids_set = set(tree_ids_list)

        if len(tree_ids_list) != len(tree_ids_set):
            seen: set = set()
            dups: List[str] = []
            for tid in tree_ids_list:
                if tid in seen:
                    dups.append(tid)
                seen.add(tid)
            raise _LayoutTreeValidationError(
                f"Duplicate element ids in LayoutTree: {sorted(set(dups))}"
            )

        missing = sorted(foreground_ids - tree_ids_set)
        extra = sorted(tree_ids_set - foreground_ids)
        if missing or extra:
            raise _LayoutTreeValidationError(
                f"LayoutTree element mismatch. missing={missing}, extra={extra}"
            )
