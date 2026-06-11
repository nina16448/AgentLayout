"""Step 21 -- Aesthetic eval per experiment.md (COLE single-call 5-axis JSON).

Aligned with layout_agent/experiment.md "Aesthetic Scores" section:
the verbatim COLE Quality Assurance Prompt (Jia et al. 2023, arXiv 2311.16974)
is sent once per image; the model returns a JSON object with 1-10 integer
scores for all 5 COLE axes. We aggregate and report only 4 axes per
experiment.md spec:

  * S_DL  Design and Layout                       (lower = cluttered, no hierarchy)
  * S_QL  Content Relevance and Effectiveness     (lower = irrelevant to purpose)
  * S_TV  Typography and Color Scheme             (lower = clashes, unreadable)
  * S_IO  Innovation and Originality              (lower = generic / trend-following)

The 5th axis (Graphics and Images) is collected for completeness but NOT
included in S_Mean -- experiment.md explicitly lists only those 4.

SEGA Table 3 reference (Crello, SEGA-13B): S_DL 6.149, S_QL 6.745,
S_TV 6.348, S_IO 6.038, S_Mean 6.320. NOTE: SEGA paper does not state which
4 of the 5 COLE axes it uses; numbers are kept as informational anchors but
cross-paper comparison is best taken as indicative.

Cost: ~$0.005-0.015 per gpt-4o vision call (single call/image vs 4 prior).
Run:  conda activate meta && python layout_agent/output/step21_phaseb_eval.py
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import re
import statistics
import sys
import traceback
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from openai import AsyncOpenAI

OUT = Path(__file__).resolve().parent
DRAWN = OUT / "step13_drawn_ids.json"
CONFIG2 = Path.home() / ".metagpt" / "config2.yaml"
MODEL = "gpt-4o"
SEGA_13B_REF = {"SDL": 6.149, "SQL": 6.745, "STV": 6.348, "SIO": 6.038, "Smean": 6.320}
REPORT_AXES = ("SDL", "SQL", "STV", "SIO")

COLE_PROMPT = """You are an autonomous AI Assistant who aids designers by providing insightful, objective, and constructive critiques of graphic design projects.

Your goals are:
- Deliver comprehensive and unbiased evaluations of graphic designs based on established design principles and industry standards.
- Identify potential areas for improvement and suggest actionable feedback to enhance the overall aesthetic and effectiveness of the designs.
- Maintain a consistent and high standard of critique.
- Utilize coordinate information for data description relative to the upper left corner of the image, with the upper left corner serving as the origin, the right as the positive direction, and the downward as the positive direction.

Please abide by the following rules:
- Strive to score as objectively as possible.
- Grade seriously. A flawless design can earn 10 points, a mediocre design can only earn 7 points, a design with obvious shortcomings can only earn 4 points, and a very poor design can only earn 1-2 points.
- Keep your reasoning concise when rating, and describe it as briefly as possible. If the output is too long, it will be truncated.
- Only respond in JSON format, no other information.

Grading criteria:

Design and Layout (1-10): The graphic design should present a clean, balanced, and consistent layout. The organization of elements should enhance the message, with clear paths for the eye to follow. A score of 10 signifies a layout that maximizes readability and visual appeal, while a 1 indicates a cluttered, confusing layout with no clear hierarchy or flow.

Content Relevance and Effectiveness (1-10): The content should be not only relevant to its purpose but also engaging for the intended audience, effectively communicating the intended message. A score of 10 means the content resonates with the target audience, aligns with the design's purpose, and enhances the overall message. A score of 1 indicates the content is irrelevant or does not connect with the audience.

Typography and Color Scheme (1-10): Typography and color should work together to enhance readability and harmonize with other design elements. This includes font selection, size, line spacing, color, and placement, as well as the overall color scheme of the design. A score of 10 represents excellent use of typography and color that aligns with the design's purpose and aesthetic, while a score of 1 indicates poor use of these elements that hinders readability or clashes with the design.

Graphics and Images (1-10): Any graphics or images used should enhance the design rather than distract from it. They should be high quality, relevant, and harmonious with other elements. A score of 10 indicates graphics or images that enhance the overall design and message, while a 1 indicates low-quality, irrelevant, or distracting visuals.

Innovation and Originality (1-10): The design should display an original, creative approach. It should not just follow trends but also show a unique interpretation of the brief. A score of 10 indicates a highly creative and innovative design that stands out in its originality, while a score of 1 indicates a lack of creativity or a generic approach.

