"""Step 90 -- semantic-group metrics: SGC / TLC / PCA.

The paper's core claim is that the Layout Tree's semantic grouping is
reflected in the final canvas, yet neither the six SEGA-style geometry
metrics nor the COLE aesthetic scores measure that directly. This module
quantifies "do same-group elements actually sit together" with three
deterministic metrics (no LLM anywhere, per the project's "geometry belongs
to code" philosophy):

* SGC (Semantic Group Compactness)  = D_inter / (D_intra + D_inter + eps),
  range [0, 1), higher is better, 0.5 = grouping not expressed at all.
* TLC (Tree Layout Consistency)     = fraction of (i, j, l) triplets with
  i,j in the same group and l outside it where d(i,j) < d(i,l); ties score
  0.5. Random layouts expect 0.5.
* PCA (Parent-Child Adjacency)      = fraction of non-root tree edges (p, c)
  with d(p, c) <= median_{j != p} d(p, j).

Shared distance: L1 bounding-box *gap* (not center distance) on
[0,1]-normalized boxes -- overlapping or touching boxes have d = 0.

Fairness contract: for one sample, every method (agent, designer GT, SEGA
baselines) must be scored against the SAME tree. The tree source is a
pluggable input here (callers pass any LayoutTree); id mismatches are
handled by an explicit alignment layer (`align_by_type_order`) and samples
that cannot be aligned are skipped with a recorded reason -- never forced.

Fixed evaluation rules:
* boxes are normalized by canvas width/height before any distance,
* only foreground elements count -- an element participates iff its id is
  in the tree (backgrounds are excluded from trees by construction),
* the n x n distance matrix is computed once per sample and shared by all
  three metrics.
"""
from __future__ import annotations

from statistics import median, pstdev
from typing import Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from metagpt.ext.agentlayout.schema import LayoutTree, LayoutTreeNode

EPS: float = 1e-6

# A box is (x, y, w, h). Pixel boxes are floats in canvas units; normalized
# boxes are the same tuple divided by canvas width/height.
Box = Tuple[float, float, float, float]


# ============================================================
# Shared distance
# ============================================================


def l1_gap(a: Box, b: Box) -> float:
    """L1 gap distance between two boxes: max(0, gap_x) + max(0, gap_y).

    gap_x is the horizontal empty space between the two x-intervals
    (negative when they overlap), gap_y likewise. Overlapping or touching
    boxes therefore score exactly 0.
    """
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    gap_x = max(ax, bx) - min(ax + aw, bx + bw)
    gap_y = max(ay, by) - min(ay + ah, by + bh)
    return max(0.0, gap_x) + max(0.0, gap_y)


# ============================================================
# Tree -> groups / edges
# ============================================================


def tree_groups(tree: LayoutTree) -> List[List[str]]:
    """Each direct child of root, together with its whole subtree, is one group.

    A single-element child of root forms a singleton group.
    """
    return [child.iter_ids() for child in tree.root.children]


def tree_parent_child_edges(tree: LayoutTree) -> List[Tuple[str, str]]:
    """All parent-child edges of the tree, EXCLUDING edges from the root."""
    edges: List[Tuple[str, str]] = []

    def _walk(node: LayoutTreeNode) -> None:
        for child in node.children:
            edges.append((node.id, child.id))
            _walk(child)

    for top in tree.root.children:
        _walk(top)
    return edges


# ============================================================
# Per-sample result
# ============================================================


class SampleMetrics(BaseModel):
    """One (sample, method) row of the per-sample JSON output."""

    sample_id: str
    method: str
    sgc: Optional[float] = None
    tlc: Optional[float] = None
    pca: Optional[float] = None
    n_elements: int = 0
    n_groups: int = 0
    n_triplets: int = 0
    skip_reasons: List[str] = Field(default_factory=list)


