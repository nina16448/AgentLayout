"""Deterministic A3 metrics against one injected human reference tree.

This module intentionally does not reuse ``semantic_group_metrics.py``.  That
legacy Step-90 implementation derives groups from root subtrees and was used
for predicted-tree self-consistency.  A3 instead uses the explicit, exact
partition in :class:`A3LayoutTree.groups` and obtains non-root parent-child
edges directly from :class:`A3TreeNode.parent_id`.

Layout realization metrics follow ``layout_agent/Metrics.md`` exactly:

* SGC uses a group-level mean for within-group distances and a pair-level mean
  for cross-group distances;
* TLC scores strict wins as 1 and distance ties as 0.5;
* PCA compares each non-root parent-child distance with the parent's median
  distance to every other foreground element.

The caller must pass the reference tree explicitly for every candidate.  No
predicted tree or global tree state is read, so all experimental arms can be
evaluated against the same human oracle.
"""
from __future__ import annotations

from itertools import combinations
from statistics import median
from typing import Dict, List, Optional, Sequence, Set, Tuple

from pydantic import BaseModel, ConfigDict, Field

from metagpt.ext.agentlayout.layout_tree_v3 import A3LayoutTree
from metagpt.ext.agentlayout.schema import Candidate, LayoutElement


EPS = 1e-6
Box = Tuple[float, float, float, float]
Pair = Tuple[str, str]
DistanceMatrix = Dict[str, Dict[str, float]]


def l1_gap(a: Box, b: Box) -> float:
    """Return the L1 empty-space gap between two axis-aligned bounding boxes.

    Boxes use ``(left, top, width, height)``.  Overlap or contact on an axis
    contributes zero on that axis, so overlapping and touching boxes have
    total distance zero when they also overlap/contact on the other axis.
    """
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    gap_x = max(ax, bx) - min(ax + aw, bx + bw)
    gap_y = max(ay, by) - min(ay + ah, by + bh)
    return max(0.0, gap_x) + max(0.0, gap_y)


class HumanTreeLayoutMetrics(BaseModel):
    """One sample/method row for A3 human-tree layout realization metrics."""

    model_config = ConfigDict(extra="forbid")

    sample_id: str
    method: str
    sgc: Optional[float] = None
    tlc: Optional[float] = None
    pca: Optional[float] = None
    n_elements: int = 0
    n_groups: int = 0
    n_triplets: int = 0
    skip_reasons: List[str] = Field(default_factory=list)


class PRF1Metrics(BaseModel):
    """Precision/recall/F1 plus the set cardinalities that produced them."""

    model_config = ConfigDict(extra="forbid")

    precision: float
    recall: float
    f1: float
    n_true_positive: int
    n_predicted: int
    n_reference: int


class TreePredictionMetrics(BaseModel):
    """Predicted A3 tree accuracy against one human-oracle A3 tree.

    Primary metrics exclude every relation involving a human node whose
    confidence is exactly ``0.5``.  Those ambiguous nodes are not forced into
    the error count: their semantic matches and the number of excluded
    structural relations are reported separately.

    ``semantic_role_accuracy`` is exact, case-sensitive string equality.  No
    normalized role score is reported.
    """

    model_config = ConfigDict(extra="forbid")

    same_group: PRF1Metrics
    parent_child: PRF1Metrics
    semantic_type_accuracy: Optional[float] = None
    semantic_role_accuracy: Optional[float] = None
    n_certain_nodes: int
    n_uncertain_nodes: int
    uncertain_node_ids: List[str] = Field(default_factory=list)
    uncertain_same_group: Optional[PRF1Metrics] = None
    uncertain_parent_child: Optional[PRF1Metrics] = None
    uncertain_semantic_type_accuracy: Optional[float] = None
    uncertain_semantic_role_accuracy: Optional[float] = None
    excluded_predicted_same_group_pairs: int = 0
    excluded_reference_same_group_pairs: int = 0
    excluded_predicted_parent_child_edges: int = 0
    excluded_reference_parent_child_edges: int = 0


