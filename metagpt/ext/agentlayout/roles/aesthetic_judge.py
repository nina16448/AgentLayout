"""AestheticJudgeRole -- Role wrapper around the JudgeAesthetic Action.

Watches GenerateLayout output. Pulls CandidatesBatch (latest), DesignSpec,
and LayoutTree from history, then runs the multimodal JudgeAesthetic Action.
The MVP emits the judgement and stops the Role flow regardless of
ACCEPT / REJECT -- feedback routing on REJECT remains in ``LayoutPipeline``
and is intentionally NOT replicated at the Role layer in this milestone
(future work: a feedback-routing Role that re-emits to AnalystRole or
LayoutGeneratorRole based on IterationState).
"""
from __future__ import annotations

from metagpt.logs import logger
from metagpt.roles import Role
from metagpt.schema import Message

from metagpt.ext.agentlayout.actions.analyze_brief import AnalyzeBrief
from metagpt.ext.agentlayout.actions.generate_layout import GenerateLayout
from metagpt.ext.agentlayout.actions.judge_aesthetic import JudgeAesthetic
from metagpt.ext.agentlayout.actions.plan_assets import PlanAssets
from metagpt.ext.agentlayout.pipeline import default_white_background
from metagpt.ext.agentlayout.schema import (
    CandidatesBatch,
    DesignSpec,
    JudgeDecision,
    LayoutTree,
)
from metagpt.utils.common import any_to_str


class AestheticJudgeRole(Role):
    name: str = "AestheticJudge"
    profile: str = "Aesthetic Judge"
    goal: str = (
        "Evaluate every candidate visually with a multimodal LLM. "
        "Pick the best candidate; ACCEPT if total >= 75, REJECT with feedback otherwise."
    )
    constraints: str = (
        "Output must conform to metagpt.ext.agentlayout.schema.AestheticJudgement; "
        "feedback is required iff decision='reject'."
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_actions([JudgeAesthetic])
        self._watch([GenerateLayout])

    def _find_by_cause(self, cause_cls, expected_type):
        """Walk env-wide history backwards for a Message with given cause_by + payload type.

        We use ``self.rc.env.history`` (not ``self.rc.history``) because this
        Role only ``_watch``es GenerateLayout -- DesignSpec and LayoutTree
        messages were broadcast env-wide but not observed by this Role.
        """
        cause = any_to_str(cause_cls)
        for m in reversed(self.rc.env.history.get()):
            if m.cause_by == cause and isinstance(m.instruct_content, expected_type):
                return m.instruct_content
        raise ValueError(
            f"AestheticJudgeRole could not find a {cause_cls.__name__} message with "
            f"a {expected_type.__name__} payload in env history."
        )

    async def _act(self) -> Message:
        if self.rc.news:
            latest = self.rc.news[-1]
        else:
            latest = self.rc.history[-1]
        candidates_batch = latest.instruct_content
        if not isinstance(candidates_batch, CandidatesBatch):
            raise ValueError(
                "AestheticJudgeRole expected CandidatesBatch as instruct_content "
                f"(from GenerateLayout). Got: {type(candidates_batch).__name__}"
            )
        candidates = candidates_batch.candidates

        spec = self._find_by_cause(AnalyzeBrief, DesignSpec)
        tree = self._find_by_cause(PlanAssets, LayoutTree)
        bg = default_white_background(spec.canvas)

        judge: JudgeAesthetic = self.actions[0]
        judgement = await judge.run(candidates=candidates, spec=spec, tree=tree, bg=bg)

        accepted = judgement.decision == JudgeDecision.ACCEPT
        logger.info(
            f"AestheticJudgeRole verdict: {'ACCEPT' if accepted else 'REJECT'} "
            f"(best={judgement.best_candidate_id})"
        )
        return Message(
            content=(
                f"Judgement: {judgement.decision.value}, "
                f"best={judgement.best_candidate_id}."
            ),
            instruct_content=judgement,
            role=self.profile,
            cause_by=JudgeAesthetic,
        )
