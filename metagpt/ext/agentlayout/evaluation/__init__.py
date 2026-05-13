"""Evaluation metrics for AgentLayout.

This module hosts quantitative metrics that compare generated layouts
against ground-truth dataset annotations (Crello in the MVP scope).

Currently provided:
  * iou: Element-level Intersection-over-Union with id-based matching.

Planned (future work):
  * read_order: Spearman correlation between predicted reading order and
    designer-annotated importance order.
  * fid: Frechet Inception Distance between rendered PNG and ground-truth
    preview image.
"""
from metagpt.ext.agentlayout.evaluation.baselines import (
    centered_stack,
    random_layout,
)
from metagpt.ext.agentlayout.evaluation.iou import (
    LayoutIoUResult,
    bbox_iou,
    layout_iou,
)

__all__ = [
    "bbox_iou",
    "layout_iou",
    "LayoutIoUResult",
    "random_layout",
    "centered_stack",
]
