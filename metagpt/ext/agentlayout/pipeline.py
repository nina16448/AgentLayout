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

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from metagpt.ext.agentlayout.actions.analyze_brief import AnalyzeBrief, AssetInput
from metagpt.ext.agentlayout.actions.compose_concept import ComposeConcept
from metagpt.ext.agentlayout.actions.generate_layout import GenerateLayout
from metagpt.ext.agentlayout.actions.judge_aesthetic import JudgeAesthetic
from metagpt.ext.agentlayout.actions.plan_assets import PlanAssets
from metagpt.ext.agentlayout.schema import (
    AestheticFeedback,
    BackgroundAnalysis,
    Candidate,
    Canvas,
    CompositionConcept,
    ConceptBatch,
    DesignSpec,
    FeedbackTarget,
    IterationState,
    JudgeDecision,
    K_VALID,
    LayoutTree,
    UnderlayRegion,
    VisualObservation,
)
from metagpt.ext.agentlayout.roles.iteration_state import ACCEPT_CONSECUTIVE_STOP
from metagpt.ext.agentlayout.tools.asset_analyzer import AssetAnalyzer
from metagpt.ext.agentlayout.tools.background_analyzer import resolve_background
from metagpt.ext.agentlayout.tools.feedback_verifier import (
    check_observation,
    compliance_report,
)
from metagpt.ext.agentlayout.tools.quality_checker import (
    CheckResult,
    filter_valid,
    rank_candidates_by_violations,
)
from metagpt.logs import logger


# ============================================================
# Helpers
# ============================================================


# Step 85 issue ledger: rubric priority (lower = fix first) and how many open
# issues are fed to the mapper per round. The ledger is the loop's MEMORY --
# the judge looks and estimates targets per image, but once an issue is
# opened its target persists verbatim until the verifier retires it
# geometrically. This kills the Step 84 oscillation ("too far" -> "too
# cramped" -> "not centered" on near-identical layouts).
_LEDGER_PRIORITY = {
    "title_misplaced": 0,
    "lockup_broken": 1,
    "text_off_panel": 2,
    "text_overlap": 3,
    "text_too_small": 4,
    "text_too_large": 4,
    "text_illegible": 5,
    "text_tilted": 6,
}
LEDGER_FEED_K: int = 2


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

    n_concepts: Optional[int] = Field(default=None, ge=1, le=5)
    """Step 83: override the CompositionDirector's concept count. None keeps
    the ComposeConcept default (3). Set 1 for single-candidate deep-review
    mode -- Step 82 showed the three concepts had converged to near-identical
    layouts, so best-of-3 paid 3x mapper cost for a fake choice."""


