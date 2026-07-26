from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from PIL import Image

from metagpt.ext.agentlayout.a3_config import A3RunConfig, ModelCallConfig
from metagpt.ext.agentlayout.a3_pipeline import A3L0Pipeline
from metagpt.ext.agentlayout.a3_pipeline_l1 import A3L1GatedPipeline
from metagpt.ext.agentlayout.a3_stage_binding import A3StageBinding
from metagpt.ext.agentlayout.layout_tree_v3 import (
    A3LayoutTree,
    A3TreeGroup,
    A3TreeNode,
)
from metagpt.ext.agentlayout.schema import Candidate, CompositionConcept
from metagpt.ext.agentlayout.tools.analyst_vision import (
    A3AnalystOutput,
    A3AssetUnderstanding,
)
from metagpt.ext.agentlayout.tools.director_contract import A3ConceptSet
from metagpt.ext.agentlayout.tools.judge_critic import (
    ActionableIssue,
    JudgeCriticResult,
)
from metagpt.ext.agentlayout.tools.judge_select import JudgeSelectResult
from metagpt.ext.agentlayout.tools.text_bitmap_normalizer import (
    R3Asset,
    R3AssetManifest,
    R3NormalizationConfig,
)


MODEL = "gpt-5.4-mini-2026-03-17"
REPO = Path(__file__).resolve().parents[4]


def _config(loop: str = "L0") -> A3RunConfig:
    stages = ["analyst", "asset_planner", "composition_director", "coordinate_mapper", "judge_select"]
    if loop == "L1-Gated":
        stages.append("judge_critic")
    return A3RunConfig(
        loop=loop,
        internal_judge=MODEL,
        dataset_split="crello-cached-smoke",
        models={stage: ModelCallConfig(model=MODEL) for stage in stages},
    )


def _manifest(tmp_path: Path) -> R3AssetManifest:
    def _png(name: str, size=(64, 32)) -> str:
        path = tmp_path / name
        if not path.exists():
            Image.new("RGBA", size, (200, 40, 40, 255)).save(path)
        return str(path)

    return R3AssetManifest(
        sample_id="sample01",
        canvas_width=800,
        canvas_height=600,
        normalization=R3NormalizationConfig(),
        source_pfull_manifest_sha256="2" * 64,
        assets=[
            R3Asset(
                asset_id="asset_0001",
                role="placeable",
                media_type="text_bitmap",
                content="SUMMER SALE",
                asset_ref=_png("asset_0001_r3_text.png", (96, 18)),
                sha256="0" * 64,
                bitmap_width=96,
                bitmap_height=18,
                bitmap_aspect_ratio=96 / 18,
            ),
            R3Asset(
                asset_id="asset_0002",
                role="placeable",
                media_type="raster",
                content=None,
                asset_ref=_png("asset_0002.png", (64, 64)),
                sha256="1" * 64,
                bitmap_width=64,
                bitmap_height=64,
                bitmap_aspect_ratio=1.0,
            ),
        ],
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
                group_id="group_main",
                group_label="main",
                parent_id="root",
                relation_to_parent="root",
                ordering_priority=0,
                confidence=0.9,
            ),
            A3TreeNode(
                asset_id="asset_0002",
                semantic_type="product_image",
                semantic_role="focal product",
                group_id="group_main",
                group_label="main",
                parent_id="root",
                relation_to_parent="root",
                ordering_priority=1,
                confidence=0.9,
            ),
        ],
        groups=[
            A3TreeGroup(
                group_id="group_main",
                label="main",
                member_ids=["asset_0001", "asset_0002"],
                ordering_priority=0,
                confidence=0.9,
            )
        ],
    )


def _concept(name: str) -> CompositionConcept:
    return CompositionConcept(
        name=name,
        focal_element="asset_0002",
        focal_placement="left half",
        text_placement="right third",
        visual_flow="Z pattern",
        whitespace="wide margins",
        typography_mood="bold sans",
    )


def _candidate(shift: int = 0) -> Candidate:
    return Candidate(
        candidate_id="candidate",
        elements=[
            {"id": "asset_0001", "left": 400 + shift, "top": 60, "width": 320, "height": 60, "z_index": 1},
            {"id": "asset_0002", "left": 40, "top": 100, "width": 300, "height": 300, "z_index": 0},
        ],
    )


