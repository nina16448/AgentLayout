"""Step 54 / Experiment C: render-parity decomposition (2026-06-11). Offline.

Question: how much of the blind design_layout/typography gap is the RENDER
CHANNEL (font resolution, raster compositing, style defaults) rather than
the Generator's layout decisions? Steps 51/53 showed the gap survives blind
judging and gate removal; the remaining systemic suspect is render parity.

Method: rebuild each designer GROUND-TRUTH layout from crello meta.json
geometry and render it through OUR renderer, then blind-judge
our-render(GT layout) vs the GT preview image (step51 protocol). The
geometry is identical by construction, so any consistent GT preference
measures the render channel alone.

Style metadata the GT lacks (font_size/color) is derived ONLY from GT
geometry + our own deterministic tools (no LLM, no hand tuning):
  font_size = bbox_height / n_lines * 0.75   (Generator always emits
              font_size in the real pipeline, so leaving the 32px default
              would be a strawman)
  color     = BackgroundAnalyzer recommended_text_color
Elements: kind=text -> TEXT; kind=image not promoted to background ->
PRODUCT_IMAGE; kind=underlay -> DECORATIVE_IMAGE; background plates go
through the same _composite_background_plates path the live runner uses.
z_index = designer element order (idx).

Renders are named step54_render_parity_crello_<id>_r1a1.png so the
parameterized step51 blind judge consumes them directly:
  python layout_agent/output/step51_blind_judge_audit.py \
      --render-prefix step54_render_parity \
      --out step54_blind_judge_results.json --gt-control 0

Usage:
  conda activate meta && python layout_agent/output/step54_render_parity.py
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from run_role_team_live_crello import (  # noqa: E402
    _composite_background_plates,
    load_crello_sample,
)

from metagpt.ext.agentlayout.schema import (  # noqa: E402
    Candidate,
    Canvas,
    DesignSpec,
    Element,
    LayoutElement,
    SemanticType,
    VisualType,
)
from metagpt.ext.agentlayout.tools.background_analyzer import (  # noqa: E402
    resolve_background,
)
from metagpt.ext.agentlayout.tools.renderer import render_to_file  # noqa: E402


def build_gt_spec_and_candidate(meta: dict, descriptors: list):
    bg_ref = _composite_background_plates(meta, descriptors)
    promoted_idx = None
    if bg_ref is None:
        for d in descriptors:
            if d.get("kind") == "image" and d.get("asset_ref"):
                bg_ref = d["asset_ref"]
                promoted_idx = d.get("idx")
                break
    canvas = Canvas(
        width=int(meta["canvas_width"]),
        height=int(meta["canvas_height"]),
        background_asset_ref=bg_ref,
    )
    text_color = resolve_background(canvas).recommended_text_color

    spec_elements, layout_elements = [], []
    n_text = 0
    for d in descriptors:
        kind = d.get("kind")
        idx = d.get("idx")
        if kind == "background_candidate" or idx == promoted_idx:
            continue
        if kind == "text":
            n_text += 1
            sem = SemanticType.TITLE if n_text == 1 else SemanticType.BODY_TEXT
            vis = VisualType.TEXT
            content, asset_ref = d.get("content") or "", None
        elif kind == "image":
            sem, vis = SemanticType.PRODUCT_IMAGE, VisualType.IMAGE
            content, asset_ref = None, d.get("asset_ref")
        elif kind == "underlay":
            sem, vis = SemanticType.DECORATIVE_IMAGE, VisualType.IMAGE
            content, asset_ref = None, d.get("asset_ref")
        else:
            continue
        if vis == VisualType.IMAGE and not asset_ref:
            continue
        eid = f"gt_{kind}_{idx}"
        spec_elements.append(
            Element(
                id=eid,
                semantic_type=sem,
                visual_type=vis,
                content=content,
                asset_ref=asset_ref,
            )
        )
        layout_kwargs = {
            "id": eid,
            "left": int(round(float(d["left"]))),
            "top": int(round(float(d["top"]))),
            "width": max(1, int(round(float(d["width"])))),
            "height": max(1, int(round(float(d["height"])))),
            "z_index": int(idx),
        }
        if vis == VisualType.TEXT:
            n_lines = content.count("\n") + 1
            layout_kwargs["font_size"] = max(
                8, int(float(d["height"]) / n_lines * 0.75)
            )
            layout_kwargs["color"] = text_color
        layout_elements.append(LayoutElement(**layout_kwargs))

    spec = DesignSpec(canvas=canvas, elements=spec_elements)
    cand = Candidate(candidate_id="gt_rerender", elements=layout_elements)
    return spec, cand


def main():
    ids = json.load(open(HERE / "step13_drawn_ids.json"))["ids"]
    n_ok = 0
    for sid in ids:
        sample_dir = HERE / f"crello_{sid}"
        if not (sample_dir / "meta.json").exists():
            print(f"  [skip] {sid[:8]}: no cache")
            continue
        meta, descriptors = load_crello_sample(sample_dir)
        try:
            spec, cand = build_gt_spec_and_candidate(meta, descriptors)
            out = HERE / f"step54_render_parity_crello_{sid}_r1a1.png"
            render_to_file(cand, spec, out)
            n_ok += 1
            print(
                f"  {sid[:8]} elements={len(cand.elements):2d} "
                f"-> {out.name}"
            )
        except Exception as err:  # noqa: BLE001
            print(f"  [fail] {sid[:8]}: {type(err).__name__}: {err}")
    print(f"\nrendered {n_ok}/{len(ids)} GT layouts through our renderer")


if __name__ == "__main__":
    main()
