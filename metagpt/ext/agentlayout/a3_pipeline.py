"""Canonical A3 L0 orchestrator: best-of-3 selection, then unconditional stop.

This is the only sanctioned A3 execution boundary; A3 runs must never fall
back to the legacy multi-round ``pipeline.LayoutPipeline``. The L0 flow is:

    Analyst (once, frozen)
      -> Asset Planner (once, frozen; only for the T2 arm)
      -> Composition Director (exactly 3 concepts)
      -> Coordinate Mapper (one candidate per concept)
      -> deterministic QC (recorded per candidate, never silently dropped)
      -> Judge-Select picks B0
      -> stop unconditionally.

Judge-Critic is intentionally not wired here: L0 ends at selection, and the
one gated revision that consumes critic output arrives with A3-07.

Stages are injected as callables so the orchestration contract can be
verified offline with fake Actions and zero API calls.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from metagpt.ext.agentlayout.a3_config import A3RunConfig
from metagpt.ext.agentlayout.layout_tree_v3 import (
    A3LayoutTree,
    TreeCondition,
    make_tree_condition,
)
from metagpt.ext.agentlayout.run_manifest import (
    ErrorRecord,
    sha256_file,
    write_json_once,
)
from metagpt.ext.agentlayout.tools.analyst_vision import A3AnalystOutput
from metagpt.ext.agentlayout.tools.judge_select import (
    JUDGE_SELECT_CANDIDATE_COUNT,
    JudgeSelectCandidate,
    JudgeSelectResult,
    validate_selection,
)


A3_L0_PIPELINE_VERSION = "a3.l0-pipeline.v1"
A3_R0_BUNDLE_SCHEMA_VERSION = "a3.r0-bundle.v1"
A3_L0_CANDIDATE_POLICY_VERSION = "a3.l0-candidate-policy.v1"
R0_SLOT_IDS = ("r0_candidate_01", "r0_candidate_02", "r0_candidate_03")
DEGRADATION_ALL_QC_FAILED = "all_qc_failed"

TreeArm = Literal["T0", "T1", "T2", "T3"]


class QCVerdict(BaseModel):
    """Deterministic QC outcome for one candidate."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    violations: List[str] = Field(default_factory=list)


class R0SlotRecord(BaseModel):
    """Provenance for one of the exactly three R0 candidate slots."""

    model_config = ConfigDict(extra="forbid")

    slot_id: str
    status: Literal["completed", "failed"]
    concept_summary: Optional[str] = None
    candidate: Optional[Dict[str, Any]] = None
    render_ref: Optional[str] = None
    render_sha256: Optional[str] = None
    qc_passed: Optional[bool] = None
    qc_violations: List[str] = Field(default_factory=list)
    error: Optional[str] = None


class R0Bundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["a3.r0-bundle.v1"] = A3_R0_BUNDLE_SCHEMA_VERSION
    policy_version: Literal["a3.l0-candidate-policy.v1"] = A3_L0_CANDIDATE_POLICY_VERSION
    slots: List[R0SlotRecord] = Field(
        ...,
        min_length=JUDGE_SELECT_CANDIDATE_COUNT,
        max_length=JUDGE_SELECT_CANDIDATE_COUNT,
    )


