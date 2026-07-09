"""Team / Environment driver for the Role-based AgentLayout flow.

This module provides the entry points that wire AgentLayout into MetaGPT's
``Team`` + ``Environment`` framework, complementing the orchestrator-style
``LayoutPipeline`` in ``pipeline.py``.

Two ways to invoke the system are now supported:

    # (a) orchestrator style (pipeline.py)
    from metagpt.ext.agentlayout.pipeline import LayoutPipeline
    result = await LayoutPipeline().run(user_brief=..., asset_list=[...])

    # (b) Role / Team style (this module) -- thesis chapter 6.x integration
    from metagpt.ext.agentlayout.team import run_team
    team = await run_team(user_brief=..., asset_list=[...])
    final_msg = team.env.history[-1]   # carries AestheticJudgement

The Role flow now mirrors LayoutPipeline.run end-to-end: forward pass
(Analyst -> AssetPlanner -> LayoutGenerator -> AestheticJudge) plus REJECT
feedback routing via IterationStateRole, which re-publishes the feedback
to either LayoutGeneratorRole or AnalystRole through sentinel cause_by tags
(RetryGeneration / RetryAnalyst).

``n_round`` defaults to 16 to leave headroom for at most ``max_total_rounds``
reject cycles -- each Generator-target cycle costs ~3 ticks (Generator ->
Judge -> IterationState), each Analyst-target cycle costs ~5 ticks (Analyst
-> AssetPlanner -> Generator -> Judge -> IterationState).
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from metagpt.actions import UserRequirement
from metagpt.environment.base_env import Environment
from metagpt.schema import Message
from metagpt.team import Team

from metagpt.ext.agentlayout.actions.analyze_brief import AssetInput
from metagpt.ext.agentlayout.roles import (
    AestheticJudgeRole,
    AnalystRole,
    AssetPlannerRole,
    CompositionDirectorRole,
    IterationStateRole,
    LayoutGeneratorRole,
)


# ============================================================
# Payload contract for the kickoff UserRequirement Message
# ============================================================


class LayoutBrief(BaseModel):
    """Pydantic payload carried by the kickoff UserRequirement Message.

    AnalystRole reads this from ``Message.instruct_content`` and unpacks it
    into the ``AnalyzeBrief.run(user_brief=..., asset_list=...)`` call.
    Defining a typed model here means the kickoff contract is enforced by
    Pydantic validation, just like every downstream contract in the schema.
    """

    user_brief: str = Field(..., min_length=1)
    asset_list: List[AssetInput] = Field(default_factory=list)


# ============================================================
# Team factory + driver
# ============================================================


def build_team(
    *,
    investment: float = 3.0,
    n_round: int = 16,
    env: Optional[Environment] = None,
    max_total_rounds: int = 5,
) -> Team:
    """Construct a Team with all five AgentLayout Roles already hired.

    ``investment`` is the LLM cost cap (USD). ``n_round`` here is only used as
    the default number of dispatcher ticks; ``team.run`` accepts an override.
    ``max_total_rounds`` caps the number of REJECT cycles IterationStateRole
    will route -- mirrors PipelineConfig.max_total_rounds.
    """
    env = env or Environment()
    team = Team(env=env, investment=investment)
    team.hire(
        [
            AnalystRole(),
            AssetPlannerRole(),
            # "先想再畫" refactor: the CompositionDirector imagines concepts that
            # the CoordinateMapper (LayoutGeneratorRole) turns into pixels.
            CompositionDirectorRole(),
            LayoutGeneratorRole(),
            AestheticJudgeRole(),
            IterationStateRole(max_total_rounds=max_total_rounds),
        ]
    )
    return team


async def run_team(
    *,
    user_brief: str,
    asset_list: List[AssetInput],
    n_round: int = 16,
    investment: float = 3.0,
    max_total_rounds: int = 5,
) -> Team:
    """Build the team, publish the kickoff message, and drive the dispatcher.

    Returns the Team after ``team.run`` finishes. The accepted (or rejected)
    AestheticJudgement lives in ``team.env.history`` -- callers can scan
    backwards for a ``cause_by == any_to_str(JudgeAesthetic)`` message.
    """
    team = build_team(
        investment=investment,
        n_round=n_round,
        max_total_rounds=max_total_rounds,
    )

    brief = LayoutBrief(user_brief=user_brief, asset_list=asset_list)
    kickoff = Message(
        content=user_brief,
        instruct_content=brief,
        role="User",
        cause_by=UserRequirement,
    )
    team.env.publish_message(kickoff)

    await team.run(n_round=n_round)
    return team
