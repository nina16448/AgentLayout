"""Judge-Critic contract: element-level actionable issues for B0 only.

Critique is deliberately decoupled from selection (new_plam.md section 4.4).
The result schema has no score, ranking or verdict fields, and every issue
must use a closed issue type from the repair gate (new_plam.md section 4.5),
so vague opinions such as "not beautiful enough" or "lacks creativity" are
structurally unrepresentable rather than merely discouraged by the prompt.
"""
from __future__ import annotations

import hashlib
import json
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from metagpt.ext.agentlayout.run_manifest import sha256_file, write_json_once


A3_JUDGE_CRITIC_RESULT_VERSION = "a3.judge-critic-result.v1"
A3_JUDGE_CRITIC_REQUEST_VERSION = "a3.judge-critic-request.v1"
JUDGE_CRITIC_MAX_ISSUES = 2
JUDGE_CRITIC_REQUEST_FILENAME = "judge_critic_request.json"
JUDGE_CRITIC_RESULT_FILENAME = "judge_critic_result.json"


class CriticIssueType(str, Enum):
    """Closed vocabulary; only gate-eligible, targetable defects exist."""

    OVERLAP = "overlap"
    CLIPPING = "clipping"
    OUT_OF_BOUNDS = "out_of_bounds"
    MISALIGNMENT = "misalignment"
    SPACING = "spacing"
    LOCKUP = "lockup"
    TEXT_TOO_SMALL = "text_too_small"
    ILLEGIBLE_TEXT = "illegible_text"
    POOR_CONTRAST = "poor_contrast"
    TEXT_ON_BUSY_REGION = "text_on_busy_region"
    HIERARCHY_ERROR = "hierarchy_error"
    TREE_INCONSISTENCY = "tree_inconsistency"


class ActionableIssue(BaseModel):
    """One element-level defect precise enough for one revision instruction."""

    model_config = ConfigDict(extra="forbid")

    target_asset_ids: List[str] = Field(..., min_length=1)
    issue_type: CriticIssueType
    observation: str = Field(..., min_length=1)
    desired_change: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def _unique_targets(self) -> "ActionableIssue":
        if len(set(self.target_asset_ids)) != len(self.target_asset_ids):
            raise ValueError("target_asset_ids contains duplicates")
        return self


class JudgeCriticResult(BaseModel):
    """At most two actionable issues; an empty list is a valid outcome."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["a3.judge-critic-result.v1"] = A3_JUDGE_CRITIC_RESULT_VERSION
    issues: List[ActionableIssue] = Field(..., max_length=JUDGE_CRITIC_MAX_ISSUES)


class JudgeCriticRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["a3.judge-critic-request.v1"] = A3_JUDGE_CRITIC_REQUEST_VERSION
    prompt: str
    prompt_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    b0_candidate_id: str = Field(..., min_length=1)
    render_ref: str = Field(..., min_length=1)
    render_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    known_asset_ids: List[str] = Field(..., min_length=1)


def validate_critic_targets(result: JudgeCriticResult, known_asset_ids: List[str]) -> None:
    """Every issue must target assets that actually exist in the layout."""
    known = set(known_asset_ids)
    for index, issue in enumerate(result.issues):
        unknown = sorted(set(issue.target_asset_ids) - known)
        if unknown:
            raise ValueError(f"issue {index} targets unknown asset IDs: {unknown}")


def build_judge_critic_prompt(
    b0_candidate_id: str,
    known_asset_ids: List[str],
    context: Optional[Dict[str, Any]] = None,
) -> str:
    schema = JudgeCriticResult.model_json_schema()
    return f"""Role: You are Judge-Critic in AgentLayout A3.

You are shown ONLY the already-selected best candidate (B0) as a single
image attachment. Selection is finished; do not re-rank, re-select or
compare against other candidates.

Task: report at most {JUDGE_CRITIC_MAX_ISSUES} element-level actionable issues.

# Rules
- Each issue must name at least one existing target asset ID, exactly one
  closed issue_type from the schema, and a desired change precise enough to
  become one verifier check or one revision instruction.
- Vague opinions such as "not beautiful enough" or "lacks creativity" are
  not actionable issues; omit them entirely.
- Do NOT output an overall score, grade, ranking or verdict of any kind.
- If nothing is actionable, return an empty issues list.

# B0 candidate
{json.dumps({"candidate_id": b0_candidate_id}, ensure_ascii=False)}

# Known asset IDs
{json.dumps(known_asset_ids, ensure_ascii=False, indent=2)}

# Structured context
{json.dumps(context or {}, ensure_ascii=False, indent=2)}

# Output JSON Schema
{json.dumps(schema, ensure_ascii=False, indent=2)}

Output one JSON object only, without markdown fences."""


def build_judge_critic_request(
    *,
    b0_candidate_id: str,
    render_ref: str,
    known_asset_ids: List[str],
    context: Optional[Dict[str, Any]] = None,
) -> JudgeCriticRequest:
    prompt = build_judge_critic_prompt(b0_candidate_id, known_asset_ids, context)
    return JudgeCriticRequest(
        prompt=prompt,
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        b0_candidate_id=b0_candidate_id,
        render_ref=render_ref,
        render_sha256=sha256_file(Path(render_ref)),
        known_asset_ids=list(known_asset_ids),
    )


def save_judge_critic_request(request: JudgeCriticRequest, output_dir: Path) -> None:
    """Persist the exact critique request without allowing artifact replacement."""
    output_dir.mkdir(parents=True, exist_ok=False)
    write_json_once(
        output_dir / JUDGE_CRITIC_REQUEST_FILENAME, request.model_dump(mode="json")
    )


def parse_judge_critic_result(response: str) -> JudgeCriticResult:
    text = (response or "").strip()
    if not text.startswith("{") or "```" in text:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    return JudgeCriticResult.model_validate(json.loads(text))
