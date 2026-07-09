"""LayoutGeneratorRole -- Role wrapper around GenerateLayout (the CoordinateMapper).

"先想再畫" refactor (2026-06-25). This Role no longer invents the composition; it
turns the CompositionDirector's concepts into pixels. The class name is kept
(``LayoutGeneratorRole``) so ``team.py`` imports do not break, but its profile is
now "Coordinate Mapper".

Triggered by either:

  * A ``ComposeConcept`` Message (forward pass): the ConceptBatch comes from
    ``Message.instruct_content`` directly.
  * A ``RetryCoordinates`` Message from ``IterationStateRole`` (a feedback
    re-run): the concepts are unchanged (reused from env history); only the
    typography/colour feedback and the previous best layout are injected.

The DesignSpec, LayoutTree and ConceptBatch are pulled from env history. Each
concept produces exactly one candidate; QC filters them, degrading to the
least-violating candidates when none pass so the Judge always has something.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from metagpt.logs import logger
from metagpt.roles import Role
from metagpt.schema import Message
from metagpt.utils.common import any_to_str

from metagpt.ext.agentlayout.actions.analyze_brief import AnalyzeBrief
from metagpt.ext.agentlayout.actions.compose_concept import ComposeConcept
from metagpt.ext.agentlayout.actions.generate_layout import GenerateLayout
from metagpt.ext.agentlayout.actions.plan_assets import PlanAssets
from metagpt.ext.agentlayout.tools.background_analyzer import resolve_background
from metagpt.ext.agentlayout.roles.iteration_state import RetryCoordinates, RetryPayload
from metagpt.ext.agentlayout.schema import (
    AestheticFeedback,
    BackgroundAnalysis,
    Candidate,
    CandidatesBatch,
    ConceptBatch,
    DesignSpec,
    LayoutTree,
)
from metagpt.ext.agentlayout.tools.quality_checker import (
    CheckResult,
    filter_valid,
    rank_candidates_by_violations,
)


class LayoutGeneratorRole(Role):
    name: str = "LayoutGenerator"
    profile: str = "Coordinate Mapper"
    goal: str = (
        "Translate each art-director composition concept into exact pixel "
        "coordinates for one layout candidate that passes the Quality Checker."
    )
    constraints: str = (
        "Each candidate must contain every spec element id verbatim, satisfy "
        "boundary checks and hard_constraints (Quality Checker)."
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_actions([GenerateLayout])
        self._watch([ComposeConcept, RetryCoordinates])
        # Round counter so candidate-id prefixes stay unique across retries.
        self._round: int = 0

    def _find_by_cause(self, cause_cls, expected_type):
        """Walk env-wide history backwards for a matching cause_by + payload type."""
        cause = any_to_str(cause_cls)
        for m in reversed(self.rc.env.history.get()):
            if m.cause_by == cause and isinstance(m.instruct_content, expected_type):
                return m.instruct_content
        raise ValueError(
            f"LayoutGeneratorRole could not find a {cause_cls.__name__} message "
            f"with a {expected_type.__name__} payload in env history."
        )

    async def _generate_from_concepts(
        self,
        spec: DesignSpec,
        tree: LayoutTree,
        bg: BackgroundAnalysis,
        concepts: ConceptBatch,
        round_idx: int,
        feedback: Optional[AestheticFeedback],
        prev_best_layout: Optional[Dict[str, Tuple[float, float, float, float]]] = None,
        prev_best_subscores: Optional[Dict[str, int]] = None,
    ) -> Tuple[List[Candidate], List[CheckResult]]:
        """One CoordinateMapper candidate per concept, then QC (mirror of the
        pipeline's ``_generate_from_concepts``)."""
        gen: GenerateLayout = self.actions[0]
        pool: List[Candidate] = []
        for i, concept in enumerate(concepts.concepts):
            try:
                batch = await gen.run(
                    spec=spec,
                    tree=tree,
                    bg=bg,
                    concept=concept,
                    feedback=feedback,
                    prev_best_layout=prev_best_layout,
                    prev_best_subscores=prev_best_subscores,
                )
            except Exception as err:  # noqa: BLE001
                logger.warning(
                    f"LayoutGeneratorRole: concept {i} ('{concept.name}') failed to "
                    f"map ({err}); skipping it."
                )
                continue
            for cand in batch.candidates:
                cand.candidate_id = f"r{round_idx}_c{i}_{cand.candidate_id}"
            pool.extend(batch.candidates)

        kept, all_reports = filter_valid(pool, spec, bg=bg)
        if kept:
            return kept, all_reports

        degraded = rank_candidates_by_violations(pool, all_reports)
        if degraded:
            logger.warning(
                f"LayoutGeneratorRole: 0/{len(pool)} candidates passed QC across "
                f"{len(concepts.concepts)} concept(s); degrading to "
                f"{len(degraded)} least-violating candidate(s)."
            )
        return degraded, all_reports

    async def _act(self) -> Message:
        if self.rc.news:
            latest = self.rc.news[-1]
        else:
            latest = self.rc.history[-1]
        is_retry = latest.cause_by == any_to_str(RetryCoordinates)
        feedback: Optional[AestheticFeedback] = None
        prev_best_layout: Optional[Dict[str, Tuple[float, float, float, float]]] = None
        prev_best_subscores: Optional[Dict[str, int]] = None

        if is_retry:
            payload = latest.instruct_content
            if not isinstance(payload, RetryPayload):
                raise ValueError(
                    "LayoutGeneratorRole expected RetryPayload on a RetryCoordinates "
                    f"Message. Got: {type(payload).__name__ if payload else 'None'}"
                )
            feedback = payload.feedback
            prev_best_layout = payload.prev_best_layout
            prev_best_subscores = payload.prev_best_subscores
            # Concepts are reused on a coordinate retry.
            concepts = self._find_by_cause(ComposeConcept, ConceptBatch)
            self._round += 1
            logger.info(
                f"LayoutGeneratorRole: coordinate retry (iteration={payload.iteration}); "
                "reusing concepts, applying typography/colour feedback."
            )
        else:
            concepts = latest.instruct_content
            if not isinstance(concepts, ConceptBatch):
                raise ValueError(
                    "LayoutGeneratorRole expected ConceptBatch as instruct_content "
                    f"(from ComposeConcept). Got: {type(concepts).__name__}"
                )
            self._round = 0

        spec = self._find_by_cause(AnalyzeBrief, DesignSpec)
        tree = self._find_by_cause(PlanAssets, LayoutTree)
        bg = resolve_background(spec.canvas)

        kept, reports = await self._generate_from_concepts(
            spec,
            tree,
            bg,
            concepts,
            self._round,
            feedback,
            prev_best_layout=prev_best_layout,
            prev_best_subscores=prev_best_subscores,
        )
        if not kept:
            raise RuntimeError(
                "LayoutGeneratorRole: produced 0 candidates from "
                f"{len(concepts.concepts)} concept(s); nothing to degrade to."
            )

        batch = CandidatesBatch(candidates=kept)
        qc_dropped = sum(1 for r in reports if not r.passed)
        logger.info(
            f"LayoutGeneratorRole produced {len(batch.candidates)} candidate(s) "
            f"(QC dropped={qc_dropped}, retry={is_retry})."
        )
        return Message(
            content=f"CandidatesBatch ready: {len(batch.candidates)} candidate(s).",
            instruct_content=batch,
            role=self.profile,
            cause_by=GenerateLayout,
        )
