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
    """

    feedback: AestheticFeedback
    iteration: int = Field(..., ge=1)
    target: FeedbackTarget


# ============================================================
# IterationStateRole
# ============================================================


class IterationStateRole(Role):
    """Owns ``IterationState`` and routes Aesthetic-Judge REJECTs.

    Watches ``JudgeAesthetic``. On every judgement Message:

    * If decision = ACCEPT -> log and stop.
    * If decision = REJECT -> increment iteration counter, decide target via
      ``IterationState.next_target()``, publish a Message tagged with the
      appropriate sentinel Action so the target Role re-triggers.

    Hard cap on retries is governed by ``max_total_rounds`` to mirror
    ``PipelineConfig.max_total_rounds`` semantics in the orchestrator path.
    """

    name: str = "IterationState"
    profile: str = "Iteration Router"
    goal: str = (
        "Bookkeep AestheticJudgement decisions across rounds and route REJECT "
        "feedback to either the Layout Generator (early rounds) or the Analyst "
        "(after the generator-feedback budget is spent), mirroring "
        "LayoutPipeline.run."
    )
    constraints: str = (
        "Emits a no-op Message on ACCEPT (no cause_by re-trigger). "
        "On REJECT emits exactly one Message tagged with cause_by=RetryAnalyst "
        "or RetryGeneration. Stops after max_total_rounds rejects."
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

        if judgement.decision == JudgeDecision.ACCEPT:
            logger.info(
                f"IterationStateRole: ACCEPT received "
                f"(iteration={self._state.iteration}, "
                f"best={judgement.best_candidate_id}). No re-trigger."
            )
            return Message(
                content=f"Accepted at iteration {self._state.iteration}.",
                role=self.profile,
                cause_by=IterationStop,
            )

        # REJECT path -- mirror pipeline.py:215-250.
        self._state.iteration += 1
        self._state.last_feedback = judgement.feedback
        self._state.feedback_target = self._state.next_target()
        target = self._state.feedback_target
        feedback: AestheticFeedback = judgement.feedback  # type: ignore[assignment]

        if self._state.iteration > self.max_total_rounds:
            logger.warning(
                f"IterationStateRole: max_total_rounds={self.max_total_rounds} "
                f"exhausted at iteration {self._state.iteration}. Stopping."
            )
            return Message(
                content=(
                    f"Max rounds ({self.max_total_rounds}) exhausted; "
                    f"stopping after {self._state.iteration} reject(s)."
                ),
                role=self.profile,
                cause_by=IterationStop,
            )

        retry_payload = RetryPayload(
            feedback=feedback,
            iteration=self._state.iteration,
            target=target,
        )

        if target == FeedbackTarget.LAYOUT_GENERATOR:
            cause_cls: type[Action] = RetryGeneration
            target_name = "LayoutGenerator"
        else:
            cause_cls = RetryAnalyst
            target_name = "Analyst"

        logger.info(
            f"IterationStateRole: REJECT iteration={self._state.iteration} -> "
            f"routing to {target_name} (cause_by={cause_cls.__name__})."
        )
        return Message(
            content=(
                f"Reject iteration={self._state.iteration}; "
                f"retry via {target_name}."
            ),
            instruct_content=retry_payload,
            role=self.profile,
            cause_by=cause_cls,
            send_to={target_name},
        )
