"""LayoutPipeline -- end-to-end orchestrator for the AgentLayout pipeline.

This driver wires together every implemented component:

    Analyst (LLM)            -> DesignSpec
    AssetAnalyzer (Python)   -> DesignSpec enriched in place
    AssetPlanner (LLM)       -> LayoutTree
    [ generator+QC top-up loop ]
        GenerateLayout (LLM) -> CandidatesBatch
        filter_valid (QC)    -> kept candidates
        repeat until len(kept) >= k_valid OR max_topup_rounds exhausted
    AestheticJudge (multimodal LLM) -> AestheticJudgement
        ACCEPT -> return PipelineResult
        REJECT -> route feedback per IterationState.next_target()
                    iteration 1..N (default N=2) -> Layout Generator
                    iteration N+1..              -> Analyst (re-spec, re-plan)

The pipeline is intentionally library-light: every dependency is injected via
``__init__`` so tests can swap in fakes that skip real LLM calls. The real
constructors are wired by default for production use.

Background Analyzer and CLIP Embedder are not yet implemented; the driver
accepts a manually-constructed ``BackgroundAnalysis`` (or falls back to
:func:`default_white_background`), and ``AssetAnalyzer`` ships a
``semantic_relevance`` stub until CLIP lands.
"""
from __future__ import annotations

from typing import List, Optional, Set, Tuple

from pydantic import BaseModel, ConfigDict, Field

from metagpt.ext.agentlayout.actions.analyze_brief import AnalyzeBrief, AssetInput
from metagpt.ext.agentlayout.actions.generate_layout import GenerateLayout
from metagpt.ext.agentlayout.actions.judge_aesthetic import JudgeAesthetic
from metagpt.ext.agentlayout.actions.plan_assets import PlanAssets
from metagpt.ext.agentlayout.schema import (
    AestheticFeedback,
    BackgroundAnalysis,
    Candidate,
    Canvas,
    DesignSpec,
    FeedbackTarget,
    IterationState,
    JudgeDecision,
    K_VALID,
    LayoutTree,
)
from metagpt.ext.agentlayout.tools.asset_analyzer import AssetAnalyzer
from metagpt.ext.agentlayout.tools.quality_checker import CheckResult, filter_valid
from metagpt.logs import logger


# ============================================================
# Helpers
# ============================================================


def default_white_background(canvas: Canvas) -> BackgroundAnalysis:
    """BackgroundAnalysis stub derived from the Canvas itself.

    Returns an empty ``safe_zones`` (Layout Generator treats unknown regions as
    permissible) and a palette + text-color pair derived from
    ``canvas.background_color`` when set, falling back to white otherwise.

    The 2026-05-14 step 7 change made Analyst infer a non-white
    ``background_color`` by default; keeping this stub palette consistent with
    the rendered PNG prevents the Aesthetic Judge from seeing a tinted canvas
    while reading ``dominant_palette=['#FFFFFF']``.
    """
    bg_hex = canvas.background_color or "#FFFFFF"
    return BackgroundAnalysis(
        safe_zones=[],
        dominant_palette=[bg_hex],
        recommended_text_color=_text_color_for_background(bg_hex),
    )


def _text_color_for_background(bg_hex: str) -> str:
    """Pick a readable text color (dark on light, light on dark) by luminance."""
    h = bg_hex.lstrip("#")
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return "#111111"
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return "#111111" if luminance >= 128 else "#F4F4F4"


# ============================================================
# Config + result models
# ============================================================


class PipelineConfig(BaseModel):
    """Tunable knobs exposed to callers (defaults match schema-level constants)."""

    k_valid: int = Field(default=K_VALID, ge=1)
    """Target number of QC-passing candidates per Aesthetic Judge call."""

    max_topup_rounds: int = Field(default=3, ge=1)
    """Cap on Generator re-calls when QC keeps fewer than k_valid."""

    max_total_rounds: int = Field(default=5, ge=1)
    """Cap on Aesthetic Judge iterations before the pipeline gives up."""

    min_candidates_to_judge: int = Field(default=1, ge=1)
    """Below this, QC is treated as catastrophic and the pipeline aborts."""


