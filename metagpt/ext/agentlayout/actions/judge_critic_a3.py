"""Vision-required A3 Judge-Critic action: actionable issues for B0 only."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image
from pydantic import ValidationError

from metagpt.actions import Action
from metagpt.ext.agentlayout.tools.analyst_vision import image_to_base64
from metagpt.ext.agentlayout.tools.judge_critic import (
    JUDGE_CRITIC_RESULT_FILENAME,
    JudgeCriticResult,
    build_judge_critic_request,
    parse_judge_critic_result,
    save_judge_critic_request,
    validate_critic_targets,
)
from metagpt.logs import logger


A3_JUDGE_CRITIC_MAX_RETRIES = 3


class JudgeCriticA3(Action):
    """Inspect the selected B0 render alone and emit at most two issues.

    This action never ranks, scores or re-selects; selection belongs to the
    separate JudgeSelectA3 call that already happened. It only produces the
    gate-ready actionable-issue contract consumed by the A3-07 repair gate.
    """

    name: str = "JudgeCriticA3"
    desc: str = "A3 internal Judge-Critic: element-level actionable issues on B0."

    def __init__(self, *, expected_model: str, **kwargs):
        super().__init__(**kwargs)
        self.expected_model = expected_model

    async def run(
        self,
        *,
        b0_candidate_id: str,
        render_ref: str,
        known_asset_ids: List[str],
        context: Optional[Dict[str, Any]] = None,
        artifacts_dir: Optional[Path] = None,
    ) -> JudgeCriticResult:
        if not self.llm.support_image_input():
            raise RuntimeError(
                "A3 Judge-Critic requires image input; text-only fallback is forbidden"
            )
        actual_model = str(getattr(self.llm, "model", ""))
        if actual_model != self.expected_model:
            raise RuntimeError(
                f"A3 Judge-Critic model mismatch: expected {self.expected_model!r}, "
                f"got {actual_model!r}"
            )

        request = build_judge_critic_request(
            b0_candidate_id=b0_candidate_id,
            render_ref=render_ref,
            known_asset_ids=known_asset_ids,
            context=context,
        )
        if artifacts_dir is not None:
            save_judge_critic_request(request, artifacts_dir)
        with Image.open(render_ref) as render:
            images = [image_to_base64(render)]
        prompt = request.prompt
        last_error: Optional[Exception] = None
        for attempt in range(1, A3_JUDGE_CRITIC_MAX_RETRIES + 1):
            response = await self.llm.aask(prompt, images=images)
            if artifacts_dir is not None:
                response_path = artifacts_dir / f"attempt_{attempt:02d}_response.txt"
                with response_path.open("x", encoding="utf-8") as handle:
                    handle.write(response)
            try:
                result = parse_judge_critic_result(response)
                validate_critic_targets(result, known_asset_ids)
                if artifacts_dir is not None:
                    from metagpt.ext.agentlayout.run_manifest import write_json_once

                    write_json_once(
                        artifacts_dir / JUDGE_CRITIC_RESULT_FILENAME,
                        result.model_dump(mode="json"),
                    )
                return result
            except (ValueError, ValidationError) as error:
                last_error = error
                logger.warning(
                    f"JudgeCriticA3 attempt {attempt}/{A3_JUDGE_CRITIC_MAX_RETRIES} "
                    f"failed: {error}"
                )
                prompt = (
                    request.prompt
                    + "\n\n# Previous response validation error\n"
                    + str(error)
                    + "\nReturn a corrected complete JSON object."
                )
        raise ValueError(
            f"JudgeCriticA3 failed after {A3_JUDGE_CRITIC_MAX_RETRIES} attempts: {last_error}"
        )
