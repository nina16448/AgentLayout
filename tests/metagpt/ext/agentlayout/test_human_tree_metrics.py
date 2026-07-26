from __future__ import annotations

import random
from typing import Dict, List, Optional, Sequence, Tuple

import pytest

from metagpt.ext.agentlayout.layout_tree_v3 import (
    A3LayoutTree,
    A3TreeGroup,
    A3TreeNode,
)
from metagpt.ext.agentlayout.schema import Candidate, LayoutElement
from metagpt.ext.agentlayout.tools.human_tree_metrics import (
    evaluate_layout_realization,
    evaluate_tree_prediction,
    l1_gap,
)


Box = Tuple[int, int, int, int]


def _asset_id(index: int) -> str:
    return f"asset_{index:04d}"


def _tree(
    groups: Sequence[Sequence[int]],
    *,
    source: str = "human_oracle",
    parents: Optional[Dict[int, int]] = None,
    semantic_types: Optional[Dict[int, str]] = None,
    semantic_roles: Optional[Dict[int, str]] = None,
    confidences: Optional[Dict[int, float]] = None,
) -> A3LayoutTree:
    parents = parents or {}
    semantic_types = semantic_types or {}
    semantic_roles = semantic_roles or {}
    confidences = confidences or {}
    group_by_asset = {
        index: group_index
        for group_index, members in enumerate(groups)
        for index in members
    }
    nodes: List[A3TreeNode] = []
    for index in sorted(group_by_asset):
        group_index = group_by_asset[index]
        parent = parents.get(index)
        nodes.append(
            A3TreeNode(
                asset_id=_asset_id(index),
                semantic_type=semantic_types.get(index, "other"),
                semantic_role=semantic_roles.get(index, f"role {index}"),
                group_id=f"group_{group_index}",
                group_label=f"group {group_index}",
                parent_id="root" if parent is None else _asset_id(parent),
                relation_to_parent="root" if parent is None else "peer",
                ordering_priority=index,
                confidence=confidences.get(index, 1.0),
            )
        )
    tree_groups = [
        A3TreeGroup(
            group_id=f"group_{group_index}",
            label=f"group {group_index}",
            member_ids=[_asset_id(index) for index in members],
            ordering_priority=group_index,
            confidence=1.0,
        )
        for group_index, members in enumerate(groups)
    ]
    return A3LayoutTree(source=source, nodes=nodes, groups=tree_groups)


def _candidate(boxes: Dict[int, Box]) -> Candidate:
    return Candidate(
        candidate_id="candidate_01",
        elements=[
            LayoutElement(
                id=_asset_id(index),
                left=left,
                top=top,
                width=width,
                height=height,
                z_index=index,
            )
            for index, (left, top, width, height) in sorted(boxes.items())
        ],
    )


def _evaluate(tree: A3LayoutTree, boxes: Dict[int, Box]):
    return evaluate_layout_realization(
        tree=tree,
        candidate=_candidate(boxes),
        canvas_width=100,
        canvas_height=100,
        sample_id="sample_01",
        method="test",
    )


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ((0.0, 0.0, 2.0, 2.0), (1.0, 1.0, 2.0, 2.0), 0.0),
        ((0.0, 0.0, 2.0, 2.0), (2.0, 0.5, 1.0, 1.0), 0.0),
        ((0.0, 0.0, 2.0, 2.0), (3.0, 0.5, 1.0, 1.0), 1.0),
        ((0.0, 0.0, 2.0, 2.0), (0.5, 4.0, 1.0, 1.0), 2.0),
        ((0.0, 0.0, 2.0, 2.0), (4.0, 5.0, 1.0, 1.0), 5.0),
    ],
    ids=["overlap", "touch", "horizontal", "vertical", "diagonal"],
)
def test_l1_gap_five_required_cases(left, right, expected):
    assert l1_gap(left, right) == expected


