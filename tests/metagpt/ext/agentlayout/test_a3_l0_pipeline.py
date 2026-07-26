from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from PIL import Image

from metagpt.ext.agentlayout.a3_config import A3RunConfig, ModelCallConfig
from metagpt.ext.agentlayout.a3_pipeline import (
    A3L0Pipeline,
    A3L0PipelineError,
    QCVerdict,
    R0_SLOT_IDS,
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
from metagpt.ext.agentlayout.tools.judge_critic import build_judge_critic_request
from metagpt.ext.agentlayout.tools.judge_select import (
    JudgeSelectCandidate,
    JudgeSelectResult,
    build_judge_select_request,
)


MODEL = "gpt-5.4-mini-2026-03-17"
STAGES = ("analyst", "asset_planner", "composition_director", "coordinate_mapper", "judge_select")


def _config(loop: str = "L0") -> A3RunConfig:
    stages = list(STAGES) + (["judge_critic"] if loop == "L1-Gated" else [])
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


def _tree(source: str = "predicted") -> A3LayoutTree:
    return A3LayoutTree(
        source=source,
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


class Stages:
    """Fake stage callables with call accounting for orchestration tests."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        concept_count: int = 3,
        failing_slots: Optional[set] = None,
        qc_pass: bool = True,
        selection: Optional[JudgeSelectResult] = None,
    ):
        self.tmp_path = tmp_path
        self.concept_count = concept_count
        self.failing_slots = failing_slots or set()
        self.qc_pass = qc_pass
        self.selection = selection or JudgeSelectResult(
            ranking=["r0_candidate_02", "r0_candidate_01", "r0_candidate_03"],
            selected_candidate_id="r0_candidate_02",
        )
        self.analyst_calls = 0
        self.planner_calls = 0
        self.director_calls = 0
        self.mapper_calls = 0
        self.qc_calls = 0
        self.judge_calls = 0
        self.mapper_conditions: List[TreeCondition] = []
        self.judge_candidates: List[JudgeSelectCandidate] = []

    async def analyst(self, user_brief: str) -> A3AnalystOutput:
        self.analyst_calls += 1
        return _analyst_output()

    async def planner(self, analyst_output: A3AnalystOutput) -> A3LayoutTree:
        self.planner_calls += 1
        return _tree()

    async def director(self, analyst_output, condition) -> List[str]:
        self.director_calls += 1
        return [f"concept_{index}" for index in range(self.concept_count)]

    async def mapper(self, concept, condition) -> Dict[str, Any]:
        self.mapper_calls += 1
        self.mapper_conditions.append(condition)
        if self.mapper_calls - 1 in self.failing_slots:
            raise RuntimeError("mapper parse failure after retries")
        return {"concept": str(concept), "elements": ["asset_0001", "asset_0002", "asset_0003"]}

    async def renderer(self, candidate: Dict[str, Any], slot_id: str) -> str:
        path = self.tmp_path / f"{slot_id}.png"
        if not path.exists():
            Image.new("RGB", (4, 4), "white").save(path)
        return str(path)

    def qc(self, candidate: Dict[str, Any]) -> QCVerdict:
        self.qc_calls += 1
        if self.qc_pass:
            return QCVerdict(passed=True)
        return QCVerdict(passed=False, violations=["text below minimum size"])

    async def judge_select(self, candidates: List[JudgeSelectCandidate]) -> JudgeSelectResult:
        self.judge_calls += 1
        self.judge_candidates = candidates
        return self.selection


def _pipeline(stages: Stages, *, config: Optional[A3RunConfig] = None, artifacts_dir=None):
    return A3L0Pipeline(
        config=config or _config(),
        analyst=stages.analyst,
        planner=stages.planner,
        director=stages.director,
        mapper=stages.mapper,
        renderer=stages.renderer,
        qc=stages.qc,
        judge_select=stages.judge_select,
        artifacts_dir=artifacts_dir,
    )


def test_l0_selects_b0_and_stops_unconditionally(tmp_path):
    stages = Stages(tmp_path)
    result = asyncio.run(_pipeline(stages).run(user_brief="Summer sale poster"))

    assert (stages.analyst_calls, stages.planner_calls) == (1, 1)
    assert stages.director_calls == 1
    assert stages.mapper_calls == 3
    assert stages.qc_calls == 3
    assert stages.judge_calls == 1
    assert result.loop == "L0"
    assert result.stop_reason == "l0_unconditional_stop"
    assert result.b0_slot_id == "r0_candidate_02"
    assert result.degradations == []
    assert [slot.slot_id for slot in result.bundle.slots] == list(R0_SLOT_IDS)
    assert all(slot.status == "completed" for slot in result.bundle.slots)
    assert all(slot.render_sha256 for slot in result.bundle.slots)
    assert len(stages.judge_candidates) == 3


def test_selection_and_critique_are_two_independent_calls(tmp_path):
    stages = Stages(tmp_path)
    pipeline = _pipeline(stages)
    result = asyncio.run(pipeline.run(user_brief="Summer sale poster"))

    # L0 performed exactly one judge call, and the orchestrator has no critic hook.
    assert stages.judge_calls == 1
    assert not hasattr(pipeline, "judge_critic")
    assert not hasattr(result.judge_select, "issues")

    # A critique of B0 is a second, separate request with its own contract.
    b0 = next(s for s in result.bundle.slots if s.slot_id == result.b0_slot_id)
    select_request = build_judge_select_request(stages.judge_candidates)
    critic_request = build_judge_critic_request(
        b0_candidate_id=b0.slot_id,
        render_ref=b0.render_ref,
        known_asset_ids=["asset_0001", "asset_0002", "asset_0003"],
    )
    assert select_request.prompt_sha256 != critic_request.prompt_sha256
    assert "Judge-Select" in select_request.prompt
    assert "Judge-Critic" in critic_request.prompt
    assert critic_request.b0_candidate_id == result.b0_slot_id


def test_director_must_return_exactly_three_concepts(tmp_path):
    stages = Stages(tmp_path, concept_count=2)
    artifacts = tmp_path / "artifacts_concepts"
    with pytest.raises(A3L0PipelineError, match="ConceptCountMismatch"):
        asyncio.run(
            _pipeline(stages, artifacts_dir=artifacts).run(user_brief="brief")
        )
    assert stages.judge_calls == 0
    record = json.loads((artifacts / "errors" / "error_0000.json").read_text())
    assert record["error_type"] == "ConceptCountMismatch"
    assert record["details"]["policy_version"] == "a3.l0-candidate-policy.v1"


def test_candidate_shortfall_fails_closed_without_judge_call(tmp_path):
    stages = Stages(tmp_path, failing_slots={1})
    artifacts = tmp_path / "artifacts_shortfall"
    with pytest.raises(A3L0PipelineError, match="CandidateShortfall"):
        asyncio.run(
            _pipeline(stages, artifacts_dir=artifacts).run(user_brief="brief")
        )
    assert stages.judge_calls == 0
    bundle = json.loads((artifacts / "r0_bundle.json").read_text())
    statuses = {slot["slot_id"]: slot["status"] for slot in bundle["slots"]}
    assert statuses["r0_candidate_02"] == "failed"
    assert bundle["policy_version"] == "a3.l0-candidate-policy.v1"
    record = json.loads((artifacts / "errors" / "error_0000.json").read_text())
    assert record["error_type"] == "CandidateShortfall"
    assert record["details"]["failed_slots"][0]["slot_id"] == "r0_candidate_02"


def test_all_qc_fail_is_marked_degradation_not_silent_promotion(tmp_path):
    stages = Stages(tmp_path, qc_pass=False)
    result = asyncio.run(_pipeline(stages).run(user_brief="brief"))
    assert stages.judge_calls == 1
    assert result.degradations == ["all_qc_failed"]
    assert all(slot.qc_passed is False for slot in result.bundle.slots)
    assert all(slot.qc_violations for slot in result.bundle.slots)
    assert all(not candidate.qc_passed for candidate in stages.judge_candidates)


def test_invalid_judge_permutation_is_rejected(tmp_path):
    stages = Stages(
        tmp_path,
        selection=JudgeSelectResult(
            ranking=["r0_candidate_02", "r0_candidate_01", "r0_candidate_99"],
            selected_candidate_id="r0_candidate_02",
        ),
    )
    with pytest.raises(ValueError, match="permutation"):
        asyncio.run(_pipeline(stages).run(user_brief="brief"))


def test_t0_and_t1_never_plan_and_never_gain_a_tree(tmp_path):
    for arm, has_roles in (("T0", False), ("T1", True)):
        arm_dir = tmp_path / arm
        arm_dir.mkdir(exist_ok=True)
        stages = Stages(arm_dir)
        result = asyncio.run(_pipeline(stages).run(user_brief="brief", tree_arm=arm))
        assert stages.planner_calls == 0
        assert result.tree_arm == arm
        assert len(stages.mapper_conditions) == 3
        first = stages.mapper_conditions[0]
        assert all(condition is first for condition in stages.mapper_conditions)
        assert first.tree is None
        assert (first.flat_roles is not None) is has_roles


def test_t3_requires_external_oracle_and_skips_planner(tmp_path):
    stages = Stages(tmp_path)
    with pytest.raises(ValueError, match="human oracle tree"):
        asyncio.run(_pipeline(stages).run(user_brief="brief", tree_arm="T3"))

    stages = Stages(tmp_path)
    result = asyncio.run(
        _pipeline(stages).run(
            user_brief="brief", tree_arm="T3", oracle_tree=_tree("human_oracle")
        )
    )
    assert stages.planner_calls == 0
    assert stages.mapper_conditions[0].tree.source == "human_oracle"
    assert result.tree_arm == "T3"


def test_artifacts_are_write_once_and_run_dirs_cannot_be_reused(tmp_path):
    artifacts = tmp_path / "artifacts_happy"
    stages = Stages(tmp_path)
    asyncio.run(_pipeline(stages, artifacts_dir=artifacts).run(user_brief="brief"))
    for filename in (
        "analyst_output.json",
        "tree_condition.json",
        "r0_bundle.json",
        "judge_select_result.json",
        "l0_result.json",
    ):
        assert (artifacts / filename).exists()
    assert list((artifacts / "errors").iterdir()) == []
    with pytest.raises(FileExistsError):
        _pipeline(Stages(tmp_path), artifacts_dir=artifacts)


def test_l0_pipeline_rejects_a_mismatched_loop_config(tmp_path):
    stages = Stages(tmp_path)
    with pytest.raises(ValueError, match="implements loop='L0'"):
        _pipeline(stages, config=_config("L1-Gated"))


def test_pipeline_source_has_no_legacy_loop_constructs():
    repo = Path(__file__).resolve().parents[4]
    source = (repo / "metagpt/ext/agentlayout/a3_pipeline.py").read_text()
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
    assert "LayoutPipeline" not in source.replace(
        "pipeline.LayoutPipeline", ""
    ), "A3 orchestrator must not delegate to the legacy pipeline"
