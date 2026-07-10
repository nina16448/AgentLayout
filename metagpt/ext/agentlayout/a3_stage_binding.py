"""Bind real A3 Actions, renderer and QC to the pipeline stage callables.

One binding instance serves one sample. It owns the per-sample state the
pipeline contract keeps implicit — the frozen Analyst output, the derived
rendering DesignSpec, the R0 concept order — and it records one call record
per stage invocation (wall time plus, when the LLM exposes a usage/cost
manager, the token deltas), so a run can persist an honest per-sample cost
trail even when the provider reports nothing.

Import-light on purpose: the LLM-backed Action instances arrive duck-typed
(anything with the matching ``async run``), so offline tests can wire fakes
without pulling the ``metagpt.actions`` dependency chain.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from metagpt.ext.agentlayout.a3_pipeline import QCVerdict, R0_SLOT_IDS, R0SlotRecord
from metagpt.ext.agentlayout.layout_tree_v3 import A3LayoutTree, TreeCondition
from metagpt.ext.agentlayout.run_manifest import write_json_once
from metagpt.ext.agentlayout.schema import Candidate, CompositionConcept, DesignSpec
from metagpt.ext.agentlayout.tools.analyst_vision import (
    A3AnalystOutput,
    analyst_output_to_design_spec,
    image_to_base64,
)
from metagpt.ext.agentlayout.tools.issue_verifier import verify_issues
from metagpt.ext.agentlayout.tools.judge_critic import JudgeCriticResult
from metagpt.ext.agentlayout.tools.judge_select import (
    JudgeSelectCandidate,
    JudgeSelectResult,
)
from metagpt.ext.agentlayout.tools.quality_checker import check_candidate
from metagpt.ext.agentlayout.tools.renderer import render_to_file
from metagpt.ext.agentlayout.tools.repair_gate import IssueVerification, RepairDecision
from metagpt.ext.agentlayout.tools.text_bitmap_normalizer import R3AssetManifest


A3_STAGE_CALL_RECORD_VERSION = "a3.stage-call-record.v1"


class StageCallRecord(BaseModel):
    """Wall time and (when available) token usage for one stage call."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = A3_STAGE_CALL_RECORD_VERSION
    stage: str
    seconds: float = Field(..., ge=0.0)
    usage: Optional[Dict[str, Any]] = None


def _usage_snapshot(action: Any) -> Optional[Dict[str, Any]]:
    """Best-effort token totals from a MetaGPT cost manager; None if absent."""
    manager = getattr(getattr(action, "llm", None), "cost_manager", None)
    if manager is None:
        return None
    snapshot = {
        key: getattr(manager, key)
        for key in ("total_prompt_tokens", "total_completion_tokens", "total_cost")
        if hasattr(manager, key)
    }
    return snapshot or None


