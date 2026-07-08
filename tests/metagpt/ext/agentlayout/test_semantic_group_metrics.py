"""Step 90 -- acceptance tests for the semantic-group metrics (SGC / TLC / PCA).

Written BEFORE the implementation, per the task spec in
layout_agent/new_experiment.md. Covers:

1. the shared L1 bounding-box gap distance (5 cases: overlap, touching,
   horizontal, vertical, diagonal separation),
2. the group extraction rule (root's direct children subtrees),
3. the five acceptance scenarios (compact, scattered, all-stacked,
   single-group skip, Monte-Carlo TLC sanity),
4. the id-alignment layer for baselines whose element ids differ.
"""
from __future__ import annotations

import random

import pytest

from metagpt.ext.agentlayout.schema import LayoutTree, LayoutTreeNode
from metagpt.ext.agentlayout.tools.semantic_group_metrics import (
    aggregate,
    aggregate_markdown,
    align_by_type_order,
    evaluate_sample,
    l1_gap,
    qualitative_picks,
    tree_groups,
    tree_parent_child_edges,
)


# ------------------------------------------------------------------
# Fixture helpers
# ------------------------------------------------------------------


def _node(el_id: str, *children: LayoutTreeNode) -> LayoutTreeNode:
    return LayoutTreeNode(id=el_id, children=list(children))


def _tree(*children: LayoutTreeNode) -> LayoutTree:
    return LayoutTree(root=LayoutTreeNode(id="root", children=list(children)))


# Two groups of two: (a1 -> a2), (b1 -> b2).
TWO_PAIR_TREE = _tree(_node("a1", _node("a2")), _node("b1", _node("b2")))


def _eval(tree, boxes, cw=1000, ch=1000, method="test"):
    return evaluate_sample(
        tree=tree,
        pixel_boxes=boxes,
        canvas_width=cw,
        canvas_height=ch,
        sample_id="s1",
        method=method,
    )


# ------------------------------------------------------------------
# 1. Distance function (normalized boxes: x, y, w, h)
# ------------------------------------------------------------------


def test_l1_gap_overlap_is_zero():
    assert l1_gap((0.0, 0.0, 0.5, 0.5), (0.3, 0.3, 0.5, 0.5)) == 0.0


def test_l1_gap_touching_is_zero():
    # Right edge of A == left edge of B, vertically aligned.
    assert l1_gap((0.0, 0.0, 0.3, 0.3), (0.3, 0.0, 0.3, 0.3)) == 0.0


def test_l1_gap_horizontal_separation():
    # A ends at x=0.2, B starts at x=0.5 -> gap_x = 0.3; y ranges overlap.
    assert l1_gap((0.0, 0.0, 0.2, 0.2), (0.5, 0.0, 0.2, 0.2)) == pytest.approx(0.3)


def test_l1_gap_vertical_separation():
    assert l1_gap((0.0, 0.0, 0.2, 0.2), (0.0, 0.6, 0.2, 0.2)) == pytest.approx(0.4)


def test_l1_gap_diagonal_separation():
    # gap_x = 0.3 and gap_y = 0.4 -> 0.7.
    assert l1_gap((0.0, 0.0, 0.2, 0.2), (0.5, 0.6, 0.2, 0.2)) == pytest.approx(0.7)


def test_l1_gap_symmetry():
    a, b = (0.1, 0.2, 0.15, 0.1), (0.7, 0.5, 0.2, 0.3)
    assert l1_gap(a, b) == pytest.approx(l1_gap(b, a))


# ------------------------------------------------------------------
# 2. Group extraction (root's direct children subtrees)
# ------------------------------------------------------------------


def test_tree_groups_doc_example():
    # root |- product_img_1 - headline_1 - {pricetag_1, caption_1, caption_2}
    #      |- logo_1
    tree = _tree(
        _node(
            "product_img_1",
            _node("headline_1", _node("pricetag_1"), _node("caption_1"), _node("caption_2")),
        ),
        _node("logo_1"),
    )
    groups = tree_groups(tree)
    assert sorted(len(g) for g in groups) == [1, 5]
    big = next(g for g in groups if len(g) == 5)
    assert set(big) == {"product_img_1", "headline_1", "pricetag_1", "caption_1", "caption_2"}
    assert ["logo_1"] in groups


