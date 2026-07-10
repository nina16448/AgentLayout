"""Vision-required A3 Coordinate Mapper action (contract in tools/mapper_contract).

Also carries the single L1 revision call: the same action re-runs once with
the gate's revision instruction and the B0 elements as the editing base.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from pydantic import ValidationError

from metagpt.actions import Action
from metagpt.ext.agentlayout.layout_tree_v3 import TreeCondition
from metagpt.ext.agentlayout.schema import Candidate, CompositionConcept
from metagpt.ext.agentlayout.tools.mapper_contract import (
    build_mapper_request,
    parse_candidate,
    validate_candidate_coverage,
)
from metagpt.ext.agentlayout.tools.text_bitmap_normalizer import R3AssetManifest
from metagpt.logs import logger


A3_MAPPER_MAX_RETRIES = 3


class GenerateLayoutA3(Action):
    """Map one concept (or one gated revision) to exact pixel coordinates."""

    name: str = "GenerateLayoutA3"
    desc: str = "A3 Coordinate Mapper: concept plus tree condition to pixel bboxes."

    def __init__(self, *, expected_model: str, **kwargs):
        super().__init__(**kwargs)
        self.expected_model = expected_model

    async def run(
        self,
        *,
        concept: CompositionConcept,
        condition: TreeCondition,
        manifest: R3AssetManifest,
        background_image_b64: str,
        revision_instruction: Optional[str] = None,
        base_elements: Optional[List[Dict]] = None,
        artifacts_dir: Optional[Path] = None,
    ) -> Candidate:
        if not self.llm.support_image_input():
            raise RuntimeError(
                "A3 Mapper requires image input; text-only fallback is forbidden"
            )
        actual_model = str(getattr(self.llm, "model", ""))
        if actual_model != self.expected_model:
            raise RuntimeError(
                f"A3 Mapper model mismatch: expected {self.expected_model!r}, "
                f"got {actual_model!r}"
            )
        if (revision_instruction is None) != (base_elements is None):
            raise ValueError(
                "revision_instruction and base_elements must be provided together"
            )
        request = build_mapper_request(
            concept=concept,
            condition=condition,
            manifest=manifest,
            revision_instruction=revision_instruction,
            base_elements=base_elements,
        )
        if artifacts_dir is not None:
            artifacts_dir.mkdir(parents=True, exist_ok=False)
            from metagpt.ext.agentlayout.run_manifest import write_json_once

            write_json_once(
                artifacts_dir / "mapper_request.json", request.model_dump(mode="json")
            )
        asset_ids = [asset.asset_id for asset in manifest.foreground_assets()]
        attempt_prompt = request.prompt
        last_error: Optional[Exception] = None
        for attempt in range(1, A3_MAPPER_MAX_RETRIES + 1):
            response = await self.llm.aask(attempt_prompt, images=[background_image_b64])
            if artifacts_dir is not None:
                response_path = artifacts_dir / f"attempt_{attempt:02d}_response.txt"
                with response_path.open("x", encoding="utf-8") as handle:
                    handle.write(response)
            try:
                candidate = parse_candidate(response)
                validate_candidate_coverage(candidate, asset_ids)
                if artifacts_dir is not None:
                    from metagpt.ext.agentlayout.run_manifest import write_json_once

                    write_json_once(
                        artifacts_dir / "candidate.json",
                        candidate.model_dump(mode="json"),
                    )
                return candidate
            except (ValueError, ValidationError) as error:
                last_error = error
                logger.warning(
                    f"GenerateLayoutA3 attempt {attempt}/{A3_MAPPER_MAX_RETRIES} "
                    f"failed: {error}"
                )
                attempt_prompt = (
                    request.prompt
                    + "\n\n# Previous response validation error\n"
                    + str(error)
                    + "\nReturn a corrected complete JSON object."
                )
        raise ValueError(
            f"GenerateLayoutA3 failed after {A3_MAPPER_MAX_RETRIES} attempts: {last_error}"
        )
