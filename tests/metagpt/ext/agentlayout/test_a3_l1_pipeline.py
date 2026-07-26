from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import pytest
from PIL import Image

from metagpt.ext.agentlayout.a3_config import A3RunConfig, ModelCallConfig
from metagpt.ext.agentlayout.a3_pipeline import QCVerdict, R0SlotRecord
from metagpt.ext.agentlayout.a3_pipeline_l1 import (
    B1_SLOT_ID,
    A3L1GatedPipeline,
)
from metagpt.ext.agentlayout.layout_tree_v3 import (
    A3LayoutTree,
    A3TreeGroup,
    A3TreeNode,
    TreeCondition,
)
from metagpt.ext.agentlayout.tools.analyst_vision import (
    A3AnalystOutput,
    A3AssetUnderstanding,
)
from metagpt.ext.agentlayout.tools.judge_critic import (
    ActionableIssue,
    JudgeCriticResult,
)
from metagpt.ext.agentlayout.tools.judge_select import (
    JudgeSelectCandidate,
    JudgeSelectResult,
)
from metagpt.ext.agentlayout.tools.repair_gate import (
    IssueVerification,
    RepairDecision,
    RepairRoute,
)


MODEL = "gpt-5.4-mini-2026-03-17"
L1_STAGES = (
    "analyst",
    "asset_planner",
    "composition_director",
    "coordinate_mapper",
    "judge_select",
    "judge_critic",
)


def _config(loop: str = "L1-Gated") -> A3RunConfig:
    stages = L1_STAGES if loop == "L1-Gated" else L1_STAGES[:-1]
    return A3RunConfig(
        loop=loop,
        internal_judge=MODEL,
        dataset_split="crello-test",
        models={stage: ModelCallConfig(model=MODEL) for stage in stages},
    )


def _analyst_output() -> A3AnalystOutput:
    return A3AnalystOutput(
        background_summary="Quiet blue background",
        design_intent="Promote a summer sale",
        style_keywords=["bright"],
        assets=[
            A3AssetUnderstanding(
                asset_id="asset_0001",
                semantic_type="title",
                description="Main sale heading",
                semantic_role="primary message",
            ),
            A3AssetUnderstanding(
                asset_id="asset_0002",
                semantic_type="pricetag",
                description="Discount price",
                semantic_role="offer qualifier",
            ),
            A3AssetUnderstanding(
                asset_id="asset_0003",
                semantic_type="product_image",
                description="Featured shoe",
                semantic_role="focal product",
            ),
        ],
    )


def _tree() -> A3LayoutTree:
    return A3LayoutTree(
        source="predicted",
        nodes=[
            A3TreeNode(
                asset_id="asset_0001",
                semantic_type="title",
                semantic_role="primary message",
                group_id="group_offer",
                group_label="offer lockup",
                parent_id="root",
                relation_to_parent="root",
                ordering_priority=0,
                confidence=0.95,
            ),
            A3TreeNode(
                asset_id="asset_0002",
                semantic_type="pricetag",
                semantic_role="offer qualifier",
                group_id="group_offer",
                group_label="offer lockup",
                parent_id="asset_0001",
                relation_to_parent="qualifies",
                ordering_priority=1,
                confidence=0.9,
            ),
            A3TreeNode(
                asset_id="asset_0003",
                semantic_type="product_image",
                semantic_role="focal product",
                group_id="group_product",
                group_label="product",
                parent_id="root",
                relation_to_parent="root",
                ordering_priority=0,
                confidence=0.92,
            ),
        ],
        groups=[
            A3TreeGroup(
                group_id="group_offer",
                label="offer lockup",
                member_ids=["asset_0001", "asset_0002"],
                ordering_priority=0,
                confidence=0.94,
            ),
            A3TreeGroup(
                group_id="group_product",
                label="product",
                member_ids=["asset_0003"],
                ordering_priority=1,
                confidence=0.92,
            ),
        ],
    )


def _overlap_issue(targets=None) -> ActionableIssue:
    return ActionableIssue(
        target_asset_ids=targets or ["asset_0001"],
        issue_type="overlap",
        observation="headline overlaps the product image",
        desired_change="move asset_0001 above asset_0003 with clear separation",
    )


