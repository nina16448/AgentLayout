"""Deterministic B0/B1 issue verifier for the A3 L1 guard.

Maps every closed Judge-Critic issue type to one deterministic predicate
over the two candidates' geometry (policy ``a3.issue-verifier.v1``):

- STRICT types (overlap, clipping, out_of_bounds, text_too_small,
  illegible_text, misalignment): a measurable geometric quantity must
  strictly improve from B0 to B1.
- PROXY types (spacing, lockup, poor_contrast, text_on_busy_region,
  hierarchy_error, tree_inconsistency): true improvement needs pixels or
  semantics this verifier does not see, so the check is an honest
  acted-upon proxy — every target element must actually have moved or been
  resized. The proxy nature is versioned here and recorded in the evidence
  string; Gate C, not this verifier, judges perceptual quality.

Missing targets or malformed candidates never pass: improved=False with the
failure spelled out, so the B0/B1 guard falls back to B0.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from metagpt.ext.agentlayout.schema import Candidate, LayoutElement
from metagpt.ext.agentlayout.tools.judge_critic import CriticIssueType
from metagpt.ext.agentlayout.tools.repair_gate import IssueVerification, RepairDecision


A3_ISSUE_VERIFIER_VERSION = "a3.issue-verifier.v1"

STRICT_TYPES = {
    CriticIssueType.OVERLAP,
    CriticIssueType.CLIPPING,
    CriticIssueType.OUT_OF_BOUNDS,
    CriticIssueType.TEXT_TOO_SMALL,
    CriticIssueType.ILLEGIBLE_TEXT,
    CriticIssueType.MISALIGNMENT,
}
PROXY_TYPES = set(CriticIssueType) - STRICT_TYPES


def _by_id(candidate: Candidate) -> Dict[str, LayoutElement]:
    return {element.id: element for element in candidate.elements}


def _bbox(element: LayoutElement) -> Tuple[int, int, int, int]:
    return (
        element.left,
        element.top,
        element.left + element.width,
        element.top + element.height,
    )


def _intersection_area(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> int:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return ix * iy


def _target_overlap_area(candidate: Candidate, targets: List[str]) -> int:
    elements = _by_id(candidate)
    total = 0
    for target in targets:
        target_bbox = _bbox(elements[target])
        for other_id, other in elements.items():
            if other_id == target or (other_id in targets and other_id < target):
                continue
            total += _intersection_area(target_bbox, _bbox(other))
    return total


def _out_of_canvas_area(
    candidate: Candidate, targets: List[str], canvas_width: int, canvas_height: int
) -> int:
    elements = _by_id(candidate)
    total = 0
    for target in targets:
        left, top, right, bottom = _bbox(elements[target])
        full = (right - left) * (bottom - top)
        inside = _intersection_area(
            (left, top, right, bottom), (0, 0, canvas_width, canvas_height)
        )
        total += full - inside
    return total


def _min_target_area(candidate: Candidate, targets: List[str]) -> int:
    elements = _by_id(candidate)
    return min(elements[t].width * elements[t].height for t in targets)


def _alignment_error(candidate: Candidate, targets: List[str]) -> int:
    """Distance of each target to its nearest axis guide from other elements."""
    elements = _by_id(candidate)
    total = 0
    for target in targets:
        element = elements[target]
        guides_x: List[int] = []
        guides_y: List[int] = []
        for other_id, other in elements.items():
            if other_id == target:
                continue
            guides_x += [other.left, other.left + other.width // 2, other.left + other.width]
            guides_y += [other.top, other.top + other.height // 2, other.top + other.height]
        if not guides_x:
            continue
        candidates_x = [element.left, element.left + element.width // 2, element.left + element.width]
        candidates_y = [element.top, element.top + element.height // 2, element.top + element.height]
        total += min(abs(cx - gx) for cx in candidates_x for gx in guides_x)
        total += min(abs(cy - gy) for cy in candidates_y for gy in guides_y)
    return total


def _targets_moved(b0: Candidate, b1: Candidate, targets: List[str]) -> bool:
    before, after = _by_id(b0), _by_id(b1)
    return all(_bbox(before[t]) != _bbox(after[t]) for t in targets)


def verify_issues(
    decision: RepairDecision,
    b0: Candidate,
    b1: Candidate,
    *,
    canvas_width: int,
    canvas_height: int,
) -> List[IssueVerification]:
    """One deterministic verdict per gated issue, in issue order."""
    b0_ids, b1_ids = set(_by_id(b0)), set(_by_id(b1))
    results: List[IssueVerification] = []
    for index, issue in enumerate(decision.issues):
        targets = list(issue.target_asset_ids)
        missing = sorted(set(targets) - (b0_ids & b1_ids))
        if missing:
            results.append(
                IssueVerification(
                    issue_index=index,
                    improved=False,
                    evidence=f"[{A3_ISSUE_VERIFIER_VERSION}] targets missing "
                    f"from candidates: {missing}",
                )
            )
            continue

        kind = issue.issue_type
        if kind is CriticIssueType.OVERLAP:
            before = _target_overlap_area(b0, targets)
            after = _target_overlap_area(b1, targets)
            improved, metric = after < before, f"target overlap area {before} -> {after}"
        elif kind in (CriticIssueType.CLIPPING, CriticIssueType.OUT_OF_BOUNDS):
            before = _out_of_canvas_area(b0, targets, canvas_width, canvas_height)
            after = _out_of_canvas_area(b1, targets, canvas_width, canvas_height)
            improved, metric = after < before, f"out-of-canvas area {before} -> {after}"
        elif kind in (CriticIssueType.TEXT_TOO_SMALL, CriticIssueType.ILLEGIBLE_TEXT):
            before = _min_target_area(b0, targets)
            after = _min_target_area(b1, targets)
            improved, metric = after > before, f"min target area {before} -> {after}"
        elif kind is CriticIssueType.MISALIGNMENT:
            before = _alignment_error(b0, targets)
            after = _alignment_error(b1, targets)
            improved, metric = after < before, f"alignment error {before} -> {after}"
        else:
            moved = _targets_moved(b0, b1, targets)
            improved = moved
            metric = (
                f"acted-upon proxy for {kind.value}: all targets "
                f"{'moved/resized' if moved else 'left untouched'}"
            )
        results.append(
            IssueVerification(
                issue_index=index,
                improved=improved,
                evidence=f"[{A3_ISSUE_VERIFIER_VERSION}] {metric}",
            )
        )
    return results
