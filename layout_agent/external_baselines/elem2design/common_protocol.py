"""Dependency-free protocol logic shared by the Elem2Design baseline scripts.

Everything here is pure Python so it can be unit-tested in the repo's normal
test environment without the heavyweight e2d inference environment.

Fairness contract (see layout_agent/experiment_plan.md):
* the baseline may see R3 bitmaps, text contents, canvas size, official
  predicted roles, and its own intermediate renders — nothing else;
* GT geometry, GT roles, A3 trees/candidates/renders, and P-Full native
  pixel dimensions must never enter the model input.
"""
from __future__ import annotations

import json
import random
import re
from typing import Any, Dict, List, Sequence, Tuple

PROTOCOL_VERSION = "a3.external-baseline-run.v1"
BASELINE_SEED = 42
N_LAYERS = 5
LAYER_NAMES = ["background", "underlay", "logo/image", "text", "embellishment"]

# Keys that must never appear as dict keys anywhere inside test.json
# (canvas_width/canvas_height live only in the human preamble text).
FORBIDDEN_KEYS = frozenset(
    {
        "left", "top", "x", "y", "bbox", "angle", "z_index",
        "native_width", "native_height", "font_size",
    }
)
# "width"/"height" are forbidden as element keys but legal as canvas keys.
FORBIDDEN_UNLESS_CANVAS = frozenset({"width", "height"})
CANVAS_KEYS = frozenset({"canvas_width", "canvas_height"})


def deterministic_layer_order(
    asset_ids: Sequence[str],
    roles_by_asset: Dict[str, int],
    sample_id: str,
    seed: int = BASELINE_SEED,
) -> List[str]:
    """Per-sample deterministic shuffle followed by a stable sort on layer.

    The official pipeline shuffles with one global RNG over the whole split
    and then role-sorts; with a different sample subset that exact stream is
    unreproducible, so we document a per-sample RNG keyed on (seed, sample_id)
    instead.  Stable sort preserves the shuffled order within each layer,
    matching the official shuffle→sort composition.
    """
    rng = random.Random(f"{seed}:{sample_id}")
    shuffled = list(asset_ids)
    rng.shuffle(shuffled)
    return sorted(shuffled, key=lambda asset_id: roles_by_asset[asset_id])


def build_index_map(ordered_asset_ids: Sequence[str]) -> Dict[str, Any]:
    """Model element index <-> asset id bookkeeping for one sample."""
    return {
        "index_to_asset": list(ordered_asset_ids),
        "asset_to_index": {a: i for i, a in enumerate(ordered_asset_ids)},
    }


def scan_forbidden_keys(node: Any, path: str = "$") -> List[str]:
    """Recursively find forbidden dict keys inside a JSON-like structure."""
    violations: List[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in FORBIDDEN_KEYS or (
                key in FORBIDDEN_UNLESS_CANVAS and key not in CANVAS_KEYS
            ):
                violations.append(f"{path}.{key}")
            violations.extend(scan_forbidden_keys(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            violations.extend(scan_forbidden_keys(item, f"{path}[{i}]"))
    return violations


def assert_placeholder_gpt_turns(conversations: Sequence[Dict[str, str]]) -> None:
    """Every gpt turn in test.json must be the '{}' placeholder, never GT."""
    gpt_turns = [c for c in conversations if c.get("from") == "gpt"]
    if len(gpt_turns) != N_LAYERS:
        raise ValueError(f"expected {N_LAYERS} gpt turns, got {len(gpt_turns)}")
    for turn in gpt_turns:
        if turn.get("value") != "{}":
            raise ValueError("gpt turn is not the '{}' placeholder — GT leakage risk")


_TURN_SPLIT = re.compile(r"\s*\$\$\$\$\$\s*")
_ELEMENT_RE = re.compile(r"\{.*?\}", re.DOTALL)


def split_prediction_turns(prediction: str) -> List[str]:
    """Split the official ' ##### <output> $$$$$ ' concatenation into turns."""
    turns = [t for t in _TURN_SPLIT.split(prediction) if t.strip()]
    return [t.split("#####", 1)[-1].strip() for t in turns]


def parse_elements(turn_text: str) -> List[Dict[str, Any]]:
    """Extract element dicts from one turn's raw model output.

    Non-JSON fragments are skipped (official parser behaviour); the empty
    layer marker '{}' yields no elements.
    """
    elements: List[Dict[str, Any]] = []
    for fragment in _ELEMENT_RE.findall(turn_text):
        if fragment == "{}":
            continue
        try:
            data = json.loads(fragment)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data:
            elements.append(data)
    return elements


def _finite_positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and value == value and abs(value) != float("inf") and value > 0


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and value == value and abs(value) != float("inf")


def convert_sample(
    turns: Sequence[Sequence[Dict[str, Any]]],
    index_to_asset: Sequence[str],
    texts_by_asset: Dict[str, str],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Convert per-layer parsed elements into A3 candidate elements.

    Returns (elements, errors).  Any error makes the sample a failure —
    callers must not silently drop elements. Geometry passes through with
    finite/positive checks and pixel rounding only; no clipping, no repair.
    """
    errors: List[str] = []
    seen: Dict[str, int] = {}
    elements: List[Dict[str, Any]] = []
    for layer_index, layer_elements in enumerate(turns):
        for order, element in enumerate(layer_elements):
            index = element.get("index")
            if not isinstance(index, int) or not (0 <= index < len(index_to_asset)):
                errors.append(f"layer{layer_index}: bad element index {index!r}")
                continue
            asset_id = index_to_asset[index]
            if asset_id in seen:
                errors.append(f"duplicate prediction for {asset_id}")
                continue
            seen[asset_id] = layer_index
            geometry = {}
            ok = True
            for key in ("left", "top", "width", "height"):
                value = element.get(key)
                valid = _finite_positive(value) if key in ("width", "height") else _finite(value)
                if not valid or not isinstance(value, (int, float)):
                    errors.append(f"{asset_id}: invalid {key}={value!r}")
                    ok = False
                else:
                    geometry[key] = int(round(float(value)))
            if not ok:
                continue
            if geometry["width"] <= 0 or geometry["height"] <= 0:
                errors.append(f"{asset_id}: non-positive size after rounding")
                continue
            angle = element.get("angle")
            converted: Dict[str, Any] = {
                "id": asset_id,
                **geometry,
                "angle": float(angle) if isinstance(angle, (int, float)) and _finite(angle) else 0.0,
                "z_index": layer_index * 1000 + order,
            }
            if texts_by_asset.get(asset_id):
                font_size = element.get("font_size")
                if isinstance(font_size, (int, float)) and _finite_positive(font_size):
                    converted["font_size"] = int(round(float(font_size)))
                if isinstance(element.get("font"), str) and element["font"]:
                    converted["font_family"] = element["font"]
                color = element.get("color")
                if (
                    isinstance(color, list) and len(color) == 3
                    and all(isinstance(c, (int, float)) and _finite(c) and 0 <= c <= 255 for c in color)
                ):
                    converted["color"] = "#{:02x}{:02x}{:02x}".format(
                        *(int(round(float(c))) for c in color)
                    )
                if isinstance(element.get("text_align"), str) and element["text_align"]:
                    converted["text_align"] = element["text_align"]
            elements.append(converted)

    missing = [a for a in index_to_asset if a not in seen]
    if missing:
        errors.append(f"missing predictions for: {','.join(sorted(missing))}")
    return elements, errors