def _explicit_groups(tree: A3LayoutTree) -> List[List[str]]:
    """Read the A3 group partition verbatim; never infer root subtrees."""
    return [list(group.member_ids) for group in tree.groups]


def _parent_child_edges(tree: A3LayoutTree) -> Set[Pair]:
    """Return directed non-root edges from the normalized parent_id fields."""
    return {
        (node.parent_id, node.asset_id)
        for node in tree.nodes
        if node.parent_id != "root"
    }


def _distance_matrix(ids: Sequence[str], boxes: Dict[str, Box]) -> DistanceMatrix:
    matrix: DistanceMatrix = {asset_id: {asset_id: 0.0} for asset_id in ids}
    for index, left_id in enumerate(ids):
        for right_id in ids[index + 1 :]:
            distance = l1_gap(boxes[left_id], boxes[right_id])
            matrix[left_id][right_id] = distance
            matrix[right_id][left_id] = distance
    return matrix


def _sgc(
    groups: Sequence[Sequence[str]],
    distances: DistanceMatrix,
    skip_reasons: List[str],
) -> Optional[float]:
    if len(groups) < 2:
        skip_reasons.append("sgc:single_group")
        return None

    within_group_means: List[float] = []
    for group in groups:
        pair_distances = [distances[a][b] for a, b in combinations(group, 2)]
        if pair_distances:
            within_group_means.append(sum(pair_distances) / len(pair_distances))
    if not within_group_means:
        skip_reasons.append("sgc:all_groups_singleton")
        return None

    cross_group_distances = [
        distances[left][right]
        for left_index, left_group in enumerate(groups)
        for right_group in groups[left_index + 1 :]
        for left in left_group
        for right in right_group
    ]
    if not cross_group_distances:
        # Structurally unreachable for a valid A3 partition with >=2 groups,
        # but preserve the Metrics.md D_inter skip contract fail-closed.
        skip_reasons.append("sgc:single_group")
        return None

    d_intra = sum(within_group_means) / len(within_group_means)
    d_inter = sum(cross_group_distances) / len(cross_group_distances)
    return d_inter / (d_intra + d_inter + EPS)


def _tlc(
    ids: Sequence[str],
    group_by_id: Dict[str, int],
    distances: DistanceMatrix,
    skip_reasons: List[str],
) -> Tuple[Optional[float], int]:
    score = 0.0
    n_triplets = 0
    for anchor in ids:
        anchor_group = group_by_id[anchor]
        same_group = [
            asset_id
            for asset_id in ids
            if asset_id != anchor and group_by_id[asset_id] == anchor_group
        ]
        other_group = [
            asset_id for asset_id in ids if group_by_id[asset_id] != anchor_group
        ]
        for same_id in same_group:
            same_distance = distances[anchor][same_id]
            for other_id in other_group:
                other_distance = distances[anchor][other_id]
                n_triplets += 1
                if same_distance < other_distance:
                    score += 1.0
                elif same_distance == other_distance:
                    score += 0.5
    if n_triplets == 0:
        skip_reasons.append("tlc:no_triplets")
        return None, 0
    return score / n_triplets, n_triplets


def _pca(
    tree: A3LayoutTree,
    ids: Sequence[str],
    distances: DistanceMatrix,
    skip_reasons: List[str],
) -> Optional[float]:
    edges = sorted(_parent_child_edges(tree))
    if not edges:
        skip_reasons.append("pca:no_edges")
        return None

    hits = 0
    for parent_id, child_id in edges:
        comparison_distances = [
            distances[parent_id][asset_id]
            for asset_id in ids
            if asset_id != parent_id
        ]
        if distances[parent_id][child_id] <= median(comparison_distances):
            hits += 1
    return hits / len(edges)


