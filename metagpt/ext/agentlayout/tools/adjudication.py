"""Deterministic, no-winner adjudication materials for A3 human trees.

The independent annotations remain immutable.  This module builds a separate
write-once packet that shows every annotator's decision, all pairwise
agreement reports, and a form in which only unanimous fields are prefilled.
It never chooses an annotator or resolves a disagreement automatically.
"""
from __future__ import annotations

import hashlib
from itertools import combinations
from pathlib import Path
from statistics import mean
from typing import Dict, List, Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from metagpt.ext.agentlayout.layout_tree_v3 import A3LayoutTree, TreeRelation
from metagpt.ext.agentlayout.run_manifest import write_json_once
from metagpt.ext.agentlayout.schema import SemanticType
from metagpt.ext.agentlayout.tools.annotation import (
    A3_HUMAN_ANNOTATION_VERSION,
    ANNOTATION_FORM_FILENAME,
    ANNOTATION_PACKET_FILENAME,
    AdjudicationRecord,
    AgreementReport,
    HumanAnnotation,
    annotation_to_oracle_tree,
    compute_agreement,
    validate_annotation_coverage,
)
from metagpt.ext.agentlayout.tools.text_bitmap_normalizer import R3AssetManifest


A3_ADJUDICATION_PACKET_VERSION = "a3.adjudication-packet.v1"
ADJUDICATION_PACKET_FILENAME = "adjudication_packet.json"
ADJUDICATED_ANNOTATION_FORM_FILENAME = "annotation_adjudicated_form.json"
ADJUDICATION_RECORD_FORM_FILENAME = "adjudication_record_form.json"
ADJUDICATED_ANNOTATION_FILENAME = "annotation_adjudicated.json"
ADJUDICATION_RECORD_FILENAME = "adjudication_record.json"

DisagreementField = Literal[
    "semantic_type",
    "semantic_role",
    "same_group_members",
    "group_id",
    "group_label",
    "parent_id",
    "relation_to_parent",
    "uncertain",
]
DISAGREEMENT_FIELD_ORDER: Sequence[DisagreementField] = (
    "semantic_type",
    "semantic_role",
    "same_group_members",
    "group_id",
    "group_label",
    "parent_id",
    "relation_to_parent",
    "uncertain",
)


class AnnotationSubmission(BaseModel):
    """One validated source annotation and its immutable file provenance."""

    model_config = ConfigDict(extra="forbid")

    annotator_id: str = Field(..., min_length=1)
    filename: str = Field(..., min_length=1)
    sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    annotation: HumanAnnotation

    @model_validator(mode="after")
    def _identity_matches(self) -> "AnnotationSubmission":
        if self.annotation.annotator_id != self.annotator_id:
            raise ValueError("submission annotator_id disagrees with annotation")
        if self.filename != f"annotation_{self.annotator_id}.json":
            raise ValueError("submission filename does not match annotator_id")
        return self


class AnnotationSourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    annotator_id: str
    filename: str
    sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")


class PairwiseAgreementEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    annotator_ids: List[str] = Field(..., min_length=2, max_length=2)
    agreement: AgreementReport


class AnnotatorAssetDecision(BaseModel):
    """One annotator's decision with group membership made explicit."""

    model_config = ConfigDict(extra="forbid")

    annotator_id: str
    semantic_type: SemanticType
    semantic_role: str
    same_group_member_ids: List[str]
    group_id: str
    group_label: str
    parent_id: str
    relation_to_parent: TreeRelation
    uncertain: bool


class AssetAdjudicationComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    decisions: List[AnnotatorAssetDecision]
    disagreement_fields: List[DisagreementField] = Field(default_factory=list)


class AnnotatorSampleDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    annotator_id: str
    sample_uncertain: bool
    notes: Optional[str] = None


