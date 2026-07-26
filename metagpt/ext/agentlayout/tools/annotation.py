"""Human reference-tree annotation contract for A3 Gates A/B.

Implements new_plam.md section 5.3:

- the annotation packet shows ONLY the user brief, the foreground contact
  sheet, text content and stable asset IDs — never the designer GT layout
  and (deliberately) not even the base background, so grouping judgements
  come from asset semantics alone;
- every sample needs at least two independent annotations; disagreements are
  resolved by an explicit adjudication record, never silently;
- the merged result is a normal ``A3LayoutTree`` with ``source="human_oracle"``
  plus per-pair agreement statistics, so tree metrics and the T3 arm consume
  the same versioned contract as predicted trees.
"""
from __future__ import annotations

import hashlib
import json
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Literal, Optional, Set, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from metagpt.ext.agentlayout.layout_tree_v3 import (
    A3LayoutTree,
    A3TreeGroup,
    A3TreeNode,
    TreeRelation,
)
from metagpt.ext.agentlayout.run_manifest import write_json_once
from metagpt.ext.agentlayout.schema import SemanticType
from metagpt.ext.agentlayout.tools.text_bitmap_normalizer import R3AssetManifest


A3_ANNOTATION_PACKET_VERSION = "a3.annotation-packet.v1"
A3_HUMAN_ANNOTATION_VERSION = "a3.human-annotation.v1"
A3_ADJUDICATION_VERSION = "a3.annotation-adjudication.v1"
ANNOTATION_FORM_FILENAME = "annotation_form.json"
ANNOTATION_PACKET_FILENAME = "annotation_packet.json"


class AnnotationAssetView(BaseModel):
    """Exactly the asset information an annotator is allowed to see."""

    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(..., pattern=r"^asset_[0-9]{4}$")
    media_type: Literal["raster", "text_bitmap"]
    content: Optional[str] = None


class AnnotationPacket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["a3.annotation-packet.v1"] = A3_ANNOTATION_PACKET_VERSION
    sample_id: str
    user_brief: str
    assets: List[AnnotationAssetView]
    contact_sheet_files: List[str]
    packet_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")


class AnnotatedAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(..., pattern=r"^asset_[0-9]{4}$")
    semantic_type: SemanticType
    semantic_role: str = Field(..., min_length=1)
    group_id: str = Field(..., pattern=r"^group_[A-Za-z0-9._-]+$")
    group_label: str = Field(..., min_length=1)
    parent_id: str = "root"
    relation_to_parent: TreeRelation = TreeRelation.ROOT
    uncertain: bool = False


