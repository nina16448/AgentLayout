"""Structured A3 Asset Planner producing the versioned Layout Tree."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from metagpt.actions import Action
from metagpt.ext.agentlayout.layout_tree_v3 import (
    A3LayoutTree,
    apply_analyst_semantics,
    build_tree_request,
    parse_layout_tree,
    save_tree_request,
    validate_tree_against_analyst,
)
from metagpt.ext.agentlayout.tools.analyst_vision import A3AnalystOutput
from metagpt.logs import logger


A3_PLANNER_MAX_RETRIES = 3


class PlanAssetsA3(Action):
    name: str = "PlanAssetsA3"
    desc: str = "Produce a versioned explicit Layout Tree before coordinate placement."

    def __init__(self, *, expected_model: str, **kwargs):
        super().__init__(**kwargs)
        self.expected_model = expected_model

    async def run(
        self,
        *,
        analyst: A3AnalystOutput,
        artifacts_dir: Optional[Path] = None,
    ) -> A3LayoutTree:
        actual_model = str(getattr(self.llm, "model", ""))
        if actual_model != self.expected_model:
            raise RuntimeError(
                f"A3 Planner model mismatch: expected {self.expected_model!r}, "
                f"got {actual_model!r}"
            )
        request = build_tree_request(analyst)
        if artifacts_dir is not None:
            save_tree_request(request, artifacts_dir)
        prompt = request.prompt
        last_error: Optional[Exception] = None
        for attempt in range(1, A3_PLANNER_MAX_RETRIES + 1):
            response = await self.llm.aask(prompt)
            if artifacts_dir is not None:
                response_path = artifacts_dir / f"attempt_{attempt:02d}_response.txt"
                with response_path.open("x", encoding="utf-8") as handle:
                    handle.write(response)
            try:
                tree = parse_layout_tree(response)
                tree = apply_analyst_semantics(tree, analyst)
                validate_tree_against_analyst(tree, analyst)
                if artifacts_dir is not None:
                    from metagpt.ext.agentlayout.run_manifest import write_json_once

                    write_json_once(
                        artifacts_dir / "layout_tree.json", tree.model_dump(mode="json")
                    )
                return tree
            except (ValueError, ValidationError) as error:
                last_error = error
                logger.warning(
                    f"PlanAssetsA3 attempt {attempt}/{A3_PLANNER_MAX_RETRIES} failed: {error}"
                )
                prompt = (
                    request.prompt
                    + "\n\n# Previous response validation error\n"
                    + str(error)
                    + "\nReturn a corrected complete JSON object."
                )
        raise ValueError(f"PlanAssetsA3 failed after {A3_PLANNER_MAX_RETRIES} attempts: {last_error}")
