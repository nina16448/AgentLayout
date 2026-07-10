from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from metagpt.ext.agentlayout.layout_tree_v3 import make_tree_condition
from metagpt.ext.agentlayout.tools.analyst_vision import (
    A3AnalystOutput,
    A3AssetUnderstanding,
)
from metagpt.ext.agentlayout.tools.annotation import (
    AdjudicationRecord,
    AnnotatedAsset,
    HumanAnnotation,
    annotation_to_oracle_tree,
    build_annotation_packet,
    compute_agreement,
    save_annotation_packet,
    validate_annotation_coverage,
)
from metagpt.ext.agentlayout.tools.text_bitmap_normalizer import (
    R3Asset,
    R3AssetManifest,
    R3NormalizationConfig,
)


def _manifest(tmp_path: Path) -> R3AssetManifest:
    def _png(name: str, size=(64, 32)) -> str:
        path = tmp_path / name
        if not path.exists():
            Image.new("RGBA", size, (200, 40, 40, 255)).save(path)
        return str(path)

    def _asset(index: int, media: str, content=None) -> R3Asset:
        return R3Asset(
            asset_id=f"asset_{index:04d}",
            role="placeable",
            media_type=media,
            content=content,
            asset_ref=_png(f"asset_{index:04d}.png"),
            sha256=str(index) * 64,
            bitmap_width=64,
            bitmap_height=32,
            bitmap_aspect_ratio=2.0,
        )

    return R3AssetManifest(
        sample_id="sample01",
        canvas_width=800,
        canvas_height=600,
        normalization=R3NormalizationConfig(),
        source_pfull_manifest_sha256="a" * 64,
        assets=[
            _asset(1, "text_bitmap", "SUMMER SALE"),
            _asset(2, "text_bitmap", "50% OFF"),
            _asset(3, "raster"),
        ],
    )


def _annotated(asset_id, semantic_type, role, group, parent="root", relation="root"):
    return AnnotatedAsset(
        asset_id=asset_id,
        semantic_type=semantic_type,
        semantic_role=role,
        group_id=f"group_{group}",
        group_label=group,
        parent_id=parent,
        relation_to_parent=relation,
    )


def _annotation(annotator="ann_a", price_group="offer") -> HumanAnnotation:
    return HumanAnnotation(
        sample_id="sample01",
        annotator_id=annotator,
        assets=[
            _annotated("asset_0001", "title", "main headline", "offer"),
            _annotated(
                "asset_0002", "pricetag", "discount", price_group,
                parent="asset_0001", relation="qualifies",
            ),
            _annotated("asset_0003", "product_image", "hero product", "product"),
        ],
    )


def test_packet_exposes_only_brief_ids_media_and_content(tmp_path):
    packet = build_annotation_packet(
        _manifest(tmp_path), "Summer sale poster", ["asset_contact_sheet_01.png"]
    )
    assert packet.version == "a3.annotation-packet.v1"
    payload = packet.model_dump(mode="json")
    text = json.dumps(payload)
    # No GT geometry, no file paths, no background reference reach annotators.
    for forbidden in ('"left"', '"top"', '"width"', '"height"', "/home/", "background"):
        assert forbidden not in text
    assert [a["asset_id"] for a in payload["assets"]] == [
        "asset_0001", "asset_0002", "asset_0003",
    ]
    assert payload["assets"][0]["content"] == "SUMMER SALE"


def test_packet_and_form_are_write_once(tmp_path):
    packet = build_annotation_packet(_manifest(tmp_path), "brief", [])
    output = tmp_path / "annotation"
    save_annotation_packet(packet, output)
    form = json.loads((output / "annotation_form.json").read_text())
    assert form["version"] == "a3.human-annotation.v1"
    assert [a["asset_id"] for a in form["assets"]] == [
        "asset_0001", "asset_0002", "asset_0003",
    ]
    assert all(a["semantic_type"] == "" for a in form["assets"])
    with pytest.raises(FileExistsError):
        save_annotation_packet(packet, output)


def test_annotation_coverage_and_duplicates_are_enforced(tmp_path):
    manifest = _manifest(tmp_path)
    validate_annotation_coverage(_annotation(), manifest)
    partial = _annotation()
    partial = partial.model_copy(update={"assets": partial.assets[:-1]})
    with pytest.raises(ValueError, match="coverage mismatch"):
        validate_annotation_coverage(partial, manifest)
    with pytest.raises(ValidationError, match="duplicate"):
        HumanAnnotation(
            sample_id="sample01",
            annotator_id="ann_a",
            assets=[
                _annotated("asset_0001", "title", "x", "g"),
                _annotated("asset_0001", "title", "x", "g"),
            ],
        )


def test_agreement_report_detects_grouping_and_type_disagreement():
    identical = compute_agreement(_annotation("ann_a"), _annotation("ann_b"))
    assert identical.same_group_jaccard == 1.0
    assert identical.edge_jaccard == 1.0
    assert identical.role_type_agreement == 1.0
    assert identical.disagreeing_assets == []

    # ann_b puts the price tag in its own group -> the shared pair disappears.
    split = compute_agreement(_annotation("ann_a"), _annotation("ann_b", price_group="price"))
    assert split.same_group_jaccard == 0.0
    assert split.edge_jaccard == 1.0


def test_adjudication_record_requires_two_annotators():
    agreement = compute_agreement(_annotation("ann_a"), _annotation("ann_b"))
    record = AdjudicationRecord(
        sample_id="sample01",
        annotator_ids=["ann_a", "ann_b"],
        adjudicator_id="adj_1",
        agreement=agreement,
    )
    assert record.version == "a3.annotation-adjudication.v1"
    with pytest.raises(ValidationError):
        AdjudicationRecord(
            sample_id="sample01",
            annotator_ids=["ann_a"],
            adjudicator_id="adj_1",
            agreement=agreement,
        )


def test_adjudicated_annotation_becomes_a_valid_t3_oracle_tree():
    tree = annotation_to_oracle_tree(_annotation())
    assert tree.source == "human_oracle"
    assert {node.asset_id for node in tree.nodes} == {
        "asset_0001", "asset_0002", "asset_0003",
    }
    price = next(node for node in tree.nodes if node.asset_id == "asset_0002")
    assert price.parent_id == "asset_0001"
    assert price.relation_to_parent.value == "qualifies"

    # The oracle tree plugs straight into the T3 ablation arm.
    analyst = A3AnalystOutput(
        background_summary="bg",
        design_intent="intent",
        assets=[
            A3AssetUnderstanding(
                asset_id=f"asset_{i:04d}",
                semantic_type="other",
                description="d",
                semantic_role="r",
            )
            for i in (1, 2, 3)
        ],
    )
    condition = make_tree_condition("T3", analyst, tree=tree)
    assert condition.tree.source == "human_oracle"


def test_uncertain_assets_carry_reduced_confidence():
    annotation = _annotation()
    annotation.assets[1].uncertain = True
    tree = annotation_to_oracle_tree(annotation)
    price = next(node for node in tree.nodes if node.asset_id == "asset_0002")
    assert price.confidence == 0.5