def test_compact_groups_score_near_one_and_scattered_groups_score_below_half():
    tree = _tree([[0, 1], [2, 3]], parents={1: 0, 3: 2})
    compact = _evaluate(
        tree,
        {
            0: (0, 0, 10, 10),
            1: (10, 0, 10, 10),
            2: (80, 80, 10, 10),
            3: (90, 80, 10, 10),
        },
    )
    assert compact.sgc is not None and compact.sgc > 0.999
    assert compact.tlc == 1.0
    assert compact.pca == 1.0

    scattered = _evaluate(
        tree,
        {
            0: (0, 0, 10, 10),
            1: (80, 0, 10, 10),
            2: (10, 0, 10, 10),
            3: (90, 0, 10, 10),
        },
    )
    assert scattered.sgc is not None and scattered.sgc < 0.5
    assert scattered.tlc is not None and scattered.tlc < 0.5


def test_all_overlapping_boxes_make_tlc_exactly_half():
    result = _evaluate(
        _tree([[0, 1], [2, 3]]),
        {index: (20, 20, 10, 10) for index in range(4)},
    )
    assert result.tlc == 0.5


def test_single_group_and_all_singletons_record_metric_specific_skips():
    single_group = _evaluate(
        _tree([[0, 1]], parents={1: 0}),
        {0: (0, 0, 10, 10), 1: (50, 0, 10, 10)},
    )
    assert single_group.sgc is None
    assert "sgc:single_group" in single_group.skip_reasons
    assert single_group.tlc is None
    assert "tlc:no_triplets" in single_group.skip_reasons

    all_singletons = _evaluate(
        _tree([[0], [1]]),
        {0: (0, 0, 10, 10), 1: (50, 0, 10, 10)},
    )
    assert all_singletons.sgc is None
    assert "sgc:all_groups_singleton" in all_singletons.skip_reasons
    assert all_singletons.tlc is None
    assert "tlc:no_triplets" in all_singletons.skip_reasons
    assert all_singletons.pca is None
    assert "pca:no_edges" in all_singletons.skip_reasons


def test_pca_uses_non_root_edges_and_parent_distance_median():
    tree = _tree([[0, 1], [2], [3]], parents={1: 0})
    result = _evaluate(
        tree,
        {
            0: (0, 0, 5, 5),
            1: (90, 0, 5, 5),
            2: (10, 0, 5, 5),
            3: (20, 0, 5, 5),
        },
    )
    assert result.pca == 0.0


def test_sgc_uses_group_level_not_pair_level_intra_mean():
    tree = _tree([[0, 1], [2, 3, 4]])
    result = _evaluate(
        tree,
        {
            0: (0, 0, 1, 1),
            1: (10, 0, 1, 1),
            2: (20, 0, 1, 1),
            3: (21, 0, 1, 1),
            4: (22, 0, 1, 1),
        },
    )
    # Normalized D_intra = mean(0.09, mean(0, 0.01, 0)) = 0.046666...;
    # D_inter = mean(0.19, 0.20, 0.21, 0.09, 0.10, 0.11) = 0.15.
    expected = 0.15 / (0.04666666666666667 + 0.15 + 1e-6)
    assert result.sgc == pytest.approx(expected)


def test_candidate_boxes_are_normalized_and_non_tree_elements_are_ignored():
    tree = _tree([[0, 1], [2, 3]], parents={1: 0, 3: 2})
    base = _evaluate(
        tree,
        {
            0: (0, 0, 10, 10),
            1: (10, 0, 10, 10),
            2: (80, 80, 10, 10),
            3: (90, 80, 10, 10),
        },
    )
    scaled = _candidate(
        {
            0: (0, 0, 20, 20),
            1: (20, 0, 20, 20),
            2: (160, 160, 20, 20),
            3: (180, 160, 20, 20),
        }
    )
    scaled.elements.append(
        LayoutElement(
            id="bg_canvas",
            left=0,
            top=0,
            width=200,
            height=200,
            z_index=0,
        )
    )
    scaled.elements.append(scaled.elements[-1].model_copy())
    normalized = evaluate_layout_realization(
        tree=tree,
        candidate=scaled,
        canvas_width=200,
        canvas_height=200,
        sample_id="sample_01",
        method="scaled",
    )
    assert normalized.sgc == base.sgc
    assert normalized.tlc == base.tlc
    assert normalized.pca == base.pca
    assert normalized.n_elements == 4


