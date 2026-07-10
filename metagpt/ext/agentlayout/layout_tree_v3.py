"""Versioned explicit Layout Tree contract for AgentLayout A3."""
from __future__ import annotations

import hashlib
import json
from enum import Enum
from pathlib import Path
from typing import Dict, List, Literal, Optional, Set, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from metagpt.ext.agentlayout.schema import SemanticType
from metagpt.ext.agentlayout.tools.analyst_vision import A3AnalystOutput
from metagpt.ext.agentlayout.run_manifest import write_json_once


A3_LAYOUT_TREE_SCHEMA_VERSION = "a3.layout-tree.v1"
A3_TREE_REQUEST_VERSION = "a3.layout-tree-request.v1"


class TreeRelation(str, Enum):
    ROOT = "root"
    CONTAINS = "contains"
    SUPPORTS = "supports"
    QUALIFIES = "qualifies"
    IDENTIFIES = "identifies"
    CALLS_TO_ACTION = "calls_to_action"
    DECORATES = "decorates"
    SEQUENCE_AFTER = "sequence_after"
    PEER = "peer"


class A3TreeNode(BaseModel):
    """One foreground asset and its explicit semantic parent relation."""

    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(..., pattern=r"^asset_[0-9]{4}$")
    semantic_type: SemanticType
    semantic_role: str = Field(..., min_length=1)
    group_id: str = Field(..., pattern=r"^group_[A-Za-z0-9._-]+$")
    group_label: str = Field(..., min_length=1)
    parent_id: Union[Literal["root"], str] = "root"
    relation_to_parent: TreeRelation
    ordering_priority: int = Field(..., ge=0)
    confidence: float = Field(..., ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _parent_contract(self) -> "A3TreeNode":
        if self.parent_id != "root" and not self.parent_id.startswith("asset_"):
            raise ValueError("parent_id must be 'root' or another stable asset ID")
        if self.parent_id == self.asset_id:
            raise ValueError("a tree node cannot parent itself")
        if self.parent_id == "root" and self.relation_to_parent != TreeRelation.ROOT:
            raise ValueError("root children must use relation_to_parent='root'")
        if self.parent_id != "root" and self.relation_to_parent == TreeRelation.ROOT:
            raise ValueError("non-root edges cannot use relation_to_parent='root'")
        return self


class A3TreeGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: str = Field(..., pattern=r"^group_[A-Za-z0-9._-]+$")
    label: str = Field(..., min_length=1)
    member_ids: List[str] = Field(..., min_length=1)
    ordering_priority: int = Field(..., ge=0)
    confidence: float = Field(..., ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _unique_members(self) -> "A3TreeGroup":
        if len(self.member_ids) != len(set(self.member_ids)):
            raise ValueError(f"group {self.group_id} contains duplicate members")
        return self


class A3LayoutTree(BaseModel):
    """Normalized tree with explicit edges and non-overlapping semantic groups."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["a3.layout-tree.v1"] = A3_LAYOUT_TREE_SCHEMA_VERSION
    source: Literal["predicted", "human_oracle"]
    root_label: str = Field(default="foreground_layout", min_length=1)
    nodes: List[A3TreeNode] = Field(..., min_length=1)
    groups: List[A3TreeGroup] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _structural_validity(self) -> "A3LayoutTree":
        node_ids = [node.asset_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("Layout Tree contains duplicate asset IDs")
        known: Set[str] = set(node_ids)
        for node in self.nodes:
            if node.parent_id != "root" and node.parent_id not in known:
                raise ValueError(
                    f"node {node.asset_id} references missing parent {node.parent_id}"
                )

        parents = {node.asset_id: node.parent_id for node in self.nodes}
        for asset_id in node_ids:
            seen: Set[str] = set()
            current = asset_id
            while current != "root":
                if current in seen:
                    raise ValueError(f"Layout Tree contains a cycle through {current}")
                seen.add(current)
                current = parents[current]

        group_ids = [group.group_id for group in self.groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("Layout Tree contains duplicate group IDs")
        groups_by_id = {group.group_id: group for group in self.groups}
        memberships: Dict[str, str] = {}
        for group in self.groups:
            unknown = set(group.member_ids) - known
            if unknown:
                raise ValueError(f"group {group.group_id} contains unknown assets {sorted(unknown)}")
            for member in group.member_ids:
                if member in memberships:
                    raise ValueError(
                        f"asset {member} appears in multiple groups: "
                        f"{memberships[member]}, {group.group_id}"
                    )
                memberships[member] = group.group_id
        missing_groups = known - set(memberships)
        if missing_groups:
            raise ValueError(f"assets missing group membership: {sorted(missing_groups)}")
        for node in self.nodes:
            group = groups_by_id.get(node.group_id)
            if group is None or memberships[node.asset_id] != node.group_id:
                raise ValueError(f"node {node.asset_id} has inconsistent group_id")
            if node.group_label != group.label:
                raise ValueError(f"node {node.asset_id} group_label disagrees with group label")
        return self


class A3TreeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["a3.layout-tree-request.v1"] = A3_TREE_REQUEST_VERSION
    prompt: str
    prompt_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")


class FlatRoleEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    semantic_type: SemanticType
    semantic_role: str


class TreeCondition(BaseModel):
    """Typed T0/T1/T2/T3 input boundary for later Mapper ablations."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    arm: Literal["T0", "T1", "T2", "T3"]
    asset_ids: List[str]
    flat_roles: Optional[List[FlatRoleEntry]] = None
    tree: Optional[A3LayoutTree] = None

    @model_validator(mode="after")
    def _arm_payload(self) -> "TreeCondition":
        if self.arm == "T0" and (self.flat_roles is not None or self.tree is not None):
            raise ValueError("T0 must not carry roles or a tree")
        if self.arm == "T1" and (self.flat_roles is None or self.tree is not None):
            raise ValueError("T1 requires flat_roles and forbids a tree")
        if self.arm in {"T2", "T3"} and (self.tree is None or self.flat_roles is not None):
            raise ValueError(f"{self.arm} requires a tree and forbids flat_roles")
        if self.arm == "T2" and self.tree and self.tree.source != "predicted":
            raise ValueError("T2 requires source='predicted'")
        if self.arm == "T3" and self.tree and self.tree.source != "human_oracle":
            raise ValueError("T3 requires source='human_oracle'")
        return self


def validate_tree_asset_coverage(tree: A3LayoutTree, analyst: A3AnalystOutput) -> None:
    expected = {asset.asset_id: asset for asset in analyst.assets}
    actual = {node.asset_id: node for node in tree.nodes}
    if set(expected) != set(actual):
        raise ValueError(
            f"Layout Tree coverage mismatch: missing={sorted(set(expected) - set(actual))}, "
            f"extra={sorted(set(actual) - set(expected))}"
        )


def validate_tree_against_analyst(tree: A3LayoutTree, analyst: A3AnalystOutput) -> None:
    """Validate the predicted T2 tree against its Analyst semantic source."""
    validate_tree_asset_coverage(tree, analyst)
    expected = {asset.asset_id: asset for asset in analyst.assets}
    actual = {node.asset_id: node for node in tree.nodes}
    semantic_mismatch = [
        asset_id
        for asset_id, node in actual.items()
        if node.semantic_type != expected[asset_id].semantic_type
    ]
    if semantic_mismatch:
        raise ValueError(f"Layout Tree semantic_type mismatch: {semantic_mismatch}")
    role_mismatch = [
        asset_id
        for asset_id, node in actual.items()
        if node.semantic_role != expected[asset_id].semantic_role
    ]
    if role_mismatch:
        raise ValueError(f"Layout Tree semantic_role mismatch: {role_mismatch}")


def build_tree_prompt(analyst: A3AnalystOutput) -> str:
    payload = {
        "design_intent": analyst.design_intent,
        "style_keywords": analyst.style_keywords,
        "assets": [asset.model_dump(mode="json") for asset in analyst.assets],
    }
    schema = A3LayoutTree.model_json_schema()
    return f"""Role: You are the Asset Planner in AgentLayout A3.

Build an explicit semantic Layout Tree BEFORE any coordinates are generated.

# Analyst semantic output
{json.dumps(payload, ensure_ascii=False, indent=2)}

# Rules
- Include every foreground asset ID exactly once. Never invent or rename IDs.
- Preserve each asset's semantic_type from the Analyst.
- Assign a concise semantic_role, exactly one semantic group, a parent, relation,
  ordering priority and confidence.
- Use parent_id="root" and relation_to_parent="root" for top-level assets.
- Every non-root parent must be another supplied asset ID; no cycles.
- Decorative assets remain represented and grouped; never drop them.
- Do NOT output coordinates, bbox, size, font size, z-index or asset paths.
- source MUST be "predicted".

# Output JSON Schema
{json.dumps(schema, ensure_ascii=False, indent=2)}

Output one JSON object only, without markdown fences."""


def build_tree_request(analyst: A3AnalystOutput) -> A3TreeRequest:
    prompt = build_tree_prompt(analyst)
    return A3TreeRequest(
        prompt=prompt,
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
    )


def save_tree_request(request: A3TreeRequest, output_dir: Path) -> None:
    """Persist the exact Planner request without allowing artifact replacement."""
    output_dir.mkdir(parents=True, exist_ok=False)
    write_json_once(output_dir / "planner_request.json", request.model_dump(mode="json"))


def parse_layout_tree(response: str) -> A3LayoutTree:
    text = (response or "").strip()
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    return A3LayoutTree.model_validate(json.loads(text))


def make_tree_condition(
    arm: Literal["T0", "T1", "T2", "T3"],
    analyst: A3AnalystOutput,
    *,
    tree: Optional[A3LayoutTree] = None,
) -> TreeCondition:
    asset_ids = [asset.asset_id for asset in analyst.assets]
    if arm == "T0":
        return TreeCondition(arm=arm, asset_ids=asset_ids)
    if arm == "T1":
        roles = [
            FlatRoleEntry(
                asset_id=asset.asset_id,
                semantic_type=asset.semantic_type,
                semantic_role=asset.semantic_role,
            )
            for asset in analyst.assets
        ]
        return TreeCondition(arm=arm, asset_ids=asset_ids, flat_roles=roles)
    if tree is None:
        raise ValueError(f"{arm} requires a tree")
    if arm == "T2":
        validate_tree_against_analyst(tree, analyst)
    else:
        # T3 is independently human-annotated and may intentionally correct
        # Analyst roles/types. Only stable-ID/sample coverage is shared.
        validate_tree_asset_coverage(tree, analyst)
    return TreeCondition(arm=arm, asset_ids=asset_ids, tree=tree)