def evaluate_sample(
    *,
    tree: LayoutTree,
    pixel_boxes: Dict[str, Box],
    canvas_width: float,
    canvas_height: float,
    sample_id: str,
    method: str,
) -> SampleMetrics:
    """Score one final layout against one semantic tree.

    ``pixel_boxes`` maps element id -> (left, top, width, height) in canvas
    pixels. Ids present in the layout but absent from the tree (e.g. the
    background) are ignored; tree ids missing from the layout invalidate the
    whole sample (recorded, not guessed).
    """
    groups = tree_groups(tree)
    tree_ids = [i for g in groups for i in g]
    result = SampleMetrics(
        sample_id=sample_id, method=method, n_groups=len(groups)
    )

    missing = [i for i in tree_ids if i not in pixel_boxes]
    if missing:
        result.skip_reasons.append(f"missing_elements:{','.join(sorted(missing))}")
        return result

    boxes: Dict[str, Box] = {
        i: (
            pixel_boxes[i][0] / canvas_width,
            pixel_boxes[i][1] / canvas_height,
            pixel_boxes[i][2] / canvas_width,
            pixel_boxes[i][3] / canvas_height,
        )
        for i in tree_ids
    }
    result.n_elements = len(tree_ids)

    # n <= ~15, so the O(n^2) matrix + O(n^3) triplets are trivially cheap.
    dist: Dict[str, Dict[str, float]] = {i: {} for i in tree_ids}
    for idx, i in enumerate(tree_ids):
        for j in tree_ids[idx + 1 :]:
            d = l1_gap(boxes[i], boxes[j])
            dist[i][j] = d
            dist[j][i] = d

    group_of: Dict[str, int] = {}
    for k, g in enumerate(groups):
        for i in g:
            group_of[i] = k

    result.sgc = _sgc(groups, dist, result.skip_reasons)
    result.tlc, result.n_triplets = _tlc(tree_ids, group_of, dist, result.skip_reasons)
    result.pca = _pca(tree, tree_ids, dist, result.skip_reasons)
    return result


def _sgc(
    groups: List[List[str]],
    dist: Dict[str, Dict[str, float]],
    skip_reasons: List[str],
) -> Optional[float]:
    """SGC = D_inter / (D_intra + D_inter + eps).

    D_intra: group-level mean (mean over groups of the group's mean pairwise
    distance) so large groups do not dominate. D_inter: pair-level mean over
    all cross-group pairs.
    """
    intra_means: List[float] = []
    for g in groups:
        if len(g) < 2:
            continue
        pair_ds = [dist[a][b] for x, a in enumerate(g) for b in g[x + 1 :]]
        intra_means.append(sum(pair_ds) / len(pair_ds))
    if not intra_means:
        skip_reasons.append("sgc:all_groups_singleton")
        return None

    inter_ds: List[float] = []
    for gi in range(len(groups)):
        for gj in range(gi + 1, len(groups)):
            inter_ds.extend(dist[a][b] for a in groups[gi] for b in groups[gj])
    if not inter_ds:
        skip_reasons.append("sgc:single_group")
        return None

    d_intra = sum(intra_means) / len(intra_means)
    d_inter = sum(inter_ds) / len(inter_ds)
    return d_inter / (d_intra + d_inter + EPS)


def _tlc(
    ids: List[str],
    group_of: Dict[str, int],
    dist: Dict[str, Dict[str, float]],
    skip_reasons: List[str],
) -> Tuple[Optional[float], int]:
    """TLC over triplets (i, j, l): i,j same group, l outside i's group.

    d(i,j) < d(i,l) scores 1, a tie scores 0.5 (frequent when both are 0),
    otherwise 0.
    """
    score = 0.0
    n_triplets = 0
    for i in ids:
        gi = group_of[i]
        same = [j for j in ids if j != i and group_of[j] == gi]
        other = [k for k in ids if group_of[k] != gi]
        for j in same:
            d_ij = dist[i][j]
            for k in other:
                d_ik = dist[i][k]
                n_triplets += 1
                if d_ij < d_ik:
                    score += 1.0
                elif d_ij == d_ik:
                    score += 0.5
    if n_triplets == 0:
        skip_reasons.append("tlc:no_triplets")
        return None, 0
    return score / n_triplets, n_triplets


def _pca(
    tree: LayoutTree,
    ids: List[str],
    dist: Dict[str, Dict[str, float]],
    skip_reasons: List[str],
) -> Optional[float]:
    """Fraction of non-root edges (p, c) with d(p,c) <= median_{j!=p} d(p,j)."""
    edges = tree_parent_child_edges(tree)
    if not edges:
        skip_reasons.append("pca:no_edges")
        return None
    hits = 0
    for p, c in edges:
        others = [dist[p][j] for j in ids if j != p]
        if dist[p][c] <= median(others):
            hits += 1
    return hits / len(edges)


# ============================================================
# Id alignment layer (for baselines whose ids differ from the tree's)
# ============================================================


