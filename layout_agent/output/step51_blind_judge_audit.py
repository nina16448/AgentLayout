"""Step 51 blind re-judge audit (2026-06-11).

Measures label/position bias in the step41 pairwise oracle. Three conditions:
  cond2 blind A-vs-GT : neutral labels ("Design 1/2"), no layout JSON, no
                        safe zones; every pair judged TWICE (candidate first,
                        then candidate second) to isolate position bias.
  cond3 GT-vs-GT      : the same GT image attached twice; an unbiased judge
                        must output tie on every axis.
Baseline (cond1, labeled CANDIDATE/GROUND-TRUTH) = step49 N=20 run: B 32/32.

Usage:
  python layout_agent/output/step51_blind_judge_audit.py
  # Step 53 gate-off renders, no GT control (position bias already proven 0):
  python layout_agent/output/step51_blind_judge_audit.py \
      --render-prefix step53_gate_off_oracle \
      --out step53_blind_judge_results.json --gt-control 0
"""

import argparse
import asyncio
import base64
import glob
import json
import re
import sys
from pathlib import Path

import yaml
from openai import AsyncOpenAI

HERE = Path(__file__).resolve().parent
CONFIG2 = Path.home() / ".metagpt" / "config2.yaml"
MODEL = "gpt-4o"  # judge model, identical to step41/48/49/50
AXES = [
    "design_layout",
    "content_relevance",
    "typography_color",
    "graphics_images",
    "innovation_originality",
]
N_GT_CONTROL = 10

BLIND_PROMPT = """You are an expert graphic design critic comparing TWO poster designs
that were produced for the SAME design brief and the SAME asset set.

The FIRST attached image is "Design 1". The SECOND attached image is "Design 2".

For EACH of these 5 axes:
  design_layout, content_relevance, typography_color, graphics_images,
  innovation_originality
pick a winner ("1", "2", or "tie") and write a ONE-LINE reason.

Tie-breaking rule: pick "1" or "2" ONLY when you can name a SPECIFIC,
OBJECTIVE way that design improves on the other (cleaner hierarchy, more
readable typography, more distinctive composition). When both look
comparable / either-or / one has only a subtle edge -> output "tie".

Output STRICT JSON only, no markdown fences:
{"axes": {"design_layout": {"winner": "...", "reason": "..."},
          "content_relevance": {"winner": "...", "reason": "..."},
          "typography_color": {"winner": "...", "reason": "..."},
          "graphics_images": {"winner": "...", "reason": "..."},
          "innovation_originality": {"winner": "...", "reason": "..."}},
 "overall_winner": "...",
 "summary": "..."}
"""


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def _mime(path: Path) -> str:
    return "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png"


def _parse(text: str):
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    axes = obj.get("axes", {})
    if set(axes) != set(AXES):
        return None
    for a in AXES:
        if str(axes[a].get("winner")) not in ("1", "2", "tie"):
            return None
    if str(obj.get("overall_winner")) not in ("1", "2", "tie"):
        return None
    return obj


async def _judge(client: AsyncOpenAI, first: Path, second: Path, max_retries: int = 3):
    content = [{"type": "text", "text": BLIND_PROMPT}]
    for p in (first, second):
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:{_mime(p)};base64,{_b64(p)}"}}
        )
    last = None
    for attempt in range(max_retries):
        try:
            resp = await client.chat.completions.create(
                model=MODEL,
                temperature=0.0,
                max_tokens=1200,
                messages=[{"role": "user", "content": content}],
            )
            parsed = _parse(resp.choices[0].message.content or "")
            if parsed is not None:
                return parsed
            last = "unparseable"
        except Exception as err:  # noqa: BLE001
            last = f"{type(err).__name__}: {err}"
        await asyncio.sleep(1.0)
    print(f"    [warn] judge failed: {last}")
    return None


def _norm(winner: str, cand_position: str) -> str:
    """Map positional winner ('1'/'2'/'tie') to semantic ('cand'/'gt'/'tie')."""
    if winner == "tie":
        return "tie"
    if cand_position == "first":
        return "cand" if winner == "1" else "gt"
    return "cand" if winner == "2" else "gt"


