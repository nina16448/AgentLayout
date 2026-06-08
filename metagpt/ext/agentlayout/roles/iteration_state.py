"""IterationStateRole -- Role-layer feedback router for Aesthetic Judge REJECTs.

This Role closes the only known MVP gap in the Role / Team flow: when the
Aesthetic Judge returns ``REJECT``, this Role plays the role that
``LayoutPipeline`` plays in the orchestrator-style driver -- it owns the
``IterationState`` bookkeeping and routes the feedback to either the
``LayoutGeneratorRole`` (for the first ``GENERATOR_FEEDBACK_ROUNDS`` rejects)
or back to the ``AnalystRole`` (for subsequent rejects, which rebuild the
DesignSpec from scratch).

Routing is performed via the ``cause_by`` channel rather than ``send_to``,
mirroring how MetaGPT itself dispatches Messages: we publish a Message with
``cause_by=RetryGeneration`` (or ``RetryAnalyst``) and the upstream Role's
``_watch`` list picks it up. ``RetryGeneration`` and ``RetryAnalyst`` are
**sentinel Actions** -- empty ``Action`` subclasses that carry no LLM logic;
they exist purely to mark intent, the same pattern MetaGPT uses for the
built-in ``UserRequirement`` Action.

Termination conditions:
  * Latest judgement decision is ACCEPT -> emit a no-op Message, stop.
  * ``state.iteration`` reached ``max_total_rounds`` -- emit a no-op Message,
    the Team's tick loop will run out and the run ends without an accept (the
    same exhaustion semantics as ``LayoutPipeline``).
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from pydantic import BaseModel, Field

from metagpt.actions import Action
from metagpt.logs import logger
from metagpt.roles import Role
from metagpt.schema import Message

from metagpt.ext.agentlayout.actions.judge_aesthetic import JudgeAesthetic
from metagpt.ext.agentlayout.schema import (
    AestheticFeedback,
    AestheticJudgement,
    FeedbackTarget,
    IterationState,
    JudgeDecision,
)


# Refinement Loop termination: pipeline stops when the Judge has accepted
# this many times in a row (initial accept + one post-refinement accept).
ACCEPT_CONSECUTIVE_STOP: int = 2


# ============================================================
# Sentinel Actions for re-trigger routing
# ============================================================


class RetryAnalyst(Action):
    """Sentinel cause_by tag: re-trigger AnalystRole with Aesthetic feedback."""


class RetryGeneration(Action):
    """Sentinel cause_by tag: re-trigger LayoutGeneratorRole with Aesthetic feedback."""


class IterationStop(Action):
    """Sentinel cause_by tag: ACCEPT or max-rounds no-op terminator.

    No Role in the AgentLayout team watches this Action. We attach it to the
    IterationStateRole's no-op return Messages so the framework's default
    cause_by (which falls back to UserRequirement, see metagpt.schema.Message
    line 269) does not accidentally re-trigger AnalystRole.
    """


# ============================================================
# Pydantic payload for the retry messages
# ============================================================


class RetryPayload(BaseModel):
    """Typed payload attached to retry Messages' ``instruct_content``.

    Carries the Aesthetic-Judge feedback that AnalystRole / LayoutGeneratorRole
    must consume on the retry pass, plus the iteration counter for tracing.

    Refinement Loop (2026-05-20): when ``target`` is LAYOUT_GENERATOR, also
    carries the previous best candidate's bbox dict and four sub-scores so the
    Generator can run in refinement (anchored-edit) mode instead of cold-start.
    """

    feedback: AestheticFeedback
    iteration: int = Field(..., ge=1)
    target: FeedbackTarget
    prev_best_layout: Optional[Dict[str, Tuple[float, float, float, float]]] = Field(
        default=None,
        description=(
            "Previous round's best candidate bbox dict, populated by the Aesthetic "
            "Judge via JudgeAesthetic._attach_best_candidate_layout(). Consumed "
            "by LayoutGeneratorRole / GenerateLayout in refinement mode. None "
            "when target=ANALYST or in legacy reject-only routing."
        ),
    )
    prev_best_subscores: Optional[Dict[str, int]] = Field(
        default=None,
        description=(
            "Previous round's best candidate COLE 5-axis subscores "
            "{design_layout, content_relevance, typography_color, "
            "graphics_images, innovation_originality} in 1-10. Generator uses "
            "the lowest-scoring axis to prioritise its refinement edits. "
            "(Step 30 migration 2026-06-09: was 4 dims 0-25 pre-Step 30.)"
        ),
    )


# ============================================================
# IterationStateRole
# ============================================================


class IterationStateRole(Role):
    """Owns ``IterationState`` and routes every Aesthetic-Judge verdict.

    Watches ``JudgeAesthetic``. On every judgement Message (Refinement Loop):

    * decision = ACCEPT and consecutive_accepts < ACCEPT_CONSECUTIVE_STOP and
      iteration < max_total_rounds -> route to LayoutGenerator with
      prev_best_layout so it runs a mandatory refinement pass.
    * decision = ACCEPT and (consecutive_accepts >= ACCEPT_CONSECUTIVE_STOP or
      iteration >= max_total_rounds) -> emit IterationStop, pipeline ends.
    * decision = REJECT and iteration < max_total_rounds -> route to
      LayoutGenerator (first N rounds) or Analyst (after N) via the existing
      next_target() logic, carrying prev_best_layout when targeting Generator.
    * decision = REJECT and iteration >= max_total_rounds -> emit IterationStop.
    """

    name: str = "IterationState"
    profile: str = "Iteration Router"
    goal: str = (
        "Bookkeep AestheticJudgement decisions across rounds and route every "
        "verdict (accept or reject) to either the Layout Generator (refinement "
        "pass) or the Analyst (after the generator-feedback budget is spent), "
        "mirroring LayoutPipeline.run's Refinement Loop."
    )
    constraints: str = (
        "Refinement Loop: emits a RetryGeneration / RetryAnalyst Message on "
        "every verdict (accept or reject) until either consecutive_accepts "
        "reaches ACCEPT_CONSECUTIVE_STOP or iteration reaches max_total_rounds, "
        "at which point an IterationStop Message terminates the loop."
    )

    # Default mirrors PipelineConfig.max_total_rounds in pipeline.py.
    max_total_rounds: int = 5

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_actions([])  # routing-only; no LLM action to run
        self._watch([JudgeAesthetic])
        self._state = IterationState()

    @property
    def state(self) -> IterationState:
        """Public read-only handle for tests / introspection."""
        return self._state

    async def _act(self) -> Message:
        latest = self.rc.history[-1]
        judgement = latest.instruct_content
        if not isinstance(judgement, AestheticJudgement):
            raise ValueError(
                "IterationStateRole expected AestheticJudgement as instruct_content "
                f"(from JudgeAesthetic). Got: {type(judgement).__name__}"
            )

        is_accept = judgement.decision == JudgeDecision.ACCEPT

        # Bookkeeping. Refinement Loop counts every verdict, not just rejects.
        self._state.iteration += 1
        self._state.last_feedback = judgement.feedback
        if is_accept:
            self._state.consecutive_accepts += 1
        else:
            self._state.consecutive_accepts = 0
            self._state.reject_count += 1

        # Termination check #1: refinement actually converged (two accepts in a row).
        if is_accept and self._state.consecutive_accepts >= ACCEPT_CONSECUTIVE_STOP:
            logger.info(
                f"IterationStateRole: ACCEPT received "
                f"(iteration={self._state.iteration}, "
                f"consecutive_accepts={self._state.consecutive_accepts}, "
                f"best={judgement.best_candidate_id}). Refinement converged."
            )
            return Message(
                content=(
                    f"Accepted at iteration {self._state.iteration} after "
                    f"{self._state.consecutive_accepts} consecutive accepts."
                ),
                role=self.profile,
                cause_by=IterationStop,
            )

        # Termination check #2: hard cap on total rounds.
        if self._state.iteration > self.max_total_rounds:
            logger.warning(
                f"IterationStateRole: max_total_rounds={self.max_total_rounds} "
                f"exhausted at iteration {self._state.iteration}. Stopping "
                f"(last decision={judgement.decision.value})."
            )
            return Message(
                content=(
                    f"Max rounds ({self.max_total_rounds}) exhausted; "
                    f"stopping after {self._state.iteration} verdict(s)."
                ),
                role=self.profile,
                cause_by=IterationStop,
            )

        # Route the verdict. ACCEPT always routes to LAYOUT_GENERATOR (mandatory
        # refinement pass). REJECT uses next_target() (Generator early, Analyst
        # after N).
        if is_accept:
            target = FeedbackTarget.LAYOUT_GENERATOR
        else:
            target = self._state.next_target()
        self._state.feedback_target = target
        feedback: AestheticFeedback = judgement.feedback

        if target == FeedbackTarget.LAYOUT_GENERATOR:
            cause_cls: type[Action] = RetryGeneration
            target_name = "LayoutGenerator"
            # Refinement Loop: carry prev_best_layout + subscores so the
            # Generator runs in anchored-edit mode.
            prev_layout = judgement.best_candidate_layout
            prev_scores = self._extract_best_subscores(judgement)
        else:
            cause_cls = RetryAnalyst
            target_name = "Analyst"
            # Analyst re-specs from scratch; bbox anchoring is not meaningful.
            prev_layout = None
            prev_scores = None

        retry_payload = RetryPayload(
            feedback=feedback,
            iteration=self._state.iteration,
            target=target,
            prev_best_layout=prev_layout,
            prev_best_subscores=prev_scores,
        )

        logger.info(
            f"IterationStateRole: {judgement.decision.value.upper()} "
            f"iteration={self._state.iteration} -> routing to {target_name} "
            f"(cause_by={cause_cls.__name__}, "
            f"consecutive_accepts={self._state.consecutive_accepts})."
        )
        return Message(
            content=(
                f"{judgement.decision.value.capitalize()} "
                f"iteration={self._state.iteration}; refine via {target_name}."
            ),
            instruct_content=retry_payload,
            role=self.profile,
            cause_by=cause_cls,
            send_to={target_name},
        )

    @staticmethod
    def _extract_best_subscores(
        judgement: AestheticJudgement,
    ) -> Optional[Dict[str, int]]:
        """Find the best candidate's COLE 5-axis subscores from the evaluations list."""
        for ev in judgement.evaluations:
            if ev.candidate_id == judgement.best_candidate_id:
                return {
                    "design_layout": ev.scores.design_layout,
                    "content_relevance": ev.scores.content_relevance,
                    "typography_color": ev.scores.typography_color,
                    "graphics_images": ev.scores.graphics_images,
                    "innovation_originality": ev.scores.innovation_originality,
                }
        return None
