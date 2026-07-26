from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

import json

from metagpt.ext.agentlayout.layout_tree_v3 import (
    A3LayoutTree,
    A3TreeGroup,
    A3TreeNode,
    TreeRelation,
    apply_analyst_semantics,
    build_tree_request,
    make_tree_condition,
    parse_layout_tree,
    save_tree_request,
    validate_tree_against_analyst,
)
from metagpt.ext.agentlayout.tools.analyst_vision import (
    A3AnalystOutput,
    A3AssetUnderstanding,
)


def _analyst() -> A3AnalystOutput:
    return A3AnalystOutput(
        background_summary="Quiet blue background",
        design_intent="Promote a summer sale",
        style_keywords=["bright", "commercial"],
        language="en",
        assets=[
            A3AssetUnderstanding(
                asset_id="asset_0001",
                semantic_type="title",
                description="Main sale heading",
                semantic_role="primary message",
                key_message="SUMMER SALE",
            ),
            A3AssetUnderstanding(
                asset_id="asset_0002",
                semantic_type="pricetag",
                description="Discount price",
                semantic_role="offer qualifier",
                key_message="50% OFF",
            ),
            A3AssetUnderstanding(
                asset_id="asset_0003",
                semantic_type="product_image",
                description="Featured shoe",
                semantic_role="focal product",
            ),
        ],
    )


def _tree(source="predicted") -> A3LayoutTree:
    return A3LayoutTree(
        source=source,
        nodes=[
            A3TreeNode(
                asset_id="asset_0001",
                semantic_type="title",
                semantic_role="primary message",
                group_id="group_offer",
                group_label="offer lockup",
                parent_id="root",
                relation_to_parent="root",
                ordering_priority=0,
                confidence=0.95,
            ),
            A3TreeNode(
                asset_id="asset_0002",
                semantic_type="pricetag",
                semantic_role="offer qualifier",
                group_id="group_offer",
                group_label="offer lockup",
                parent_id="asset_0001",
                relation_to_parent="qualifies",
                ordering_priority=1,
                confidence=0.9,
            ),
            A3TreeNode(
                asset_id="asset_0003",
                semantic_type="product_image",
                semantic_role="focal product",
                group_id="group_product",
                group_label="product",
                parent_id="root",
                relation_to_parent="root",
                ordering_priority=0,
                confidence=0.92,
            ),
        ],
        groups=[
            A3TreeGroup(
                group_id="group_offer",
                label="offer lockup",
                member_ids=["asset_0001", "asset_0002"],
                ordering_priority=0,
                confidence=0.94,
            ),
            A3TreeGroup(
                group_id="group_product",
                label="product",
                member_ids=["asset_0003"],
                ordering_priority=1,
                confidence=0.92,
            ),
        ],
    )


def test_valid_tree_has_explicit_roles_groups_edges_order_and_confidence():
    tree = _tree()
    validate_tree_against_analyst(tree, _analyst())
    assert tree.schema_version == "a3.layout-tree.v1"
    price = next(node for node in tree.nodes if node.asset_id == "asset_0002")
    assert price.parent_id == "asset_0001"
    assert price.relation_to_parent == TreeRelation.QUALIFIES
    assert price.group_id == "group_offer"
    assert price.ordering_priority == 1
    assert price.confidence == 0.9


def test_missing_parent_is_rejected():
    payload = _tree().model_dump(mode="json")
    payload["nodes"][1]["parent_id"] = "asset_9999"
    with pytest.raises(ValidationError, match="missing parent"):
        A3LayoutTree.model_validate(payload)


def test_cycle_is_rejected():
    payload = _tree().model_dump(mode="json")
    payload["nodes"][0]["parent_id"] = "asset_0002"
    payload["nodes"][0]["relation_to_parent"] = "supports"
    with pytest.raises(ValidationError, match="cycle"):
        A3LayoutTree.model_validate(payload)


def test_group_partition_is_exact_and_non_overlapping():
    payload = _tree().model_dump(mode="json")
    payload["groups"][1]["member_ids"].append("asset_0002")
    with pytest.raises(ValidationError, match="multiple groups"):
        A3LayoutTree.model_validate(payload)

    payload = _tree().model_dump(mode="json")
    payload["groups"][0]["member_ids"].remove("asset_0002")
    with pytest.raises(ValidationError, match="missing group membership"):
        A3LayoutTree.model_validate(payload)


def test_node_group_label_must_match_group_contract():
    payload = _tree().model_dump(mode="json")
    payload["nodes"][0]["group_label"] = "different"
    with pytest.raises(ValidationError, match="group_label"):
        A3LayoutTree.model_validate(payload)


def test_confidence_and_root_relation_are_validated():
    payload = _tree().model_dump(mode="json")
    payload["nodes"][0]["confidence"] = 1.1
    with pytest.raises(ValidationError, match="less than or equal to 1"):
        A3LayoutTree.model_validate(payload)

    payload = _tree().model_dump(mode="json")
    payload["nodes"][0]["relation_to_parent"] = "peer"
    with pytest.raises(ValidationError, match="root children"):
        A3LayoutTree.model_validate(payload)


def test_tree_must_exactly_cover_analyst_ids_and_semantic_types():
    analyst = _analyst()
    missing_payload = _tree().model_dump(mode="json")
    missing_payload["nodes"] = missing_payload["nodes"][:-1]
    missing_payload["groups"] = missing_payload["groups"][:-1]
    missing = A3LayoutTree.model_validate(missing_payload)
    with pytest.raises(ValueError, match="coverage mismatch"):
        validate_tree_against_analyst(missing, analyst)

    mismatch_payload = _tree().model_dump(mode="json")
    mismatch_payload["nodes"][0]["semantic_type"] = "caption"
    mismatch = A3LayoutTree.model_validate(mismatch_payload)
    with pytest.raises(ValueError, match="semantic_type mismatch"):
        validate_tree_against_analyst(mismatch, analyst)

    role_payload = _tree().model_dump(mode="json")
    role_payload["nodes"][0]["semantic_role"] = "rewritten role"
    role_mismatch = A3LayoutTree.model_validate(role_payload)
    with pytest.raises(ValueError, match="semantic_role mismatch"):
        validate_tree_against_analyst(role_mismatch, analyst)


