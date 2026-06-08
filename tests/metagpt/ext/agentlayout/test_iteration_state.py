"""Pytest suite for IterationStateRole — fully offline, no LLM call.

Ported from:
  layout_agent/output/verify_iteration_corner.py     (3 corner cases, 11 inv)
  layout_agent/output/verify_roles_mvp.py Role 5     (1 MVP case, 6 inv)

Total: 17 invariants exercised across 4 pytest functions. Cost = $0.

Run only this file (recommended because tests/metagpt/ext is on `norecursedirs`):

    pytest tests/metagpt/ext/agentlayout/test_iteration_state.py -v

Or the whole agentlayout test dir:

    pytest tests/metagpt/ext/agentlayout/ -v
"""
from __future__ import annotations

import pytest

from metagpt.schema import Message
from metagpt.utils.common import any_to_str

from metagpt.ext.agentlayout.actions.judge_aesthetic import JudgeAesthetic
from metagpt.ext.agentlayout.roles.iteration_state import (
    IterationStateRole,
    IterationStop,
    RetryAnalyst,
    RetryGeneration,
    RetryPayload,
)
from metagpt.ext.agentlayout.schema import (
    AestheticFeedback,
    AestheticJudgement,
    Evaluation,
    GENERATOR_FEEDBACK_ROUNDS,
    JudgeDecision,
    JudgeScores,
)


# ============================================================
# Shared fixture helpers
# ============================================================


def _judgement(decision: JudgeDecision, cand_id: str = "r0_cand_01") -> AestheticJudgement:
    """Schema invariant: total == sum(scores). On the COLE 5-axis 1-10 scale
    (Step 30+): REJECT 5x6=30 (well below ACCEPT_THRESHOLD=35), ACCEPT 5x8=40.

    Refinement Loop (2026-05-20): ACCEPT must also carry feedback (polish-step
    suggestions consumed by the mandatory next refinement round).
    """
    if decision == JudgeDecision.ACCEPT:
        scores = JudgeScores(
            design_layout=8,
            content_relevance=8,
            typography_color=8,
            graphics_images=8,
            innovation_originality=8,
        )
        total = 40
        feedback = AestheticFeedback(
            common_issues="overall good; minor polish",
            suggestions=["nudge headline_1 +5% width"],
        )
    else:
        scores = JudgeScores(
            design_layout=6,
            content_relevance=6,
            typography_color=6,
            graphics_images=6,
            innovation_originality=6,
        )
        total = 30
        feedback = AestheticFeedback(
            common_issues="title too small",
            suggestions=[
                "increase title height to >= 40% canvas height",
                "move logo_1 to top-right",
            ],
        )
    return AestheticJudgement(
        decision=decision,
        best_candidate_id=cand_id,
        evaluations=[
            Evaluation(
                candidate_id=cand_id,
                total=total,
                scores=scores,
                strengths="ok",
                weaknesses="none",
            )
        ],
        feedback=feedback,
    )


async def _feed(role: IterationStateRole, j: AestheticJudgement) -> Message:
    """Add a Judge message to memory then trigger _act(). rc.history is a property
    over rc.memory — must use memory.add, not history.append."""
    msg = Message(
        content="judge",
        instruct_content=j,
        role="Aesthetic Judge",
        cause_by=JudgeAesthetic,
    )
    role.rc.memory.add(msg)
    role.rc.news = [msg]
    return await role._act()


def _cause(m: Message) -> str:
    return m.cause_by.rsplit(".", 1)[-1]


# ============================================================
# Corner case 1 — max_total_rounds boundary
# ============================================================


@pytest.mark.asyncio
async def test_boundary_max_rounds_emits_iterationstop_at_third_reject():
    """max=2, 3 rejects: chain = [RetryGen, RetryGen, IterationStop]."""
    role = IterationStateRole(max_total_rounds=2)

    out1 = await _feed(role, _judgement(JudgeDecision.REJECT))
    out2 = await _feed(role, _judgement(JudgeDecision.REJECT))
    out3 = await _feed(role, _judgement(JudgeDecision.REJECT))

    chain = [_cause(out1), _cause(out2), _cause(out3)]

    assert out1.cause_by == any_to_str(RetryGeneration), f"got {chain[0]}"
    assert out2.cause_by == any_to_str(RetryGeneration), f"got {chain[1]}"
    assert out3.cause_by == any_to_str(IterationStop), f"got {chain[2]}"
    assert out3.instruct_content is None
    assert role.state.iteration == 3
    assert GENERATOR_FEEDBACK_ROUNDS == 2


# ============================================================
# Corner case 2 — duplicate judgement increments (Role does NOT dedupe)
# ============================================================


