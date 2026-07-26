"""Vision-required A3 Composition Director action (contract in tools/director_contract)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from metagpt.actions import Action
from metagpt.ext.agentlayout.layout_tree_v3 import TreeCondition
from metagpt.ext.agentlayout.tools.analyst_vision import A3AnalystOutput
from metagpt.ext.agentlayout.tools.director_contract import (
    A3ConceptSet,
    build_director_request,
    parse_concept_set,
    validate_concepts_against_assets,
)
from metagpt.logs import logger


A3_DIRECTOR_MAX_RETRIES = 3


class ComposeConceptA3(Action):
    """Produce exactly three distinct concepts from frozen Analyst semantics."""

    name: str = "ComposeConceptA3"
    desc: str = "A3 Composition Director: three spatially distinct concepts."

    def __init__(self, *, expected_model: str, **kwargs):
        super().__init__(**kwargs)
        self.expected_model = expected_model

    async def run(
        self,
        *,
        analyst: A3AnalystOutput,
        condition: TreeCondition,
        canvas: str,
        background_image_b64: str,
        artifacts_dir: Optional[Path] = None,
    ) -> A3ConceptSet:
        if not self.llm.support_image_input():
            raise RuntimeError(
                "A3 Director requires image input; text-only fallback is forbidden"
            )
        actual_model = str(getattr(self.llm, "model", ""))
        if actual_model != self.expected_model:
            raise RuntimeError(
                f"A3 Director model mismatch: expected {self.expected_model!r}, "
                f"got {actual_model!r}"
            )
        request = build_director_request(analyst, condition, canvas)
        if artifacts_dir is not None:
            artifacts_dir.mkdir(parents=True, exist_ok=False)
            from metagpt.ext.agentlayout.run_manifest import write_json_once

            write_json_once(
                artifacts_dir / "director_request.json", request.model_dump(mode="json")
            )
        attempt_prompt = request.prompt
        last_error: Optional[Exception] = None
        for attempt in range(1, A3_DIRECTOR_MAX_RETRIES + 1):
            response = await self.llm.aask(attempt_prompt, images=[background_image_b64])
            if artifacts_dir is not None:
                response_path = artifacts_dir / f"attempt_{attempt:02d}_response.txt"
                with response_path.open("x", encoding="utf-8") as handle:
                    handle.write(response)
            try:
                concept_set = parse_concept_set(response)
                validate_concepts_against_assets(concept_set, condition)
                if artifacts_dir is not None:
                    from metagpt.ext.agentlayout.run_manifest import write_json_once

                    write_json_once(
                        artifacts_dir / "concept_set.json",
                        concept_set.model_dump(mode="json"),
                    )
                return concept_set
            except (ValueError, ValidationError) as error:
                last_error = error
                logger.warning(
                    f"ComposeConceptA3 attempt {attempt}/{A3_DIRECTOR_MAX_RETRIES} "
                    f"failed: {error}"
                )
                attempt_prompt = (
                    request.prompt
                    + "\n\n# Previous response validation error\n"
                    + str(error)
                    + "\nReturn a corrected complete JSON object."
                )
        raise ValueError(
            f"ComposeConceptA3 failed after {A3_DIRECTOR_MAX_RETRIES} attempts: {last_error}"
        )