class A3L0Result(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pipeline_version: Literal["a3.l0-pipeline.v1"] = A3_L0_PIPELINE_VERSION
    loop: Literal["L0"] = "L0"
    tree_arm: TreeArm
    degradations: List[str] = Field(default_factory=list)
    bundle: R0Bundle
    judge_select: JudgeSelectResult
    b0_slot_id: str
    stop_reason: Literal["l0_unconditional_stop"] = "l0_unconditional_stop"


class A3L0PipelineError(RuntimeError):
    """Fail-closed pipeline failure carrying its versioned error record."""

    def __init__(self, record: ErrorRecord):
        super().__init__(f"{record.error_type}: {record.message}")
        self.record = record


class A3L0Pipeline:
    """L0 orchestrator; every stage runs at most once per sample."""

    def __init__(
        self,
        *,
        config: A3RunConfig,
        analyst: Callable[[str], Awaitable[A3AnalystOutput]],
        planner: Callable[[A3AnalystOutput], Awaitable[A3LayoutTree]],
        director: Callable[[A3AnalystOutput, TreeCondition], Awaitable[List[Any]]],
        mapper: Callable[[Any, TreeCondition], Awaitable[Dict[str, Any]]],
        renderer: Callable[[Dict[str, Any], str], Awaitable[str]],
        qc: Callable[[Dict[str, Any]], QCVerdict],
        judge_select: Callable[[List[JudgeSelectCandidate]], Awaitable[JudgeSelectResult]],
        artifacts_dir: Optional[Path] = None,
    ):
        if config.loop != "L0":
            raise NotImplementedError(
                "A3L0Pipeline only implements loop='L0'; the gated single revision "
                "for loop='L1-Gated' arrives with A3-07"
            )
        self.config = config
        self.analyst = analyst
        self.planner = planner
        self.director = director
        self.mapper = mapper
        self.renderer = renderer
        self.qc = qc
        self.judge_select = judge_select
        self.artifacts_dir = artifacts_dir
        self._error_count = 0
        if artifacts_dir is not None:
            artifacts_dir.mkdir(parents=True, exist_ok=False)
            (artifacts_dir / "errors").mkdir()

    def _save(self, filename: str, payload: Any) -> None:
        if self.artifacts_dir is not None:
            write_json_once(self.artifacts_dir / filename, payload)

    def _fail(
        self, *, stage: str, error_type: str, message: str, details: Dict[str, Any]
    ) -> A3L0PipelineError:
        record = ErrorRecord(
            stage=stage,
            error_type=error_type,
            message=message,
            details={"policy_version": A3_L0_CANDIDATE_POLICY_VERSION, **details},
        )
        if self.artifacts_dir is not None:
            path = self.artifacts_dir / "errors" / f"error_{self._error_count:04d}.json"
            self._error_count += 1
            write_json_once(path, record.model_dump(mode="json"))
        return A3L0PipelineError(record)

    async def _resolve_tree_condition(
        self,
        analyst_output: A3AnalystOutput,
        tree_arm: TreeArm,
        oracle_tree: Optional[A3LayoutTree],
    ) -> TreeCondition:
        """T2 plans once and freezes; T0/T1/T3 never invoke the Planner."""
        if tree_arm == "T2":
            tree = await self.planner(analyst_output)
            return make_tree_condition("T2", analyst_output, tree=tree)
        if tree_arm == "T3":
            if oracle_tree is None:
                raise ValueError("T3 requires an externally provided human oracle tree")
            return make_tree_condition("T3", analyst_output, tree=oracle_tree)
        if oracle_tree is not None:
            raise ValueError(f"{tree_arm} must not receive a tree")
        return make_tree_condition(tree_arm, analyst_output)

    async def run(
        self,
        *,
        user_brief: str,
        tree_arm: TreeArm = "T2",
        oracle_tree: Optional[A3LayoutTree] = None,
    ) -> A3L0Result:
        analyst_output = await self.analyst(user_brief)
        self._save("analyst_output.json", analyst_output.model_dump(mode="json"))

        condition = await self._resolve_tree_condition(analyst_output, tree_arm, oracle_tree)
        self._save("tree_condition.json", condition.model_dump(mode="json"))

        concepts = await self.director(analyst_output, condition)
        if len(concepts) != len(R0_SLOT_IDS):
            raise self._fail(
                stage="composition_director",
                error_type="ConceptCountMismatch",
                message=(
                    f"Director must return exactly {len(R0_SLOT_IDS)} concepts, "
                    f"got {len(concepts)}"
                ),
                details={"expected": len(R0_SLOT_IDS), "actual": len(concepts)},
            )

        slots: List[R0SlotRecord] = []
        for slot_id, concept in zip(R0_SLOT_IDS, concepts):
            summary = str(concept)[:500]
            try:
                candidate = await self.mapper(concept, condition)
                render_ref = await self.renderer(candidate, slot_id)
                slots.append(
                    R0SlotRecord(
                        slot_id=slot_id,
                        status="completed",
                        concept_summary=summary,
                        candidate=candidate,
                        render_ref=render_ref,
                        render_sha256=sha256_file(Path(render_ref)),
                    )
                )
            except Exception as error:  # noqa: BLE001 -- per-slot failure is provenance
                slots.append(
                    R0SlotRecord(
                        slot_id=slot_id,
                        status="failed",
                        concept_summary=summary,
                        error=f"{type(error).__name__}: {error}",
                    )
                )

        completed = [slot for slot in slots if slot.status == "completed"]
        if len(completed) != len(R0_SLOT_IDS):
            self._save(
                "r0_bundle.json", R0Bundle(slots=slots).model_dump(mode="json")
            )
            raise self._fail(
                stage="r0_candidates",
                error_type="CandidateShortfall",
                message=(
                    f"only {len(completed)}/{len(R0_SLOT_IDS)} R0 candidates completed; "
                    "Judge-Select requires exactly three complete renders"
                ),
                details={
                    "failed_slots": [
                        {"slot_id": slot.slot_id, "error": slot.error}
                        for slot in slots
                        if slot.status == "failed"
                    ]
                },
            )

        for slot in slots:
            verdict = self.qc(slot.candidate)
            slot.qc_passed = verdict.passed
            slot.qc_violations = list(verdict.violations)

        degradations: List[str] = []
        if not any(slot.qc_passed for slot in slots):
            # Candidates keep their explicit QC-failed marks all the way into
            # the result; nothing is promoted to an unmarked official candidate.
            degradations.append(DEGRADATION_ALL_QC_FAILED)

        bundle = R0Bundle(slots=slots)
        self._save("r0_bundle.json", bundle.model_dump(mode="json"))

        judge_candidates = [
            JudgeSelectCandidate(
                candidate_id=slot.slot_id,
                render_ref=slot.render_ref,
                qc_passed=slot.qc_passed,
                qc_violations=slot.qc_violations,
            )
            for slot in slots
        ]
        selection = await self.judge_select(judge_candidates)
        validate_selection(selection, [slot.slot_id for slot in slots])
        self._save("judge_select_result.json", selection.model_dump(mode="json"))

        result = A3L0Result(
            tree_arm=tree_arm,
            degradations=degradations,
            bundle=bundle,
            judge_select=selection,
            b0_slot_id=selection.selected_candidate_id,
        )
        self._save("l0_result.json", result.model_dump(mode="json"))
        return result