class TraceEntry(BaseModel):
    """One line of pipeline-level history for debugging / paper analytics."""

    round_idx: int
    decision: str  # 'accept' | 'reject'
    feedback_target: Optional[str] = None  # 'layout_generator' | 'analyst' | None on accept
    candidate_count: int  # candidates handed to Aesthetic Judge
    qc_filtered_count: int  # how many were dropped by Quality Checker this round


class PipelineResult(BaseModel):
    """Top-level pipeline output. Returned only on ACCEPT."""

    model_config = ConfigDict(arbitrary_types_allowed=False)

    accepted_candidate: Candidate
    judgement: "AestheticJudgement"
    spec: DesignSpec
    tree: LayoutTree
    iteration_state: IterationState
    trace: List[TraceEntry] = Field(default_factory=list)


from metagpt.ext.agentlayout.schema import AestheticJudgement  # noqa: E402  (forward-ref resolution)
PipelineResult.model_rebuild()


class PipelineError(RuntimeError):
    """Raised when the pipeline cannot produce an accepted result.

    Causes:
      * Quality Checker drops every candidate in every top-up round
      * ``max_total_rounds`` exhausted with the most recent decision being REJECT
    """


# ============================================================
# Pipeline driver
# ============================================================


class LayoutPipeline:
    """Orchestrate Analyst -> AssetAnalyzer -> AssetPlanner -> Generator(+QC) -> Judge."""

    def __init__(
        self,
        *,
        analyze: Optional[AnalyzeBrief] = None,
        asset_analyzer: Optional[AssetAnalyzer] = None,
        plan: Optional[PlanAssets] = None,
        generate: Optional[GenerateLayout] = None,
        judge: Optional[JudgeAesthetic] = None,
        config: Optional[PipelineConfig] = None,
    ) -> None:
        # Real constructors by default; tests inject fakes with the same .run() shape.
        self.analyze = analyze or AnalyzeBrief()
        self.asset_analyzer = asset_analyzer or AssetAnalyzer()
        self.plan = plan or PlanAssets()
        self.generate = generate or GenerateLayout()
        self.judge = judge or JudgeAesthetic()
        self.config = config or PipelineConfig()

    async def run(
        self,
        *,
        user_brief: str,
        asset_list: List[AssetInput],
        bg: Optional[BackgroundAnalysis] = None,
    ) -> PipelineResult:
        """Drive the pipeline to either an accepted Candidate or a PipelineError."""
        # Step 1: build the initial spec + enrich + plan.
        spec = await self.analyze.run(user_brief=user_brief, asset_list=asset_list)
        self.asset_analyzer.run(spec)
        tree = await self.plan.run(spec=spec)
        bg_resolved = bg if bg is not None else default_white_background(spec.canvas)

        state = IterationState()
        trace: List[TraceEntry] = []
        gen_feedback: Optional[AestheticFeedback] = None  # latest reject feedback for the generator

        for round_idx in range(self.config.max_total_rounds):
            kept, reports = await self._generate_with_topup(spec, tree, bg_resolved, gen_feedback)
            qc_dropped = sum(1 for r in reports if not r.passed)

            if len(kept) < self.config.min_candidates_to_judge:
                raise PipelineError(
                    f"Round {round_idx}: only {len(kept)} candidate(s) passed Quality "
                    f"Checker after {self.config.max_topup_rounds} top-up round(s); "
                    f"required >= {self.config.min_candidates_to_judge}."
                )

            judgement = await self.judge.run(
                candidates=kept, spec=spec, tree=tree, bg=bg_resolved
            )

            if judgement.decision == JudgeDecision.ACCEPT:
                trace.append(
                    TraceEntry(
                        round_idx=round_idx,
                        decision="accept",
                        feedback_target=None,
                        candidate_count=len(kept),
                        qc_filtered_count=qc_dropped,
                    )
                )
                accepted = self._find_candidate(kept, judgement.best_candidate_id)
                logger.info(
                    f"LayoutPipeline accepted at round {round_idx} "
                    f"(best={judgement.best_candidate_id}, total={len(trace)} round(s))"
                )
                return PipelineResult(
                    accepted_candidate=accepted,
                    judgement=judgement,
                    spec=spec,
                    tree=tree,
                    iteration_state=state,
                    trace=trace,
                )

            # REJECT path: increment first, then ask state for routing.
            state.iteration += 1
            state.last_feedback = judgement.feedback
            state.feedback_target = state.next_target()

            trace.append(
                TraceEntry(
                    round_idx=round_idx,
                    decision="reject",
                    feedback_target=state.feedback_target.value,
                    candidate_count=len(kept),
                    qc_filtered_count=qc_dropped,
                )
            )

            if state.feedback_target == FeedbackTarget.LAYOUT_GENERATOR:
                # Spec + tree stay; next round's generator gets the feedback.
                gen_feedback = judgement.feedback
                logger.info(
                    f"LayoutPipeline reject -> Layout Generator (iteration={state.iteration})"
                )
            else:
                # ANALYST: regenerate spec from scratch with feedback,
                # re-enrich, re-plan, and clear the generator-side feedback.
                logger.info(
                    f"LayoutPipeline reject -> Analyst (iteration={state.iteration}); "
                    "rebuilding spec + tree."
                )
                spec = await self.analyze.run(
                    user_brief=user_brief,
                    asset_list=asset_list,
                    feedback=judgement.feedback,
                )
                self.asset_analyzer.run(spec)
                tree = await self.plan.run(spec=spec)
                gen_feedback = None

        raise PipelineError(
            f"Max rounds ({self.config.max_total_rounds}) exhausted without accept. "
            f"Iterations recorded: {state.iteration}."
        )

    # --------------------------------------------------------
    # Internals
    # --------------------------------------------------------

    async def _generate_with_topup(
        self,
        spec: DesignSpec,
        tree: LayoutTree,
        bg: BackgroundAnalysis,
        feedback: Optional[AestheticFeedback],
    ) -> Tuple[List[Candidate], List[CheckResult]]:
        """Call GenerateLayout repeatedly until we have ``k_valid`` QC-passing candidates."""
        kept: List[Candidate] = []
        all_reports: List[CheckResult] = []
        seen_ids: Set[str] = set()

        for topup_idx in range(self.config.max_topup_rounds):
            batch = await self.generate.run(spec=spec, tree=tree, bg=bg, feedback=feedback)

            # Re-prefix candidate ids so concurrent top-up batches do not collide.
            # The LLM tends to emit cand_1..cand_5 each call; without prefixing,
            # the second batch would shadow the first.
            for cand in batch.candidates:
                cand.candidate_id = f"r{topup_idx}_{cand.candidate_id}"

            new_kept, reports = filter_valid(batch.candidates, spec)
            all_reports.extend(reports)
            for cand in new_kept:
                if cand.candidate_id not in seen_ids:
                    kept.append(cand)
                    seen_ids.add(cand.candidate_id)

            if len(kept) >= self.config.k_valid:
                break

        # Trim to exactly k_valid so Aesthetic Judge always sees a stable target size.
        return kept[: self.config.k_valid], all_reports

    @staticmethod
    def _find_candidate(candidates: List[Candidate], candidate_id: str) -> Candidate:
        for cand in candidates:
            if cand.candidate_id == candidate_id:
                return cand
        raise PipelineError(
            f"Aesthetic Judge accepted candidate '{candidate_id}' which is not in the "
            "candidates handed to it. This should be impossible -- JudgeAesthetic "
            "validates this internally."
        )