async def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--render-prefix", default="step41_layout_aware_oracle")
    parser.add_argument("--out", default="step51_blind_judge_results.json")
    parser.add_argument("--gt-control", type=int, default=N_GT_CONTROL)
    args = parser.parse_args()

    cfg = yaml.safe_load(CONFIG2.read_text())
    llm = cfg.get("llm", {})
    if not llm.get("api_key"):
        sys.exit(f"no llm.api_key in {CONFIG2}")
    client = AsyncOpenAI(
        api_key=llm["api_key"], base_url=llm.get("base_url") or "https://api.openai.com/v1"
    )

    ids = json.load(open(HERE / "step13_drawn_ids.json"))["ids"]
    pairs = []
    for i in ids:
        pngs = sorted(glob.glob(str(HERE / f"{args.render_prefix}_crello_{i}_r1a*.png")))
        gt = HERE / f"crello_{i}" / "ground_truth_preview.jpg"
        if pngs and gt.exists():
            pairs.append((i, Path(pngs[-1]), gt))
    print(f"pairs: {len(pairs)}  | judge model: {MODEL}")

    results = {"blind": [], "gt_control": []}

    # ---- cond2: blind, both orders per pair -------------------------------
    for sid, cand, gt in pairs:
        for cand_position in ("first", "second"):
            first, second = (cand, gt) if cand_position == "first" else (gt, cand)
            verdict = await _judge(client, first, second)
            if verdict is None:
                continue
            row = {
                "id": sid,
                "cand_position": cand_position,
                "overall": _norm(verdict["overall_winner"], cand_position),
                "axes": {
                    a: {
                        "winner": _norm(verdict["axes"][a]["winner"], cand_position),
                        "reason": verdict["axes"][a]["reason"],
                    }
                    for a in AXES
                },
                "summary": verdict.get("summary", ""),
            }
            results["blind"].append(row)
            print(f"  {sid[:8]} cand_{cand_position:6s} -> overall={row['overall']}")

    # ---- cond3: GT vs GT control ------------------------------------------
    for sid, _, gt in pairs[: args.gt_control]:
        verdict = await _judge(client, gt, gt)
        if verdict is None:
            continue
        row = {
            "id": sid,
            "overall": verdict["overall_winner"],
            "axes": {a: verdict["axes"][a]["winner"] for a in AXES},
        }
        results["gt_control"].append(row)
        print(f"  GTvsGT {sid[:8]} -> overall={row['overall']}")

    # ---- summary -----------------------------------------------------------
    print("\n=== cond3 GT-vs-GT control (unbiased judge => 100% tie) ===")
    from collections import Counter

    ov = Counter(r["overall"] for r in results["gt_control"])
    print(f"  overall: {dict(ov)}  (n={len(results['gt_control'])})")
    ax_ctrl = {a: Counter(r["axes"][a] for r in results["gt_control"]) for a in AXES}
    for a in AXES:
        print(f"  {a:24s} {dict(ax_ctrl[a])}")

    print("\n=== cond2 blind cand-vs-GT (baseline labeled run: GT won 32/32) ===")
    for pos in ("first", "second"):
        rows = [r for r in results["blind"] if r["cand_position"] == pos]
        ov = Counter(r["overall"] for r in rows)
        print(f"  cand_{pos}: overall {dict(ov)}  (n={len(rows)})")
    print("  per-axis (both orders pooled):")
    for a in AXES:
        c = Counter(r["axes"][a]["winner"] for r in results["blind"])
        print(f"  {a:24s} cand={c.get('cand',0)} gt={c.get('gt',0)} tie={c.get('tie',0)}")

    # order-flip consistency: same pair, different order, different verdict?
    by_id = {}
    for r in results["blind"]:
        by_id.setdefault(r["id"], {})[r["cand_position"]] = r["overall"]
    flips = sum(
        1 for v in by_id.values() if len(v) == 2 and v["first"] != v["second"]
    )
    both = sum(1 for v in by_id.values() if len(v) == 2)
    print(f"\n  order-flip inconsistency: {flips}/{both} pairs change overall verdict when order swaps")

    out = HERE / args.out
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