class TraceEntry(BaseModel):
    """One line of pipeline-level history for debugging / paper analytics."""

    round_idx: int
    decision: str  # 'accept' | 'reject'
    feedback_target: Optional[str] = None  # 'layout_generator' | 'analyst' | None on accept
    candidate_count: int  # candidates handed to Aesthetic Judge
    qc_filtered_count: int  # how many were dropped by Quality Checker this round
    ledger_open: Optional[int] = None
    """Step 85: number of unretired ledger issues after this round."""
    compliance: Optional[Dict[str, Any]] = None
    """Step 77: when the PREVIOUS round's judge emitted visual_observations and
    they were fed to the CoordinateMapper, this round's best candidate is
    machine-checked against them (n_total / n_verifiable / n_satisfied / rate).
    None when no observations were pending."""


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

    Step 76b (selection-effect fix): on the max-rounds path the error carries
    the LAST round's judge-preferred candidate (``best_candidate`` +
    ``spec`` + ``judgement``), so evaluation drivers can render and
    blind-judge exhausted runs too. Without this, blind win-rates are
    conditioned on internal acceptance — a selection effect (the Step 76 A/B
    could only judge 13/19 vs 9/19 rows). Attributes stay ``None`` on the
    zero-candidates path.
    """

    best_candidate: Optional[Candidate] = None
    spec: Optional[DesignSpec] = None
    judgement: Optional["AestheticJudgement"] = None
    trace: Optional[List["TraceEntry"]] = None  # Step 77: keeps compliance rows


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
        compose: Optional[ComposeConcept] = None,
        generate: Optional[GenerateLayout] = None,
        judge: Optional[JudgeAesthetic] = None,
        config: Optional[PipelineConfig] = None,
    ) -> None:
        # Real constructors by default; tests inject fakes with the same .run() shape.
        self.analyze = analyze or AnalyzeBrief()
        self.asset_analyzer = asset_analyzer or AssetAnalyzer()
        self.plan = plan or PlanAssets()
        # "先想再畫" refactor: the CompositionDirector (compose) imagines the
        # spatial concept; the CoordinateMapper (generate) turns each concept
        # into one candidate. Both default to real constructors.
        self.compose = compose or ComposeConcept()
        self.generate = generate or GenerateLayout()
        self.judge = judge or JudgeAesthetic()
        self.config = config or PipelineConfig()

    async def run(
        self,
        *,
        user_brief: str,
        asset_list: List[AssetInput],
        bg: Optional[BackgroundAnalysis] = None,
        underlay_regions: Optional[List[UnderlayRegion]] = None,
        round_callback: Optional[Any] = None,
    ) -> PipelineResult:
        """Drive the pipeline to either an accepted Candidate or a PipelineError.

        ``underlay_regions`` (Step 76): baked-underlay hints from the SEGA-style
        Crello preprocessor. They are merged into the resolved BackgroundAnalysis
        so both the Composition Director and the CoordinateMapper see them; the
        caller does not need to build a full BackgroundAnalysis itself.

        ``round_callback`` (Step 82): optional observer called after EVERY judge
        round as ``round_callback(round_idx, kept, judgement, spec)`` -- lets
        evaluation drivers snapshot each round's candidates (e.g. render the
        per-round evolution). Exceptions inside the callback are swallowed; it
        can never affect the run.
        """
        # Step 1: build the initial spec + enrich + plan.
        spec = await self.analyze.run(user_brief=user_brief, asset_list=asset_list)
        self.asset_analyzer.run(spec)
        tree = await self.plan.run(spec=spec)
        # Content-aware: caller-supplied bg wins; otherwise run real U2Net
        # safe-zone analysis when spec.canvas has a background image, else the
        # historical solid-color stub (resolve_background handles both).
        bg_resolved = bg if bg is not None else resolve_background(spec.canvas)
        if underlay_regions:
            bg_resolved = bg_resolved.model_copy(
                update={"underlay_regions": list(underlay_regions)}
            )

        state = IterationState()
        trace: List[TraceEntry] = []
        # "先想再畫" loop state:
        #   concepts        -- current ConceptBatch; None forces a re-compose.
        #   coord_feedback  -- typography/colour feedback for the CoordinateMapper
        #                      (kept only while the SAME concepts are reused).
        concepts: Optional[ConceptBatch] = None
        coord_feedback: Optional[AestheticFeedback] = None
        coord_prev_layout: Optional[Dict[str, Tuple[float, float, float, float]]] = None
        coord_prev_scores: Optional[Dict[str, int]] = None
        # Step 84: design-reject loop closure -- the judge's reasons + the
        # rejected concepts flow BACK to the CompositionDirector, which used
        # to re-imagine blind (Step 83 trace: R0-R3 identical concepts).
        compose_feedback: Optional[AestheticFeedback] = None
        compose_prev_concepts: Optional[ConceptBatch] = None
        # Step 84b (user): the mapper needs the REJECTED coordinates as a
        # contrast reference, otherwise "fix it" has no baseline to differ
        # from. True on the round right after a design reject.
        coord_revision: bool = False
        # Step 85 issue ledger: (kind, target_id) -> the FIRST observation
        # opened for that issue (target persists verbatim until retired).
        # Step 88b: retired keeps the observation so a geometric REGRESSION
        # re-opens it with the ORIGINAL target -- retirement is not immunity.
        ledger_open: Dict[Tuple[str, str], VisualObservation] = {}
        ledger_retired: Dict[Tuple[str, str], VisualObservation] = {}
        last_accept_result: Optional[PipelineResult] = None  # set when Judge accepts
        # Step 76b: judge-preferred candidate of the most recent REJECT round,
        # attached to PipelineError so drivers can still render/judge it.
        last_reject_best: Optional[Candidate] = None
        last_judgement: Optional[AestheticJudgement] = None

        for round_idx in range(self.config.max_total_rounds):
            # Phase 2: imagine composition concepts (first round, or whenever a
            # design_layout/innovation reject invalidated the previous ones).
            if concepts is None:
                compose_kwargs = {}
                if self.config.n_concepts is not None:
                    compose_kwargs["n"] = self.config.n_concepts
                if compose_feedback is not None or compose_prev_concepts is not None:
                    compose_kwargs["feedback"] = compose_feedback
                    compose_kwargs["prev_concepts"] = compose_prev_concepts
                concepts = await self.compose.run(
                    spec=spec, bg=bg_resolved, **compose_kwargs
                )
                compose_feedback = None
                compose_prev_concepts = None
                if not coord_revision:
                    # New concepts -> stale coordinate feedback. EXCEPT in
                    # revision mode (Step 84b): there the rejected layout +
                    # criticisms are deliberately carried to the mapper as a
                    # contrast reference.
                    coord_feedback = None
                    coord_prev_layout = None
                    coord_prev_scores = None
                logger.info(
                    f"LayoutPipeline round {round_idx}: composed "
                    f"{len(concepts.concepts)} concept(s)."
                )

            # Phase 3+4: one CoordinateMapper candidate per concept, then QC.
            kept, reports = await self._generate_from_concepts(
                spec,
                tree,
                bg_resolved,
                concepts,
                round_idx,
                coord_feedback,
                prev_best_layout=coord_prev_layout,
                prev_best_subscores=coord_prev_scores,
                revision=coord_revision,
            )
            coord_revision = False
            qc_dropped = sum(1 for r in reports if not r.passed)

            if len(kept) < self.config.min_candidates_to_judge:
                raise PipelineError(
                    f"Round {round_idx}: only {len(kept)} candidate(s) available "
                    f"from {len(concepts.concepts)} concept(s) after degradation; "
                    f"required >= {self.config.min_candidates_to_judge}."
                )

            # Phase 5: judge.
            judgement = await self.judge.run(
                candidates=kept, spec=spec, tree=tree, bg=bg_resolved
            )
            state.iteration += 1
            state.last_feedback = judgement.feedback
            is_accept = judgement.decision == JudgeDecision.ACCEPT
            last_judgement = judgement
            # Step 77: if the feedback fed into THIS round carried visual
            # observations, machine-check the round's best candidate against
            # them -- the compliance rate localises loop failures.
            compliance_dict: Optional[Dict[str, Any]] = None
            try:
                best_now: Optional[Candidate] = self._find_candidate(
                    kept, judgement.best_candidate_id
                )
            except PipelineError:
                best_now = None
            if best_now is not None and coord_feedback is not None \
                    and coord_feedback.visual_observations:
                try:
                    report = compliance_report(
                        coord_feedback.visual_observations, best_now
                    )
                    compliance_dict = report.model_dump(exclude={"checks"})
                    logger.info(
                        f"LayoutPipeline round {round_idx}: visual-observation "
                        f"compliance {report.n_satisfied}/{report.n_verifiable} "
                        f"(rate={report.rate})."
                    )
                    # Step 85: geometric retirement -- an issue leaves the
                    # ledger only when the verifier confirms it fixed.
                    for check in report.checks:
                        key = (check.kind, check.target_id)
                        if check.satisfied and key in ledger_open:
                            ledger_retired[key] = ledger_open.pop(key)
                except PipelineError:
                    pass  # judge picked an id not in kept -- impossible in theory

            # Step 88b: regression watch -- retired issues are re-checked
            # against every round's best candidate; a fixed defect that
            # comes back re-opens with its ORIGINAL target.
            if best_now is not None and ledger_retired:
                for key, obs in list(ledger_retired.items()):
                    chk = check_observation(obs, best_now)
                    if chk.verifiable and chk.satisfied is False:
                        ledger_open[key] = ledger_retired.pop(key)
                        logger.info(
                            f"LayoutPipeline round {round_idx}: ledger issue "
                            f"{key} REGRESSED -- reopened with original target."
                        )

            # Step 85: ingest NEW issues (judge may only append; an open or
            # retired issue keeps its original target -- no re-litigation),
            # then replace the fed view with the top-priority open items.
            for obs in judgement.feedback.visual_observations:
                key = (obs.kind.value, obs.target_id)
                if key not in ledger_open and key not in ledger_retired:
                    ledger_open[key] = obs
            if ledger_open or judgement.feedback.visual_observations:
                judgement.feedback.visual_observations = sorted(
                    ledger_open.values(),
                    key=lambda o: _LEDGER_PRIORITY.get(o.kind.value, 9),
                )[:LEDGER_FEED_K]
            # Step 89: retired targets ride along as KEEP constraints so the
            # next mapper call (especially accept-round polish) cannot undo
            # what the loop already fixed.
            if ledger_retired:
                judgement.feedback.keep_constraints = list(ledger_retired.values())[:4]

            # Step 88b: the round observer fires AFTER ledger processing so
            # drivers dump the TRUE fed view, not the inspector's raw output.
            if round_callback is not None:
                try:
                    round_callback(round_idx, kept, judgement, spec)
                except Exception as err:  # noqa: BLE001 -- observer only
                    logger.warning(f"LayoutPipeline round_callback failed: {err}")
            if is_accept:
                state.consecutive_accepts += 1
            else:
                state.consecutive_accepts = 0
                state.reject_count += 1
                try:
                    last_reject_best = self._find_candidate(
                        kept, judgement.best_candidate_id
                    )
                except PipelineError:
                    last_reject_best = None  # defensive; judge validates the id

            decision_label = "accept" if is_accept else "reject"

            # Phase 6: route the verdict.
            #   ACCEPT            -> CoordinateMapper (mandatory refinement pass)
            #   REJECT design/inn -> CompositionDirector (re-imagine)
            #   REJECT (too many) -> Analyst (rebuild spec)
            #   REJECT otherwise  -> CoordinateMapper (typography/colour fix)
            if is_accept:
                state.feedback_target = FeedbackTarget.LAYOUT_GENERATOR
            elif state.next_target() == FeedbackTarget.ANALYST:
                state.feedback_target = FeedbackTarget.ANALYST
            elif self._worst_axis(judgement) in ("design_layout", "innovation_originality"):
                state.feedback_target = FeedbackTarget.COMPOSITION_DIRECTOR
            else:
                state.feedback_target = FeedbackTarget.LAYOUT_GENERATOR

            trace.append(
                TraceEntry(
                    round_idx=round_idx,
                    decision=decision_label,
                    feedback_target=state.feedback_target.value,
                    candidate_count=len(kept),
                    qc_filtered_count=qc_dropped,
                    ledger_open=len(ledger_open) if (ledger_open or ledger_retired) else None,
                    compliance=compliance_dict,
                )
            )

            if is_accept:
                accepted = self._find_candidate(kept, judgement.best_candidate_id)
                last_accept_result = PipelineResult(
                    accepted_candidate=accepted,
                    judgement=judgement,
                    spec=spec,
                    tree=tree,
                    iteration_state=state,
                    trace=trace,
                )
                if state.consecutive_accepts >= ACCEPT_CONSECUTIVE_STOP:
                    logger.info(
                        f"LayoutPipeline accepted at round {round_idx} "
                        f"(best={judgement.best_candidate_id}, "
                        f"consecutive_accepts={state.consecutive_accepts}). "
                        "Refinement converged."
                    )
                    return last_accept_result

            target = state.feedback_target
            if target == FeedbackTarget.COMPOSITION_DIRECTOR:
                # Composition itself is weak -> revise the concepts next round.
                # Step 84: carry the judge's reasons + the rejected concepts to
                # the Director instead of discarding them (the discard made
                # every re-imagined round regenerate the same concept).
                logger.info(
                    f"LayoutPipeline reject -> CompositionDirector "
                    f"(iteration={state.iteration}); revising concepts against "
                    f"the judge's criticisms."
                )
                compose_feedback = judgement.feedback
                compose_prev_concepts = concepts
                concepts = None
                # Step 84b: keep the rejected layout + criticisms for the
                # mapper as a CONTRAST reference (revision mode), instead of
                # cold-starting it.
                coord_feedback = judgement.feedback
                coord_prev_layout = judgement.best_candidate_layout
                coord_prev_scores = self._best_subscores(judgement)
                coord_revision = True
            elif target == FeedbackTarget.ANALYST:
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
                concepts = None
                coord_feedback = None
                coord_prev_layout = None
                coord_prev_scores = None
            else:
                # CoordinateMapper: keep the concepts, feed the typography/colour
                # (or accept-refinement) suggestions plus the previous best bbox.
                coord_feedback = judgement.feedback
                coord_prev_layout = judgement.best_candidate_layout
                coord_prev_scores = self._best_subscores(judgement)
                logger.info(
                    f"LayoutPipeline {decision_label} -> CoordinateMapper "
                    f"(iteration={state.iteration}, mode=micro-adjust)"
                )

        if last_accept_result is not None:
            logger.warning(
                f"LayoutPipeline: refinement did not converge within "
                f"{self.config.max_total_rounds} rounds; returning the most "
                "recent accept verdict."
            )
            return last_accept_result
        err = PipelineError(
            f"Max rounds ({self.config.max_total_rounds}) exhausted without accept. "
            f"Iterations recorded: {state.iteration}."
        )
        # Step 76b: let evaluation drivers render/judge the exhausted run too,
        # instead of silently dropping it from blind statistics.
        err.best_candidate = last_reject_best
        err.spec = spec
        err.judgement = last_judgement
        err.trace = trace
        raise err

    @staticmethod
    def _worst_axis(judgement: AestheticJudgement) -> Optional[str]:
        """Lowest-scoring COLE axis of the best candidate (drives reject routing)."""
        scores = LayoutPipeline._best_subscores(judgement)
        if not scores:
            return None
        return min(scores.items(), key=lambda kv: kv[1])[0]

    @staticmethod
    def _best_subscores(judgement: AestheticJudgement) -> Optional[Dict[str, int]]:
        """Extract COLE 5-axis subscores of the best candidate from the verdict."""
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

    # --------------------------------------------------------
    # Internals
    # --------------------------------------------------------

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
        revision: bool = False,
    ) -> Tuple[List[Candidate], List[CheckResult]]:
        """Map each concept to exactly ONE candidate, then QC-filter.

        "先想再畫" refactor: replaces the old k_valid top-up loop. Three genuinely
        different concepts beat fifteen variations of one centred template, so we
        do not top up -- each concept produces one candidate. A concept whose
        CoordinateMapper call fails outright is skipped (fault tolerance). When
        QC rejects everything, we degrade to the least-violating candidates so
        the Judge still has something to score.
        """
        pool: List[Candidate] = []
        for i, concept in enumerate(concepts.concepts):
            try:
                batch = await self.generate.run(
                    spec=spec,
                    tree=tree,
                    bg=bg,
                    concept=concept,
                    feedback=feedback,
                    prev_best_layout=prev_best_layout,
                    prev_best_subscores=prev_best_subscores,
                    revision=revision,
                )
            except (ValueError, Exception) as err:  # noqa: BLE001
                logger.warning(
                    f"LayoutPipeline: concept {i} ('{concept.name}') failed to map "
                    f"to coordinates ({err}); skipping it."
                )
                continue
            # Prefix ids with round + concept so candidates never collide and the
            # winning candidate is traceable back to its concept.
            for cand in batch.candidates:
                cand.candidate_id = f"r{round_idx}_c{i}_{cand.candidate_id}"
            pool.extend(batch.candidates)

        kept, all_reports = filter_valid(pool, spec, bg=bg)
        if kept:
            return kept, all_reports

        degraded = rank_candidates_by_violations(pool, all_reports)[: self.config.k_valid]
        if degraded:
            logger.warning(
                f"LayoutPipeline: 0/{len(pool)} candidates passed QC across "
                f"{len(concepts.concepts)} concept(s); degrading to "
                f"{len(degraded)} least-violating candidate(s) so the run continues."
            )
        return degraded, all_reports

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