def evaluate_layout_realization(
    *,
    tree: A3LayoutTree,
    candidate: Candidate,
    canvas_width: float,
    canvas_height: float,
    sample_id: str,
    method: str,
) -> HumanTreeLayoutMetrics:
    """Evaluate one pixel ``Candidate`` against an injected human A3 tree.

    Candidate boxes are divided by canvas width/height before distance
    computation.  Candidate elements absent from the tree (including legacy
    ``bg_*`` elements) are ignored.  Missing or duplicate required IDs make
    all three metrics undefined and are recorded instead of guessed.
    """
    if tree.source != "human_oracle":
        raise ValueError("layout realization requires tree source='human_oracle'")
    if canvas_width <= 0 or canvas_height <= 0:
        raise ValueError("canvas_width and canvas_height must be positive")

    groups = _explicit_groups(tree)
    tree_ids = [asset_id for group in groups for asset_id in group]
    result = HumanTreeLayoutMetrics(
        sample_id=sample_id,
        method=method,
        n_elements=len(tree_ids),
        n_groups=len(groups),
    )

    tree_id_set = set(tree_ids)
    candidate_by_id: Dict[str, LayoutElement] = {}
    duplicate_ids: Set[str] = set()
    for element in candidate.elements:
        if element.id not in tree_id_set:
            continue
        if element.id in candidate_by_id:
            duplicate_ids.add(element.id)
        else:
            candidate_by_id[element.id] = element
    if duplicate_ids:
        result.skip_reasons.append(
            f"layout:duplicate_elements:{','.join(sorted(duplicate_ids))}"
        )
    missing_ids = sorted(set(tree_ids) - set(candidate_by_id))
    if missing_ids:
        result.skip_reasons.append(f"layout:missing_elements:{','.join(missing_ids)}")
    if result.skip_reasons:
        return result

    normalized_boxes: Dict[str, Box] = {}
    for asset_id in tree_ids:
        element = candidate_by_id[asset_id]
        normalized_boxes[asset_id] = (
            element.left / canvas_width,
            element.top / canvas_height,
            element.width / canvas_width,
            element.height / canvas_height,
        )
    distances = _distance_matrix(tree_ids, normalized_boxes)
    group_by_id = {
        asset_id: group_index
        for group_index, group in enumerate(groups)
        for asset_id in group
    }

    result.sgc = _sgc(groups, distances, result.skip_reasons)
    result.tlc, result.n_triplets = _tlc(
        tree_ids, group_by_id, distances, result.skip_reasons
    )
    result.pca = _pca(tree, tree_ids, distances, result.skip_reasons)
    return result


def _same_group_pairs(
    tree: A3LayoutTree, included_ids: Optional[Set[str]] = None
) -> Set[Pair]:
    pairs: Set[Pair] = set()
    for group in tree.groups:
        members = sorted(
            asset_id
            for asset_id in group.member_ids
            if included_ids is None or asset_id in included_ids
        )
        pairs.update(combinations(members, 2))
    return pairs


def _filtered_edges(
    tree: A3LayoutTree, included_ids: Optional[Set[str]] = None
) -> Set[Pair]:
    edges = _parent_child_edges(tree)
    if included_ids is None:
        return edges
    return {
        (parent_id, child_id)
        for parent_id, child_id in edges
        if parent_id in included_ids and child_id in included_ids
    }


def _prf(predicted: Set[Pair], reference: Set[Pair]) -> PRF1Metrics:
    true_positive = len(predicted & reference)
    if not predicted and not reference:
        precision = recall = f1 = 1.0
    else:
        precision = true_positive / len(predicted) if predicted else 0.0
        recall = true_positive / len(reference) if reference else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )
    return PRF1Metrics(
        precision=precision,
        recall=recall,
        f1=f1,
        n_true_positive=true_positive,
        n_predicted=len(predicted),
        n_reference=len(reference),
    )


def _accuracy(matches: Sequence[bool]) -> Optional[float]:
    return sum(matches) / len(matches) if matches else None