class L1Stages:
    """Fake L1 stage callables with call accounting."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        critic_result: Optional[JudgeCriticResult] = None,
        b0_verdict: Optional[QCVerdict] = None,
        b1_verdict: Optional[QCVerdict] = None,
        verifier_improved: bool = True,
        repair_error: bool = False,
        pairwise_result: Optional[str] = None,
    ):
        self.tmp_path = tmp_path
        self.critic_result = (
            critic_result
            if critic_result is not None
            else JudgeCriticResult(issues=[_overlap_issue()])
        )
        self.b0_verdict = b0_verdict or QCVerdict(passed=True)
        self.b1_verdict = b1_verdict or QCVerdict(passed=True)
        self.verifier_improved = verifier_improved
        self.repair_error = repair_error
        self.pairwise_result = pairwise_result
        self.analyst_calls = 0
        self.planner_calls = 0
        self.director_calls = 0
        self.mapper_calls = 0
        self.critic_calls = 0
        self.repair_calls = 0
        self.verifier_calls = 0
        self.pairwise_calls = 0
        self.judge_calls = 0
        self.mapper_conditions: List[TreeCondition] = []
        self.repair_condition: Optional[TreeCondition] = None
        self.repair_decision: Optional[RepairDecision] = None

    async def analyst(self, user_brief: str) -> A3AnalystOutput:
        self.analyst_calls += 1
        return _analyst_output()

    async def planner(self, analyst_output: A3AnalystOutput) -> A3LayoutTree:
        self.planner_calls += 1
        return _tree()

    async def director(self, analyst_output, condition) -> List[str]:
        self.director_calls += 1
        return ["concept_0", "concept_1", "concept_2"]

    async def mapper(self, concept, condition) -> Dict[str, Any]:
        self.mapper_calls += 1
        self.mapper_conditions.append(condition)
        return {"concept": str(concept)}

    async def renderer(self, candidate: Dict[str, Any], slot_id: str) -> str:
        path = self.tmp_path / f"{slot_id}.png"
        if not path.exists():
            Image.new("RGB", (4, 4), "white").save(path)
        return str(path)

    def qc(self, candidate: Dict[str, Any]) -> QCVerdict:
        return self.b1_verdict if candidate.get("revised") else self.b0_verdict

    async def judge_select(self, candidates: List[JudgeSelectCandidate]) -> JudgeSelectResult:
        self.judge_calls += 1
        return JudgeSelectResult(
            ranking=["r0_candidate_02", "r0_candidate_01", "r0_candidate_03"],
            selected_candidate_id="r0_candidate_02",
        )

    async def judge_critic(
        self, b0: R0SlotRecord, known_asset_ids: List[str]
    ) -> JudgeCriticResult:
        self.critic_calls += 1
        return self.critic_result

    async def repair(
        self, b0: R0SlotRecord, decision: RepairDecision, condition: TreeCondition
    ) -> Dict[str, Any]:
        self.repair_calls += 1
        self.repair_condition = condition
        self.repair_decision = decision
        if self.repair_error:
            raise RuntimeError("revision output failed schema validation 3 times")
        return {"revised": True, "base": b0.slot_id}

    def verifier(
        self, decision: RepairDecision, b0: R0SlotRecord, b1: R0SlotRecord
    ) -> List[IssueVerification]:
        self.verifier_calls += 1
        return [
            IssueVerification(
                issue_index=index,
                improved=self.verifier_improved,
                evidence="deterministic geometry comparison of B0 vs B1",
            )
            for index in range(len(decision.issues))
        ]

    async def pairwise_select(
        self, b0: R0SlotRecord, b1: R0SlotRecord
    ) -> Literal["B0", "B1"]:
        self.pairwise_calls += 1
        return self.pairwise_result


def _pipeline(stages: L1Stages, *, config=None, artifacts_dir=None, with_pairwise=False):
    return A3L1GatedPipeline(
        config=config or _config(),
        analyst=stages.analyst,
        planner=stages.planner,
        director=stages.director,
        mapper=stages.mapper,
        renderer=stages.renderer,
        qc=stages.qc,
        judge_select=stages.judge_select,
        judge_critic=stages.judge_critic,
        repair=stages.repair,
        verifier=stages.verifier,
        pairwise_select=stages.pairwise_select if with_pairwise else None,
        artifacts_dir=artifacts_dir,
    )


def test_no_actionable_issue_outputs_b0_without_any_revision(tmp_path):
    stages = L1Stages(tmp_path, critic_result=JudgeCriticResult(issues=[]))
    result = asyncio.run(_pipeline(stages).run(user_brief="brief"))
    assert stages.critic_calls == 1
    assert stages.repair_calls == 0
    assert stages.verifier_calls == 0
    assert result.repair_attempted is False
    assert result.b1 is None and result.guard is None and result.verifications is None
    assert result.final_slot_id == result.b0_slot_id == "r0_candidate_02"
    assert result.stop_reason == "l1_unconditional_stop"
    assert result.repair_decision.outcome == "no_actionable_issue"


def test_verified_revision_is_kept_as_b1(tmp_path):
    stages = L1Stages(tmp_path)
    result = asyncio.run(_pipeline(stages).run(user_brief="brief"))
    assert stages.repair_calls == 1
    assert stages.verifier_calls == 1
    assert result.repair_attempted is True
    assert result.repair_decision.route is RepairRoute.COORDINATE_MAPPER
    assert result.guard.deterministic_passed is True
    assert result.guard.pairwise_used is False
    assert result.guard.winner == "B1"
    assert result.final_slot_id == B1_SLOT_ID
    assert result.b1.status == "completed"
    assert result.b1.render_sha256
    # The revision consumed the same frozen tree condition as the R0 mappers.
    assert stages.repair_condition is stages.mapper_conditions[0]
    assert stages.repair_decision is result.repair_decision


def test_upstream_stages_stay_frozen_and_revision_happens_once(tmp_path):
    stages = L1Stages(tmp_path)
    asyncio.run(_pipeline(stages).run(user_brief="brief"))
    assert stages.analyst_calls == 1
    assert stages.planner_calls == 1
    assert stages.director_calls == 1
    assert stages.mapper_calls == 3
    assert stages.judge_calls == 1
    assert stages.critic_calls == 1
    assert stages.repair_calls == 1


def test_unimproved_issue_keeps_b0(tmp_path):
    stages = L1Stages(tmp_path, verifier_improved=False)
    result = asyncio.run(_pipeline(stages).run(user_brief="brief"))
    assert result.guard.winner == "B0"
    assert result.final_slot_id == "r0_candidate_02"
    assert any("issues_not_improved" in reason for reason in result.guard.reasons)


def test_new_hard_violation_in_b1_keeps_b0(tmp_path):
    stages = L1Stages(
        tmp_path,
        b1_verdict=QCVerdict(passed=False, violations=["text clipped at canvas edge"]),
    )
    result = asyncio.run(_pipeline(stages).run(user_brief="brief"))
    assert result.guard.winner == "B0"
    assert any("new_hard_violations" in reason for reason in result.guard.reasons)


def test_completeness_drop_in_b1_keeps_b0(tmp_path):
    stages = L1Stages(
        tmp_path,
        b0_verdict=QCVerdict(passed=True, completeness=1.0),
        b1_verdict=QCVerdict(passed=True, completeness=0.5),
    )
    result = asyncio.run(_pipeline(stages).run(user_brief="brief"))
    assert result.guard.winner == "B0"
    assert any("completeness_decreased" in reason for reason in result.guard.reasons)


def test_pairwise_selection_can_demote_but_not_rescue(tmp_path):
    demote = L1Stages(tmp_path, pairwise_result="B0")
    result = asyncio.run(_pipeline(demote, with_pairwise=True).run(user_brief="brief"))
    assert demote.pairwise_calls == 1
    assert result.guard.pairwise_used is True
    assert result.guard.winner == "B0"
    assert result.final_slot_id == "r0_candidate_02"

    confirm_dir = tmp_path / "confirm"
    confirm_dir.mkdir()
    confirm = L1Stages(confirm_dir, pairwise_result="B1")
    result = asyncio.run(_pipeline(confirm, with_pairwise=True).run(user_brief="brief"))
    assert result.guard.winner == "B1"

    failed_dir = tmp_path / "failed_check"
    failed_dir.mkdir()
    rescued = L1Stages(failed_dir, verifier_improved=False, pairwise_result="B1")
    result = asyncio.run(_pipeline(rescued, with_pairwise=True).run(user_brief="brief"))
    assert rescued.pairwise_calls == 0
    assert result.guard.winner == "B0"


def test_failed_revision_falls_back_to_b0_with_error_record(tmp_path):
    stages = L1Stages(tmp_path, repair_error=True)
    artifacts = tmp_path / "artifacts_failed"
    result = asyncio.run(
        _pipeline(stages, artifacts_dir=artifacts).run(user_brief="brief")
    )
    assert result.repair_attempted is True
    assert result.b1.status == "failed"
    assert result.verifications is None
    assert result.guard.winner == "B0"
    assert any("repair_execution_failed" in reason for reason in result.guard.reasons)
    assert result.final_slot_id == "r0_candidate_02"
    record = json.loads((artifacts / "errors" / "error_0000.json").read_text())
    assert record["error_type"] == "RepairExecutionFailed"


def test_invalid_critic_targets_are_rejected(tmp_path):
    stages = L1Stages(
        tmp_path,
        critic_result=JudgeCriticResult(issues=[_overlap_issue(targets=["asset_9999"])]),
    )
    with pytest.raises(ValueError, match="unknown asset IDs"):
        asyncio.run(_pipeline(stages).run(user_brief="brief"))


def test_l1_artifacts_are_written_once(tmp_path):
    artifacts = tmp_path / "artifacts_l1"
    stages = L1Stages(tmp_path)
    asyncio.run(_pipeline(stages, artifacts_dir=artifacts).run(user_brief="brief"))
    for filename in (
        "analyst_output.json",
        "tree_condition.json",
        "r0_bundle.json",
        "judge_select_result.json",
        "judge_critic_result.json",
        "repair_decision.json",
        "b1_candidate.json",
        "issue_verifications.json",
        "b0b1_guard.json",
        "l1_result.json",
    ):
        assert (artifacts / filename).exists()
    assert not (artifacts / "l0_result.json").exists()
    with pytest.raises(FileExistsError):
        _pipeline(L1Stages(tmp_path), artifacts_dir=artifacts)


def test_run_from_r0_reuses_a_persisted_r0_phase_without_upstream_calls(tmp_path):
    # Gate C contract: the L1 arm consumes the L0 arm's exact R0 outcome, so
    # the two arms differ by the revision tail alone.
    producer = L1Stages(tmp_path)
    full = asyncio.run(_pipeline(producer).run(user_brief="brief"))

    consumer = L1Stages(tmp_path)
    pipeline = _pipeline(consumer)
    from metagpt.ext.agentlayout.a3_pipeline import R0PhaseOutcome

    outcome = R0PhaseOutcome(
        analyst_output=_analyst_output(),
        condition=producer.mapper_conditions[0],
        bundle=full.bundle,
        degradations=list(full.degradations),
        selection=full.judge_select,
    )
    tail = asyncio.run(pipeline.run_from_r0(outcome=outcome))

    # No upstream stage ran on the consumer side; only the tail did.
    assert consumer.analyst_calls == 0
    assert consumer.planner_calls == 0
    assert consumer.director_calls == 0
    assert consumer.judge_calls == 0
    assert consumer.critic_calls == 1
    assert consumer.repair_calls == 1
    # The tail worked on the same B0 and reached the same kind of result.
    assert tail.b0_slot_id == full.b0_slot_id
    assert tail.bundle == full.bundle
    assert tail.judge_select == full.judge_select
    assert tail.stop_reason == "l1_unconditional_stop"


def test_l1_pipeline_rejects_a_mismatched_loop_config(tmp_path):
    stages = L1Stages(tmp_path)
    with pytest.raises(ValueError, match="implements loop='L1-Gated'"):
        _pipeline(stages, config=_config("L0"))


def test_l1_pipeline_source_has_no_legacy_loop_constructs():
    repo = Path(__file__).resolve().parents[4]
    source = (repo / "metagpt/ext/agentlayout/a3_pipeline_l1.py").read_text()
    for forbidden in (
        "ACCEPT",
        "REJECT",
        "threshold",
        "consecutive",
        "max_total_rounds",
        "ledger",
        "polish",
        "IterationState",
        "reroute",
        "while ",
    ):
        assert forbidden not in source, f"legacy loop construct found: {forbidden}"
