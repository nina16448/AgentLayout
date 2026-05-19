"""LayoutGeneratorRole -- Role wrapper around GenerateLayout + QC top-up loop.

Triggered by either:

  * A ``PlanAssets`` Message (the initial pass): the LayoutTree comes from
    ``Message.instruct_content`` directly.
  * A ``RetryGeneration`` Message from ``IterationStateRole`` (a feedback
    re-run): the LayoutTree must be retrieved from env history (the most
    recent PlanAssets output stays valid; only the generation prompt changes
    via the injected Aesthetic feedback).

In both cases the DesignSpec is pulled from env history -- this Role does
not observe AnalystRole's output directly. Internally runs the same
K_VALID-target top-up loop as ``LayoutPipeline._generate_with_topup``: each
top-up batch's candidate IDs are prefixed ``r{i}_`` so they do not collide
across calls.

Background analysis is a CV module (not a Role): ``resolve_background``
runs U2Net saliency on ``canvas.background_asset_ref`` when present and
falls back to the solid-color stub for image-less specs.
"""
from __future__ import annotations

from typing import List, Optional, Set, Tuple

from metagpt.logs import logger
from metagpt.roles import Role
from metagpt.schema import Message
from metagpt.utils.common import any_to_str

from metagpt.ext.agentlayout.actions.analyze_brief import AnalyzeBrief
from metagpt.ext.agentlayout.actions.generate_layout import GenerateLayout
from metagpt.ext.agentlayout.actions.plan_assets import PlanAssets
from metagpt.ext.agentlayout.tools.background_analyzer import resolve_background
from metagpt.ext.agentlayout.roles.iteration_state import RetryGeneration, RetryPayload
from metagpt.ext.agentlayout.schema import (
    AestheticFeedback,
    BackgroundAnalysis,
    Candidate,
    CandidatesBatch,
    DesignSpec,
    K_VALID,
    LayoutTree,
)
from metagpt.ext.agentlayout.tools.quality_checker import (
    CheckResult,
    filter_valid,
    rank_candidates_by_violations,
)


