from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from metagpt.ext.agentlayout.tools.judge_critic import (
    ActionableIssue,
    CriticIssueType,
    JudgeCriticResult,
    build_judge_critic_prompt,
    build_judge_critic_request,
    parse_judge_critic_result,
    save_judge_critic_request,
    validate_critic_targets,
)
from metagpt.ext.agentlayout.tools.judge_select import JudgeSelectResult


KNOWN_IDS = ["asset_0001", "asset_0002", "asset_0003"]


def _issue(**overrides) -> dict:
    payload = {
        "target_asset_ids": ["asset_0001"],
        "issue_type": "overlap",
        "observation": "headline overlaps the product image",
        "desired_change": "move asset_0001 above asset_0003 with clear separation",
    }
    payload.update(overrides)
    return payload


def _result(issue_count: int = 1) -> JudgeCriticResult:
    issues = [
        ActionableIssue.model_validate(_issue(target_asset_ids=[KNOWN_IDS[i]]))
        for i in range(issue_count)
    ]
    return JudgeCriticResult(issues=issues)


def test_at_most_two_actionable_issues_are_allowed():
    assert len(_result(2).issues) == 2
    with pytest.raises(ValidationError, match="at most 2"):
        JudgeCriticResult(
            issues=[
                ActionableIssue.model_validate(_issue(target_asset_ids=[asset_id]))
                for asset_id in KNOWN_IDS
            ]
        )


def test_vague_opinions_are_structurally_unrepresentable():
    for vague in ("not_beautiful_enough", "lacks_creativity", "boring", "low_quality"):
        with pytest.raises(ValidationError):
            ActionableIssue.model_validate(_issue(issue_type=vague))
    assert "not_beautiful_enough" not in {item.value for item in CriticIssueType}


def test_every_issue_requires_targets_closed_type_and_desired_change():
    with pytest.raises(ValidationError):
        ActionableIssue.model_validate(_issue(target_asset_ids=[]))
    with pytest.raises(ValidationError):
        ActionableIssue.model_validate(_issue(desired_change=""))
    with pytest.raises(ValidationError, match="duplicates"):
        ActionableIssue.model_validate(
            _issue(target_asset_ids=["asset_0001", "asset_0001"])
        )


def test_result_has_no_overall_score_ranking_or_verdict_fields():
    assert set(JudgeCriticResult.model_fields) == {"schema_version", "issues"}
    payload = _result().model_dump(mode="json")
    for forbidden in ("overall_score", "total", "ranking", "verdict", "selected_candidate_id"):
        with pytest.raises(ValidationError):
            JudgeCriticResult.model_validate({**payload, forbidden: 1})


def test_zero_issues_is_a_valid_outcome():
    empty = JudgeCriticResult(issues=[])
    assert empty.issues == []
    validate_critic_targets(empty, KNOWN_IDS)


def test_issue_targets_must_exist_in_the_layout():
    result = _result()
    validate_critic_targets(result, KNOWN_IDS)
    with pytest.raises(ValueError, match="unknown asset IDs"):
        validate_critic_targets(result, ["asset_0002", "asset_0003"])


def test_prompt_sees_only_b0_and_forbids_scores_and_reranking():
    prompt = build_judge_critic_prompt("r0_candidate_02", KNOWN_IDS)
    assert "Judge-Critic" in prompt
    assert "ONLY the already-selected best candidate" in prompt
    assert "r0_candidate_02" in prompt
    assert "ACCEPT" not in prompt
    assert "REJECT" not in prompt
    assert "threshold" not in prompt
    assert "Do NOT output an overall score" in prompt
    assert "at most 2" in prompt
    assert '"not beautiful enough"' in prompt and '"lacks creativity"' in prompt


def test_request_records_b0_render_hash_and_is_write_once(tmp_path):
    render = tmp_path / "b0.png"
    Image.new("RGB", (4, 4), "white").save(render)
    request = build_judge_critic_request(
        b0_candidate_id="r0_candidate_02",
        render_ref=str(render),
        known_asset_ids=KNOWN_IDS,
    )
    assert request.version == "a3.judge-critic-request.v1"
    assert len(request.prompt_sha256) == 64
    assert len(request.render_sha256) == 64
    assert request.b0_candidate_id == "r0_candidate_02"

    output = tmp_path / "judge_critic"
    save_judge_critic_request(request, output)
    assert (output / "judge_critic_request.json").exists()
    with pytest.raises(FileExistsError):
        save_judge_critic_request(request, output)


def test_parser_accepts_fenced_json():
    result = _result()
    parsed = parse_judge_critic_result("```json\n" + result.model_dump_json() + "\n```")
    assert parsed == result


def test_selection_and_critique_contracts_are_disjoint():
    select_fields = set(JudgeSelectResult.model_fields)
    critic_fields = set(JudgeCriticResult.model_fields)
    assert "issues" not in select_fields
    assert "ranking" not in critic_fields
    assert "selected_candidate_id" not in critic_fields
    assert select_fields & critic_fields == {"schema_version"}

    critic_prompt = build_judge_critic_prompt("r0_candidate_02", KNOWN_IDS)
    assert "Judge-Select" not in critic_prompt


def test_action_enforces_vision_exact_model_single_render_and_retry():
    repo = Path(__file__).resolve().parents[4]
    source = (repo / "metagpt/ext/agentlayout/actions/judge_critic_a3.py").read_text()
    assert "support_image_input" in source
    assert "actual_model != self.expected_model" in source
    assert "images = [image_to_base64(render)]" in source
    assert "aask(prompt, images=images)" in source
    assert "Previous response validation error" in source
    assert "validate_critic_targets" in source