def test_layout_id_mismatches_are_skipped_without_guessing():
    tree = _tree([[0, 1], [2]], parents={1: 0})
    missing = evaluate_layout_realization(
        tree=tree,
        candidate=_candidate({0: (0, 0, 10, 10), 1: (20, 0, 10, 10)}),
        canvas_width=100,
        canvas_height=100,
        sample_id="sample_01",
        method="missing",
    )
    assert missing.sgc is None and missing.tlc is None and missing.pca is None
    assert missing.skip_reasons == ["layout:missing_elements:asset_0002"]

    duplicate = _candidate(
        {0: (0, 0, 10, 10), 1: (20, 0, 10, 10), 2: (40, 0, 10, 10)}
    )
    duplicate.elements.append(duplicate.elements[0].model_copy())
    duplicate_result = evaluate_layout_realization(
        tree=tree,
        candidate=duplicate,
        canvas_width=100,
        canvas_height=100,
        sample_id="sample_01",
        method="duplicate",
    )
    assert duplicate_result.sgc is None
    assert duplicate_result.skip_reasons == [
        "layout:duplicate_elements:asset_0000"
    ]


def test_random_layouts_have_half_tlc_in_fixed_seed_monte_carlo():
    rng = random.Random(20260711)
    tree = _tree([[0, 1], [2, 3]])
    values = []
    for _ in range(1000):
        candidate = _candidate(
            {
                index: (rng.randrange(0, 951), rng.randrange(0, 951), 50, 50)
                for index in range(4)
            }
        )
        result = evaluate_layout_realization(
            tree=tree,
            candidate=candidate,
            canvas_width=1000,
            canvas_height=1000,
            sample_id="monte_carlo",
            method="random",
        )
        assert result.tlc is not None
        values.append(result.tlc)
    assert sum(values) / len(values) == pytest.approx(0.5, abs=0.05)


REFERENCE_TYPES = {
    0: "title",
    1: "pricetag",
    2: "product_image",
    3: "logo",
}
REFERENCE_ROLES = {index: f"reference role {index}" for index in range(4)}


def test_tree_prediction_metrics_perfect_case():
    human = _tree(
        [[0, 1, 2], [3]],
        parents={1: 0, 2: 1},
        semantic_types=REFERENCE_TYPES,
        semantic_roles=REFERENCE_ROLES,
    )
    predicted = _tree(
        [[0, 1, 2], [3]],
        source="predicted",
        parents={1: 0, 2: 1},
        semantic_types=REFERENCE_TYPES,
        semantic_roles=REFERENCE_ROLES,
    )
    result = evaluate_tree_prediction(predicted, human)
    assert result.same_group.precision == 1.0
    assert result.same_group.recall == 1.0
    assert result.same_group.f1 == 1.0
    assert result.parent_child.f1 == 1.0
    assert result.semantic_type_accuracy == 1.0
    assert result.semantic_role_accuracy == 1.0
    assert result.n_uncertain_nodes == 0


