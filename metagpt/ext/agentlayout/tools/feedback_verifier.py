"""Step 77 -- machine verification of Judge visual observations.

Each :class:`VisualObservation` kind maps to a geometric predicate over the
retried candidate. Computing "was this observation acted on?" per retry gives
a COMPLIANCE RATE -- the diagnostic Step 59 lacked. It localises a feedback-
loop failure:

    compliance high + blind unchanged  -> the Judge's advice has no value
                                          (perception/articulation problem)
    compliance low                     -> the generator still ignores
                                          instructions (execution problem)
    compliance high + blind improves   -> the loop works

Observations that cannot be checked (missing element, missing target field)
are counted as UNVERIFIABLE and excluded from the rate, but reported so a
sloppy judge (emitting unverifiable items) is visible too.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from metagpt.ext.agentlayout.schema import (
    Candidate,
    LayoutElement,
    VisualObservation,
    VisualObservationKind,
)

# An element counts as "inside" a target bbox when at least this fraction of
# its own area overlaps it (same convention as the Step 76 preprocessor's
# GT-text-on-underlay test).
INSIDE_MIN_OVERLAP: float = 0.5

# Two elements count as overlapping when their intersection exceeds this
# fraction of the SMALLER element's area (tiny corner touches are fine).
OVERLAP_MAX_FRACTION: float = 0.05

# |angle| at or below this many degrees counts as upright.
TILT_TOLERANCE_DEG: float = 2.0

# Colour compliance is "close enough", not byte-equal: an inspector target of
# #F4F4F4 answered with #FFFFFF is compliance in spirit (Euclidean RGB
# distance 19), while keeping #111111 (distance ~394) is not. 60 admits
# same-tone substitutions and rejects tone flips.
COLOR_TOLERANCE: float = 60.0


class ObservationCheck(BaseModel):
    """Verdict for one observation against one candidate."""

    kind: str
    target_id: str
    verifiable: bool
    satisfied: Optional[bool] = None  # None when unverifiable
    detail: str = ""


class ComplianceReport(BaseModel):
    """Aggregate compliance of a candidate against a set of observations."""

    n_total: int
    n_verifiable: int
    n_satisfied: int
    rate: Optional[float] = Field(
        default=None, description="n_satisfied / n_verifiable; None when nothing verifiable."
    )
    checks: List[ObservationCheck] = Field(default_factory=list)


def _bbox(el: LayoutElement) -> tuple:
    return (el.left, el.top, el.left + el.width, el.top + el.height)


def _intersection_area(a: tuple, b: tuple) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return ix * iy


def _inside_fraction(el: LayoutElement, target_bbox: List[int]) -> float:
    area = max(1, el.width * el.height)
    return _intersection_area(_bbox(el), tuple(target_bbox)) / area


def _color_distance(hex_a: str, hex_b: str) -> Optional[float]:
    """Euclidean RGB distance; None when either hex fails to parse."""
    try:
        a = hex_a.lstrip("#")
        b = hex_b.lstrip("#")
        ra, ga, ba = int(a[0:2], 16), int(a[2:4], 16), int(a[4:6], 16)
        rb, gb, bb = int(b[0:2], 16), int(b[2:4], 16), int(b[4:6], 16)
    except (ValueError, IndexError):
        return None
    return ((ra - rb) ** 2 + (ga - gb) ** 2 + (ba - bb) ** 2) ** 0.5


def check_observation(obs: VisualObservation, candidate: Candidate) -> ObservationCheck:
    """Evaluate one observation's predicate against the candidate."""
    elements: Dict[str, LayoutElement] = {el.id: el for el in candidate.elements}
    base = dict(kind=obs.kind.value, target_id=obs.target_id)
    el = elements.get(obs.target_id)
    if el is None:
        return ObservationCheck(
            **base, verifiable=False, detail=f"element '{obs.target_id}' not in candidate"
        )

    if obs.kind in (
        VisualObservationKind.TEXT_OFF_PANEL,
        VisualObservationKind.TEXT_ILLEGIBLE,
        VisualObservationKind.TITLE_MISPLACED,   # Step 85: move into target_bbox
        VisualObservationKind.LOCKUP_BROKEN,     # Step 85: move into target_bbox
    ):
        # text_illegible may be fixed EITHER by recolouring to the concrete hex
        # OR by moving into the suggested calm region; the bbox-target kinds only
        # by moving into the given range.
        checks = []
        if obs.target_bbox is not None:
            frac = _inside_fraction(el, obs.target_bbox)
            checks.append((frac >= INSIDE_MIN_OVERLAP, f"inside_fraction={frac:.2f}"))
        if obs.kind == VisualObservationKind.TEXT_ILLEGIBLE and obs.target_color:
            el_color = (el.color or "").upper()
            dist = _color_distance(el_color, obs.target_color) if el_color else None
            checks.append(
                (
                    dist is not None and dist <= COLOR_TOLERANCE,
                    f"color={el_color or '<none>'} (dist={dist if dist is None else round(dist, 1)})",
                )
            )
        if not checks:
            return ObservationCheck(
                **base, verifiable=False, detail="no target_bbox/target_color to verify against"
            )
        satisfied = any(ok for ok, _ in checks)
        return ObservationCheck(
            **base, verifiable=True, satisfied=satisfied,
            detail="; ".join(d for _, d in checks),
        )

    if obs.kind in (VisualObservationKind.TEXT_TOO_SMALL, VisualObservationKind.TEXT_TOO_LARGE):
        if obs.target_area_px is None:
            return ObservationCheck(**base, verifiable=False, detail="no target_area_px")
        area = el.width * el.height
        if obs.kind == VisualObservationKind.TEXT_TOO_SMALL:
            satisfied = area >= obs.target_area_px
        else:
            satisfied = area <= obs.target_area_px
        return ObservationCheck(
            **base, verifiable=True, satisfied=satisfied,
            detail=f"area={area} vs target={obs.target_area_px}",
        )

    if obs.kind == VisualObservationKind.TEXT_OVERLAP:
        other = elements.get(obs.second_id or "")
        if other is None:
            return ObservationCheck(
                **base, verifiable=False, detail=f"second element '{obs.second_id}' not in candidate"
            )
        inter = _intersection_area(_bbox(el), _bbox(other))
        smaller = max(1, min(el.width * el.height, other.width * other.height))
        frac = inter / smaller
        return ObservationCheck(
            **base, verifiable=True, satisfied=frac <= OVERLAP_MAX_FRACTION,
            detail=f"overlap_fraction={frac:.2f}",
        )

    if obs.kind == VisualObservationKind.TEXT_TILTED:
        satisfied = abs(el.angle) <= TILT_TOLERANCE_DEG
        return ObservationCheck(
            **base, verifiable=True, satisfied=satisfied, detail=f"angle={el.angle}"
        )

    return ObservationCheck(**base, verifiable=False, detail=f"unknown kind {obs.kind}")


def compliance_report(
    observations: List[VisualObservation], candidate: Candidate
) -> ComplianceReport:
    """Aggregate compliance of ``candidate`` against ``observations``."""
    checks = [check_observation(obs, candidate) for obs in observations]
    verifiable = [c for c in checks if c.verifiable]
    satisfied = [c for c in verifiable if c.satisfied]
    return ComplianceReport(
        n_total=len(checks),
        n_verifiable=len(verifiable),
        n_satisfied=len(satisfied),
        rate=(len(satisfied) / len(verifiable)) if verifiable else None,
        checks=checks,
    )