Respond ONLY with a JSON object of this exact shape, no markdown fence:
{
  "design_and_layout": {"score": <int 1-10>, "reason": "<short string>"},
  "content_relevance_and_effectiveness": {"score": <int 1-10>, "reason": "<short string>"},
  "typography_and_color_scheme": {"score": <int 1-10>, "reason": "<short string>"},
  "graphics_and_images": {"score": <int 1-10>, "reason": "<short string>"},
  "innovation_and_originality": {"score": <int 1-10>, "reason": "<short string>"}
}"""

AXIS_KEY_MAP = {
    "SDL": "design_and_layout",
    "SQL": "content_relevance_and_effectiveness",
    "STV": "typography_and_color_scheme",
    "SGI": "graphics_and_images",
    "SIO": "innovation_and_originality",
}


def _load_openai_client() -> AsyncOpenAI:
    cfg = yaml.safe_load(CONFIG2.read_text())
    llm = cfg.get("llm", {})
    api_key = llm.get("api_key")
    base_url = llm.get("base_url") or "https://api.openai.com/v1"
    if not api_key:
        sys.exit(f"no llm.api_key in {CONFIG2}; cannot call OpenAI.")
    return AsyncOpenAI(api_key=api_key, base_url=base_url)


def _load_ids(ids_path: Optional[Path] = None) -> List[str]:
    drawn = json.loads((ids_path or DRAWN).read_text())
    ids = drawn.get("ids") or drawn
    return list(ids.keys()) if isinstance(ids, dict) else ids


def _png_b64(sample_id: str, source: str = "agent", render_prefix: Optional[str] = None) -> Optional[str]:
    if source == "designer-gt":
        p = OUT / f"crello_{sample_id}" / "ground_truth_preview.jpg"
        if not p.exists():
            return None
        return base64.b64encode(p.read_bytes()).decode()
    if render_prefix:
        # Same protocol as step51_blind_judge_audit: last attempt's render.
        pngs = sorted(OUT.glob(f"{render_prefix}_crello_{sample_id}_r1a*.png"))
        if not pngs:
            return None
        return base64.b64encode(pngs[-1].read_bytes()).decode()
    for cand in (
        OUT / f"step22_coldstart_crello_{sample_id}_render.png",
        OUT / f"role_live_crello_{sample_id}_last_reject.png",
    ):
        if cand.exists():
            return base64.b64encode(cand.read_bytes()).decode()
    return None


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _strip_fence(text: str) -> str:
    m = _JSON_FENCE_RE.search(text)
    return m.group(1) if m else text


def _parse_cole_json(text: str) -> Optional[Dict[str, int]]:
    """Parse the COLE response into {axis_key: int 1..10} for all 5 axes.

    Returns None if any axis is missing or score is not an integer 1..10.
    """
    if not text:
        return None
    try:
        obj = json.loads(_strip_fence(text.strip()))
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    out: Dict[str, int] = {}
    for axis_key, json_key in AXIS_KEY_MAP.items():
        entry = obj.get(json_key)
        if isinstance(entry, dict):
            score = entry.get("score")
        else:
            score = entry
        if not isinstance(score, int) or score < 1 or score > 10:
            return None
        out[axis_key] = score
    return out


async def _score_image(
    client: AsyncOpenAI,
    png_b64: str,
    max_retries: int = 2,
) -> Optional[Dict[str, int]]:
    """Single COLE multimodal call -> dict of 5 axis ints."""
    last_err: Optional[str] = None
    for attempt in range(max_retries):
        try:
            resp = await client.chat.completions.create(
                model=MODEL,
                temperature=0.0,
                max_tokens=600,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": COLE_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{png_b64}"
                                },
                            },
                        ],
                    }
                ],
            )
            text = (resp.choices[0].message.content or "").strip()
            parsed = _parse_cole_json(text)
            if parsed is not None:
                return parsed
            last_err = f"unparseable response: {text[:200]!r}"
        except Exception as err:
            last_err = f"{type(err).__name__}: {err}"
        if attempt < max_retries - 1:
            await asyncio.sleep(1.0)
    print(f"  [warn] COLE call failed after {max_retries}: {last_err}")
    return None


async def _process_sample(
    client: AsyncOpenAI,
    sample_id: str,
    idx: int,
    total: int,
    source: str = "agent",
    render_prefix: Optional[str] = None,
) -> dict:
    b64 = _png_b64(sample_id, source=source, render_prefix=render_prefix)
    if b64 is None:
        print(f"[{idx:2d}/{total}] {sample_id}  SKIP (no PNG, source={source})")
        return {"id": sample_id, "status": "no_png"}

    scores = await _score_image(client, b64)
    if scores is None:
        return {"id": sample_id, "status": "parse_failed"}

    smean = statistics.mean(scores[k] for k in REPORT_AXES)
    print(
        f"[{idx:2d}/{total}] {sample_id}  "
        f"SDL={scores['SDL']}  SQL={scores['SQL']}  STV={scores['STV']}  "
        f"SIO={scores['SIO']}  (SGI={scores['SGI']})  Smean={smean:.3f}"
    )
    return {
        "id": sample_id,
        "status": "ok",
        "scores": scores,
        "smean": smean,
    }


def _aggregate(samples: List[dict]) -> Dict[str, float]:
    ok = [s for s in samples if s.get("status") == "ok"]
    agg: Dict[str, float] = {}
    for axis in ("SDL", "SQL", "STV", "SGI", "SIO"):
        agg[axis] = float(statistics.mean(s["scores"][axis] for s in ok)) if ok else 0.0
    agg["Smean"] = float(statistics.mean(s["smean"] for s in ok)) if ok else 0.0
    return agg


def _print_table(agg: Dict[str, float], n_ok: int, n_total: int) -> None:
    print()
    print("=" * 78)
    print(f"STEP 21 -- COLE 5-axis aesthetic (experiment.md spec) Crello (N={n_ok}/{n_total})")
    print("=" * 78)
    cols = ["SDL", "SQL", "STV", "SIO", "Smean"]
    hdr = f"{'Method':<28}" + "".join(f"{c:>10}" for c in cols)
    print(hdr)
    print("-" * len(hdr))
    print("AgentLayout".ljust(28) + "".join(f"{agg[c]:>10.3f}" for c in cols))
    print("SEGA-13B (Table 3 ref)".ljust(28) + "".join(f"{SEGA_13B_REF[c]:>10.3f}" for c in cols))
    print("delta (AL - SEGA-13B)".ljust(28) + "".join(f"{agg[c] - SEGA_13B_REF[c]:>+10.3f}" for c in cols))
    print(f"SGI (not in Smean): {agg['SGI']:.3f}")
    print()
    print("Notes:")
    print("  * Single COLE QA Prompt call/image -> JSON 5 axes; Smean over 4 axes")
    print("    (DL/CR/TV/IO) per experiment.md. Graphics and Images collected but")
    print("    excluded from Smean.")
    print("  * SEGA Table 3 reference shown as anchor; the SEGA paper does not")
    print("    state which 4 of COLE's 5 axes it averages, so cross-paper Smean")
    print("    comparison is informational.")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source",
        choices=["agent", "designer-gt"],
        default="agent",
        help=(
            "agent       = score our pipeline renders (Step 21 default); "
            "designer-gt = score Crello designer original ground_truth_preview.jpg "
            "(Step 21b: judge-bias control)."
        ),
    )
    parser.add_argument(
        "--out",
        default=None,
        help=(
            "Output JSON path. Default: step21_phaseb_results.json (source=agent) "
            "or step21b_phaseb_designer_gt.json (source=designer-gt)."
        ),
    )
    parser.add_argument(
        "--max-samples",
        type=int,
        default=None,
        help="Limit run to first N ids (smoke testing). Default: all ids in file.",
    )
    parser.add_argument(
        "--render-prefix",
        default=None,
        help=(
            "Score oracle live renders instead: glob "
            "{prefix}_crello_{id}_r1a*.png and take the LAST attempt "
            "(blind-judge pngs[-1] protocol). E.g. step58b_live."
        ),
    )
    parser.add_argument(
        "--ids-file",
        default=str(DRAWN),
        help=(
            "Path to ids JSON (default: step13_drawn_ids.json, N=20). "
            "Use step22_n100_ids.json for the N=100 run."
        ),
    )
    args = parser.parse_args()
    if args.out is None:
        args.out = str(
            OUT
            / (
                "step21b_phaseb_designer_gt.json"
                if args.source == "designer-gt"
                else "step21_phaseb_results.json"
            )
        )

    ids_path = Path(args.ids_file)
    ids = _load_ids(ids_path)
    if args.max_samples is not None:
        ids = ids[: args.max_samples]
    total = len(ids)
    print(f"[setup] N={total} ids from {ids_path.name}; model={MODEL}; mode=COLE single-call JSON")

    client = _load_openai_client()
    samples = []
    for idx, sid in enumerate(ids, 1):
        try:
            result = await _process_sample(
                client, sid, idx, total, source=args.source, render_prefix=args.render_prefix
            )
        except Exception:
            traceback.print_exc()
            result = {"id": sid, "status": "driver_crash"}
        samples.append(result)

    agg = _aggregate(samples)
    n_ok = sum(1 for s in samples if s.get("status") == "ok")
    scope = (
        "Step 21 -- COLE QA Prompt single-call JSON aesthetic eval per "
        "layout_agent/experiment.md. 5 axes scored (DL/CR/TV/GI/IO); Smean "
        "over 4 axes (DL/CR/TV/IO) per experiment.md spec. Source = "
        f"{args.source} renders."
    )
    output = {
        "scope": scope,
        "source": args.source,
        "model": MODEL,
        "n_total": total,
        "n_completed": n_ok,
        "sega_13b_reference": SEGA_13B_REF,
        "samples": samples,
        "aggregate": agg,
    }
    Path(args.out).write_text(json.dumps(output, ensure_ascii=False, indent=2))
    _print_table(agg, n_ok, total)
    print(f"\n[done] wrote {Path(args.out).name} (N completed = {n_ok}/{total})")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