class AdjudicationPacket(BaseModel):
    """Read-only evidence packet for a human adjudicator."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["a3.adjudication-packet.v1"] = A3_ADJUDICATION_PACKET_VERSION
    sample_id: str
    annotator_ids: List[str] = Field(..., min_length=2)
    annotation_directory: str = "annotation"
    contact_sheet_files: List[str] = Field(default_factory=list)
    sources: List[AnnotationSourceRef]
    sample_decisions: List[AnnotatorSampleDecision]
    pairwise_agreements: List[PairwiseAgreementEntry]
    aggregate_agreement: AgreementReport
    assets: List[AssetAdjudicationComparison]
    requires_adjudication: bool

    @model_validator(mode="after")
    def _internal_provenance(self) -> "AdjudicationPacket":
        if len(self.annotator_ids) != len(set(self.annotator_ids)):
            raise ValueError("adjudication packet contains duplicate annotator IDs")
        if [source.annotator_id for source in self.sources] != self.annotator_ids:
            raise ValueError("source order/coverage disagrees with annotator_ids")
        if [item.annotator_id for item in self.sample_decisions] != self.annotator_ids:
            raise ValueError("sample decision coverage disagrees with annotator_ids")
        expected_pairs = [
            list(pair) for pair in combinations(self.annotator_ids, 2)
        ]
        if [entry.annotator_ids for entry in self.pairwise_agreements] != expected_pairs:
            raise ValueError("pairwise agreement coverage is incomplete or reordered")
        if self.aggregate_agreement != _aggregate_agreement(self.pairwise_agreements):
            raise ValueError("aggregate agreement disagrees with pairwise reports")
        asset_ids = [asset.asset_id for asset in self.assets]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("adjudication packet contains duplicate asset IDs")
        for asset in self.assets:
            if [item.annotator_id for item in asset.decisions] != self.annotator_ids:
                raise ValueError(
                    f"asset {asset.asset_id} decision coverage disagrees with annotator_ids"
                )
        expected_required = (
            len({item.sample_uncertain for item in self.sample_decisions}) > 1
            or any(asset.disagreement_fields for asset in self.assets)
        )
        if self.requires_adjudication != expected_required:
            raise ValueError("requires_adjudication disagrees with packet differences")
        return self


def load_annotation_submissions(
    annotation_dir: Path, manifest: R3AssetManifest
) -> List[AnnotationSubmission]:
    """Load every independent annotation in a packet directory fail-closed."""
    reserved = {ANNOTATION_FORM_FILENAME, ANNOTATION_PACKET_FILENAME}
    paths = [
        path
        for path in sorted(annotation_dir.glob("annotation_*.json"))
        if path.name not in reserved
    ]
    submissions: List[AnnotationSubmission] = []
    for path in paths:
        raw = path.read_bytes()
        annotation = HumanAnnotation.model_validate_json(raw)
        if annotation.sample_id != manifest.sample_id:
            raise ValueError(
                f"{path.name} sample_id {annotation.sample_id!r} does not match "
                f"manifest {manifest.sample_id!r}"
            )
        filename_id = path.stem.removeprefix("annotation_")
        if annotation.annotator_id != filename_id:
            raise ValueError(
                f"{path.name} filename suffix {filename_id!r} does not match "
                f"annotator_id {annotation.annotator_id!r}"
            )
        validate_annotation_coverage(annotation, manifest)
        # HumanAnnotation validates local fields; oracle conversion additionally
        # catches missing parents, cycles and inconsistent group labels.
        annotation_to_oracle_tree(annotation)
        submissions.append(
            AnnotationSubmission(
                annotator_id=annotation.annotator_id,
                filename=path.name,
                sha256=hashlib.sha256(raw).hexdigest(),
                annotation=annotation,
            )
        )
    if len(submissions) < 2:
        raise ValueError(
            f"at least two independent annotations are required; found {len(submissions)}"
        )
    return sorted(submissions, key=lambda item: item.annotator_id)


def _mean_optional(values: Sequence[Optional[float]]) -> Optional[float]:
    defined = [value for value in values if value is not None]
    return mean(defined) if defined else None


def _aggregate_agreement(
    pairwise: Sequence[PairwiseAgreementEntry],
) -> AgreementReport:
    disagreeing_assets = sorted(
        {
            asset_id
            for entry in pairwise
            for asset_id in entry.agreement.disagreeing_assets
        }
    )
    return AgreementReport(
        same_group_jaccard=_mean_optional(
            [entry.agreement.same_group_jaccard for entry in pairwise]
        ),
        edge_jaccard=_mean_optional(
            [entry.agreement.edge_jaccard for entry in pairwise]
        ),
        role_type_agreement=mean(
            entry.agreement.role_type_agreement for entry in pairwise
        ),
        disagreeing_assets=disagreeing_assets,
    )


def _group_members(annotation: HumanAnnotation) -> Dict[str, List[str]]:
    members_by_group: Dict[str, List[str]] = {}
    for asset in annotation.assets:
        members_by_group.setdefault(asset.group_id, []).append(asset.asset_id)
    return {
        asset.asset_id: sorted(members_by_group[asset.group_id])
        for asset in annotation.assets
    }


def _comparison(
    asset_id: str, submissions: Sequence[AnnotationSubmission]
) -> AssetAdjudicationComparison:
    decisions: List[AnnotatorAssetDecision] = []
    for submission in submissions:
        by_id = {asset.asset_id: asset for asset in submission.annotation.assets}
        asset = by_id[asset_id]
        decisions.append(
            AnnotatorAssetDecision(
                annotator_id=submission.annotator_id,
                semantic_type=asset.semantic_type,
                semantic_role=asset.semantic_role,
                same_group_member_ids=_group_members(submission.annotation)[asset_id],
                group_id=asset.group_id,
                group_label=asset.group_label,
                parent_id=asset.parent_id,
                relation_to_parent=asset.relation_to_parent,
                uncertain=asset.uncertain,
            )
        )

    json_decisions = [decision.model_dump(mode="json") for decision in decisions]
    source_key = {field: field for field in DISAGREEMENT_FIELD_ORDER}
    source_key["same_group_members"] = "same_group_member_ids"
    disagreements = [
        field
        for field in DISAGREEMENT_FIELD_ORDER
        if len({str(item[source_key[field]]) for item in json_decisions}) > 1
    ]
    return AssetAdjudicationComparison(
        asset_id=asset_id,
        decisions=decisions,
        disagreement_fields=disagreements,
    )


def build_adjudication_packet(
    submissions: Sequence[AnnotationSubmission],
    *,
    annotation_directory: str = "annotation",
    contact_sheet_files: Optional[Sequence[str]] = None,
) -> AdjudicationPacket:
    """Build a comparison packet without selecting or merging any answer."""
    ordered = sorted(submissions, key=lambda item: item.annotator_id)
    if len(ordered) < 2:
        raise ValueError("at least two independent annotations are required")
    annotator_ids = [item.annotator_id for item in ordered]
    if len(annotator_ids) != len(set(annotator_ids)):
        raise ValueError("duplicate annotator IDs are not independent annotations")
    sample_ids = {item.annotation.sample_id for item in ordered}
    if len(sample_ids) != 1:
        raise ValueError("all annotations must describe the same sample")

    asset_sets = [
        {asset.asset_id for asset in item.annotation.assets} for item in ordered
    ]
    if any(asset_ids != asset_sets[0] for asset_ids in asset_sets[1:]):
        raise ValueError("annotations have different asset coverage")
    for item in ordered:
        annotation_to_oracle_tree(item.annotation)

    pairwise = [
        PairwiseAgreementEntry(
            annotator_ids=[left.annotator_id, right.annotator_id],
            agreement=compute_agreement(left.annotation, right.annotation),
        )
        for left, right in combinations(ordered, 2)
    ]
    comparisons = [
        _comparison(asset_id, ordered) for asset_id in sorted(asset_sets[0])
    ]
    sample_decisions = [
        AnnotatorSampleDecision(
            annotator_id=item.annotator_id,
            sample_uncertain=item.annotation.sample_uncertain,
            notes=item.annotation.notes,
        )
        for item in ordered
    ]
    sample_uncertainty_differs = len(
        {decision.sample_uncertain for decision in sample_decisions}
    ) > 1
    return AdjudicationPacket(
        sample_id=sample_ids.pop(),
        annotator_ids=annotator_ids,
        annotation_directory=annotation_directory,
        contact_sheet_files=list(contact_sheet_files or []),
        sources=[
            AnnotationSourceRef(
                annotator_id=item.annotator_id,
                filename=item.filename,
                sha256=item.sha256,
            )
            for item in ordered
        ],
        sample_decisions=sample_decisions,
        pairwise_agreements=pairwise,
        aggregate_agreement=_aggregate_agreement(pairwise),
        assets=comparisons,
        requires_adjudication=sample_uncertainty_differs
        or any(item.disagreement_fields for item in comparisons),
    )


def _unanimous(decisions: Sequence[Dict], field: str, unresolved):
    values = [decision[field] for decision in decisions]
    return values[0] if all(value == values[0] for value in values[1:]) else unresolved


def build_adjudication_forms(packet: AdjudicationPacket) -> tuple[Dict, Dict]:
    """Return immutable blank forms; only unanimous fields are prefilled."""
    sample_uncertain_values = [
        decision.sample_uncertain for decision in packet.sample_decisions
    ]
    sample_uncertain = (
        sample_uncertain_values[0]
        if all(
            value == sample_uncertain_values[0]
            for value in sample_uncertain_values[1:]
        )
        else None
    )
    assets = []
    for comparison in packet.assets:
        decisions = [item.model_dump(mode="json") for item in comparison.decisions]
        assets.append(
            {
                "asset_id": comparison.asset_id,
                "semantic_type": _unanimous(decisions, "semantic_type", ""),
                "semantic_role": _unanimous(decisions, "semantic_role", ""),
                "group_id": _unanimous(decisions, "group_id", ""),
                "group_label": _unanimous(decisions, "group_label", ""),
                "parent_id": _unanimous(decisions, "parent_id", ""),
                "relation_to_parent": _unanimous(
                    decisions, "relation_to_parent", ""
                ),
                "uncertain": _unanimous(decisions, "uncertain", None),
            }
        )
    annotation_form = {
        "version": A3_HUMAN_ANNOTATION_VERSION,
        "sample_id": packet.sample_id,
        "annotator_id": "",
        "assets": assets,
        "sample_uncertain": sample_uncertain,
        "notes": None,
    }
    record_form = {
        "version": "a3.annotation-adjudication.v1",
        "sample_id": packet.sample_id,
        "annotator_ids": list(packet.annotator_ids),
        "adjudicator_id": "",
        "agreement": packet.aggregate_agreement.model_dump(mode="json"),
        "resolution_notes": None,
    }
    return annotation_form, record_form


def save_adjudication_materials(
    packet: AdjudicationPacket, destination: Path
) -> None:
    """Persist one packet and its two copy-before-edit forms write-once."""
    destination.mkdir(parents=True, exist_ok=False)
    annotation_form, record_form = build_adjudication_forms(packet)
    write_json_once(
        destination / ADJUDICATION_PACKET_FILENAME,
        packet.model_dump(mode="json"),
    )
    write_json_once(
        destination / ADJUDICATED_ANNOTATION_FORM_FILENAME,
        annotation_form,
    )
    write_json_once(
        destination / ADJUDICATION_RECORD_FORM_FILENAME,
        record_form,
    )


def build_adjudication_guide() -> str:
    """Human-only adjudication instructions stored beside the queue."""
    return """# A3 Gate A/B adjudication guide