class LayoutGeneratorRole(Role):
    name: str = "LayoutGenerator"
    profile: str = "Layout Generator"
    goal: str = (
        "Place every DesignSpec element on the canvas with concrete pixel "
        "coordinates and (for text) visual style. Produce K_VALID candidates "
        "that pass the Quality Checker."
    )
    constraints: str = (
        "Each candidate must contain every spec element id verbatim, "
        "satisfy boundary checks and hard_constraints (Quality Checker)."
    )

    k_valid: int = K_VALID
    max_topup_rounds: int = 3

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_actions([GenerateLayout])
        self._watch([PlanAssets, RetryGeneration])
        # Counter to keep candidate-id prefixes unique across feedback retries.
        # Without this, a re-run after a reject would collide with the previous
        # pass's "r0_*", "r1_*" ids and the dedupe set in _generate_with_topup
        # would drop the new candidates.
        self._retry_round: int = 0

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

    async def _generate_with_topup(
        self,
        spec: DesignSpec,
        tree: LayoutTree,
        bg: BackgroundAnalysis,
        feedback: Optional[AestheticFeedback],
        prefix_offset: int,
    ) -> Tuple[List[Candidate], List[CheckResult]]:
        """Mirror of LayoutPipeline._generate_with_topup so Role mode behaves the same."""
        kept: List[Candidate] = []
        pool: List[Candidate] = []  # every generated candidate, for degradation
        all_reports: List[CheckResult] = []
        seen_ids: Set[str] = set()
        gen: GenerateLayout = self.actions[0]

        for topup_idx in range(self.max_topup_rounds):
            batch = await gen.run(spec=spec, tree=tree, bg=bg, feedback=feedback)
            for cand in batch.candidates:
                cand.candidate_id = f"r{prefix_offset + topup_idx}_{cand.candidate_id}"

            pool.extend(batch.candidates)
            new_kept, reports = filter_valid(batch.candidates, spec)
            all_reports.extend(reports)
            for cand in new_kept:
                if cand.candidate_id not in seen_ids:
                    kept.append(cand)
                    seen_ids.add(cand.candidate_id)

            if len(kept) >= self.k_valid:
                break

        if kept:
            return kept[: self.k_valid], all_reports

        # Graceful degradation (step 10b fix): no candidate passed QC -- e.g.
        # an out-of-vocabulary Analyst hint that fails every candidate
        # identically. Hard-crashing here silently shrinks evaluable N; hand
        # back the least-violating candidates so the Judge still scores and
        # IterationState can still route feedback back to the Analyst.
        degraded = rank_candidates_by_violations(pool, all_reports)[: self.k_valid]
        if degraded:
            logger.warning(
                f"LayoutGeneratorRole: 0/{len(pool)} candidates passed QC after "
                f"{self.max_topup_rounds} top-up round(s); degrading to "
                f"{len(degraded)} least-violating candidate(s) so the run continues."
            )
        return degraded, all_reports

    async def _act(self) -> Message:
        # Prefer rc.news (this tick's freshly-observed messages); fall back to
        # rc.history[-1] for the very first invocation when news is empty.
        if self.rc.news:
            latest = self.rc.news[-1]
        else:
            latest = self.rc.history[-1]
        is_retry = latest.cause_by == any_to_str(RetryGeneration)
        feedback: Optional[AestheticFeedback] = None

        if is_retry:
            payload = latest.instruct_content
            if not isinstance(payload, RetryPayload):
                raise ValueError(
                    "LayoutGeneratorRole expected RetryPayload on a RetryGeneration "
                    f"Message. Got: {type(payload).__name__ if payload else 'None'}"
                )
            feedback = payload.feedback
            tree = self._find_by_cause(PlanAssets, LayoutTree)
            self._retry_round += 1
            logger.info(
                f"LayoutGeneratorRole: retry pass (iteration={payload.iteration}); "
                "regenerating with Aesthetic feedback (tree from env history)."
            )
        else:
            tree = latest.instruct_content
            if not isinstance(tree, LayoutTree):
                raise ValueError(
                    "LayoutGeneratorRole expected LayoutTree as instruct_content "
                    f"(from PlanAssets). Got: {type(tree).__name__}"
                )
            # Reset the prefix counter on every fresh PlanAssets pass so that
            # an Analyst-driven retry (which republishes PlanAssets) starts
            # candidate ids cleanly from r0_*.
            self._retry_round = 0

        spec = self._find_by_cause(AnalyzeBrief, DesignSpec)
        # Content-aware: real U2Net safe-zone analysis when the spec carries a
        # background image, else the historical solid-color stub.
        bg = resolve_background(spec.canvas)

        prefix_offset = self._retry_round * self.max_topup_rounds
        kept, reports = await self._generate_with_topup(
            spec, tree, bg, feedback, prefix_offset
        )
        if not kept:
            # Only reachable when generation produced literally zero
            # candidates (LLM emitted empty batches every top-up round).
            # QC-strict specs no longer reach here -- _generate_with_topup
            # degrades to least-violating candidates instead.
            raise RuntimeError(
                f"LayoutGeneratorRole: generation produced 0 candidates after "
                f"{self.max_topup_rounds} top-up round(s) "
                f"(QC reports: {len(reports)}); nothing to degrade to."
            )

        batch = CandidatesBatch(candidates=kept)
        qc_dropped = sum(1 for r in reports if not r.passed)
        logger.info(
            f"LayoutGeneratorRole produced {len(batch.candidates)} valid candidates "
            f"(QC dropped={qc_dropped}, retry={is_retry})."
        )
        return Message(
            content=f"CandidatesBatch ready: {len(batch.candidates)} valid candidates.",
            instruct_content=batch,
            role=self.profile,
            cause_by=GenerateLayout,
        )