def test_tree_parent_child_edges_exclude_root():
    tree = _tree(_node("p", _node("c1"), _node("c2", _node("g1"))), _node("solo"))
    edges = tree_parent_child_edges(tree)
    assert set(edges) == {("p", "c1"), ("p", "c2"), ("c2", "g1")}


# ------------------------------------------------------------------
# 3. Acceptance scenario 1: compact groups, far apart
# ------------------------------------------------------------------


def test_compact_groups_high_sgc_perfect_tlc():
    boxes = {
        # group a: top-left corner, 10px apart
        "a1": (0, 0, 100, 100),
        "a2": (110, 0, 100, 100),
        # group b: bottom-right corner, 10px apart
        "b1": (800, 800, 100, 100),
        "b2": (800, 910, 90, 90),
    }
    m = _eval(TWO_PAIR_TREE, boxes)
    assert m.sgc is not None and m.sgc > 0.8
    assert m.tlc == pytest.approx(1.0)
    assert m.pca == pytest.approx(1.0)
    assert m.n_elements == 4
    assert m.n_groups == 2
    assert m.skip_reasons == []


# ------------------------------------------------------------------
# 4. Acceptance scenario 2: groups scattered, strangers adjacent
# ------------------------------------------------------------------


def test_scattered_groups_low_sgc_low_tlc():
    boxes = {
        # a1 sits next to b1; a2 sits next to b2 -- diagonal partners far away.
        "a1": (0, 0, 100, 100),
        "b1": (110, 0, 100, 100),
        "a2": (800, 800, 100, 100),
        "b2": (800, 910, 90, 90),
    }
    m = _eval(TWO_PAIR_TREE, boxes)
    assert m.sgc is not None and m.sgc < 0.5
    assert m.tlc is not None and m.tlc < 0.5


# ------------------------------------------------------------------
# 5. Acceptance scenario 3: everything stacked -> all ties
# ------------------------------------------------------------------


def test_all_stacked_tlc_is_half():
    boxes = {k: (100, 100, 200, 200) for k in ("a1", "a2", "b1", "b2")}
    m = _eval(TWO_PAIR_TREE, boxes)
    assert m.tlc == pytest.approx(0.5)
    # SGC must not inflate either: D_inter = 0 -> SGC = 0.
    assert m.sgc == pytest.approx(0.0)


# ------------------------------------------------------------------
# 6. Acceptance scenario 4: degenerate trees -> None + skip reasons
# ------------------------------------------------------------------


def test_single_group_skips_sgc_and_tlc():
    tree = _tree(_node("a1", _node("a2")))
    boxes = {"a1": (0, 0, 100, 100), "a2": (200, 0, 100, 100)}
    m = _eval(tree, boxes)
    assert m.sgc is None
    assert m.tlc is None
    assert any("sgc" in r for r in m.skip_reasons)
    assert any("tlc" in r for r in m.skip_reasons)
    # PCA still defined: one non-root edge.
    assert m.pca is not None


def test_all_singletons_skips_everything():
    tree = _tree(_node("a1"), _node("b1"), _node("c1"))
    boxes = {"a1": (0, 0, 10, 10), "b1": (50, 0, 10, 10), "c1": (0, 50, 10, 10)}
    m = _eval(tree, boxes)
    assert m.sgc is None and m.tlc is None and m.pca is None
    assert m.n_groups == 3
    assert m.n_triplets == 0


def test_missing_element_skips_sample():
    boxes = {"a1": (0, 0, 100, 100), "a2": (110, 0, 100, 100), "b1": (800, 800, 100, 100)}
    m = _eval(TWO_PAIR_TREE, boxes)  # b2 missing from the layout
    assert m.sgc is None and m.tlc is None and m.pca is None
    assert any("missing" in r for r in m.skip_reasons)


def test_background_boxes_are_ignored():
    boxes = {
        "bg_1": (0, 0, 1000, 1000),
        "a1": (0, 0, 100, 100),
        "a2": (110, 0, 100, 100),
        "b1": (800, 800, 100, 100),
        "b2": (800, 910, 90, 90),
    }
    m = _eval(TWO_PAIR_TREE, boxes)
    assert m.n_elements == 4  # bg_1 is not in the tree, silently ignored