class FakeActions:
    """Duck-typed stand-ins for the six real A3 Actions."""

    def __init__(self, critic_issues: Optional[List[ActionableIssue]] = None):
        self.kwargs: Dict[str, List[Dict[str, Any]]] = {}
        self.critic_issues = critic_issues or []

    def _log(self, stage: str, kwargs: Dict[str, Any]) -> None:
        self.kwargs.setdefault(stage, []).append(kwargs)

    @property
    def analyst_action(self):
        outer = self

        class _A:
            async def run(self, **kwargs):
                outer._log("analyst", kwargs)
                return _analyst_output()

        return _A()

    @property
    def planner_action(self):
        outer = self

        class _A:
            async def run(self, **kwargs):
                outer._log("planner", kwargs)
                return _tree()

        return _A()

    @property
    def director_action(self):
        outer = self

        class _A:
            async def run(self, **kwargs):
                outer._log("director", kwargs)
                return A3ConceptSet(
                    concepts=[_concept("Left bleed"), _concept("Top banner"), _concept("Centered")]
                )

        return _A()

    @property
    def mapper_action(self):
        outer = self

        class _A:
            async def run(self, **kwargs):
                outer._log("mapper", kwargs)
                shift = 40 if kwargs.get("revision_instruction") else 0
                return _candidate(shift)

        return _A()

    @property
    def judge_select_action(self):
        outer = self

        class _A:
            async def run(self, **kwargs):
                outer._log("judge_select", kwargs)
                return JudgeSelectResult(
                    ranking=["r0_candidate_02", "r0_candidate_01", "r0_candidate_03"],
                    selected_candidate_id="r0_candidate_02",
                )

        return _A()

    @property
    def judge_critic_action(self):
        outer = self

        class _A:
            async def run(self, **kwargs):
                outer._log("judge_critic", kwargs)
                return JudgeCriticResult(issues=outer.critic_issues)

        return _A()


def _binding(tmp_path: Path, fakes: FakeActions, *, with_critic: bool = False) -> A3StageBinding:
    background = tmp_path / "background_overview.png"
    if not background.exists():
        Image.new("RGB", (256, 192), "white").save(background)
    return A3StageBinding(
        r3_manifest=_manifest(tmp_path),
        background_overview_path=background,
        renders_dir=tmp_path / "renders",
        stages_dir=tmp_path / "stages",
        analyst_action=fakes.analyst_action,
        planner_action=fakes.planner_action,
        director_action=fakes.director_action,
        mapper_action=fakes.mapper_action,
        judge_select_action=fakes.judge_select_action,
        judge_critic_action=fakes.judge_critic_action if with_critic else None,
    )


def _l0(binding: A3StageBinding, config: Optional[A3RunConfig] = None) -> A3L0Pipeline:
    return A3L0Pipeline(
        config=config or _config(),
        analyst=binding.analyst,
        planner=binding.planner,
        director=binding.director,
        mapper=binding.mapper,
        renderer=binding.renderer,
        qc=binding.qc,
        judge_select=binding.judge_select,
    )


def test_binding_runs_l0_end_to_end_with_real_renderer_and_qc(tmp_path):
    fakes = FakeActions()
    binding = _binding(tmp_path, fakes)
    result = asyncio.run(_l0(binding).run(user_brief="Summer sale poster"))

    assert result.b0_slot_id == "r0_candidate_02"
    # Real renderer produced three PNG renders at canvas size.
    for slot in result.bundle.slots:
        render = Path(slot.render_ref)
        assert render.exists()
        with Image.open(render) as image:
            assert image.size == (800, 600)
    # Real QC ran and completeness is the full-coverage fraction.
    assert all(slot.qc_completeness == 1.0 for slot in result.bundle.slots)
    # Director saw the canvas string and a base64 background attachment.
    director_kwargs = fakes.kwargs["director"][0]
    assert director_kwargs["canvas"] == "800x600"
    assert isinstance(director_kwargs["background_image_b64"], str)
    # The three mapper calls got distinct write-once artifact directories.
    mapper_dirs = [str(entry["artifacts_dir"]) for entry in fakes.kwargs["mapper"]]
    assert len(set(mapper_dirs)) == 3
    # Judge-Select received structured context from the frozen Analyst output.
    assert fakes.kwargs["judge_select"][0]["context"] == {
        "design_intent": "Promote a summer sale"
    }


