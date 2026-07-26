"""Gate A ablation arm: the A3 Analyst with zero visual access.

Same output contract, validation, exact-model guard and retry policy as
``AnalyzeA3Brief``; the only difference is that no images are attached and
the prompt says so. Comparing this arm against the vision arm isolates the
causal contribution of Analyst vision (new_plam.md section 8, Gate A).
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from metagpt.actions import Action
from metagpt.ext.agentlayout.tools.analyst_vision import (
    A3AnalystOutput,
    build_text_only_analyst_prompt,
    parse_analyst_output,
    validate_asset_coverage,
)
from metagpt.ext.agentlayout.tools.text_bitmap_normalizer import R3AssetManifest
from metagpt.logs import logger


A3_TEXT_ONLY_ANALYST_MAX_RETRIES = 3


class AnalyzeA3TextOnly(Action):
    """Text/metadata-only Analyst; never attaches images by design."""

    name: str = "AnalyzeA3TextOnly"
    desc: str = "A3 Gate A ablation: Analyst without foreground/background vision."

    def __init__(self, *, expected_model: str, **kwargs):
        super().__init__(**kwargs)
        self.expected_model = expected_model

    async def run(
        self,
        *,
        user_brief: str,
        manifest: R3AssetManifest,
        artifacts_dir: Optional[Path] = None,
    ) -> A3AnalystOutput:
        actual_model = str(getattr(self.llm, "model", ""))
        if actual_model != self.expected_model:
            raise RuntimeError(
                f"A3 text-only Analyst model mismatch: expected "
                f"{self.expected_model!r}, got {actual_model!r}"
            )
        prompt = build_text_only_analyst_prompt(manifest, user_brief)
        if artifacts_dir is not None:
            artifacts_dir.mkdir(parents=True, exist_ok=False)
            from metagpt.ext.agentlayout.run_manifest import write_json_once

            write_json_once(
                artifacts_dir / "analyst_request.json",
                {
                    "version": "a3.analyst-text-only-request.v1",
                    "prompt": prompt,
                    "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                    "image_labels": [],
                },
            )
        attempt_prompt = prompt
        last_error: Optional[Exception] = None
        for attempt in range(1, A3_TEXT_ONLY_ANALYST_MAX_RETRIES + 1):
            response = await self.llm.aask(attempt_prompt)
            if artifacts_dir is not None:
                response_path = artifacts_dir / f"attempt_{attempt:02d}_response.txt"
                with response_path.open("x", encoding="utf-8") as handle:
                    handle.write(response)
            try:
                output = parse_analyst_output(response)
                validate_asset_coverage(output, manifest)
                return output
            except (ValueError, ValidationError) as error:
                last_error = error
                logger.warning(
                    f"AnalyzeA3TextOnly attempt {attempt}/"
                    f"{A3_TEXT_ONLY_ANALYST_MAX_RETRIES} failed: {error}"
                )
                attempt_prompt = (
                    prompt
                    + "\n\n# Previous response validation error\n"
                    + str(error)
                    + "\nReturn a corrected complete JSON object."
                )
        raise ValueError(
            f"AnalyzeA3TextOnly failed after {A3_TEXT_ONLY_ANALYST_MAX_RETRIES} "
            f"attempts: {last_error}"
        )
