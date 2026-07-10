from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from metagpt.ext.agentlayout.tools.judge_select import (
    JudgeSelectCandidate,
    JudgeSelectResult,
    build_judge_select_prompt,
    build_judge_select_request,
    parse_judge_select_result,
    save_judge_select_request,
    validate_selection,
)


def _render(tmp_path: Path, name: str) -> str:
    path = tmp_path / f"{name}.png"
    Image.new("RGB", (4, 4), "white").save(path)
    return str(path)


def _candidates(tmp_path: Path) -> list[JudgeSelectCandidate]:
    return [
        JudgeSelectCandidate(
            candidate_id=f"r0_candidate_{index:02d}",
            render_ref=_render(tmp_path, f"r0_candidate_{index:02d}"),
            qc_passed=index != 2,
            qc_violations=[] if index != 2 else ["element out of canvas"],
        )
        for index in (1, 2, 3)
    ]


def _result() -> JudgeSelectResult:
    return JudgeSelectResult(
        ranking=["r0_candidate_02", "r0_candidate_01", "r0_candidate_03"],
        selected_candidate_id="r0_candidate_02",
    )


def test_result_is_pure_ranking_without_score_or_verdict_fields():
    result = _result()
    assert result.schema_version == "a3.judge-select-result.v1"
    assert set(JudgeSelectResult.model_fields) == {
        "schema_version",
        "ranking",
        "selected_candidate_id",
    }
    payload = result.model_dump(mode="json")
    for forbidden in ("total", "scores", "verdict", "feedback", "suggestions"):
        with pytest.raises(ValidationError):
            JudgeSelectResult.model_validate({**payload, forbidden: 40})


def test_ranking_must_be_unique_and_selection_must_be_first():
    payload = _result().model_dump(mode="json")
    payload["ranking"] = ["r0_candidate_02", "r0_candidate_02", "r0_candidate_03"]
    with pytest.raises(ValidationError, match="duplicate"):
        JudgeSelectResult.model_validate(payload)

    payload = _result().model_dump(mode="json")
    payload["selected_candidate_id"] = "r0_candidate_03"
    with pytest.raises(ValidationError, match="first ranking entry"):
        JudgeSelectResult.model_validate(payload)


def test_selection_must_be_exact_permutation_of_submitted_candidates():
    result = _result()
    validate_selection(
        result, ["r0_candidate_01", "r0_candidate_02", "r0_candidate_03"]
    )
    with pytest.raises(ValueError, match="permutation"):
        validate_selection(
            result, ["r0_candidate_01", "r0_candidate_02", "r0_candidate_99"]
        )


def test_prompt_ranks_only_without_accept_reject_threshold_or_critique(tmp_path):
    prompt = build_judge_select_prompt(_candidates(tmp_path))
    assert "Judge-Select" in prompt
    assert "rank" in prompt.lower()
    assert "ACCEPT" not in prompt
    assert "REJECT" not in prompt
    assert "threshold" not in prompt
    assert "35" not in prompt
    assert "Do NOT write critique" in prompt
    assert "r0_candidate_01" in prompt and "r0_candidate_03" in prompt


def test_exactly_three_candidates_are_required(tmp_path):
    with pytest.raises(ValueError, match="exactly 3"):
        build_judge_select_prompt(_candidates(tmp_path)[:2])


def test_parser_accepts_fenced_json():
    result = _result()
    parsed = parse_judge_select_result("```json\n" + result.model_dump_json() + "\n```")
    assert parsed == result


def test_request_records_render_hashes_and_is_write_once(tmp_path):
    request = build_judge_select_request(_candidates(tmp_path))
    assert request.version == "a3.judge-select-request.v1"
    assert len(request.prompt_sha256) == 64
    assert set(request.render_sha256) == set(request.candidate_ids)
    assert all(len(digest) == 64 for digest in request.render_sha256.values())

    output = tmp_path / "judge_select"
    save_judge_select_request(request, output)
    assert (output / "judge_select_request.json").exists()
    with pytest.raises(FileExistsError):
        save_judge_select_request(request, output)


def test_action_enforces_vision_exact_model_three_renders_and_retry():
    repo = Path(__file__).resolve().parents[4]
    source = (repo / "metagpt/ext/agentlayout/actions/judge_select_a3.py").read_text()
    assert "support_image_input" in source
    assert "actual_model != self.expected_model" in source
    assert "JUDGE_SELECT_CANDIDATE_COUNT" in source
    assert "aask(prompt, images=images)" in source
    assert "Previous response validation error" in source
    assert "validate_selection" in source
