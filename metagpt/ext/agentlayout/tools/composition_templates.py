"""GT-calibrated coarse-composition template library (Step 62, 2026-06-12).

Source: Step 61 calibration (layout_agent/output/step61_composition_calibration.py)
over N=1,902 Crello designer layouts -- 1,168 photo+text, 734 text-only.
Sketch vocabulary: photo = largest non-background image -> 3x3 grid cell of
its center + area-ratio size bucket; text = area-weighted center of all text
elements -> 3x3 cell; relation = photo-vs-text-mass arrangement.

The Composition Director (ComposeSketch action) picks one template from
`template_menu()`; the Generator receives it as `spec.composition` plus an
end-of-prompt ATTENTION block with per-canvas pixel math; the Quality
Checker verifies compliance numerically via `cell_bounds` /
`SIZE_BUCKET_RANGES`.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# 3x3 grid cells: rows T/M/B x columns L/C/R.
CELLS = [v + h for v in ("T", "M", "B") for h in ("L", "C", "R")]

RELATIONS = ("text-on-photo", "stacked", "side-by-side", "centered-mix")

# Photo area-ratio buckets (area / canvas area). Step 61 GT marginal:
# small 20.2% / medium 34.7% / large 39.1% / bleed 6.0% -- stable across
# aspect buckets (large 36-44%).
SIZE_BUCKET_RANGES: Dict[str, Tuple[float, float]] = {
    "small": (0.05, 0.20),
    "medium": (0.20, 0.45),
    "large": (0.45, 0.80),
    "bleed": (0.80, 0.95),
}

# GT relation priors conditioned on canvas aspect (Step 61 follow-up run,
# same filters; landscape n=518 / portrait n=374 / square n=276).
RELATION_PRIOR_BY_ASPECT: Dict[str, Dict[str, float]] = {
    "landscape": {"text-on-photo": 0.45, "side-by-side": 0.42, "stacked": 0.08, "centered-mix": 0.05},
    "portrait": {"stacked": 0.44, "text-on-photo": 0.38, "side-by-side": 0.10, "centered-mix": 0.08},
    "square": {"text-on-photo": 0.47, "stacked": 0.25, "side-by-side": 0.24, "centered-mix": 0.03},
}

# Text-mass cell prior for layouts WITHOUT a photo (GT n=734): designers
# center the text mass; candidates historically push it to the T/B edges.
TEXT_ONLY_CELL_PRIOR: Dict[str, float] = {
    "MC": 0.625, "MR": 0.125, "ML": 0.106, "BC": 0.059, "TC": 0.052,
    "BL": 0.011, "TR": 0.010, "TL": 0.008, "BR": 0.004,
}

# Curated photo+text templates from the GT top-20 patterns (53% coverage).
# gt_share = summed share of the merged source patterns (N=1,168).
PHOTO_TEMPLATES: List[Dict] = [
    {
        "id": "hero-center-overlay",
        "relation": "text-on-photo",
        "photo_cell": "MC", "photo_size": "large", "text_cell": "MC",
        "gt_share": 0.199,
        "note": "Dominant designer move: large/bleed photo fills the center, "
                "all text sits ON the photo (underlay or contrast protects it).",
    },
    {
        "id": "hero-overlay-left-text",
        "relation": "text-on-photo",
        "photo_cell": "MC", "photo_size": "large", "text_cell": "ML",
        "gt_share": 0.024,
        "note": "Large centered photo; the text column rides its left third.",
    },
    {
        "id": "split-photo-right",
        "relation": "side-by-side",
        "photo_cell": "MR", "photo_size": "medium", "text_cell": "ML",
        "gt_share": 0.052,
        "note": "Photo owns the right half, text column the left half. "
                "Strongest on landscape canvases.",
    },
    {
        "id": "split-photo-left",
        "relation": "side-by-side",
        "photo_cell": "ML", "photo_size": "medium", "text_cell": "MR",
        "gt_share": 0.054,
        "note": "Mirror of split-photo-right.",
    },
    {
        "id": "centered-mix",
        "relation": "centered-mix",
        "photo_cell": "MC", "photo_size": "medium", "text_cell": "MC",
        "gt_share": 0.023,
        "note": "Photo and text interleaved near the center, no dominant axis.",
    },
    {
        "id": "stacked-text-top",
        "relation": "stacked",
        "photo_cell": "MC", "photo_size": "large", "text_cell": "TC",
        "gt_share": 0.032,
        "note": "Text band on top, photo below it. Strongest on portrait.",
    },
    {
        "id": "stacked-text-bottom",
        "relation": "stacked",
        "photo_cell": "MC", "photo_size": "large", "text_cell": "BC",
        "gt_share": 0.015,
        "note": "Photo first, text band underneath.",
    },
    {
        "id": "photo-bottom-anchor",
        "relation": "stacked",
        "photo_cell": "BC", "photo_size": "small", "text_cell": "MC",
        "gt_share": 0.016,
        "note": "Small photo anchors the bottom, centered text mass above. "
                "The only GT-sanctioned small-photo template.",
    },
]

TEXT_ONLY_TEMPLATES: List[Dict] = [
    {
        "id": "text-centered",
        "relation": None, "photo_cell": None, "photo_size": None,
        "text_cell": "MC", "gt_share": 0.625,
        "note": "Designer default for photo-less posters: text mass centered "
                "(both axes), background carries the visual interest.",
    },
    {
        "id": "text-column-right",
        "relation": None, "photo_cell": None, "photo_size": None,
        "text_cell": "MR", "gt_share": 0.125,
        "note": "Text column in the right third, e.g. beside a background "
                "focal subject on the left.",
    },
    {
        "id": "text-column-left",
        "relation": None, "photo_cell": None, "photo_size": None,
        "text_cell": "ML", "gt_share": 0.106,
        "note": "Mirror of text-column-right.",
    },
]


def aspect_bucket(canvas_width: float, canvas_height: float) -> str:
    ratio = canvas_width / canvas_height
    if ratio < 0.8:
        return "portrait"
    if ratio > 1.25:
        return "landscape"
    return "square"


def cell_bounds(cell: str, canvas_width: float, canvas_height: float) -> Tuple[float, float, float, float]:
    """Pixel rect (xl, yt, xr, yb) the element CENTER must fall inside."""
    if cell not in CELLS:
        raise ValueError(f"unknown grid cell {cell!r}; expected one of {CELLS}")
    row, col = "TMB".index(cell[0]), "LCR".index(cell[1])
    return (
        col * canvas_width / 3,
        row * canvas_height / 3,
        (col + 1) * canvas_width / 3,
        (row + 1) * canvas_height / 3,
    )


def template_by_id(template_id: str) -> Optional[Dict]:
    for tpl in PHOTO_TEMPLATES + TEXT_ONLY_TEMPLATES:
        if tpl["id"] == template_id:
            return tpl
    return None


def template_menu(has_photo: bool, aspect: str) -> List[Dict]:
    """Templates the Composition Director may choose from, with the
    aspect-conditioned GT relation prior attached for ranking context."""
    if not has_photo:
        return list(TEXT_ONLY_TEMPLATES)
    prior = RELATION_PRIOR_BY_ASPECT.get(aspect, RELATION_PRIOR_BY_ASPECT["square"])
    menu = [dict(tpl, relation_prior=prior[tpl["relation"]]) for tpl in PHOTO_TEMPLATES]
    menu.sort(key=lambda t: (t["relation_prior"], t["gt_share"]), reverse=True)
    return menu
