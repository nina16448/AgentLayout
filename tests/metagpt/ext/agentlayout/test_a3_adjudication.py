from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from metagpt.ext.agentlayout.tools.adjudication import (
    AnnotationSubmission,
    build_adjudication_forms,
    build_adjudication_packet,
    build_adjudication_guide,
    load_annotation_submissions,
    load_oracle_tree,
    save_adjudication_materials,
    validate_adjudication_submission,
)
from metagpt.ext.agentlayout.tools.annotation import (
    AdjudicationRecord,
    AnnotatedAsset,
    HumanAnnotation,
    compute_agreement,
)
from metagpt.ext.agentlayout.tools.text_bitmap_normalizer import (
    R3Asset,
    R3AssetManifest,
    R3NormalizationConfig,
)


def _asset(
    asset_id: str,
    semantic_type: str,
    semantic_role: str,
    group: str,
    *,
    parent: str = "root",
    relation: str = "root",
    uncertain: bool = False,
) -> AnnotatedAsset:
    return AnnotatedAsset(
        asset_id=asset_id,
        semantic_type=semantic_type,
        semantic_role=semantic_role,
        group_id=f"group_{group}",
        group_label=group,
        parent_id=parent,
        relation_to_parent=relation,
        uncertain=uncertain,
    )


def _annotation(
    annotator_id: str,
    *,
    price_type: str = "pricetag",
    price_role: str = "discount offer",
    price_group: str = "offer",
    price_parent: str = "asset_0001",
    price_relation: str = "qualifies",
    price_uncertain: bool = False,
) -> HumanAnnotation:
    return HumanAnnotation(
        sample_id="sample01",
        annotator_id=annotator_id,
        assets=[
            _asset("asset_0001", "title", "main headline", "offer"),
            _asset(
                "asset_0002",
                price_type,
                price_role,
                price_group,
                parent=price_parent,
                relation=price_relation,
                uncertain=price_uncertain,
            ),
            _asset("asset_0003", "product_image", "hero product", "product"),
        ],
    )


def _submission(annotation: HumanAnnotation) -> AnnotationSubmission:
    raw = json.dumps(annotation.model_dump(mode="json"), sort_keys=True).encode()
    return AnnotationSubmission(
        annotator_id=annotation.annotator_id,
        filename=f"annotation_{annotation.annotator_id}.json",
        sha256=hashlib.sha256(raw).hexdigest(),
        annotation=annotation,
    )


def _submissions():
    return [
        _submission(_annotation("ann_a")),
        _submission(
            _annotation(
                "ann_b",
                price_type="caption",
                price_role="small qualifier",
                price_uncertain=True,
            )
        ),
        _submission(
            _annotation(
                "ann_c",
                price_group="price",
                price_parent="root",
                price_relation="root",
            )
        ),
    ]


def _manifest() -> R3AssetManifest:
    return R3AssetManifest(
        sample_id="sample01",
        canvas_width=800,
        canvas_height=600,
        normalization=R3NormalizationConfig(),
        source_pfull_manifest_sha256="a" * 64,
        assets=[
            R3Asset(
                asset_id=f"asset_{index:04d}",
                role="placeable",
                media_type="raster",
                asset_ref=f"/tmp/asset_{index:04d}.png",
                sha256=str(index) * 64,
                bitmap_width=64,
                bitmap_height=32,
                bitmap_aspect_ratio=2.0,
            )
            for index in (1, 2, 3)
        ],
    )


def test_three_annotators_produce_all_pairwise_and_aggregate_agreement():
    packet = build_adjudication_packet(
        _submissions(),
        annotation_directory="samples/sample01/annotation",
        contact_sheet_files=["asset_contact_sheet_01.png"],
    )
    assert packet.version == "a3.adjudication-packet.v1"
    assert packet.annotator_ids == ["ann_a", "ann_b", "ann_c"]
    assert [entry.annotator_ids for entry in packet.pairwise_agreements] == [
        ["ann_a", "ann_b"],
        ["ann_a", "ann_c"],
        ["ann_b", "ann_c"],
    ]

    reports = [
        compute_agreement(submissions[0].annotation, submissions[1].annotation)
        for submissions in (
            (_submissions()[0], _submissions()[1]),
            (_submissions()[0], _submissions()[2]),
            (_submissions()[1], _submissions()[2]),
        )
    ]
    expected_type_mean = sum(report.role_type_agreement for report in reports) / 3
    assert packet.aggregate_agreement.role_type_agreement == pytest.approx(
        expected_type_mean
    )
    assert packet.aggregate_agreement.disagreeing_assets == ["asset_0002"]
    assert packet.requires_adjudication is True


def test_asset_comparison_exposes_options_without_choosing_a_winner():
    packet = build_adjudication_packet(_submissions())
    price = next(item for item in packet.assets if item.asset_id == "asset_0002")
    assert price.disagreement_fields == [
        "semantic_type",
        "semantic_role",
        "same_group_members",
        "group_id",
        "group_label",
        "parent_id",
        "relation_to_parent",
        "uncertain",
    ]
    assert [decision.annotator_id for decision in price.decisions] == [
        "ann_a",
        "ann_b",
        "ann_c",
    ]
    assert price.decisions[0].same_group_member_ids == ["asset_0001", "asset_0002"]
    assert price.decisions[2].same_group_member_ids == ["asset_0002"]
    assert not hasattr(price, "selected_annotator_id")