這一步由人類裁決者完成；程式只整理證據，**不得自動裁決**。

1. 查看每個 sample 的 `adjudication_packet.json`，並回到其中列出的
   annotation directory 查看 contact sheet 與全部 `annotation_*.json`。
2. 不要修改原始 annotation_*.json，也不要查看 designer GT、座標或成品。
3. 複製 `annotation_adjudicated_form.json` 為 `annotation_adjudicated.json`；
   複製 `adjudication_record_form.json` 為 `adjudication_record.json`。
4. 表單只預填三位標註者逐字一致的欄位；空字串或 null 都代表仍需人類決定。
5. `annotation_adjudicated.json` 的 `annotator_id` 必須等於 record 的
   `adjudicator_id`。逐 asset 裁決 type、自由文字 role、group、parent/relation
   與 uncertain；在 `resolution_notes` 記錄主要取捨。
6. 完成後先做 schema、coverage、cycle/group consistency 驗證，通過後才能
   轉成 T3 human-oracle tree。不得直接把任一 annotator 當作自動 winner。
7. 20 份都完成後執行：
   `python layout_agent/run_a3.py finalize-adjudication --run-dir <pilot-run>`。
   驗證採 all-or-nothing；任一份有錯就不產生任何 oracle tree。
"""


def validate_adjudication_submission(
    packet: AdjudicationPacket,
    final_annotation: HumanAnnotation,
    record: AdjudicationRecord,
) -> A3LayoutTree:
    """Validate one human decision and return its oracle tree without writing."""
    if final_annotation.sample_id != packet.sample_id or record.sample_id != packet.sample_id:
        raise ValueError("adjudication sample_id does not match packet")
    if record.annotator_ids != packet.annotator_ids:
        raise ValueError("record annotator_ids do not match packet provenance")
    if record.agreement != packet.aggregate_agreement:
        raise ValueError("record agreement does not match the frozen packet aggregate")
    if final_annotation.annotator_id != record.adjudicator_id:
        raise ValueError("final annotation annotator_id must match adjudicator_id")
    expected = {item.asset_id for item in packet.assets}
    actual = {item.asset_id for item in final_annotation.assets}
    if actual != expected:
        raise ValueError(
            f"adjudicated annotation coverage mismatch: missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )
    return annotation_to_oracle_tree(final_annotation)


def load_oracle_tree(oracle_root: Path, sample_id: str) -> A3LayoutTree:
    """Load one finalized T3 tree from ``<oracle_root>/<sample_id>.json``."""
    path = oracle_root / f"{sample_id}.json"
    tree = A3LayoutTree.model_validate_json(path.read_bytes())
    if tree.source != "human_oracle":
        raise ValueError(f"{path} must contain source='human_oracle'")
    return tree