# ------------------------------------------------------------------
# 7. Acceptance scenario 5: Monte-Carlo TLC sanity (random ~ 0.5)
# ------------------------------------------------------------------


def test_monte_carlo_random_tlc_near_half():
    tree = _tree(
        _node("a1", _node("a2"), _node("a3")),
        _node("b1", _node("b2"), _node("b3")),
    )
    rng = random.Random(42)
    values = []
    for _ in range(1000):
        boxes = {
            k: (rng.uniform(0, 900), rng.uniform(0, 900), 100, 100)
            for k in ("a1", "a2", "a3", "b1", "b2", "b3")
        }
        m = _eval(tree, boxes)
        assert m.tlc is not None
        values.append(m.tlc)
    mean_tlc = sum(values) / len(values)
    assert 0.45 <= mean_tlc <= 0.55


# ------------------------------------------------------------------
# 8. PCA behaviour
# ------------------------------------------------------------------


def test_pca_rewards_adjacent_child_and_penalizes_far_child():
    # p has child c and three unrelated far elements. Case 1: c hugs p.
    tree = _tree(_node("p", _node("c")), _node("x1"), _node("x2"), _node("x3"))
    near = {
        "p": (0, 0, 100, 100),
        "c": (105, 0, 100, 100),
        "x1": (800, 0, 100, 100),
        "x2": (0, 800, 100, 100),
        "x3": (800, 800, 100, 100),
    }
    assert _eval(tree, near).pca == pytest.approx(1.0)

    far = dict(near, c=(850, 850, 100, 100), x1=(105, 0, 100, 100))
    assert _eval(tree, far).pca == pytest.approx(0.0)


# ------------------------------------------------------------------
# 9. Id-alignment layer (semantic_type + order)
# ------------------------------------------------------------------


def test_align_by_type_order_matches_categories_in_order():
    source = [("text_1", "text"), ("text_2", "text"), ("img_1", "image")]
    target = [("layer_9", "text"), ("layer_12", "text"), ("layer_3", "image")]
    mapping = align_by_type_order(source, target)
    assert mapping == {"layer_9": "text_1", "layer_12": "text_2", "layer_3": "img_1"}


def test_align_by_type_order_count_mismatch_returns_none():
    source = [("text_1", "text"), ("text_2", "text")]
    target = [("layer_9", "text")]
    assert align_by_type_order(source, target) is None


# ------------------------------------------------------------------
# 10. Aggregation + qualitative picks
# ------------------------------------------------------------------


def _metrics_stub(sample_id, method, sgc):
    boxes = {
        "a1": (0, 0, 100, 100),
        "a2": (110, 0, 100, 100),
        "b1": (800, 800, 100, 100),
        "b2": (800, 910, 90, 90),
    }
    m = _eval(TWO_PAIR_TREE, boxes, method=method)
    return m.model_copy(update={"sample_id": sample_id, "sgc": sgc})


def test_aggregate_reports_mean_std_and_skips():
    samples = [
        _metrics_stub("s1", "agent", 0.8),
        _metrics_stub("s2", "agent", 0.6),
        _metrics_stub("s3", "agent", None).model_copy(
            update={"skip_reasons": ["sgc:single_group"]}
        ),
    ]
    rows = aggregate(samples)
    assert len(rows) == 1
    row = rows[0]
    assert row.method == "agent"
    assert row.sgc_mean == pytest.approx(0.7)
    assert row.sgc_n == 2
    assert row.sgc_skipped == 1
    md = aggregate_markdown(rows)
    assert "agent" in md and "SGC" in md


def test_qualitative_picks_sorts_by_sgc_delta():
    agent = [_metrics_stub(f"s{i}", "agent", v) for i, v in enumerate([0.9, 0.5, 0.7])]
    base = [_metrics_stub(f"s{i}", "gt", v) for i, v in enumerate([0.1, 0.5, 0.2])]
    picks = qualitative_picks(agent, base, top_k=2)
    assert [p[0] for p in picks] == ["s0", "s2"]
    assert picks[0][1] == pytest.approx(0.8)