def test_forms_prefill_only_unanimous_fields_and_leave_disagreements_unresolved():
    packet = build_adjudication_packet(_submissions())
    annotation_form, record_form = build_adjudication_forms(packet)
    title = annotation_form["assets"][0]
    price = annotation_form["assets"][1]
    assert title == {
        "asset_id": "asset_0001",
        "semantic_type": "title",
        "semantic_role": "main headline",
        "group_id": "group_offer",
        "group_label": "offer",
        "parent_id": "root",
        "relation_to_parent": "root",
        "uncertain": False,
    }
    assert price == {
        "asset_id": "asset_0002",
        "semantic_type": "",
        "semantic_role": "",
        "group_id": "",
        "group_label": "",
        "parent_id": "",
        "relation_to_parent": "",
        "uncertain": None,
    }
    assert annotation_form["annotator_id"] == ""
    assert record_form["adjudicator_id"] == ""
    assert record_form["agreement"] == packet.aggregate_agreement.model_dump(mode="json")
    with pytest.raises(ValidationError):
        HumanAnnotation.model_validate(annotation_form)
    with pytest.raises(ValidationError):
        AdjudicationRecord.model_validate(record_form)


def test_loading_submissions_validates_schema_coverage_suffix_and_structure(tmp_path):
    annotation_dir = tmp_path / "annotation"
    annotation_dir.mkdir()
    (annotation_dir / "annotation_form.json").write_text("{}", encoding="utf-8")
    for submission in _submissions():
        (annotation_dir / submission.filename).write_text(
            submission.annotation.model_dump_json(indent=2), encoding="utf-8"
        )
    loaded = load_annotation_submissions(annotation_dir, _manifest())
    assert [item.annotator_id for item in loaded] == ["ann_a", "ann_b", "ann_c"]
    assert all(len(item.sha256) == 64 for item in loaded)

    bad = annotation_dir / "annotation_wrong_name.json"
    bad.write_text(_annotation("actual_id").model_dump_json(), encoding="utf-8")
    with pytest.raises(ValueError, match="filename suffix"):
        load_annotation_submissions(annotation_dir, _manifest())


def test_packet_rejects_duplicate_annotators_samples_and_asset_coverage():
    duplicate = [_submission(_annotation("ann_a")), _submission(_annotation("ann_a"))]
    with pytest.raises(ValueError, match="duplicate annotator"):
        build_adjudication_packet(duplicate)

    wrong_sample = _annotation("ann_b").model_copy(update={"sample_id": "sample02"})
    with pytest.raises(ValueError, match="same sample"):
        build_adjudication_packet([_submission(_annotation("ann_a")), _submission(wrong_sample)])

    partial = _annotation("ann_b").model_copy(
        update={"assets": _annotation("ann_b").assets[:-1]}
    )
    with pytest.raises(ValueError, match="asset coverage"):
        build_adjudication_packet([_submission(_annotation("ann_a")), _submission(partial)])


def test_materials_are_write_once_and_guide_keeps_decisions_human(tmp_path):
    packet = build_adjudication_packet(_submissions())
    destination = tmp_path / "sample01"
    save_adjudication_materials(packet, destination)
    assert (destination / "adjudication_packet.json").exists()
    assert (destination / "annotation_adjudicated_form.json").exists()
    assert (destination / "adjudication_record_form.json").exists()
    with pytest.raises(FileExistsError):
        save_adjudication_materials(packet, destination)

    guide = build_adjudication_guide()
    assert "不得自動裁決" in guide
    assert "不要修改原始 annotation_*.json" in guide
    assert "designer GT" in guide


def test_completed_human_submission_is_validated_before_oracle_conversion():
    packet = build_adjudication_packet(_submissions())
    final_annotation = _annotation("human_adj")
    record = AdjudicationRecord(
        sample_id="sample01",
        annotator_ids=packet.annotator_ids,
        adjudicator_id="human_adj",
        agreement=packet.aggregate_agreement,
        resolution_notes="Reviewed all three independent annotations.",
    )
    tree = validate_adjudication_submission(packet, final_annotation, record)
    assert tree.source == "human_oracle"

    wrong_record = record.model_copy(update={"annotator_ids": ["ann_a", "ann_b"]})
    with pytest.raises(ValueError, match="annotator_ids"):
        validate_adjudication_submission(packet, final_annotation, wrong_record)
    with pytest.raises(ValueError, match="must match adjudicator_id"):
        validate_adjudication_submission(
            packet,
            final_annotation.model_copy(update={"annotator_id": "someone_else"}),
            record,
        )


def test_finalized_oracle_tree_loader_is_source_checked(tmp_path):
    packet = build_adjudication_packet(_submissions())
    final_annotation = _annotation("human_adj")
    record = AdjudicationRecord(
        sample_id="sample01",
        annotator_ids=packet.annotator_ids,
        adjudicator_id="human_adj",
        agreement=packet.aggregate_agreement,
    )
    tree = validate_adjudication_submission(packet, final_annotation, record)
    (tmp_path / "sample01.json").write_text(
        tree.model_dump_json(indent=2), encoding="utf-8"
    )
    assert load_oracle_tree(tmp_path, "sample01") == tree

    predicted = tree.model_copy(update={"source": "predicted"})
    (tmp_path / "sample01.json").write_text(
        predicted.model_dump_json(indent=2), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="source='human_oracle'"):
        load_oracle_tree(tmp_path, "sample01")