class HumanAnnotation(BaseModel):
    """One annotator's full response for one sample."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["a3.human-annotation.v1"] = A3_HUMAN_ANNOTATION_VERSION
    sample_id: str
    annotator_id: str = Field(..., min_length=1)
    assets: List[AnnotatedAsset]
    sample_uncertain: bool = False
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _unique_assets(self) -> "HumanAnnotation":
        ids = [asset.asset_id for asset in self.assets]
        if len(ids) != len(set(ids)):
            raise ValueError("annotation contains duplicate asset IDs")
        return self


def build_annotation_packet(
    manifest: R3AssetManifest, user_brief: str, contact_sheet_files: List[str]
) -> AnnotationPacket:
    assets = [
        AnnotationAssetView(
            asset_id=asset.asset_id,
            media_type=asset.media_type,
            content=asset.content,
        )
        for asset in manifest.foreground_assets()
    ]
    payload = json.dumps(
        {
            "sample_id": manifest.sample_id,
            "user_brief": user_brief,
            "assets": [asset.model_dump(mode="json") for asset in assets],
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return AnnotationPacket(
        sample_id=manifest.sample_id,
        user_brief=user_brief,
        assets=assets,
        contact_sheet_files=list(contact_sheet_files),
        packet_sha256=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    )


def build_annotation_form(packet: AnnotationPacket) -> Dict:
    """Empty response template one annotator fills in."""
    return {
        "version": A3_HUMAN_ANNOTATION_VERSION,
        "sample_id": packet.sample_id,
        "annotator_id": "",
        "sample_uncertain": False,
        "notes": None,
        "assets": [
            {
                "asset_id": asset.asset_id,
                "semantic_type": "",
                "semantic_role": "",
                "group_id": "",
                "group_label": "",
                "parent_id": "root",
                "relation_to_parent": "root",
                "uncertain": False,
            }
            for asset in packet.assets
        ],
    }


def save_annotation_packet(
    packet: AnnotationPacket, output_dir: Path
) -> None:
    output_dir.mkdir(parents=True, exist_ok=False)
    write_json_once(output_dir / ANNOTATION_PACKET_FILENAME, packet.model_dump(mode="json"))
    write_json_once(output_dir / ANNOTATION_FORM_FILENAME, build_annotation_form(packet))


def validate_annotation_coverage(
    annotation: HumanAnnotation, manifest: R3AssetManifest
) -> None:
    expected = {asset.asset_id for asset in manifest.foreground_assets()}
    actual = {asset.asset_id for asset in annotation.assets}
    if expected != actual:
        raise ValueError(
            f"annotation coverage mismatch: missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _same_group_pairs(annotation: HumanAnnotation) -> Set[Tuple[str, str]]:
    by_group: Dict[str, List[str]] = {}
    for asset in annotation.assets:
        by_group.setdefault(asset.group_id, []).append(asset.asset_id)
    pairs: Set[Tuple[str, str]] = set()
    for members in by_group.values():
        for a, b in combinations(sorted(members), 2):
            pairs.add((a, b))
    return pairs


def _edges(annotation: HumanAnnotation) -> Set[Tuple[str, str]]:
    return {
        (asset.parent_id, asset.asset_id)
        for asset in annotation.assets
        if asset.parent_id != "root"
    }


class AgreementReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    same_group_jaccard: Optional[float] = None
    edge_jaccard: Optional[float] = None
    role_type_agreement: float
    disagreeing_assets: List[str] = Field(default_factory=list)


def compute_agreement(a: HumanAnnotation, b: HumanAnnotation) -> AgreementReport:
    """Pairwise inter-annotator agreement; drives the adjudication queue."""
    if {x.asset_id for x in a.assets} != {x.asset_id for x in b.assets}:
        raise ValueError("annotations cover different asset sets")
    pairs_a, pairs_b = _same_group_pairs(a), _same_group_pairs(b)
    edges_a, edges_b = _edges(a), _edges(b)

    def _jaccard(left: Set, right: Set) -> Optional[float]:
        union = left | right
        return len(left & right) / len(union) if union else None

    by_id_b = {asset.asset_id: asset for asset in b.assets}
    matches = [
        asset.asset_id
        for asset in a.assets
        if asset.semantic_type == by_id_b[asset.asset_id].semantic_type
    ]
    disagreeing = sorted({x.asset_id for x in a.assets} - set(matches))
    return AgreementReport(
        same_group_jaccard=_jaccard(pairs_a, pairs_b),
        edge_jaccard=_jaccard(edges_a, edges_b),
        role_type_agreement=len(matches) / len(a.assets),
        disagreeing_assets=disagreeing,
    )


class AdjudicationRecord(BaseModel):
    """Provenance of how the final reference tree was decided."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["a3.annotation-adjudication.v1"] = A3_ADJUDICATION_VERSION
    sample_id: str
    annotator_ids: List[str] = Field(..., min_length=2)
    adjudicator_id: str = Field(..., min_length=1)
    agreement: AgreementReport
    resolution_notes: Optional[str] = None


def annotation_to_oracle_tree(annotation: HumanAnnotation) -> A3LayoutTree:
    """Convert one (adjudicated) annotation into the T3 oracle contract."""
    labels: Dict[str, str] = {}
    members: Dict[str, List[str]] = {}
    order: Dict[str, int] = {}
    for position, asset in enumerate(annotation.assets):
        labels.setdefault(asset.group_id, asset.group_label)
        members.setdefault(asset.group_id, []).append(asset.asset_id)
        order.setdefault(asset.group_id, position)
    nodes = [
        A3TreeNode(
            asset_id=asset.asset_id,
            semantic_type=asset.semantic_type,
            semantic_role=asset.semantic_role,
            group_id=asset.group_id,
            group_label=labels[asset.group_id],
            parent_id=asset.parent_id,
            relation_to_parent=asset.relation_to_parent,
            ordering_priority=position,
            confidence=0.5 if asset.uncertain else 1.0,
        )
        for position, asset in enumerate(annotation.assets)
    ]
    groups = [
        A3TreeGroup(
            group_id=group_id,
            label=labels[group_id],
            member_ids=member_ids,
            ordering_priority=order[group_id],
            confidence=1.0,
        )
        for group_id, member_ids in members.items()
    ]
    return A3LayoutTree(source="human_oracle", nodes=nodes, groups=groups)
