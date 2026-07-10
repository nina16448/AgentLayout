"""A3 L1-Gated orchestrator: one gated, verified revision on top of L0.

Extends the canonical L0 flow (new_plam.md section 4.3):

    <shared R0 phase from A3L0Pipeline, Analyst/tree frozen>
      -> Judge-Critic inspects B0 only
      -> repair gate
           no actionable issue -> output B0
           actionable issue    -> ONE routed targeted revision -> B1
      -> deterministic verifier per gated issue
      -> B0/B1 guard (issues improved, no new hard violation,
         completeness kept; optional single pairwise internal selection)
      -> keep B0 or B1
      -> stop unconditionally.

A failed revision never fails the sample: B0 already exists, so the guard
falls back to it and the failure is recorded. There is exactly one critic
call and at most one revision per sample by construction; no second round
exists in this module.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable, ClassVar, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from metagpt.ext.agentlayout.a3_pipeline import (
    A3L0Pipeline,
    R0Bundle,
    R0SlotRecord,
    TreeArm,
)
from metagpt.ext.agentlayout.layout_tree_v3 import A3LayoutTree, TreeCondition
from metagpt.ext.agentlayout.run_manifest import sha256_file
from metagpt.ext.agentlayout.tools.judge_critic import (
    JudgeCriticResult,
    validate_critic_targets,
)
from metagpt.ext.agentlayout.tools.judge_select import JudgeSelectResult
from metagpt.ext.agentlayout.tools.repair_gate import (
    B0B1GuardResult,
    IssueVerification,
    RepairDecision,
    check_b1_against_b0,
    evaluate_repair_gate,
    resolve_winner,
)


A3_L1_PIPELINE_VERSION = "a3.l1-pipeline.v1"
B1_SLOT_ID = "b1_candidate"


class A3L1Result(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pipeline_version: Literal["a3.l1-pipeline.v1"] = A3_L1_PIPELINE_VERSION
    loop: Literal["L1-Gated"] = "L1-Gated"
    tree_arm: TreeArm
    degradations: List[str] = Field(default_factory=list)
    bundle: R0Bundle
    judge_select: JudgeSelectResult
    b0_slot_id: str
    critic: JudgeCriticResult
    repair_decision: RepairDecision
    repair_attempted: bool
    b1: Optional[R0SlotRecord] = None
    verifications: Optional[List[IssueVerification]] = None
    guard: Optional[B0B1GuardResult] = None
    final_slot_id: str
    stop_reason: Literal["l1_unconditional_stop"] = "l1_unconditional_stop"


class A3L1GatedPipeline(A3L0Pipeline):
    """L0 plus at most one verified revision; then an unconditional stop."""

    LOOP: ClassVar[str] = "L1-Gated"

    def __init__(
        self,
        *,
        judge_critic: Callable[[R0SlotRecord, List[str]], Awaitable[JudgeCriticResult]],
        repair: Callable[[R0SlotRecord, RepairDecision, TreeCondition], Awaitable[Dict[str, Any]]],
        verifier: Callable[[RepairDecision, R0SlotRecord, R0SlotRecord], List[IssueVerification]],
        pairwise_select: Optional[
            Callable[[R0SlotRecord, R0SlotRecord], Awaitable[Literal["B0", "B1"]]]
        ] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.judge_critic = judge_critic
        self.repair = repair
        self.verifier = verifier
        self.pairwise_select = pairwise_select

    async def _revise_once(
        self,
        b0: R0SlotRecord,
        decision: RepairDecision,
        condition: TreeCondition,
    ) -> R0SlotRecord:
        candidate = await self.repair(b0, decision, condition)
        render_ref = await self.renderer(candidate, B1_SLOT_ID)
        verdict = self.qc(candidate)
        return R0SlotRecord(
            slot_id=B1_SLOT_ID,
            status="completed",
            concept_summary=b0.concept_summary,
            candidate=candidate,
            render_ref=render_ref,
            render_sha256=sha256_file(Path(render_ref)),
            qc_passed=verdict.passed,
            qc_violations=list(verdict.violations),
            qc_completeness=verdict.completeness,
        )

    async def run(
        self,
        *,
        user_brief: str,
        tree_arm: TreeArm = "T2",
        oracle_tree: Optional[A3LayoutTree] = None,
    ) -> A3L1Result:
        outcome = await self._run_r0_phase(
            user_brief=user_brief, tree_arm=tree_arm, oracle_tree=oracle_tree
        )
        b0 = next(
            slot
            for slot in outcome.bundle.slots
            if slot.slot_id == outcome.selection.selected_candidate_id
        )
        known_asset_ids = [asset.asset_id for asset in outcome.analyst_output.assets]

        critic = await self.judge_critic(b0, known_asset_ids)
        validate_critic_targets(critic, known_asset_ids)
        self._save("judge_critic_result.json", critic.model_dump(mode="json"))

        decision = evaluate_repair_gate(critic, known_asset_ids)
        self._save("repair_decision.json", decision.model_dump(mode="json"))

        b1: Optional[R0SlotRecord] = None
        verifications: Optional[List[IssueVerification]] = None
        guard: Optional[B0B1GuardResult] = None
        repair_attempted = decision.outcome == "one_targeted_repair"

        if repair_attempted:
            try:
                b1 = await self._revise_once(b0, decision, outcome.condition)
            except Exception as error:  # noqa: BLE001 -- B0 already exists; fall back
                record = self._record_error(
                    stage="l1_targeted_revision",
                    error_type="RepairExecutionFailed",
                    message=f"{type(error).__name__}: {error}",
                    details={"b0_slot_id": b0.slot_id, "route": decision.route},
                )
                b1 = R0SlotRecord(
                    slot_id=B1_SLOT_ID,
                    status="failed",
                    concept_summary=b0.concept_summary,
                    error=record.message,
                )
                guard = B0B1GuardResult(
                    deterministic_passed=False,
                    reasons=[f"repair_execution_failed: {record.message}"],
                    winner="B0",
                )
            if guard is None:
                verifications = self.verifier(decision, b0, b1)
                self._save(
                    "issue_verifications.json",
                    [verification.model_dump(mode="json") for verification in verifications],
                )
                check = check_b1_against_b0(
                    verifications=verifications,
                    issue_count=len(decision.issues),
                    b0_violations=b0.qc_violations,
                    b1_violations=b1.qc_violations,
                    b0_completeness=b0.qc_completeness,
                    b1_completeness=b1.qc_completeness,
                )
                pairwise_winner: Optional[Literal["B0", "B1"]] = None
                pairwise_used = False
                if check.passed and self.pairwise_select is not None:
                    pairwise_winner = await self.pairwise_select(b0, b1)
                    pairwise_used = True
                guard = B0B1GuardResult(
                    deterministic_passed=check.passed,
                    reasons=list(check.reasons)
                    + (["pairwise_internal_selection"] if pairwise_used else []),
                    pairwise_used=pairwise_used,
                    winner=resolve_winner(check, pairwise_winner),
                )
            self._save("b1_candidate.json", b1.model_dump(mode="json"))
            self._save("b0b1_guard.json", guard.model_dump(mode="json"))

        final_slot_id = (
            B1_SLOT_ID if guard is not None and guard.winner == "B1" else b0.slot_id
        )
        result = A3L1Result(
            tree_arm=tree_arm,
            degradations=outcome.degradations,
            bundle=outcome.bundle,
            judge_select=outcome.selection,
            b0_slot_id=b0.slot_id,
            critic=critic,
            repair_decision=decision,
            repair_attempted=repair_attempted,
            b1=b1,
            verifications=verifications,
            guard=guard,
            final_slot_id=final_slot_id,
        )
        self._save("l1_result.json", result.model_dump(mode="json"))
        return result
