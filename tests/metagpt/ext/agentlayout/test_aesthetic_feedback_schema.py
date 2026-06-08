"""Pytest offline tests for the AestheticFeedback / Suggestion schema upgrade.

Added 2026-05-14 alongside the Judge prompt upgrade (priority 3 in NEXT_SESSION).
Covers backward-compat parsing of legacy JSON, structured_suggestions round-trip,
numeric-kind validation, color-hex validation, and the AestheticJudgement +
AestheticFeedback decision-feedback invariant.

No LLM calls. Pure schema validation. Should land at <0.05s.

    pytest tests/metagpt/ext/agentlayout/test_aesthetic_feedback_schema.py -v --no-cov
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from metagpt.ext.agentlayout.schema import (
    ACCEPT_THRESHOLD,
    AestheticFeedback,
    AestheticJudgement,
    Evaluation,
    JudgeDecision,
    JudgeScores,
    Suggestion,
    SuggestionKind,
)


# ============================================================
# Backward compatibility: legacy JSON without structured_suggestions
# ============================================================


def test_legacy_json_without_structured_still_parses():
    """JSON produced by the pre-2026-05-14 prompt must still validate.

    `structured_suggestions` defaults to an empty list so old fixtures, old
    Crello driver runs, and older saved AestheticJudgement records all parse.
    """
    fb = AestheticFeedback.model_validate(
        {
            "common_issues": "Headline too small.",
            "suggestions": ["Increase headline_1 size."],
        }
    )
    assert fb.common_issues == "Headline too small."
    assert fb.suggestions == ["Increase headline_1 size."]
    assert fb.structured_suggestions == []


def test_legacy_aesthetic_judgement_reject_still_parses():
    """A full reject Judgement WITHOUT structured_suggestions must round-trip."""
    payload = {
        "decision": "reject",
        "best_candidate_id": "cand_03",
        "evaluations": [
            {
                "candidate_id": "cand_03",
                "total": 30,
                "scores": {
                    "design_layout": 6,
                    "content_relevance": 6,
                    "typography_color": 6,
                    "graphics_images": 6,
                    "innovation_originality": 6,
                },
                "strengths": "palette ok",
                "weaknesses": "headline_1 too small",
            }
        ],
        "feedback": {
            "common_issues": "headline_1 dominance missing",
            "suggestions": ["Increase headline_1 size."],
        },
    }
    j = AestheticJudgement.model_validate(payload)
    assert j.decision == JudgeDecision.REJECT
    assert j.feedback is not None
    assert j.feedback.structured_suggestions == []


# ============================================================
# New: structured_suggestions parsing + Suggestion type checks
# ============================================================


def test_structured_suggestions_round_trip():
    payload = {
        "common_issues": "x",
        "suggestions": [],
        "structured_suggestions": [
            {
                "kind": "resize",
                "target_id": "headline_1",
                "metric": "height",
                "op": ">=",
                "value": 80,
                "rationale": "Currently too small.",
            },
            {
                "kind": "color",
                "target_id": "bg_1",
                "metric": "color",
                "op": "set_to",
                "value": "#1A1A2E",
            },
            {
                "kind": "spacing",
                "target_id": "logo_1",
                "metric": "gap_to:headline_1",
                "op": "<=",
                "value": 40.0,
            },
        ],
    }
    fb = AestheticFeedback.model_validate(payload)
    assert len(fb.structured_suggestions) == 3
    kinds = [s.kind for s in fb.structured_suggestions]
    assert kinds == [SuggestionKind.RESIZE, SuggestionKind.COLOR, SuggestionKind.SPACING]
    dumped = json.loads(fb.model_dump_json())
    assert dumped["structured_suggestions"][0]["value"] == 80
    assert dumped["structured_suggestions"][1]["value"] == "#1A1A2E"


@pytest.mark.parametrize(
    "kind",
    [
        SuggestionKind.RESIZE,
        SuggestionKind.MOVE,
        SuggestionKind.SPACING,
        SuggestionKind.TYPOGRAPHY,
        SuggestionKind.ZORDER,
    ],
)
def test_numeric_kind_rejects_string_value(kind):
    """Numeric kinds must reject string values (would otherwise mask 'bigger' / 'larger')."""
    with pytest.raises(ValidationError):
        Suggestion(
            kind=kind,
            target_id="x",
            metric="width",
            op=">=",
            value="big",
        )


@pytest.mark.parametrize("value", [80, 80.0, 0, -10, 1.5])
def test_numeric_kind_accepts_int_and_float(value):
    s = Suggestion(
        kind=SuggestionKind.RESIZE,
        target_id="x",
        metric="width",
        op=">=",
        value=value,
    )
    assert s.value == value


@pytest.mark.parametrize(
    "bad_value",
    ["blue", "ff00ff", "#GG0000", "#12", "rgba(1,2,3,0)"],
)
def test_color_kind_rejects_invalid_hex(bad_value):
    with pytest.raises(ValidationError):
        Suggestion(
            kind=SuggestionKind.COLOR,
            target_id="x",
            metric="color",
            op="set_to",
            value=bad_value,
        )


@pytest.mark.parametrize("good_hex", ["#000", "#FFF", "#FFFFFF", "#1A2B3C", "#1A2B3C80"])
def test_color_kind_accepts_3_6_or_8_digit_hex(good_hex):
    s = Suggestion(
        kind=SuggestionKind.COLOR,
        target_id="x",
        metric="color",
        op="set_to",
        value=good_hex,
    )
    assert s.value == good_hex


def test_other_kind_accepts_anything():
    """OTHER is the explicit escape hatch — no value-type enforcement."""
    s_num = Suggestion(
        kind=SuggestionKind.OTHER,
        target_id="x",
        metric="something",
        op="set_to",
        value=42,
    )
    s_str = Suggestion(
        kind=SuggestionKind.OTHER,
        target_id="x",
        metric="something",
        op="set_to",
        value="freeform",
    )
    assert s_num.value == 42
    assert s_str.value == "freeform"


def test_suggestion_target_id_is_required():
    with pytest.raises(ValidationError):
        Suggestion(
            kind=SuggestionKind.RESIZE,
            metric="width",
            op=">=",
            value=100,
        )


# ============================================================
# AestheticJudgement.decision <-> feedback invariant still holds
# ============================================================


def _ev(cid: str, total: int) -> Evaluation:
    """Build a valid Evaluation on the COLE 5-axis 1-10 scale (Step 30+).

    Distributes `total` across 5 axes so each axis lies in [1, 10] for any
    `total` in the supported [5, 46] band. Above 46 the remainder axis would
    exceed 10; tests that need >46 must construct the Evaluation manually.
    """
    base = total // 5
    return Evaluation(
        candidate_id=cid,
        total=total,
        scores=JudgeScores(
            design_layout=base,
            content_relevance=base,
            typography_color=base,
            graphics_images=base,
            innovation_originality=total - 4 * base,
        ),
        strengths="s",
        weaknesses="w",
    )


def test_accept_with_feedback_is_required_refinement_loop():
    """Refinement Loop (2026-05-20): ACCEPT must now carry feedback too
    (polish-step suggestions for the mandatory next refinement round)."""
    j = AestheticJudgement(
        decision=JudgeDecision.ACCEPT,
        best_candidate_id="cand_01",
        evaluations=[_ev("cand_01", 40)],
        feedback=AestheticFeedback(
            common_issues="overall good, minor polish only",
            suggestions=["bump price_1 +15%"],
            structured_suggestions=[
                Suggestion(
                    kind=SuggestionKind.RESIZE,
                    target_id="price_1",
                    metric="width",
                    op="increase_by",
                    value=24,
                )
            ],
        ),
    )
    assert j.decision == JudgeDecision.ACCEPT
    assert j.feedback is not None
    assert j.feedback.structured_suggestions[0].kind == SuggestionKind.RESIZE


def test_accept_without_feedback_is_now_invalid():
    """Refinement Loop (2026-05-20): feedback is required on both accept and
    reject; an accept with feedback=None must fail validation."""
    with pytest.raises(ValidationError):
        AestheticJudgement(
            decision=JudgeDecision.ACCEPT,
            best_candidate_id="cand_01",
            evaluations=[_ev("cand_01", 40)],
            feedback=None,
        )


def test_reject_must_have_feedback():
    with pytest.raises(ValidationError):
        AestheticJudgement(
            decision=JudgeDecision.REJECT,
            best_candidate_id="cand_01",
            evaluations=[_ev("cand_01", 25)],
            feedback=None,
        )


def test_reject_with_only_structured_suggestions_is_valid():
    """The schema does NOT require free-text `suggestions` to be non-empty;
    the prompt asks for both, but a structured-only feedback must still parse."""
    j = AestheticJudgement(
        decision=JudgeDecision.REJECT,
        best_candidate_id="cand_01",
        evaluations=[_ev("cand_01", 25)],
        feedback=AestheticFeedback(
            common_issues="x",
            suggestions=[],
            structured_suggestions=[
                Suggestion(
                    kind=SuggestionKind.TYPOGRAPHY,
                    target_id="headline_1",
                    metric="font_size",
                    op=">=",
                    value=72,
                ),
            ],
        ),
    )
    assert j.feedback is not None
    assert len(j.feedback.structured_suggestions) == 1
    assert j.feedback.structured_suggestions[0].kind == SuggestionKind.TYPOGRAPHY


# ============================================================
# ACCEPT_THRESHOLD calibration history:
#   2026-05-14: 80 -> 75 on the legacy 4-axis 0-25 scale (total 0-100).
#   2026-06-09: 75 -> 35 on the COLE 5-axis 1-10 scale (total 5-50). Step 30
#               migration: 35 = 5 * 7 = mean axis 7/10 = COLE "mediocre" anchor.
# ============================================================


def test_accept_threshold_is_35():
    """Pinned constant. Step 30 migration anchored ACCEPT_THRESHOLD to 35 on
    the COLE 5-axis 1-10 scale. If someone changes it, they must (a) update
    this test, (b) update schema.py's calibration history docstring, and (c)
    re-run verify_judge_corner.py to re-measure the Crello GT baseline on the
    new scale before committing."""
    assert ACCEPT_THRESHOLD == 35


def test_accept_threshold_inside_cole_scale_bounds():
    """The COLE 5-axis 1-10 schema constrains Evaluation.total to [5, 50].
    The threshold must sit strictly inside this range, above the 'all axes
    mediocre = 5*5 = 25' floor and below the perfect 50 ceiling, so the
    acceptance comparison is non-degenerate."""
    COLE_MEDIOCRE_FLOOR = 25  # 5 axes * 5/10 mediocre anchor
    COLE_MAX_TOTAL = 50  # 5 axes * 10/10 ceiling
    assert ACCEPT_THRESHOLD > COLE_MEDIOCRE_FLOOR
    assert ACCEPT_THRESHOLD < COLE_MAX_TOTAL


def test_accept_judgement_at_exactly_threshold_validates():
    """A candidate scoring exactly ACCEPT_THRESHOLD must be acceptable
    (the comparison in the prompt is `>=`).

    Refinement Loop (2026-05-20): the polish-step `feedback` payload must
    accompany every accept; we pass a minimal one to exercise the new contract.
    """
    j = AestheticJudgement(
        decision=JudgeDecision.ACCEPT,
        best_candidate_id="cand_01",
        evaluations=[_ev("cand_01", ACCEPT_THRESHOLD)],
        feedback=AestheticFeedback(
            common_issues="threshold polish",
            suggestions=["minor +5% width on cand_01"],
            structured_suggestions=[
                Suggestion(
                    kind=SuggestionKind.RESIZE,
                    target_id="cand_01",
                    metric="width",
                    op="increase_by",
                    value=8,
                )
            ],
        ),
    )
    assert j.decision == JudgeDecision.ACCEPT
    assert j.evaluations[0].total == ACCEPT_THRESHOLD


def test_reject_judgement_just_below_threshold_validates():
    """A candidate one point below the threshold must still produce a
    well-formed reject Judgement with feedback."""
    below = ACCEPT_THRESHOLD - 1
    j = AestheticJudgement(
        decision=JudgeDecision.REJECT,
        best_candidate_id="cand_01",
        evaluations=[_ev("cand_01", below)],
        feedback=AestheticFeedback(common_issues="x"),
    )
    assert j.decision == JudgeDecision.REJECT
    assert j.evaluations[0].total == below
    assert j.feedback is not None


# ============================================================
# Judge PROMPT_TEMPLATE metric whitelist (2026-05-14 leak fix)
# ============================================================


def test_judge_prompt_lists_metric_whitelist_and_forbids_right_bottom():
    """2026-05-14 live run produced metric=\"right\"/\"bottom\" suggestions which
    the Layout schema does not have, causing Generator to derive coordinates
    that collided in QC. The Judge prompt must explicitly call out the
    forbidden values so the LLM stops emitting them."""
    from metagpt.ext.agentlayout.actions.judge_aesthetic import PROMPT_TEMPLATE

    # The per-kind whitelist mentions the legal schema field names.
    assert "kind=resize" in PROMPT_TEMPLATE
    assert "kind=move" in PROMPT_TEMPLATE
    assert '"width"' in PROMPT_TEMPLATE
    assert '"left"' in PROMPT_TEMPLATE
    # The explicit "NEVER emit" guard on `right` / `bottom`.
    assert 'metric: "right"' in PROMPT_TEMPLATE
    assert 'metric: "bottom"' in PROMPT_TEMPLATE
    assert "NEVER emit" in PROMPT_TEMPLATE


def test_judge_prompt_explains_size_preference_area_math():
    """2026-05-14 live run #2: metric whitelist fix worked (no more bottom/
    right), but QC retry still failed 15/15 because Judge emitted only a
    `resize width>=600` suggestion. title_1 ended up 600x100 which is
    area_ratio=0.0625, below the `prominent` lower bound of 0.10. The Judge
    prompt must teach the LLM that prominent => area >= 0.10*canvas_area, so
    a width-only resize is insufficient — width AND height must be raised
    together."""
    from metagpt.ext.agentlayout.actions.judge_aesthetic import PROMPT_TEMPLATE

    # The area-math rule is spelled out so the LLM knows what gate it hits.
    assert "width * height >= 0.10" in PROMPT_TEMPLATE
    assert "prominent" in PROMPT_TEMPLATE
    # The fix instruction: emit BOTH width and height when enlarging.
    assert "BOTH a width AND a height" in PROMPT_TEMPLATE
    # The worked example numbers add up.
    assert "600 * 180 = 108000" in PROMPT_TEMPLATE