def evaluate_tree_prediction(
    predicted: A3LayoutTree, human_oracle: A3LayoutTree
) -> TreePredictionMetrics:
    """Compare a predicted T2 tree with the human oracle for the same sample.

    Both trees must cover the exact same stable asset IDs.  Human nodes with
    confidence ``0.5`` are excluded from primary pair, edge, type and role
    metrics, then summarized separately.  Empty-vs-empty relation sets score
    1/1/1 because the prediction exactly matches the absence of a relation;
    a one-sided empty set scores zero under the usual zero-division rule.
    """
    if predicted.source != "predicted":
        raise ValueError("predicted tree must use source='predicted'")
    if human_oracle.source != "human_oracle":
        raise ValueError("human oracle tree must use source='human_oracle'")

    predicted_nodes = {node.asset_id: node for node in predicted.nodes}
    reference_nodes = {node.asset_id: node for node in human_oracle.nodes}
    if set(predicted_nodes) != set(reference_nodes):
        raise ValueError(
            "tree asset coverage mismatch: "
            f"missing={sorted(set(reference_nodes) - set(predicted_nodes))}, "
            f"extra={sorted(set(predicted_nodes) - set(reference_nodes))}"
        )

    uncertain_ids = sorted(
        node.asset_id for node in human_oracle.nodes if node.confidence == 0.5
    )
    uncertain_set = set(uncertain_ids)
    certain_ids = set(reference_nodes) - uncertain_set

    predicted_pairs_full = _same_group_pairs(predicted)
    reference_pairs_full = _same_group_pairs(human_oracle)
    predicted_pairs = _same_group_pairs(predicted, certain_ids)
    reference_pairs = _same_group_pairs(human_oracle, certain_ids)
    predicted_edges_full = _filtered_edges(predicted)
    reference_edges_full = _filtered_edges(human_oracle)
    predicted_edges = _filtered_edges(predicted, certain_ids)
    reference_edges = _filtered_edges(human_oracle, certain_ids)

    certain_order = sorted(certain_ids)
    type_matches = [
        predicted_nodes[asset_id].semantic_type
        == reference_nodes[asset_id].semantic_type
        for asset_id in certain_order
    ]
    role_matches = [
        predicted_nodes[asset_id].semantic_role
        == reference_nodes[asset_id].semantic_role
        for asset_id in certain_order
    ]
    uncertain_type_matches = [
        predicted_nodes[asset_id].semantic_type
        == reference_nodes[asset_id].semantic_type
        for asset_id in uncertain_ids
    ]
    uncertain_role_matches = [
        predicted_nodes[asset_id].semantic_role
        == reference_nodes[asset_id].semantic_role
        for asset_id in uncertain_ids
    ]
    uncertain_predicted_pairs = predicted_pairs_full - predicted_pairs
    uncertain_reference_pairs = reference_pairs_full - reference_pairs
    uncertain_predicted_edges = predicted_edges_full - predicted_edges
    uncertain_reference_edges = reference_edges_full - reference_edges

    return TreePredictionMetrics(
        same_group=_prf(predicted_pairs, reference_pairs),
        parent_child=_prf(predicted_edges, reference_edges),
        semantic_type_accuracy=_accuracy(type_matches),
        semantic_role_accuracy=_accuracy(role_matches),
        n_certain_nodes=len(certain_ids),
        n_uncertain_nodes=len(uncertain_ids),
        uncertain_node_ids=uncertain_ids,
        uncertain_same_group=(
            _prf(uncertain_predicted_pairs, uncertain_reference_pairs)
            if uncertain_ids
            else None
        ),
        uncertain_parent_child=(
            _prf(uncertain_predicted_edges, uncertain_reference_edges)
            if uncertain_ids
            else None
        ),
        uncertain_semantic_type_accuracy=_accuracy(uncertain_type_matches),
        uncertain_semantic_role_accuracy=_accuracy(uncertain_role_matches),
        excluded_predicted_same_group_pairs=len(uncertain_predicted_pairs),
        excluded_reference_same_group_pairs=len(uncertain_reference_pairs),
        excluded_predicted_parent_child_edges=len(uncertain_predicted_edges),
        excluded_reference_parent_child_edges=len(uncertain_reference_edges),
    )
