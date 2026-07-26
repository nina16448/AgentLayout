"""Vision-required A3 Judge-Select action: rank three R0 renders, pick B0."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image
from pydantic import ValidationError

from metagpt.actions import Action
from metagpt.ext.agentlayout.tools.analyst_vision import image_to_base64
from metagpt.ext.agentlayout.tools.judge_select import (
    JUDGE_SELECT_CANDIDATE_COUNT,
    JUDGE_SELECT_RESULT_FILENAME,
    JudgeSelectCandidate,
    JudgeSelectResult,
    build_judge_select_request,
    parse_judge_select_result,
    save_judge_select_request,
    validate_selection,
)
from metagpt.logs import logger


A3_JUDGE_SELECT_MAX_RETRIES = 3


class JudgeSelectA3(Action):
    """Compare exactly three rendered R0 candidates and select B0.

    This action only ranks. It never emits critique, feedback or verdicts;
    critique belongs to the separate JudgeCriticA3 call on B0 alone.
    """

    name: str = "JudgeSelectA3"
    desc: str = "A3 internal Judge-Select: best-of-3 ranking without critique."

    def __init__(self, *, expected_model: str, **kwargs):
        super().__init__(**kwargs)
        self.expected_model = expected_model

    async def run(
        self,
        *,
        candidates: List[JudgeSelectCandidate],
        context: Optional[Dict[str, Any]] = None,
        artifacts_dir: Optional[Path] = None,
    ) -> JudgeSelectResult:
        if not self.llm.support_image_input():
            raise RuntimeError(
                "A3 Judge-Select requires image input; text-only fallback is forbidden"
            )
        actual_model = str(getattr(self.llm, "model", ""))
        if actual_model != self.expected_model:
            raise RuntimeError(
                f"A3 Judge-Select model mismatch: expected {self.expected_model!r}, "
                f"got {actual_model!r}"
            )
        if len(candidates) != JUDGE_SELECT_CANDIDATE_COUNT:
            raise ValueError(
                f"A3 Judge-Select requires exactly {JUDGE_SELECT_CANDIDATE_COUNT} "
                f"rendered candidates, got {len(candidates)}"
            )

        request = build_judge_select_request(candidates, context)
        if artifacts_dir is not None:
            save_judge_select_request(request, artifacts_dir)
        images = []
        for candidate in candidates:
            with Image.open(candidate.render_ref) as render:
                images.append(image_to_base64(render))
        candidate_ids = [candidate.candidate_id for candidate in candidates]
        prompt = request.prompt
        last_error: Optional[Exception] = None
        for attempt in range(1, A3_JUDGE_SELECT_MAX_RETRIES + 1):
            response = await self.llm.aask(prompt, images=images)
            if artifacts_dir is not None:
                response_path = artifacts_dir / f"attempt_{attempt:02d}_response.txt"
                with response_path.open("x", encoding="utf-8") as handle:
                    handle.write(response)
            try:
                result = parse_judge_select_result(response)
                validate_selection(result, candidate_ids)
                if artifacts_dir is not None:
                    from metagpt.ext.agentlayout.run_manifest import write_json_once

                    write_json_once(
                        artifacts_dir / JUDGE_SELECT_RESULT_FILENAME,
                        result.model_dump(mode="json"),
                    )
                return result
            except (ValueError, ValidationError) as error:
                last_error = error
                logger.warning(
                    f"JudgeSelectA3 attempt {attempt}/{A3_JUDGE_SELECT_MAX_RETRIES} "
                    f"failed: {error}"
                )
                prompt = (
                    request.prompt
                    + "\n\n# Previous response validation error\n"
                    + str(error)
                    + "\nReturn a corrected complete JSON object."
                )
        raise ValueError(
            f"JudgeSelectA3 failed after {A3_JUDGE_SELECT_MAX_RETRIES} attempts: {last_error}"
        )
