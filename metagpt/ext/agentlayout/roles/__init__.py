"""Role wrappers for the AgentLayout pipeline.

Each Role is a thin metagpt.roles.Role subclass that wraps exactly one Action
from ``metagpt.ext.agentlayout.actions``. The Roles communicate via
``Message.instruct_content`` carrying the schema's Pydantic types
(DesignSpec, LayoutTree, CandidatesBatch, AestheticJudgement) -- no JSON
serialization between agents.

Wiring (forward path):
    UserRequirement -> AnalystRole         (emits AnalyzeBrief msg with DesignSpec)
    AnalyzeBrief    -> AssetPlannerRole    (emits PlanAssets    msg with LayoutTree)
    PlanAssets      -> LayoutGeneratorRole (emits GenerateLayout msg with CandidatesBatch)
    GenerateLayout  -> AestheticJudgeRole  (emits JudgeAesthetic msg with AestheticJudgement)

Wiring (feedback path on REJECT):
    JudgeAesthetic  -> IterationStateRole  (owns IterationState; routes via cause_by)
        |-- iteration<=GENERATOR_FEEDBACK_ROUNDS  -> RetryGeneration -> LayoutGeneratorRole
        \\-- iteration> GENERATOR_FEEDBACK_ROUNDS -> RetryAnalyst    -> AnalystRole
                                                     (then re-runs PlanAssets / Generate / Judge)

This is the Role-driven counterpart of the orchestrator-driven
``LayoutPipeline`` in ``pipeline.py``. Both styles now produce the same
outputs (forward + feedback loop); the Role flow exists so the system
integrates with MetaGPT's Team / Environment framework (thesis chapter 6.x).
"""
from metagpt.ext.agentlayout.roles.aesthetic_judge import AestheticJudgeRole
from metagpt.ext.agentlayout.roles.analyst import AnalystRole
from metagpt.ext.agentlayout.roles.asset_planner import AssetPlannerRole
from metagpt.ext.agentlayout.roles.iteration_state import (
    IterationStateRole,
    IterationStop,
    RetryAnalyst,
    RetryGeneration,
    RetryPayload,
)
from metagpt.ext.agentlayout.roles.layout_generator import LayoutGeneratorRole

__all__ = [
    "AnalystRole",
    "AssetPlannerRole",
    "LayoutGeneratorRole",
    "AestheticJudgeRole",
    "IterationStateRole",
    "IterationStop",
    "RetryAnalyst",
    "RetryGeneration",
    "RetryPayload",
]