def test_binding_records_one_call_record_per_llm_stage(tmp_path):
    fakes = FakeActions()
    binding = _binding(tmp_path, fakes)
    asyncio.run(_l0(binding).run(user_brief="brief"))
    stages = [record.stage for record in binding.call_records]
    assert stages == [
        "analyst",
        "asset_planner",
        "composition_director",
        "coordinate_mapper",
        "coordinate_mapper",
        "coordinate_mapper",
        "judge_select",
    ]
    # Fake actions expose no cost manager: usage is honestly None, not zero.
    assert all(record.usage is None for record in binding.call_records)
    records_path = tmp_path / "stage_calls.json"
    binding.write_call_records(records_path)
    assert len(json.loads(records_path.read_text())) == 7
    with pytest.raises(FileExistsError):
        binding.write_call_records(records_path)


def test_binding_runs_l1_with_repair_using_b0_concept(tmp_path):
    issue = ActionableIssue(
        target_asset_ids=["asset_0001"],
        issue_type="overlap",
        observation="headline overlaps product",
        desired_change="move headline clear of the product",
    )
    fakes = FakeActions(critic_issues=[issue])
    binding = _binding(tmp_path, fakes, with_critic=True)
    pipeline = A3L1GatedPipeline(
        config=_config("L1-Gated"),
        analyst=binding.analyst,
        planner=binding.planner,
        director=binding.director,
        mapper=binding.mapper,
        renderer=binding.renderer,
        qc=binding.qc,
        judge_select=binding.judge_select,
        judge_critic=binding.judge_critic,
        repair=binding.repair,
        verifier=binding.verifier,
    )
    result = asyncio.run(pipeline.run(user_brief="brief"))

    assert result.repair_attempted is True
    # Judge-Critic saw only the B0 render.
    critic_kwargs = fakes.kwargs["judge_critic"][0]
    assert critic_kwargs["b0_candidate_id"] == "r0_candidate_02"
    assert critic_kwargs["known_asset_ids"] == ["asset_0001", "asset_0002"]
    # The revision call reused B0's concept (slot 02 -> index 1: "Top banner")
    # and carried the gate instruction plus B0's elements as the editing base.
    revision = fakes.kwargs["mapper"][3]
    assert revision["concept"].name == "Top banner"
    assert "exactly ONE revision" in revision["revision_instruction"]
    assert revision["base_elements"][0]["id"] == "asset_0001"
    # Deterministic verifier ran over real geometry with the manifest canvas.
    assert result.verifications is not None
    assert "a3.issue-verifier.v1" in result.verifications[0].evidence


def test_qc_reports_missing_elements_as_incomplete(tmp_path):
    fakes = FakeActions()
    binding = _binding(tmp_path, fakes)
    asyncio.run(_l0(binding).run(user_brief="brief"))
    partial = {
        "candidate_id": "candidate",
        "elements": [
            {"id": "asset_0002", "left": 40, "top": 100, "width": 300, "height": 300, "z_index": 0}
        ],
    }
    verdict = binding.qc(partial)
    assert verdict.passed is False
    assert verdict.completeness == 0.5
    assert any("missing" in violation.lower() for violation in verdict.violations)


def test_renderer_and_qc_refuse_to_run_before_the_analyst(tmp_path):
    binding = _binding(tmp_path, FakeActions())
    with pytest.raises(RuntimeError, match="Analyst has not run"):
        binding.qc({"candidate_id": "candidate", "elements": []})