def test_tree_prediction_metrics_partial_case():
    human = _tree(
        [[0, 1, 2], [3]],
        parents={1: 0, 2: 1},
        semantic_types=REFERENCE_TYPES,
        semantic_roles=REFERENCE_ROLES,
    )
    predicted_types = dict(REFERENCE_TYPES)
    predicted_types.update({2: "caption", 3: "cta"})
    predicted_roles = dict(REFERENCE_ROLES)
    predicted_roles.update({1: "wrong role 1", 3: "wrong role 3"})
    predicted = _tree(
        [[0, 1], [2, 3]],
        source="predicted",
        parents={1: 0, 2: 0},
        semantic_types=predicted_types,
        semantic_roles=predicted_roles,
    )
    result = evaluate_tree_prediction(predicted, human)
    assert result.same_group.precision == 0.5
    assert result.same_group.recall == pytest.approx(1 / 3)
    assert result.same_group.f1 == pytest.approx(0.4)
    assert result.parent_child.precision == 0.5
    assert result.parent_child.recall == 0.5
    assert result.parent_child.f1 == 0.5
    assert result.semantic_type_accuracy == 0.5
    assert result.semantic_role_accuracy == 0.5


def test_tree_prediction_metrics_zero_overlap_case():
    human = _tree(
        [[0, 1], [2, 3]],
        parents={1: 0, 3: 2},
        semantic_types=REFERENCE_TYPES,
        semantic_roles=REFERENCE_ROLES,
    )
    predicted = _tree(
        [[0, 2], [1, 3]],
        source="predicted",
        parents={2: 0, 3: 1},
        semantic_types={0: "logo", 1: "product_image", 2: "pricetag", 3: "title"},
        semantic_roles={index: f"wrong role {index}" for index in range(4)},
    )
    result = evaluate_tree_prediction(predicted, human)
    assert result.same_group.precision == 0.0
    assert result.same_group.recall == 0.0
    assert result.same_group.f1 == 0.0
    assert result.parent_child.precision == 0.0
    assert result.parent_child.recall == 0.0
    assert result.parent_child.f1 == 0.0
    assert result.semantic_type_accuracy == 0.0
    assert result.semantic_role_accuracy == 0.0


def test_uncertain_human_nodes_are_excluded_and_reported_separately():
    human = _tree(
        [[0, 1], [2]],
        parents={1: 0},
        semantic_types={0: "title", 1: "pricetag", 2: "logo"},
        semantic_roles={0: "headline", 1: "offer", 2: "brand"},
        confidences={1: 0.5},
    )
    predicted = _tree(
        [[0], [1, 2]],
        source="predicted",
        parents={1: 2},
        semantic_types={0: "title", 1: "caption", 2: "logo"},
        semantic_roles={0: "headline", 1: "wrong", 2: "brand"},
    )
    result = evaluate_tree_prediction(predicted, human)
    assert result.same_group.f1 == 1.0
    assert result.parent_child.f1 == 1.0
    assert result.semantic_type_accuracy == 1.0
    assert result.semantic_role_accuracy == 1.0
    assert result.n_uncertain_nodes == 1
    assert result.uncertain_node_ids == ["asset_0001"]
    assert result.uncertain_same_group is not None
    assert result.uncertain_same_group.f1 == 0.0
    assert result.uncertain_parent_child is not None
    assert result.uncertain_parent_child.f1 == 0.0
    assert result.uncertain_semantic_type_accuracy == 0.0
    assert result.uncertain_semantic_role_accuracy == 0.0
    assert result.excluded_reference_same_group_pairs == 1
    assert result.excluded_reference_parent_child_edges == 1


def test_tree_prediction_requires_same_asset_coverage_and_tree_sources():
    human = _tree([[0], [1]])
    predicted = _tree([[0], [1]], source="predicted")
    with pytest.raises(ValueError, match="coverage mismatch"):
        evaluate_tree_prediction(_tree([[0]], source="predicted"), human)
    with pytest.raises(ValueError, match="source='predicted'"):
        evaluate_tree_prediction(human, human)
    with pytest.raises(ValueError, match="source='human_oracle'"):
        evaluate_tree_prediction(predicted, predicted)

    with pytest.raises(ValueError, match="source='human_oracle'"):
        evaluate_layout_realization(
            tree=predicted,
            candidate=_candidate({0: (0, 0, 10, 10), 1: (20, 0, 10, 10)}),
            canvas_width=100,
            canvas_height=100,
            sample_id="sample_01",
            method="wrong_tree",
        )
