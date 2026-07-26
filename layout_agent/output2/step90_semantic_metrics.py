"""Step 90 -- first SGC / TLC / PCA numbers on the N=100 cached samples.

Scores three methods against the SAME per-sample semantic tree
(fairness contract from layout_agent/new_experiment.md):

    agent_a : step89 arm-A final layouts (the paper's main arm)
    agent_b : step89 arm-B final layouts
    gt      : designer ground truth (Crello text layers)

Tree source is pluggable. Default: PlanAssets (LLM) on the arm-A spec,
cached to trees/{sample_id}.json so re-runs are free; pass --tree-dir to
swap in e.g. human-annotated trees later.

Id alignment: the tree uses arm-A element ids. Arm-A analyst re-orders text
elements by role (title first), so ORDER-based matching against GT z-order
would silently mismatch -- instead every step89 text element carries
``asset_ref = .../asset_{gt_idx}_text.png``, giving an exact
tree_id <-> gt_idx correspondence. Arm B (independent Analyst run, different
id assignment) is bridged the same way: b_id -> gt_idx -> a_id. Samples where
the idx sets do not line up are recorded and skipped, never forced.

Run:
    conda activate meta
    python layout_agent/output2/step90_semantic_metrics.py [--limit N] [--tree-dir DIR]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from metagpt.ext.agentlayout.actions.plan_assets import PlanAssets  # noqa: E402
from metagpt.ext.agentlayout.schema import DesignSpec, LayoutTree  # noqa: E402
from metagpt.ext.agentlayout.tools.semantic_group_metrics import (  # noqa: E402
    SampleMetrics,
    aggregate,
    aggregate_markdown,
    evaluate_sample,
    qualitative_picks,
)

N100_ROOT = HERE / "step89_n100"
CRELLO_ROOT = REPO_ROOT / "layout_agent" / "output"
OUT_ROOT = HERE / "step90_semantic_metrics"
TREE_DIR_DEFAULT = OUT_ROOT / "trees"

TREE_GEN_CONCURRENCY = 8

_ASSET_IDX_RE = re.compile(r"asset_(\d+)_text\.png$")


# ------------------------------------------------------------------
# Loading helpers
# ------------------------------------------------------------------


def _sample_ids() -> List[str]:
    return sorted(p.name for p in N100_ROOT.iterdir() if (p / "a" / "spec.json").exists())


def _load_spec(sample_id: str, arm: str) -> DesignSpec:
    raw = json.loads((N100_ROOT / sample_id / arm / "spec.json").read_text())
    return DesignSpec.model_validate(raw)


def _spec_idx_map(spec: DesignSpec) -> Optional[Dict[str, int]]:
    """element id -> GT layer idx, via the text-snapshot asset_ref. None if any
    foreground element lacks a parseable ref (alignment impossible)."""
    mapping: Dict[str, int] = {}
    for el in spec.foreground_elements():
        m = _ASSET_IDX_RE.search(el.asset_ref or "")
        if not m:
            return None
        mapping[el.id] = int(m.group(1))
    return mapping


def _candidate_boxes(sample_id: str, arm: str) -> Dict[str, Tuple[float, float, float, float]]:
    raw = json.loads((N100_ROOT / sample_id / arm / "candidate.json").read_text())
    return {
        e["id"]: (e["left"], e["top"], e["width"], e["height"]) for e in raw["elements"]
    }


def _gt_meta(sample_id: str) -> dict:
    return json.loads((CRELLO_ROOT / f"crello_{sample_id}" / "meta.json").read_text())


# ------------------------------------------------------------------
# Phase 1: trees (pluggable; default = PlanAssets on the arm-A spec)
# ------------------------------------------------------------------


async def _gen_trees(sample_ids: List[str], tree_dir: Path) -> None:
    tree_dir.mkdir(parents=True, exist_ok=True)
    todo = [s for s in sample_ids if not (tree_dir / f"{s}.json").exists()]
    if not todo:
        print(f"[trees] all {len(sample_ids)} cached in {tree_dir}")
        return
    print(f"[trees] generating {len(todo)} trees via PlanAssets ...")
    plan = PlanAssets()
    sem = asyncio.Semaphore(TREE_GEN_CONCURRENCY)

    async def _one(sid: str) -> None:
        async with sem:
            spec = _load_spec(sid, "a")
            try:
                tree = await plan.run(spec=spec)
            except Exception as err:  # noqa: BLE001 -- recorded, sample later skipped
                print(f"[trees] {sid} FAILED: {err}")
                return
            (tree_dir / f"{sid}.json").write_text(tree.model_dump_json())
            print(f"[trees] {sid} ok ({len(tree.root.children)} groups)")

    await asyncio.gather(*(_one(s) for s in todo))


def _load_tree(sample_id: str, tree_dir: Path) -> Optional[LayoutTree]:
    path = tree_dir / f"{sample_id}.json"
    if not path.exists():
        return None
    return LayoutTree.model_validate(json.loads(path.read_text()))


# ------------------------------------------------------------------
# Phase 2: evaluate the three methods on the shared tree
# ------------------------------------------------------------------


def _skipped(sample_id: str, method: str, reason: str) -> SampleMetrics:
    return SampleMetrics(sample_id=sample_id, method=method, skip_reasons=[reason])


def _eval_sample(sample_id: str, tree_dir: Path) -> List[SampleMetrics]:
    tree = _load_tree(sample_id, tree_dir)
    if tree is None:
        return [_skipped(sample_id, m, "no_tree") for m in ("agent_a", "agent_b", "gt")]

    spec_a = _load_spec(sample_id, "a")
    canvas_w, canvas_h = spec_a.canvas.width, spec_a.canvas.height
    a_idx = _spec_idx_map(spec_a)
    rows: List[SampleMetrics] = []

    # -- agent_a: candidate ids ARE the tree ids.
    rows.append(
        evaluate_sample(
            tree=tree,
            pixel_boxes=_candidate_boxes(sample_id, "a"),
            canvas_width=canvas_w,
            canvas_height=canvas_h,
            sample_id=sample_id,
            method="agent_a",
        )
    )

    # -- agent_b: independent Analyst run -> bridge b_id -> gt_idx -> a_id.
    b_row: Optional[SampleMetrics] = None
    if a_idx is None:
        b_row = _skipped(sample_id, "agent_b", "id_alignment:a_spec_missing_asset_ref")
    else:
        try:
            spec_b = _load_spec(sample_id, "b")
            b_idx = _spec_idx_map(spec_b)
        except FileNotFoundError:
            b_idx = None
        if b_idx is None:
            b_row = _skipped(sample_id, "agent_b", "id_alignment:b_spec_missing_asset_ref")
        elif sorted(b_idx.values()) != sorted(a_idx.values()):
            b_row = _skipped(sample_id, "agent_b", "id_alignment:idx_set_mismatch")
        else:
            idx_to_a = {v: k for k, v in a_idx.items()}
            b_boxes_raw = _candidate_boxes(sample_id, "b")
            b_boxes = {
                idx_to_a[gt_idx]: b_boxes_raw[b_id]
                for b_id, gt_idx in b_idx.items()
                if b_id in b_boxes_raw
            }
            b_row = evaluate_sample(
                tree=tree,
                pixel_boxes=b_boxes,
                canvas_width=canvas_w,
                canvas_height=canvas_h,
                sample_id=sample_id,
                method="agent_b",
            )
    rows.append(b_row)

    # -- gt: Crello text layers, matched by exact gt_idx.
    if a_idx is None:
        rows.append(_skipped(sample_id, "gt", "id_alignment:a_spec_missing_asset_ref"))
    else:
        meta = _gt_meta(sample_id)
        gt_boxes = {
            e["idx"]: (e["left"], e["top"], e["width"], e["height"])
            for e in meta["elements"]
            if e.get("kind") == "text"
        }
        mapped = {
            a_id: gt_boxes[gt_idx] for a_id, gt_idx in a_idx.items() if gt_idx in gt_boxes
        }
        if len(mapped) != len(a_idx):
            rows.append(_skipped(sample_id, "gt", "id_alignment:gt_text_layer_missing"))
        else:
            rows.append(
                evaluate_sample(
                    tree=tree,
                    pixel_boxes=mapped,
                    canvas_width=meta["canvas_width"],
                    canvas_height=meta["canvas_height"],
                    sample_id=sample_id,
                    method="gt",
                )
            )
    return rows


# ------------------------------------------------------------------
# Reports
# ------------------------------------------------------------------


def _write_reports(samples: List[SampleMetrics]) -> None:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    (OUT_ROOT / "per_sample.json").write_text(
        json.dumps([s.model_dump() for s in samples], indent=2, ensure_ascii=False)
    )

    rows = aggregate(samples)
    order = {"agent_a": 0, "agent_b": 1, "gt": 2}
    rows.sort(key=lambda r: order.get(r.method, 9))

    by_method: Dict[str, List[SampleMetrics]] = {}
    for s in samples:
        by_method.setdefault(s.method, []).append(s)

    md = ["# Step 90 — SGC / TLC / PCA（N=100，同一棵 Asset Planner tree）", ""]
    md.append(aggregate_markdown(rows))
    md += ["", "## 質性案例挑選清單（agent_a SGC − baseline SGC 最大前 10）", ""]
    picks_out: Dict[str, List] = {}
    for baseline in ("gt", "agent_b"):
        picks = qualitative_picks(by_method.get("agent_a", []), by_method.get(baseline, []))
        picks_out[baseline] = picks
        md.append(f"### vs {baseline}")
        md.append("")
        for sid, delta in picks:
            md.append(f"- `{sid}`  Δsgc = {delta:+.3f}")
        md.append("")
    (OUT_ROOT / "aggregate.md").write_text("\n".join(md))
    (OUT_ROOT / "qualitative_picks.json").write_text(
        json.dumps(picks_out, indent=2, ensure_ascii=False)
    )
    print("\n" + "\n".join(md))


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="first N samples only")
    ap.add_argument(
        "--tree-dir",
        type=Path,
        default=TREE_DIR_DEFAULT,
        help="directory of {sample_id}.json LayoutTree files (pluggable tree source)",
    )
    ap.add_argument(
        "--no-tree-gen",
        action="store_true",
        help="never call the LLM; samples without a cached tree are skipped",
    )
    args = ap.parse_args()

    sample_ids = _sample_ids()
    if args.limit:
        sample_ids = sample_ids[: args.limit]
    print(f"[step90] {len(sample_ids)} samples, tree dir = {args.tree_dir}")

    if not args.no_tree_gen:
        await _gen_trees(sample_ids, args.tree_dir)

    samples: List[SampleMetrics] = []
    for sid in sample_ids:
        samples.extend(_eval_sample(sid, args.tree_dir))
    _write_reports(samples)


if __name__ == "__main__":
    asyncio.run(main())