def _usage_delta(
    before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    if before is None or after is None:
        return None
    return {key: after[key] - before.get(key, 0) for key in after}


class A3StageBinding:
    """Per-sample factory for the A3L0Pipeline / A3L1GatedPipeline callables."""

    def __init__(
        self,
        *,
        r3_manifest: R3AssetManifest,
        background_overview_path: Path,
        renders_dir: Path,
        stages_dir: Path,
        analyst_action: Any,
        planner_action: Any,
        director_action: Any,
        mapper_action: Any,
        judge_select_action: Any,
        judge_critic_action: Optional[Any] = None,
    ):
        self.r3_manifest = r3_manifest
        self.background_overview_path = Path(background_overview_path)
        self.renders_dir = Path(renders_dir)
        self.stages_dir = Path(stages_dir)
        self.analyst_action = analyst_action
        self.planner_action = planner_action
        self.director_action = director_action
        self.mapper_action = mapper_action
        self.judge_select_action = judge_select_action
        self.judge_critic_action = judge_critic_action
        self.call_records: List[StageCallRecord] = []
        self._analyst_output: Optional[A3AnalystOutput] = None
        self._design_spec: Optional[DesignSpec] = None
        self._concepts: List[CompositionConcept] = []
        self._mapper_calls = 0
        self._background_b64: Optional[str] = None

    def _record(self, stage: str, started: float, action: Any, usage_before) -> None:
        self.call_records.append(
            StageCallRecord(
                stage=stage,
                seconds=time.monotonic() - started,
                usage=_usage_delta(usage_before, _usage_snapshot(action)),
            )
        )

    def _background_image_b64(self) -> str:
        if self._background_b64 is None:
            with Image.open(self.background_overview_path) as image:
                self._background_b64 = image_to_base64(image)
        return self._background_b64

    def _spec(self) -> DesignSpec:
        if self._design_spec is None:
            raise RuntimeError("Analyst has not run yet; DesignSpec is unavailable")
        return self._design_spec

    # ---- pipeline callables -------------------------------------------------

    async def analyst(self, user_brief: str) -> A3AnalystOutput:
        started, usage = time.monotonic(), _usage_snapshot(self.analyst_action)
        output = await self.analyst_action.run(
            user_brief=user_brief,
            manifest=self.r3_manifest,
            artifacts_dir=self.stages_dir / "analyst",
        )
        self._record("analyst", started, self.analyst_action, usage)
        self._analyst_output = output
        self._design_spec = analyst_output_to_design_spec(output, self.r3_manifest)
        return output

    async def planner(self, analyst_output: A3AnalystOutput) -> A3LayoutTree:
        started, usage = time.monotonic(), _usage_snapshot(self.planner_action)
        tree = await self.planner_action.run(
            analyst=analyst_output, artifacts_dir=self.stages_dir / "planner"
        )
        self._record("asset_planner", started, self.planner_action, usage)
        return tree

    async def director(
        self, analyst_output: A3AnalystOutput, condition: TreeCondition
    ) -> List[CompositionConcept]:
        started, usage = time.monotonic(), _usage_snapshot(self.director_action)
        concept_set = await self.director_action.run(
            analyst=analyst_output,
            condition=condition,
            canvas=f"{self.r3_manifest.canvas_width}x{self.r3_manifest.canvas_height}",
            background_image_b64=self._background_image_b64(),
            artifacts_dir=self.stages_dir / "director",
        )
        self._record("composition_director", started, self.director_action, usage)
        self._concepts = list(concept_set.concepts)
        return self._concepts

    async def mapper(
        self, concept: CompositionConcept, condition: TreeCondition
    ) -> Dict[str, Any]:
        self._mapper_calls += 1
        started, usage = time.monotonic(), _usage_snapshot(self.mapper_action)
        candidate = await self.mapper_action.run(
            concept=concept,
            condition=condition,
            manifest=self.r3_manifest,
            background_image_b64=self._background_image_b64(),
            artifacts_dir=self.stages_dir / f"mapper_{self._mapper_calls:02d}",
        )
        self._record("coordinate_mapper", started, self.mapper_action, usage)
        return candidate.model_dump(mode="json")

    async def renderer(self, candidate: Dict[str, Any], slot_id: str) -> str:
        path = render_to_file(
            Candidate.model_validate(candidate), self._spec(), self.renders_dir / f"{slot_id}.png"
        )
        return str(path)

    def qc(self, candidate: Dict[str, Any]) -> QCVerdict:
        parsed = Candidate.model_validate(candidate)
        spec = self._spec()
        result = check_candidate(parsed, spec)
        expected = {
            element.id for element in spec.elements if element.semantic_type != "background_image"
        }
        present = {element.id for element in parsed.elements} & expected
        return QCVerdict(
            passed=result.passed,
            violations=[
                f"{violation.type.value}: {violation.detail}"
                for violation in result.violations
            ],
            completeness=len(present) / len(expected) if expected else 1.0,
        )

    async def judge_select(
        self, candidates: List[JudgeSelectCandidate]
    ) -> JudgeSelectResult:
        started, usage = time.monotonic(), _usage_snapshot(self.judge_select_action)
        result = await self.judge_select_action.run(
            candidates=candidates,
            context={"design_intent": self._analyst_output.design_intent}
            if self._analyst_output
            else None,
            artifacts_dir=self.stages_dir / "judge_select",
        )
        self._record("judge_select", started, self.judge_select_action, usage)
        return result

    async def judge_critic(
        self, b0: R0SlotRecord, known_asset_ids: List[str]
    ) -> JudgeCriticResult:
        if self.judge_critic_action is None:
            raise RuntimeError("judge_critic_action was not provided for an L1 run")
        started, usage = time.monotonic(), _usage_snapshot(self.judge_critic_action)
        result = await self.judge_critic_action.run(
            b0_candidate_id=b0.slot_id,
            render_ref=b0.render_ref,
            known_asset_ids=known_asset_ids,
            artifacts_dir=self.stages_dir / "judge_critic",
        )
        self._record("judge_critic", started, self.judge_critic_action, usage)
        return result

    async def repair(
        self, b0: R0SlotRecord, decision: RepairDecision, condition: TreeCondition
    ) -> Dict[str, Any]:
        slot_index = R0_SLOT_IDS.index(b0.slot_id)
        if slot_index >= len(self._concepts):
            raise RuntimeError("B0 concept is unavailable for the revision call")
        started, usage = time.monotonic(), _usage_snapshot(self.mapper_action)
        candidate = await self.mapper_action.run(
            concept=self._concepts[slot_index],
            condition=condition,
            manifest=self.r3_manifest,
            background_image_b64=self._background_image_b64(),
            revision_instruction=decision.revision_instruction,
            base_elements=(b0.candidate or {}).get("elements", []),
            artifacts_dir=self.stages_dir / "repair",
        )
        self._record("coordinate_mapper_revision", started, self.mapper_action, usage)
        return candidate.model_dump(mode="json")

    def verifier(
        self, decision: RepairDecision, b0: R0SlotRecord, b1: R0SlotRecord
    ) -> List[IssueVerification]:
        return verify_issues(
            decision,
            Candidate.model_validate(b0.candidate),
            Candidate.model_validate(b1.candidate),
            canvas_width=self.r3_manifest.canvas_width,
            canvas_height=self.r3_manifest.canvas_height,
        )

    # ---- persistence --------------------------------------------------------

    def write_call_records(self, path: Path) -> None:
        write_json_once(
            path, [record.model_dump(mode="json") for record in self.call_records]
        )
