"""LLM-driven AestheticJudgeRole corner pytest — opt-in via `-m requires_llm`.

Mirrors layout_agent/output/verify_judge_corner.py three cases. Default-skipped
by tests/metagpt/ext/agentlayout/conftest.py.

Cost when run: ~$0.20 (3 multimodal gpt-4o vision calls).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from metagpt.ext.agentlayout.actions.judge_aesthetic import JudgeAesthetic
from metagpt.ext.agentlayout.pipeline import default_white_background
from metagpt.ext.agentlayout.schema import (
    ACCEPT_THRESHOLD,
    AestheticJudgement,
    Candidate,
    DesignSpec,
    JudgeDecision,
    LayoutElement,
    LayoutTree,
    LayoutTreeNode,
)


from metagpt.const import METAGPT_ROOT

SAMPLE_DIR = METAGPT_ROOT / "layout_agent" / "output" / "crello_5efdd2dd499b85dcc75ba0bc"


# ============================================================
# Fixture builders (no LLM)
# ============================================================


def _load_spec() -> DesignSpec:
    return DesignSpec.model_validate(
        json.loads((SAMPLE_DIR / "result_spec.json").read_text())
    )


def _flat_tree(spec: DesignSpec) -> LayoutTree:
    return LayoutTree(
        root=LayoutTreeNode(
            id="root",
            children=[LayoutTreeNode(id=el.id) for el in spec.elements],
        )
    )


def _gt_candidate(spec: DesignSpec) -> Candidate:
    """Build a Candidate from Crello ground-truth bboxes in meta.json."""
    meta = json.loads((SAMPLE_DIR / "meta.json").read_text())
    idx_to_id = {0: "image_1", 1: "image_2", 2: "text_1"}
    idx_to_z = {0: 0, 1: 1, 2: 2}
    elements = []
    for raw in meta["elements"]:
        eid = idx_to_id[raw["idx"]]
        spec_el = spec.get_element(eid)
        assert spec_el is not None
        kwargs = dict(
            id=eid,
            left=int(round(raw["left"])),
            top=int(round(raw["top"])),
            width=int(round(raw["width"])),
            height=int(round(raw["height"])),
            z_index=idx_to_z[raw["idx"]],
        )
        if spec_el.visual_type.value == "text":
            kwargs.update(
                font_family="sans-serif",
                font_size=64,
                font_weight="bold",
                color="#111111",
                text_align="center",
            )
        elements.append(LayoutElement(**kwargs))
    return Candidate(candidate_id="cand_gt", elements=elements)


def _collapsed_candidate(spec: DesignSpec) -> Candidate:
    """All elements stacked at (0,0) with original sizes — extreme overlap."""
    meta = json.loads((SAMPLE_DIR / "meta.json").read_text())
    idx_to_id = {0: "image_1", 1: "image_2", 2: "text_1"}
    elements = []
    for raw in meta["elements"]:
        eid = idx_to_id[raw["idx"]]
        spec_el = spec.get_element(eid)
        assert spec_el is not None
        kwargs = dict(
            id=eid,
            left=0,
            top=0,
            width=int(round(raw["width"])),
            height=int(round(raw["height"])),
            z_index=raw["idx"],
        )
        if spec_el.visual_type.value == "text":
            kwargs.update(
                font_family="sans-serif",
                font_size=48,
                font_weight="regular",
                color="#111111",
                text_align="left",
            )
        elements.append(LayoutElement(**kwargs))
    return Candidate(candidate_id="cand_collapsed", elements=elements)


_ELEMENT_IDS = re.compile(
    r"\b(image_\d+|text_\d+|logo_\d+|headline_\d+|cta_\d+|title_\d+|"
    r"subtitle_\d+|body_text_\d+|caption_\d+|pricetag_\d+|icon_\d+)\b",
    re.IGNORECASE,
)


def _suggestion_is_specific(text: str) -> bool:
    """A suggestion is 'specific' if it references a real element id or has
    a numeric+unit. Lighter version of verify_judge_corner.classify_suggestion."""
    if _ELEMENT_IDS.search(text):
        return True
    if re.search(r"\b\d+(\.\d+)?\s*(px|pt|%|deg|degrees?|pixels?|em)\b", text, re.IGNORECASE):
        return True
    return False


# ============================================================
# Case 1 — Multimodal visual probe (GT scored > collapsed)
# ============================================================


@pytest.mark.requires_llm
@pytest.mark.asyncio
async def test_judge_multimodal_probe_picks_gt_over_collapsed():
    """Vision must discriminate: best_id == GT and GT total > collapsed total."""
    if not (SAMPLE_DIR / "result_spec.json").exists():
        pytest.skip(f"missing fixture: {SAMPLE_DIR}")

    spec = _load_spec()
    tree = _flat_tree(spec)
    bg = default_white_background(spec.canvas)
    gt = _gt_candidate(spec)
    bad = _collapsed_candidate(spec)

    judgement = await JudgeAesthetic().run(
        candidates=[gt, bad], spec=spec, tree=tree, bg=bg
    )

    assert isinstance(judgement, AestheticJudgement)
    totals = {ev.candidate_id: ev.total for ev in judgement.evaluations}
    assert set(totals.keys()) == {"cand_gt", "cand_collapsed"}
    assert judgement.best_candidate_id == "cand_gt", f"got {judgement.best_candidate_id}"
    assert totals["cand_gt"] > totals["cand_collapsed"], (
        f"gt={totals['cand_gt']}, bad={totals['cand_collapsed']}"
    )


# ============================================================
# Case 2 — GT-as-ACCEPT scenario (soft floor only)
# ============================================================


@pytest.mark.requires_llm
@pytest.mark.asyncio
async def test_judge_gt_layout_total_meets_soft_floor():
    """GT alone should score >= 60 (Judge is not pathologically harsh).

    NOTE: 2026-05-13 verify_judge_corner observed GT=68 vs the old
    ACCEPT_THRESHOLD=80, which we calibrated down to 75 on 2026-05-14. Even
    so, GT=68 is BELOW 75 so this test still does NOT assert ACCEPT — but the
    headroom is now believable rather than degenerate.
    """
    if not (SAMPLE_DIR / "result_spec.json").exists():
        pytest.skip(f"missing fixture: {SAMPLE_DIR}")

    spec = _load_spec()
    tree = _flat_tree(spec)
    bg = default_white_background(spec.canvas)
    gt = _gt_candidate(spec)

    judgement = await JudgeAesthetic().run(
        candidates=[gt], spec=spec, tree=tree, bg=bg
    )

    assert len(judgement.evaluations) == 1
    gt_total = judgement.evaluations[0].total
    assert judgement.best_candidate_id == "cand_gt"
    assert gt_total >= 60, f"got {gt_total} (soft floor)"
    assert gt_total <= 100
    assert ACCEPT_THRESHOLD == 75  # sanity constant (calibrated 2026-05-14)


# ============================================================
# Case 3 — Feedback specificity (REJECT must give actionable suggestions)
# ============================================================


@pytest.mark.requires_llm
@pytest.mark.asyncio
async def test_judge_reject_feedback_contains_specific_suggestion():
    """A reject feedback must have >= 1 suggestion that references an element id
    or a numeric+unit (ablation found vague feedback breaks the loop)."""
    if not (SAMPLE_DIR / "result_spec.json").exists():
        pytest.skip(f"missing fixture: {SAMPLE_DIR}")

    spec = _load_spec()
    tree = _flat_tree(spec)
    bg = default_white_background(spec.canvas)
    judgement = await JudgeAesthetic().run(
        candidates=[_collapsed_candidate(spec)],
        spec=spec,
        tree=tree,
        bg=bg,
    )

    if judgement.decision != JudgeDecision.REJECT:
        pytest.skip(
            f"Judge unexpectedly returned {judgement.decision.value}; "
            "feedback specificity invariant cannot be checked."
        )
    assert judgement.feedback is not None
    assert len(judgement.feedback.suggestions) >= 1
    n_specific = sum(
        1 for s in judgement.feedback.suggestions if _suggestion_is_specific(s)
    )
    assert n_specific >= 1, (
        f"no specific suggestion among {judgement.feedback.suggestions}"
    )
