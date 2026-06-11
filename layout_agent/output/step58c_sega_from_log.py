"""Step 58c -- recompute experiment.md geometric metrics from oracle live logs.

Zero-LLM: the step41 oracle never saves spec/candidate JSON to disk, but its
log embeds (a) the Analyst spec (first ```json block with "canvas") and
(b) every Generator batch ({"candidates": [...]}). The blind judge audits
the LAST rendered attempt (pngs[-1]), and the oracle always renders
batch.candidates[0], so per sample we score candidates[0] of the LAST batch.

Metrics (experiment.md): Ali v, Ove v, Und_l ^, Und_s ^, Occ v, Rea v --
computed via the same builders as step20_sega_eval (Phase A), so numbers are
directly comparable to the Step 29 N=1,895 table.

Usage (from layout_agent/output, conda env meta):
    python step58c_sega_from_log.py step58b_live_N20.log [more logs ...]
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT))

from step20_sega_eval import (  # noqa: E402
    _bg_image_array,
    _build_gt_layout,
    _build_layout_from_candidate,
    _load_meta,
    _per_sample_metrics,
    _saliency_from_bg,
)

from metagpt.ext.agentlayout.schema import Candidate, DesignSpec  # noqa: E402

METRIC_KEYS = [
    "alignment",
    "overlay",
    "underlay_loose",
    "underlay_strict",
    "occlusion",
    "readability",
]
LABELS = {
    "alignment": "Ali v",
    "overlay": "Ove v",
    "underlay_loose": "Und_l ^",
    "underlay_strict": "Und_s ^",
    "occlusion": "Occ v",
    "readability": "Rea v",
}

_saliency_cache: dict = {}
_bg_cache: dict = {}


def _bg_and_saliency(spec: DesignSpec):
    key = spec.canvas.background_asset_ref or ""
    if key not in _bg_cache:
        _bg_cache[key] = _bg_image_array(spec)
        _saliency_cache[key] = _saliency_from_bg(_bg_cache[key])
    return _bg_cache[key], _saliency_cache[key]


# Async loguru lines (cost_manager etc.) get interleaved into the JSON dumps,
# sometimes starting mid-line; strip from the timestamp to end-of-line.
_LOG_NOISE = re.compile(
    r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+ \| (?:INFO|WARNING|ERROR|DEBUG)\s*\|[^\n]*\n?"
)


def parse_log_last_candidates(log_path: Path):
    """Yield (sample_id, spec_dict, last_batch_first_candidate_dict)."""
    text = log_path.read_text()
    sections = re.split(r"^\[\d+/\d+\] ([0-9a-f]{24})\s*$", text, flags=re.M)
    for i in range(1, len(sections), 2):
        sid, body = sections[i], sections[i + 1]
        blocks = re.findall(r"```json\s*(\{.*?\})\s*```", body, flags=re.S)
        spec_dict = None
        last_cand = None
        for blk in blocks:
            try:
                # strict=False: Generator content strings may contain raw newlines.
                obj = json.loads(_LOG_NOISE.sub("", blk), strict=False)
            except json.JSONDecodeError:
                continue
            if "canvas" in obj and spec_dict is None:
                spec_dict = obj
            elif "candidates" in obj and obj["candidates"]:
                last_cand = obj["candidates"][0]
        if spec_dict is not None and last_cand is not None:
            yield sid, spec_dict, last_cand


def eval_log(log_path: Path, gt_rows: dict):
    rows = {}
    for sid, spec_dict, cand_dict in parse_log_last_candidates(log_path):
        spec = DesignSpec.model_validate(spec_dict)
        cand = Candidate.model_validate(cand_dict)
        cw, ch = float(spec.canvas.width), float(spec.canvas.height)
        bg_arr, saliency = _bg_and_saliency(spec)
        layout = _build_layout_from_candidate(cand, spec)
        rows[sid] = _per_sample_metrics(layout, cw, ch, bg_arr, saliency)
        print(f"  {log_path.name} {sid[:8]} AL  " + _fmt_row(rows[sid]))
        if sid not in gt_rows:
            meta = _load_meta(sid)
            if meta is None:
                print(f"  [warn] {sid[:8]} no meta.json -> GT skipped")
                continue
            gt_layout = _build_gt_layout(meta, spec)
            gt_rows[sid] = _per_sample_metrics(gt_layout, cw, ch, bg_arr, saliency)
            print(f"  {log_path.name} {sid[:8]} GT  " + _fmt_row(gt_rows[sid]))
    return rows


def _fmt_row(m: dict) -> str:
    return " ".join(
        f"{k.split('_')[0][:3]}={m[k]:.4f}" if m[k] is not None else f"{k[:3]}=skip"
        for k in METRIC_KEYS
    )


def _aggregate(rows: dict) -> dict:
    agg = {}
    for k in METRIC_KEYS:
        vals = [m[k] for m in rows.values() if m.get(k) is not None]
        agg[k] = (statistics.mean(vals), len(vals)) if vals else (None, 0)
    return agg


def main():
    logs = [Path(a) for a in sys.argv[1:]] or [OUT / "step58b_live_N20.log"]
    gt_rows: dict = {}
    per_log = {}
    for lp in logs:
        print(f"\n=== {lp.name} ===")
        per_log[lp.name] = eval_log(lp, gt_rows)

    print("\n=== experiment.md geometric metrics (mean over samples) ===")
    header = ["run"] + [LABELS[k] for k in METRIC_KEYS] + ["n"]
    print(" | ".join(f"{h:>10s}" for h in header))
    for name, rows in per_log.items():
        agg = _aggregate(rows)
        cells = [f"{name[:10]:>10s}"]
        for k in METRIC_KEYS:
            v, _ = agg[k]
            cells.append(f"{v:10.4f}" if v is not None else f"{'skip':>10s}")
        cells.append(f"{len(rows):>10d}")
        print(" | ".join(cells))
    agg = _aggregate(gt_rows)
    cells = [f"{'DesignerGT':>10s}"]
    for k in METRIC_KEYS:
        v, _ = agg[k]
        cells.append(f"{v:10.4f}" if v is not None else f"{'skip':>10s}")
    cells.append(f"{len(gt_rows):>10d}")
    print(" | ".join(cells))

    out = {
        "per_log": {
            name: {sid: m for sid, m in rows.items()} for name, rows in per_log.items()
        },
        "gt": gt_rows,
    }
    out_path = OUT / "step58c_sega_from_log.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