@pytest.mark.asyncio
async def test_duplicate_judgement_increments_counter_twice():
    """Feeding the same judgement instance twice -> counter += 2.

    Role layer is intentionally naive; Team driver owns dedupe responsibility.
    """
    role = IterationStateRole(max_total_rounds=5)
    j = _judgement(JudgeDecision.REJECT)

    out1 = await _feed(role, j)
    out2 = await _feed(role, j)

    assert out1.cause_by != any_to_str(IterationStop)
    assert out2.cause_by != any_to_str(IterationStop)
    assert role.state.iteration == 2
    assert isinstance(out1.instruct_content, RetryPayload)
    assert isinstance(out2.instruct_content, RetryPayload)


# ============================================================
# Corner case 3 — Refinement Loop: ACCEPT triggers a mandatory refinement
# ============================================================


@pytest.mark.asyncio
async def test_accept_routes_to_refinement_until_two_consecutive():
    """Refinement Loop (2026-05-20): every verdict increments iteration and
    routes back to LayoutGenerator until either consecutive_accepts >= 2 or
    iteration > max_total_rounds.

    Scenario: REJECT, ACCEPT, REJECT, ACCEPT, ACCEPT -> iteration [0..5];
    chain ends with IterationStop only after the second consecutive accept.
    """
    role = IterationStateRole(max_total_rounds=10)
    snapshots = [role.state.iteration]
    consecutive_snaps = [role.state.consecutive_accepts]
    chain: list = []

    for decision in (
        JudgeDecision.REJECT,   # iter 1, ca 0 -> RetryGeneration
        JudgeDecision.ACCEPT,   # iter 2, ca 1 -> RetryGeneration (refinement)
        JudgeDecision.REJECT,   # iter 3, ca 0 -> RetryGeneration
        JudgeDecision.ACCEPT,   # iter 4, ca 1 -> RetryGeneration (refinement)
        JudgeDecision.ACCEPT,   # iter 5, ca 2 -> IterationStop (converged)
    ):
        msg = await _feed(role, _judgement(decision))
        chain.append(_cause(msg))
        snapshots.append(role.state.iteration)
        consecutive_snaps.append(role.state.consecutive_accepts)

    assert snapshots == [0, 1, 2, 3, 4, 5], f"got {snapshots}"
    assert consecutive_snaps == [0, 0, 1, 0, 1, 2], f"got {consecutive_snaps}"
    assert chain == [
        "RetryGeneration",
        "RetryGeneration",
        "RetryGeneration",
        "RetryGeneration",
        "IterationStop",
    ], f"got {chain}"


@pytest.mark.asyncio
async def test_two_consecutive_accepts_terminate_immediately():
    """Refinement Loop convergence: 2 accepts in a row -> IterationStop on
    the second verdict (mandatory polish round confirmed the layout)."""
    role = IterationStateRole(max_total_rounds=5)

    out1 = await _feed(role, _judgement(JudgeDecision.ACCEPT))
    out2 = await _feed(role, _judgement(JudgeDecision.ACCEPT))

    assert out1.cause_by == any_to_str(RetryGeneration), f"got {_cause(out1)}"
    assert out2.cause_by == any_to_str(IterationStop), f"got {_cause(out2)}"
    assert role.state.iteration == 2
    assert role.state.consecutive_accepts == 2


# ============================================================
# MVP scenario (verify_roles_mvp.py Role 5, 6 invariants in one test)
# ============================================================


@pytest.mark.asyncio
async def test_mvp_3rejects_then_accept_routes_correctly():
    """3 rejects (Gen, Gen, Analyst per GENERATOR_FEEDBACK_ROUNDS=2) then ACCEPT.

    Refinement Loop (2026-05-20): the ACCEPT no longer terminates the loop —
    it instead emits a RetryGeneration carrying prev_best_layout so the
    Generator runs a mandatory polish pass.
    """
    role = IterationStateRole(max_total_rounds=5)

    out1 = await _feed(role, _judgement(JudgeDecision.REJECT))
    out2 = await _feed(role, _judgement(JudgeDecision.REJECT))
    out3 = await _feed(role, _judgement(JudgeDecision.REJECT))
    out4 = await _feed(role, _judgement(JudgeDecision.ACCEPT))

    assert out1.cause_by == any_to_str(RetryGeneration)
    assert out2.cause_by == any_to_str(RetryGeneration)
    assert out3.cause_by == any_to_str(RetryAnalyst), f"got {_cause(out3)}"
    # New Refinement Loop: accept routes to Generator (not IterationStop) for
    # the mandatory polish round.
    assert out4.cause_by == any_to_str(RetryGeneration), f"got {_cause(out4)}"
    assert all(
        isinstance(m.instruct_content, RetryPayload)
        for m in (out1, out2, out3, out4)
    )
    assert role.state.iteration == 4
    assert role.state.consecutive_accepts == 1