def test_hydrate_from_r0_restores_state_for_the_tail_only(tmp_path):
    fakes = FakeActions(
        critic_issues=[
            ActionableIssue(
                target_asset_ids=["asset_0001"],
                issue_type="overlap",
                observation="headline overlaps product",
                desired_change="move headline clear of the product",
            )
        ]
    )
    binding = _binding(tmp_path, fakes, with_critic=True)
    concepts = [_concept("Left bleed"), _concept("Top banner"), _concept("Centered")]
    binding.hydrate_from_r0(analyst_output=_analyst_output(), concepts=concepts)

    # QC/renderer are usable immediately — no analyst call happened.
    verdict = binding.qc(_candidate().model_dump(mode="json"))
    assert verdict.completeness == 1.0
    assert "analyst" not in fakes.kwargs

    # Repair resolves B0's concept from the hydrated concept order.
    from metagpt.ext.agentlayout.a3_pipeline import R0SlotRecord as Slot
    from metagpt.ext.agentlayout.tools.repair_gate import evaluate_repair_gate

    b0 = Slot(
        slot_id="r0_candidate_02",
        status="completed",
        candidate=_candidate().model_dump(mode="json"),
    )
    decision = evaluate_repair_gate(
        JudgeCriticResult(issues=fakes.critic_issues), ["asset_0001", "asset_0002"]
    )
    condition = None  # fakes ignore the condition payload
    asyncio.run(binding.repair(b0, decision, condition))
    assert fakes.kwargs["mapper"][0]["concept"].name == "Top banner"


def test_run_command_refuses_paid_calls_without_explicit_authorization(tmp_path):
    from metagpt.ext.agentlayout.run_manifest import A3RunStore

    config_path = tmp_path / "config.json"
    config_path.write_text(_config("L0").model_dump_json())
    ids_path = tmp_path / "ids.json"
    ids_path.write_text(json.dumps(["sample01", "sample02"]))
    A3RunStore.create(
        runs_root=tmp_path / "runs",
        run_id="smoke-test",
        config_path=config_path,
        sample_ids_path=ids_path,
        repo_root=REPO,
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO / "layout_agent" / "run_a3.py"),
            "run",
            "--run-dir",
            str(tmp_path / "runs" / "smoke-test"),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 2
    payload = json.loads(proc.stdout)
    assert payload["authorized"] is False
    # L0 + default T2: analyst+planner+director+3 mappers+select = 7 per sample.
    assert payload["budget"]["model_calls_per_sample_max"] == 7
    assert payload["budget"]["model_calls_total_max"] == 14
    assert "--allow-api-calls" in proc.stderr
    # Refusal must not have created any run outputs.
    assert not (tmp_path / "runs" / "smoke-test" / "a3_run_summary.json").exists()


def test_t3_run_preflights_complete_human_oracle_set_before_paid_gate(tmp_path):
    from metagpt.ext.agentlayout.run_manifest import A3RunStore

    config_path = tmp_path / "config.json"
    config_path.write_text(_config("L0").model_dump_json())
    ids_path = tmp_path / "ids.json"
    ids_path.write_text(json.dumps(["sample01", "sample02"]))
    run_dir = tmp_path / "runs" / "t3-test"
    A3RunStore.create(
        runs_root=tmp_path / "runs",
        run_id="t3-test",
        config_path=config_path,
        sample_ids_path=ids_path,
        repo_root=REPO,
    )
    base_command = [
        sys.executable,
        str(REPO / "layout_agent" / "run_a3.py"),
        "run",
        "--run-dir",
        str(run_dir),
        "--tree-arm",
        "T3",
    ]
    missing = subprocess.run(
        base_command,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert missing.returncode == 1
    assert "requires --oracle-trees-from" in missing.stderr

    oracle_root = tmp_path / "oracles"
    oracle_root.mkdir()
    oracle = _tree().model_copy(update={"source": "human_oracle"})
    for sample_id in ("sample01", "sample02"):
        (oracle_root / f"{sample_id}.json").write_text(oracle.model_dump_json())
        manifest_path = run_dir / "samples" / sample_id / "inputs" / "r3"
        manifest_path.mkdir(parents=True)
        manifest = _manifest(tmp_path).model_copy(update={"sample_id": sample_id})
        (manifest_path / "r3_asset_manifest.json").write_text(
            manifest.model_dump_json()
        )
    refused = subprocess.run(
        [*base_command, "--oracle-trees-from", str(oracle_root)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert refused.returncode == 2
    payload = json.loads(refused.stdout)
    assert payload["budget"]["model_calls_per_sample_max"] == 6
    assert payload["budget"]["model_calls_total_max"] == 12
    assert payload["budget"]["oracle_trees_from"] == str(oracle_root.resolve())
    assert not (run_dir / "a3_run_summary.json").exists()
