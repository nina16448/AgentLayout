"""Step 59 -- text-under-gradient QC calibration (GT-first, Step 57 SOP).

Rea (Sobel gradient under text) is the only experiment.md geometric axis
where AL clearly trails GT (0.0141 vs 0.0066, ~2x). Root cause: safe zones
are saliency-driven, not texture-driven -- a saliency-low corner can still
be gradient-high. Before adding any QC rule we calibrate on designer GT:
the threshold must pass all 20 GT layouts with margin (degradation guard,
NOT an aesthetic rule -- same philosophy as Step 57 coverage/dead-band).

Per-ELEMENT granularity (not per-sample mean): a QC violation must name the
offending text element. Convention matches metric_readability exactly:
normalised Sobel gradient of the bg, underlay bboxes mask text pixels to 0
(an underlay shields the text from texture).

Zero-LLM: specs/bg refs come from oracle live logs (same parsing as
step58c), GT boxes from crello_{id}/meta.json, candidates replayed from
ALL generator batches in the logs (like step57 replay).

Usage (layout_agent/output, conda env meta):
    python step59_text_gradient_calibration.py step56_live_N20.log step58b_live_N20.log
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

import numpy as np
from PIL import Image

OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT))

from step58c_sega_from_log import _LOG_NOISE  # noqa: E402

from metagpt.ext.agentlayout.evaluation.sega_metrics import (  # noqa: E402
    CLS_TEXT,
    CLS_UNDERLAY,
    _sobel_gradient_normalised,
)
from metagpt.ext.agentlayout.schema import DesignSpec  # noqa: E402

from step20_sega_eval import _cls_from_spec_element, _load_meta  # noqa: E402

_grad_cache: dict = {}


def _grad_for_bg(bg_ref, cw: int, ch: int) -> np.ndarray:
    key = (bg_ref or "", cw, ch)
    if key not in _grad_cache:
        if not bg_ref:
            _grad_cache[key] = np.zeros((ch, cw), dtype=np.float64)
        else:
            import cv2

            arr = np.array(Image.open(bg_ref).convert("RGB"))
            if arr.shape[1] != cw or arr.shape[0] != ch:
                arr = cv2.resize(arr, (cw, ch))
            _grad_cache[key] = _sobel_gradient_normalised(arr)
    return _grad_cache[key]


def _clip_box(left, top, width, height, cw, ch):
    xl = max(0, int(round(float(left))))
    yl = max(0, int(round(float(top))))
    xr = min(cw, int(round(float(left) + float(width))))
    yr = min(ch, int(round(float(top) + float(height))))
    if xr <= xl or yr <= yl:
        return None
    return xl, yl, xr, yr


def per_element_gradient(text_boxes, underlay_boxes, grad, cw, ch):
    """{element_key: mean grad of text pixels NOT shielded by any underlay}.

    None value = element fully shielded (trivially readable, no score).
    """
    out = {}
    for key, box in text_boxes:
        m = np.zeros((ch, cw), dtype=bool)
        xl, yl, xr, yr = box
        m[yl:yr, xl:xr] = True
        for uxl, uyl, uxr, uyr in underlay_boxes:
            m[uyl:uyr, uxl:uxr] = False
        area = int(m.sum())
        out[key] = float(grad[m].mean()) if area > 0 else None
    return out


def parse_log_samples(log_path: Path):
    """Yield (sid, spec_dict, [candidate dicts from ALL batches])."""
    text = log_path.read_text()
    sections = re.split(r"^\[\d+/\d+\] ([0-9a-f]{24})\s*$", text, flags=re.M)
    for i in range(1, len(sections), 2):
        sid, body = sections[i], sections[i + 1]
        blocks = re.findall(r"```json\s*(\{.*?\})\s*```", body, flags=re.S)
        spec_dict = None
        cands = []
        for blk in blocks:
            try:
                obj = json.loads(_LOG_NOISE.sub("", blk), strict=False)
            except json.JSONDecodeError:
                continue
            if "canvas" in obj and spec_dict is None:
                spec_dict = obj
            elif "candidates" in obj:
                cands.extend(obj["candidates"])
        if spec_dict is not None:
            yield sid, spec_dict, cands


def gt_boxes(meta: dict, cw: int, ch: int):
    texts, unders = [], []
    for k, el in enumerate(meta.get("elements", [])):
        kind = el.get("kind")
        if kind not in ("text", "underlay"):
            continue
        if None in (el.get("left"), el.get("top"), el.get("width"), el.get("height")):
            continue
        if float(el["width"]) * float(el["height"]) > 0.95 * cw * ch:
            continue
        box = _clip_box(el["left"], el["top"], el["width"], el["height"], cw, ch)
        if box is None:
            continue
        if kind == "text":
            texts.append((f"el{k}", box))
        else:
            unders.append(box)
    return texts, unders


def cand_boxes(cand: dict, cls_by_id: dict, cw: int, ch: int):
    texts, unders = [], []
    for el in cand.get("elements", []):
        cls = cls_by_id.get(el.get("id"), 2)
        if cls == 0:
            continue
        if float(el["width"]) * float(el["height"]) > 0.95 * cw * ch:
            continue
        box = _clip_box(el["left"], el["top"], el["width"], el["height"], cw, ch)
        if box is None:
            continue
        if cls == CLS_TEXT:
            texts.append((str(el.get("id")), box))
        elif cls == CLS_UNDERLAY:
            unders.append(box)
    return texts, unders


def main():
    logs = [Path(a) for a in sys.argv[1:]] or [
        OUT / "step56_live_N20.log",
        OUT / "step58b_live_N20.log",
    ]

    gt_results: dict = {}
    cand_results: dict = {}

    for lp in logs:
        for sid, spec_dict, cands in parse_log_samples(lp):
            spec = DesignSpec.model_validate(spec_dict)
            cw, ch = int(spec.canvas.width), int(spec.canvas.height)
            grad = _grad_for_bg(spec.canvas.background_asset_ref, cw, ch)

            if sid not in gt_results:
                meta = _load_meta(sid)
                if meta is not None:
                    texts, unders = gt_boxes(meta, cw, ch)
                    per_el = per_element_gradient(texts, unders, grad, cw, ch)
                    vals = [v for v in per_el.values() if v is not None]
                    gt_results[sid] = {
                        "per_element": per_el,
                        "worst": max(vals) if vals else None,
                        "n_text": len(texts),
                        "n_shielded": sum(1 for v in per_el.values() if v is None),
                    }

            cls_by_id = {el.id: _cls_from_spec_element(el) for el in spec.elements}
            for cand in cands:
                texts, unders = cand_boxes(cand, cls_by_id, cw, ch)
                per_el = per_element_gradient(texts, unders, grad, cw, ch)
                vals = [v for v in per_el.values() if v is not None]
                key = f"{lp.stem}:{sid[:8]}:{cand.get('candidate_id', '?')}:{len(cand_results)}"
                cand_results[key] = {
                    "worst": max(vals) if vals else None,
                    "worst_element": (
                        max((v, k) for k, v in per_el.items() if v is not None)[1]
                        if vals
                        else None
                    ),
                    "n_text": len(texts),
                }

    print("=== GT (designer) per-sample worst text-element gradient ===")
    gt_worsts = []
    for sid, r in sorted(gt_results.items()):
        w = r["worst"]
        if w is not None:
            gt_worsts.append(w)
        wtxt = f"{w:.4f}" if w is not None else "all-shielded"
        print(
            f"  {sid[:8]} n_text={r['n_text']:2d} shielded={r['n_shielded']:2d} worst={wtxt}"
        )
    print(
        f"\n  GT N={len(gt_results)}  worst-element: min={min(gt_worsts):.4f} "
        f"median={statistics.median(gt_worsts):.4f} max={max(gt_worsts):.4f}"
    )

    cand_worsts = [r["worst"] for r in cand_results.values() if r["worst"] is not None]
    print(f"\n=== Candidates replayed (all batches, all logs) N={len(cand_results)} ===")
    print(
        f"  worst-element: min={min(cand_worsts):.4f} "
        f"median={statistics.median(cand_worsts):.4f} max={max(cand_worsts):.4f}"
    )

    gt_max = max(gt_worsts)
    print("\n=== Threshold sweep (rule: worst text element mean-grad > T) ===")
    print(f"  GT max = {gt_max:.4f}  (threshold must be >= this, + margin)")
    for t in [round(gt_max + m, 4) for m in (0.0, 0.01, 0.02, 0.05)] + [0.10, 0.15, 0.20]:
        n_gt_fail = sum(1 for w in gt_worsts if w > t)
        n_cand = sum(1 for w in cand_worsts if w > t)
        print(
            f"  T={t:.4f}  GT fail={n_gt_fail}/{len(gt_worsts)}  "
            f"candidates caught={n_cand}/{len(cand_worsts)} "
            f"({100.0 * n_cand / len(cand_worsts):.0f}%)"
        )

    out = {
        "gt": gt_results,
        "candidates": cand_results,
        "gt_worst_max": gt_max,
    }
    out_path = OUT / "step59_text_gradient_calibration.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
