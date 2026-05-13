"""Element-level Intersection-over-Union (IoU) metric for AgentLayout.

The metric is designed for evaluating generated layouts against an
existing ground-truth annotation (e.g. Crello dataset). Both sides expose
elements as (left, top, width, height) bounding boxes plus a string id.

Matching strategy (MVP): the caller supplies an ``id_map`` dict that maps
``generated_id -> ground_truth_id``. We do not auto-match because the
generated ids come from the LLM (e.g. ``title_1``) while Crello ids are
positional indices (``0``, ``1``, ...) -- only the caller knows the
asset-feeding order, so the caller owns the mapping.

Output is a typed Pydantic ``LayoutIoUResult`` so it survives JSON
round-tripping for downstream aggregation / table emission.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

from pydantic import BaseModel, Field


BBox = Tuple[float, float, float, float]  # (left, top, width, height)


@dataclass(frozen=True)
class BBoxItem:
    """A single (id, bbox) entry, used as the input element for layout_iou."""

    id: str
    bbox: BBox  # (left, top, width, height) in pixels


def bbox_iou(a: BBox, b: BBox) -> float:
    """Standard IoU on two (left, top, width, height) bounding boxes.

    Returns 0.0 if either box has zero area or if they do not overlap.
    Returns 1.0 only when the two boxes are identical (within float epsilon).
    Always in the closed range [0.0, 1.0].
    """
    ax0, ay0, aw, ah = a
    bx0, by0, bw, bh = b
    if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
        return 0.0

    ax1 = ax0 + aw
    ay1 = ay0 + ah
    bx1 = bx0 + bw
    by1 = by0 + bh

    ix0 = max(ax0, bx0)
    iy0 = max(ay0, by0)
    ix1 = min(ax1, bx1)
    iy1 = min(ay1, by1)
    iw = max(0.0, ix1 - ix0)
    ih = max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0.0:
        return 0.0

    union = aw * ah + bw * bh - inter
    if union <= 0.0:
        return 0.0
    return float(inter / union)


class LayoutIoUResult(BaseModel):
    """Structured output of ``layout_iou`` for one generated-vs-GT layout pair.

    ``per_element`` is keyed by the **generated** id (the side that the
    AgentLayout pipeline owns), so downstream summaries can group by
    semantic_type (which is a property of the generated DesignSpec, not the
    Crello idx).
    """

    per_element: Dict[str, float] = Field(
        default_factory=dict,
        description="generated_id -> IoU value in [0, 1] for matched pairs only.",
    )
    mean: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Mean IoU across matched pairs. 0.0 if no pair matched.",
    )
    matched: int = Field(
        default=0,
        ge=0,
        description="Number of (generated, gt) pairs successfully matched.",
    )
    unmatched_generated: List[str] = Field(
        default_factory=list,
        description=(
            "Generated ids that have no entry in id_map or whose mapped GT "
            "id was missing."
        ),
    )
    unmatched_gt: List[str] = Field(
        default_factory=list,
        description="Ground-truth ids that no generated id mapped to.",
    )


def layout_iou(
    generated: Iterable[BBoxItem],
    ground_truth: Iterable[BBoxItem],
    id_map: Dict[str, str],
) -> LayoutIoUResult:
    """Compute per-element IoU between generated and ground-truth layouts.

    Args:
        generated:    iterable of BBoxItem produced by the AgentLayout pipeline.
        ground_truth: iterable of BBoxItem from the dataset annotation.
        id_map:       maps each generated_id -> ground_truth_id. Generated
                      ids absent from the map are reported as
                      ``unmatched_generated``. GT ids that no generated id
                      points to are reported as ``unmatched_gt``.

    Returns:
        LayoutIoUResult. ``mean`` is computed only over matched pairs;
        unmatched elements do NOT contribute a 0 score (callers can post-
        process if they want a "missing element penalty" variant).
    """
    gen_by_id = {item.id: item for item in generated}
    gt_by_id = {item.id: item for item in ground_truth}

    per_element: Dict[str, float] = {}
    unmatched_generated: List[str] = []
    matched_gt_ids = set()

    for gen_id, gen_item in gen_by_id.items():
        gt_id = id_map.get(gen_id)
        if gt_id is None or gt_id not in gt_by_id:
            unmatched_generated.append(gen_id)
            continue
        per_element[gen_id] = bbox_iou(gen_item.bbox, gt_by_id[gt_id].bbox)
        matched_gt_ids.add(gt_id)

    unmatched_gt = [
        gt_id for gt_id in gt_by_id.keys() if gt_id not in matched_gt_ids
    ]

    mean = (
        sum(per_element.values()) / len(per_element)
        if per_element
        else 0.0
    )
    return LayoutIoUResult(
        per_element=per_element,
        mean=mean,
        matched=len(per_element),
        unmatched_generated=unmatched_generated,
        unmatched_gt=unmatched_gt,
    )
