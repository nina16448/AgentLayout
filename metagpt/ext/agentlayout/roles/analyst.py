"""AnalystRole -- Role wrapper around the AnalyzeBrief Action.

Triggered by either:

  * The kickoff ``UserRequirement`` Message carrying a ``LayoutBrief`` payload
    (user_brief + asset_list) -- the initial pass.
  * A ``RetryAnalyst`` Message published by ``IterationStateRole`` carrying a
    ``RetryPayload`` (feedback + iteration) -- a feedback-driven re-run that
    rebuilds the DesignSpec from scratch with the Aesthetic Judge feedback
    folded into the prompt.

Both paths produce the same downstream artifact: a ``Message`` with
``cause_by=AnalyzeBrief`` and ``instruct_content=DesignSpec`` enriched by the
``AssetAnalyzer`` -- so AssetPlannerRole and LayoutGeneratorRole observe the
same shape regardless of whether this is round 1 or a feedback-retry round.
"""
from __future__ import annotations

from typing import Optional

from metagpt.actions import UserRequirement
from metagpt.logs import logger
from metagpt.roles import Role
from metagpt.schema import Message
from metagpt.utils.common import any_to_str

from metagpt.ext.agentlayout.actions.analyze_brief import AnalyzeBrief
from metagpt.ext.agentlayout.roles.iteration_state import RetryAnalyst, RetryPayload
from metagpt.ext.agentlayout.schema import AestheticFeedback
from metagpt.ext.agentlayout.tools.asset_analyzer import AssetAnalyzer


class AnalystRole(Role):
    name: str = "Analyst"
    profile: str = "Layout Analyst"
    goal: str = (
        "Parse a user's design brief and asset list into a structured DesignSpec, "
        "then enrich every element with importance and semantic_relevance."
    )
    constraints: str = (
        "Output must conform to metagpt.ext.agentlayout.schema.DesignSpec; "
        "all 12 SemanticType enum values are recognised."
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_actions([AnalyzeBrief])
        self._watch([UserRequirement, RetryAnalyst])
        self._asset_analyzer = AssetAnalyzer()

    def _find_layout_brief(self):
        """Walk env-wide history backwards to find the original LayoutBrief.

        On a feedback-retry pass (``cause_by=RetryAnalyst``) the latest message
        is a ``RetryPayload`` -- the original ``user_brief`` / ``asset_list``
        live in the kickoff UserRequirement Message that this Role consumed
        on round 1.
        """
        cause = any_to_str(UserRequirement)
        for m in reversed(self.rc.env.history.get()):
            payload = m.instruct_content
            if (
                m.cause_by == cause
                and payload is not None
                and hasattr(payload, "user_brief")
            ):
                return payload
        raise ValueError(
            "AnalystRole could not locate the original LayoutBrief in env history "
            "while handling a RetryAnalyst Message."
        )

    async def _act(self) -> Message:
        # Prefer rc.news (this tick's freshly-observed messages); fall back to
        # rc.history[-1] for the very first invocation when news is empty.
        if self.rc.news:
            msg = self.rc.news[-1]
        else:
            msg = self.rc.history[-1]
        payload = msg.instruct_content
        feedback: Optional[AestheticFeedback] = None

        is_retry = msg.cause_by == any_to_str(RetryAnalyst)
        if is_retry:
            if not isinstance(payload, RetryPayload):
                raise ValueError(
                    "AnalystRole expected RetryPayload on a RetryAnalyst Message. "
                    f"Got: {type(payload).__name__ if payload else 'None'}"
                )
            feedback = payload.feedback
            brief = self._find_layout_brief()
            user_brief = brief.user_brief
            asset_list = brief.asset_list
            logger.info(
                f"AnalystRole: retry pass (iteration={payload.iteration}); "
                "rebuilding DesignSpec with Aesthetic feedback."
            )
        else:
            if payload is None or not hasattr(payload, "user_brief"):
                raise ValueError(
                    "AnalystRole expected the kickoff Message.instruct_content to be a "
                    "LayoutBrief with user_brief + asset_list. Got: "
                    f"{type(payload).__name__ if payload else 'None'}"
                )
            user_brief = payload.user_brief
            asset_list = payload.asset_list

        analyze: AnalyzeBrief = self.actions[0]
        spec = await analyze.run(
            user_brief=user_brief, asset_list=asset_list, feedback=feedback
        )
        self._asset_analyzer.run(spec)
        spec.assert_enriched()

        logger.info(
            f"AnalystRole produced DesignSpec "
            f"(canvas={spec.canvas.width}x{spec.canvas.height}, "
            f"elements={len(spec.elements)}, retry={is_retry})"
        )
        return Message(
            content=(
                f"DesignSpec ready: {spec.canvas.width}x{spec.canvas.height}, "
                f"{len(spec.elements)} elements."
            ),
            instruct_content=spec,
            role=self.profile,
            cause_by=AnalyzeBrief,
        )
