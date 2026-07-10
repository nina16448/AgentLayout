"""Judge-Select contract: rank exactly three R0 renders and pick B0.

Selection is deliberately decoupled from critique (new_plam.md section 4.4).
The result schema carries a ranking only; it has no score, verdict, feedback
or issue fields, so critique output is structurally impossible rather than
merely discouraged by the prompt.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from metagpt.ext.agentlayout.run_manifest import sha256_file, write_json_once


A3_JUDGE_SELECT_RESULT_VERSION = "a3.judge-select-result.v1"
A3_JUDGE_SELECT_REQUEST_VERSION = "a3.judge-select-request.v1"
JUDGE_SELECT_CANDIDATE_COUNT = 3
JUDGE_SELECT_REQUEST_FILENAME = "judge_select_request.json"
JUDGE_SELECT_RESULT_FILENAME = "judge_select_result.json"


class JudgeSelectCandidate(BaseModel):
    """One fully rendered R0 candidate submitted for selection."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(..., min_length=1)
    render_ref: str = Field(..., min_length=1)
    qc_passed: bool
    qc_violations: List[str] = Field(default_factory=list)


class JudgeSelectResult(BaseModel):
    """Pure ranking outcome; no scores, no verdicts, no critique fields."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["a3.judge-select-result.v1"] = A3_JUDGE_SELECT_RESULT_VERSION
    ranking: List[str] = Field(
        ...,
        min_length=JUDGE_SELECT_CANDIDATE_COUNT,
        max_length=JUDGE_SELECT_CANDIDATE_COUNT,
    )
    selected_candidate_id: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def _selection_consistency(self) -> "JudgeSelectResult":
        if len(set(self.ranking)) != len(self.ranking):
            raise ValueError("ranking contains duplicate candidate IDs")
        if self.selected_candidate_id != self.ranking[0]:
            raise ValueError("selected_candidate_id must equal the first ranking entry")
        return self


class JudgeSelectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["a3.judge-select-request.v1"] = A3_JUDGE_SELECT_REQUEST_VERSION
    prompt: str
    prompt_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    candidate_ids: List[str] = Field(
        ...,
        min_length=JUDGE_SELECT_CANDIDATE_COUNT,
        max_length=JUDGE_SELECT_CANDIDATE_COUNT,
    )
    render_refs: Dict[str, str]
    render_sha256: Dict[str, str]


def validate_selection(result: JudgeSelectResult, candidate_ids: List[str]) -> None:
    """The ranking must be an exact permutation of the submitted candidates."""
    if sorted(result.ranking) != sorted(candidate_ids):
        raise ValueError(
            f"ranking must be a permutation of {sorted(candidate_ids)}, "
            f"got {sorted(result.ranking)}"
        )


def build_judge_select_prompt(
    candidates: List[JudgeSelectCandidate],
    context: Optional[Dict[str, Any]] = None,
) -> str:
    if len(candidates) != JUDGE_SELECT_CANDIDATE_COUNT:
        raise ValueError(
            f"Judge-Select requires exactly {JUDGE_SELECT_CANDIDATE_COUNT} candidates, "
            f"got {len(candidates)}"
        )
    listing = [
        {
            "candidate_id": candidate.candidate_id,
            "deterministic_qc_passed": candidate.qc_passed,
            "deterministic_qc_violations": candidate.qc_violations,
        }
        for candidate in candidates
    ]
    schema = JudgeSelectResult.model_json_schema()
    return f"""Role: You are Judge-Select in AgentLayout A3.

You are shown exactly three rendered R0 layout candidates as image
attachments. Attachment order matches the candidate list below.

Task: compare the three candidates as complete layouts and rank them from
best to worst overall. Exactly one candidate is always selected; selection
is unconditional.

# Rules
- Output ONLY the ranking and the selected candidate ID.
- Do NOT write critique, defect lists, improvement suggestions or feedback.
- Do NOT output scores, grades or verdicts of any kind.
- Judge holistically from the rendered images and the structured context.

# Candidates (attachment order)
{json.dumps(listing, ensure_ascii=False, indent=2)}

# Structured context
{json.dumps(context or {}, ensure_ascii=False, indent=2)}

# Output JSON Schema
{json.dumps(schema, ensure_ascii=False, indent=2)}

Output one JSON object only, without markdown fences."""


def build_judge_select_request(
    candidates: List[JudgeSelectCandidate],
    context: Optional[Dict[str, Any]] = None,
) -> JudgeSelectRequest:
    prompt = build_judge_select_prompt(candidates, context)
    render_refs = {candidate.candidate_id: candidate.render_ref for candidate in candidates}
    render_hashes = {
        candidate.candidate_id: sha256_file(Path(candidate.render_ref))
        for candidate in candidates
    }
    return JudgeSelectRequest(
        prompt=prompt,
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        candidate_ids=[candidate.candidate_id for candidate in candidates],
        render_refs=render_refs,
        render_sha256=render_hashes,
    )


def save_judge_select_request(request: JudgeSelectRequest, output_dir: Path) -> None:
    """Persist the exact selection request without allowing artifact replacement."""
    output_dir.mkdir(parents=True, exist_ok=False)
    write_json_once(
        output_dir / JUDGE_SELECT_REQUEST_FILENAME, request.model_dump(mode="json")
    )


def parse_judge_select_result(response: str) -> JudgeSelectResult:
    text = (response or "").strip()
    if not text.startswith("{") or "```" in text:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    return JudgeSelectResult.model_validate(json.loads(text))
