"""Vision-required A3 Analyst action."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from metagpt.actions import Action
from metagpt.ext.agentlayout.tools.analyst_vision import (
    A3AnalystOutput,
    build_vision_packet,
    image_to_base64,
    parse_analyst_output,
    save_vision_packet,
    validate_asset_coverage,
)
from metagpt.ext.agentlayout.tools.text_bitmap_normalizer import R3AssetManifest
from metagpt.logs import logger


A3_ANALYST_MAX_RETRIES = 3


class AnalyzeA3Brief(Action):
    """Inspect background + all foregrounds and return stable-ID semantics."""

    name: str = "AnalyzeA3Brief"
    desc: str = "A3 multimodal semantic analysis before tree planning or coordinates."

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
        if not self.llm.support_image_input():
            raise RuntimeError("A3 Analyst requires image input; text-only fallback is forbidden")
        actual_model = str(getattr(self.llm, "model", ""))
        if actual_model != self.expected_model:
            raise RuntimeError(
                f"A3 Analyst model mismatch: expected {self.expected_model!r}, "
                f"got {actual_model!r}"
            )

        packet = build_vision_packet(manifest, user_brief)
        if artifacts_dir is not None:
            save_vision_packet(packet, artifacts_dir)
        images = [image_to_base64(image) for image in packet.images]
        prompt = packet.prompt
        last_error: Optional[Exception] = None
        for attempt in range(1, A3_ANALYST_MAX_RETRIES + 1):
            response = await self.llm.aask(prompt, images=images)
            try:
                output = self._parse(response)
                validate_asset_coverage(output, manifest)
                return output
            except (ValueError, ValidationError) as error:
                last_error = error
                logger.warning(
                    f"AnalyzeA3Brief attempt {attempt}/{A3_ANALYST_MAX_RETRIES} failed: {error}"
                )
                prompt = (
                    packet.prompt
                    + "\n\n# Previous response validation error\n"
                    + str(error)
                    + "\nReturn a corrected complete JSON object."
                )
        raise ValueError(
            f"AnalyzeA3Brief failed after {A3_ANALYST_MAX_RETRIES} attempts: {last_error}"
        )

    @staticmethod
    def _parse(response: str) -> A3AnalystOutput:
        return parse_analyst_output(response)