def test_request_is_versioned_hashed_and_contains_no_paths_or_geometry_values():
    request = build_tree_request(_analyst())
    assert request.version == "a3.layout-tree-request.v1"
    assert len(request.prompt_sha256) == 64
    assert "/home/" not in request.prompt
    assert "asset_0001" in request.prompt
    assert "primary message" in request.prompt
    # Geometry words occur only in the explicit prohibition, never as input values.
    assert '"left"' not in request.prompt
    assert '"top"' not in request.prompt
    assert '"width"' not in request.prompt
    assert '"height"' not in request.prompt


def test_parser_accepts_fenced_tree_json():
    tree = _tree()
    parsed = parse_layout_tree("```json\n" + tree.model_dump_json() + "\n```")
    assert parsed == tree


def test_parser_normalizes_root_child_relation():
    # A3-08 smoke fix: a root child has exactly one legal relation value, so
    # the parser coerces it instead of burning a schema retry.
    payload = _tree().model_dump(mode="json")
    payload["nodes"][0]["relation_to_parent"] = "peer"
    parsed = parse_layout_tree(json.dumps(payload))
    assert parsed.nodes[0].relation_to_parent == TreeRelation.ROOT
    # The inverse mistake stays a hard error.
    bad = _tree().model_dump(mode="json")
    bad["nodes"][1]["relation_to_parent"] = "root"
    with pytest.raises(ValidationError, match="non-root edges"):
        parse_layout_tree(json.dumps(bad))


def test_apply_analyst_semantics_enforces_fidelity_by_construction():
    # A3-08 smoke fix: Planner paraphrases of free-text roles must not fail
    # T2 fidelity; both semantic fields are overwritten deterministically.
    analyst = _analyst()
    payload = _tree().model_dump(mode="json")
    payload["nodes"][0]["semantic_role"] = "planner paraphrased role"
    payload["nodes"][1]["semantic_type"] = "caption"
    drifted = A3LayoutTree.model_validate(payload)
    with pytest.raises(ValueError, match="mismatch"):
        validate_tree_against_analyst(drifted, analyst)
    repaired = apply_analyst_semantics(drifted, analyst)
    validate_tree_against_analyst(repaired, analyst)
    assert repaired.nodes[0].semantic_role == "primary message"
    assert repaired.nodes[1].semantic_type == "pricetag"
    # Grouping/edges — the Planner's actual judgement — are untouched.
    assert repaired.nodes[1].parent_id == "asset_0001"
    assert repaired.groups == drifted.groups


def test_tree_request_artifact_is_non_overwritable(tmp_path: Path):
    request = build_tree_request(_analyst())
    output = tmp_path / "planner"
    save_tree_request(request, output)
    assert (output / "planner_request.json").exists()
    with pytest.raises(FileExistsError):
        save_tree_request(request, output)


def test_t0_t1_t2_t3_conditions_change_only_tree_information():
    analyst = _analyst()
    t0 = make_tree_condition("T0", analyst)
    t1 = make_tree_condition("T1", analyst)
    t2 = make_tree_condition("T2", analyst, tree=_tree("predicted"))
    t3 = make_tree_condition("T3", analyst, tree=_tree("human_oracle"))
    expected_ids = [asset.asset_id for asset in analyst.assets]
    assert all(condition.asset_ids == expected_ids for condition in (t0, t1, t2, t3))
    assert t0.flat_roles is None and t0.tree is None
    assert t1.flat_roles is not None and t1.tree is None
    assert t2.tree.source == "predicted"
    assert t3.tree.source == "human_oracle"


def test_t3_oracle_may_correct_analyst_semantics_but_not_asset_ids():
    analyst = _analyst()
    oracle_payload = _tree("human_oracle").model_dump(mode="json")
    oracle_payload["nodes"][0]["semantic_type"] = "subtitle"
    oracle_payload["nodes"][0]["semantic_role"] = "human corrected role"
    oracle = A3LayoutTree.model_validate(oracle_payload)
    condition = make_tree_condition("T3", analyst, tree=oracle)
    assert condition.tree.nodes[0].semantic_role == "human corrected role"

    missing_payload = oracle.model_dump(mode="json")
    missing_payload["nodes"] = missing_payload["nodes"][:-1]
    missing_payload["groups"] = missing_payload["groups"][:-1]
    missing = A3LayoutTree.model_validate(missing_payload)
    with pytest.raises(ValueError, match="coverage mismatch"):
        make_tree_condition("T3", analyst, tree=missing)


def test_wrong_tree_source_for_ablation_arm_is_rejected():
    with pytest.raises(ValidationError, match="T2 requires"):
        make_tree_condition("T2", _analyst(), tree=_tree("human_oracle"))
    with pytest.raises(ValidationError, match="T3 requires"):
        make_tree_condition("T3", _analyst(), tree=_tree("predicted"))


def test_planner_action_enforces_model_retry_and_no_images():
    repo = Path(__file__).resolve().parents[4]
    source = (repo / "metagpt/ext/agentlayout/actions/plan_assets_a3.py").read_text()
    assert "actual_model != self.expected_model" in source
    assert "Previous response validation error" in source
    assert "aask(prompt)" in source
    assert "images=" not in source
