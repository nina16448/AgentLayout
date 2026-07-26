"""CompositionDirectorRole -- Role wrapper around ComposeConcept.

"先想再畫" refactor (2026-06-25). This Role owns the *thinking* half of layout
generation: it imagines spatial composition concepts in natural language before
any pixel is placed. It is the Role-path counterpart of the ``compose`` step in
``LayoutPipeline.run``.

Triggered by either:

  * A ``PlanAssets`` Message (the forward pass): the Asset Planner has just
    finished, so it is time to imagine concepts for the current spec.
  * A ``RetryComposition`` Message from ``IterationStateRole`` (a feedback
    re-run): the Aesthetic Judge found the composition itself weak
    (design_layout / innovation worst), so re-imagine from scratch.

The DesignSpec is pulled from env history (the most recent AnalyzeBrief output);
the background analysis is the CV module ``resolve_background`` exactly as the
CoordinateMapper uses it, so both stages see the same image.
"""
from __future__ import annotations

from metagpt.logs import logger
from metagpt.roles import Role
from metagpt.schema import Message
from metagpt.utils.common import any_to_str

from metagpt.ext.agentlayout.actions.analyze_brief import AnalyzeBrief
from metagpt.ext.agentlayout.actions.compose_concept import ComposeConcept
from metagpt.ext.agentlayout.actions.plan_assets import PlanAssets
from metagpt.ext.agentlayout.roles.iteration_state import RetryComposition
from metagpt.ext.agentlayout.schema import ConceptBatch, DesignSpec
from metagpt.ext.agentlayout.tools.background_analyzer import resolve_background


class CompositionDirectorRole(Role):
    name: str = "CompositionDirector"
    profile: str = "Composition Director"
    goal: str = (
        "Imagine spatially diverse composition concepts in natural language "
        "before any pixel-level layout, so the CoordinateMapper has a clear "
        "art-director brief instead of a constraint-heavy survival-mode prompt."
    )
    constraints: str = (
        "Output only natural-language concepts (no coordinates). Produce at "
        "least one concept; prefer 3 spatially distinct ones."
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_actions([ComposeConcept])
        self._watch([PlanAssets, RetryComposition])

    def _find_by_cause(self, cause_cls, expected_type):
        """Walk env-wide history backwards for a matching cause_by + payload type."""
        cause = any_to_str(cause_cls)
        for m in reversed(self.rc.env.history.get()):
            if m.cause_by == cause and isinstance(m.instruct_content, expected_type):
                return m.instruct_content
        raise ValueError(
            f"CompositionDirectorRole could not find a {cause_cls.__name__} message "
            f"with a {expected_type.__name__} payload in env history."
        )

    async def _act(self) -> Message:
        if self.rc.news:
            latest = self.rc.news[-1]
        else:
            latest = self.rc.history[-1]
        is_retry = latest.cause_by == any_to_str(RetryComposition)

        spec: DesignSpec = self._find_by_cause(AnalyzeBrief, DesignSpec)
        bg = resolve_background(spec.canvas)
        compose: ComposeConcept = self.actions[0]
        concepts: ConceptBatch = await compose.run(spec=spec, bg=bg)

        logger.info(
            f"CompositionDirectorRole produced {len(concepts.concepts)} concept(s) "
            f"(retry={is_retry})."
        )
        return Message(
            content=f"ConceptBatch ready: {len(concepts.concepts)} composition concept(s).",
            instruct_content=concepts,
            role=self.profile,
            cause_by=ComposeConcept,
        )