def align_by_type_order(
    source: Sequence[Tuple[str, str]],
    target: Sequence[Tuple[str, str]],
) -> Optional[Dict[str, str]]:
    """Match target element ids onto source (tree-side) ids.

    Both sides are ordered ``(id, category)`` sequences; within each
    category, the k-th target element maps to the k-th source element.
    Returns ``{target_id: source_id}`` or None when any category's counts
    differ -- callers must then record the sample as skipped, not force a
    partial match.
    """
    by_cat_source: Dict[str, List[str]] = {}
    for el_id, cat in source:
        by_cat_source.setdefault(cat, []).append(el_id)
    by_cat_target: Dict[str, List[str]] = {}
    for el_id, cat in target:
        by_cat_target.setdefault(cat, []).append(el_id)

    if {c: len(v) for c, v in by_cat_source.items()} != {
        c: len(v) for c, v in by_cat_target.items()
    }:
        return None

    mapping: Dict[str, str] = {}
    for cat, target_ids in by_cat_target.items():
        for src_id, tgt_id in zip(by_cat_source[cat], target_ids):
            mapping[tgt_id] = src_id
    return mapping


# ============================================================
# Aggregation + report
# ============================================================


class AggregateRow(BaseModel):
    """One method's row in the aggregate markdown report."""

    method: str
    n_samples: int
    sgc_mean: Optional[float] = None
    sgc_std: Optional[float] = None
    sgc_n: int = 0
    sgc_skipped: int = 0
    tlc_mean: Optional[float] = None
    tlc_std: Optional[float] = None
    tlc_n: int = 0
    tlc_skipped: int = 0
    pca_mean: Optional[float] = None
    pca_std: Optional[float] = None
    pca_n: int = 0
    pca_skipped: int = 0


def _mean_std(values: List[float]) -> Tuple[Optional[float], Optional[float]]:
    if not values:
        return None, None
    if len(values) == 1:
        return values[0], 0.0
    return sum(values) / len(values), pstdev(values)


def aggregate(samples: Sequence[SampleMetrics]) -> List[AggregateRow]:
    """Collapse per-sample rows into one AggregateRow per method."""
    methods: Dict[str, List[SampleMetrics]] = {}
    for s in samples:
        methods.setdefault(s.method, []).append(s)

    rows: List[AggregateRow] = []
    for method, group in methods.items():
        row = AggregateRow(method=method, n_samples=len(group))
        for metric in ("sgc", "tlc", "pca"):
            values = [getattr(s, metric) for s in group if getattr(s, metric) is not None]
            mean, std = _mean_std(values)
            setattr(row, f"{metric}_mean", mean)
            setattr(row, f"{metric}_std", std)
            setattr(row, f"{metric}_n", len(values))
            setattr(row, f"{metric}_skipped", len(group) - len(values))
        rows.append(row)
    return rows


def _cell(mean: Optional[float], std: Optional[float], n: int, skipped: int) -> str:
    if mean is None:
        return f"— (n=0, skip={skipped})"
    return f"{mean:.3f} ± {std:.3f} (n={n}, skip={skipped})"


def aggregate_markdown(rows: Sequence[AggregateRow]) -> str:
    """Render the aggregate table in the result.md house style."""
    lines = [
        "| method | 樣本數 | SGC ↑ | TLC ↑ | PCA ↑ |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r.method} | {r.n_samples} "
            f"| {_cell(r.sgc_mean, r.sgc_std, r.sgc_n, r.sgc_skipped)} "
            f"| {_cell(r.tlc_mean, r.tlc_std, r.tlc_n, r.tlc_skipped)} "
            f"| {_cell(r.pca_mean, r.pca_std, r.pca_n, r.pca_skipped)} |"
        )
    return "\n".join(lines)


def qualitative_picks(
    agent_samples: Sequence[SampleMetrics],
    baseline_samples: Sequence[SampleMetrics],
    top_k: int = 10,
) -> List[Tuple[str, float]]:
    """Top-k sample ids by (agent_sgc - baseline_sgc), for qualitative figures.

    Only samples where BOTH sides have a defined SGC participate.
    """
    base_by_id = {s.sample_id: s for s in baseline_samples}
    deltas: List[Tuple[str, float]] = []
    for a in agent_samples:
        b = base_by_id.get(a.sample_id)
        if b is None or a.sgc is None or b.sgc is None:
            continue
        deltas.append((a.sample_id, a.sgc - b.sgc))
    deltas.sort(key=lambda t: t[1], reverse=True)
    return deltas[:top_k]
