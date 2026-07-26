"""A3 L1-Gated single-revision policy: gate, routing and the B0/B1 guard.

Implements new_plam.md sections 4.5-4.7 as versioned, deterministic
contracts:

- the repair gate turns Judge-Critic output into at most ONE routed,
  targeted revision (or an explicit no-issue outcome);
- the routing table maps every closed critic issue type to exactly one
  revision route (Coordinate Mapper alone, or Composition Director followed
  by the Mapper); semantic-role / tree-inference doubts are not routable at
  runtime by construction, because they are not representable critic issues;
- the B0/B1 guard keeps B1 only when the original issues verifiably
  improved, no new hard violation appeared and completeness did not drop;
  anything else falls back to B0.

There is deliberately no multi-round state here: one decision, one optional
revision record, one guard verdict.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from metagpt.ext.agentlayout.tools.judge_critic import (
    ActionableIssue,
    CriticIssueType,
    JudgeCriticResult,
    validate_critic_targets,
)


A3_REPAIR_GATE_VERSION = "a3.l1-repair-gate.v1"
A3_B0B1_GUARD_VERSION = "a3.b0b1-guard.v1"


class RepairRoute(str, Enum):
    COORDINATE_MAPPER = "coordinate_mapper"
    DIRECTOR_THEN_MAPPER = "director_then_mapper"


# new_plam.md section 4.6: bbox/spacing/alignment/scale/contrast issues go to
# the Coordinate Mapper; group placement and global hierarchy issues re-enter
# through the Composition Director first. Layout-vs-tree mismatches are
# placement realization problems (the frozen tree itself is never revised).
ISSUE_ROUTING: Dict[CriticIssueType, RepairRoute] = {
    CriticIssueType.OVERLAP: RepairRoute.COORDINATE_MAPPER,
    CriticIssueType.CLIPPING: RepairRoute.COORDINATE_MAPPER,
    CriticIssueType.OUT_OF_BOUNDS: RepairRoute.COORDINATE_MAPPER,
    CriticIssueType.MISALIGNMENT: RepairRoute.COORDINATE_MAPPER,
    CriticIssueType.SPACING: RepairRoute.COORDINATE_MAPPER,
    CriticIssueType.LOCKUP: RepairRoute.COORDINATE_MAPPER,
    CriticIssueType.TEXT_TOO_SMALL: RepairRoute.COORDINATE_MAPPER,
    CriticIssueType.ILLEGIBLE_TEXT: RepairRoute.COORDINATE_MAPPER,
    CriticIssueType.POOR_CONTRAST: RepairRoute.COORDINATE_MAPPER,
    CriticIssueType.TEXT_ON_BUSY_REGION: RepairRoute.COORDINATE_MAPPER,
    CriticIssueType.HIERARCHY_ERROR: RepairRoute.DIRECTOR_THEN_MAPPER,
    CriticIssueType.TREE_INCONSISTENCY: RepairRoute.DIRECTOR_THEN_MAPPER,
}


class RepairDecision(BaseModel):
    """Versioned gate outcome: either no issue, or exactly one routed revision."""

    model_config = ConfigDict(extra="forbid")

    gate_version: Literal["a3.l1-repair-gate.v1"] = A3_REPAIR_GATE_VERSION
    outcome: Literal["no_actionable_issue", "one_targeted_repair"]
    issues: List[ActionableIssue] = Field(default_factory=list)
    route: Optional[RepairRoute] = None
    keep_asset_ids: List[str] = Field(default_factory=list)
    revision_instruction: Optional[str] = None

    @model_validator(mode="after")
    def _outcome_consistency(self) -> "RepairDecision":
        if self.outcome == "no_actionable_issue":
            if self.issues or self.route is not None or self.revision_instruction:
                raise ValueError("a no-issue decision cannot carry issues or a route")
        else:
            if not self.issues:
                raise ValueError("a targeted revision requires at least one issue")
            if self.route is None or not self.revision_instruction:
                raise ValueError("a targeted revision requires a route and an instruction")
        return self


def build_revision_instruction(issues: List[ActionableIssue], keep_asset_ids: List[str]) -> str:
    lines = [
        "Apply exactly ONE revision pass to the selected layout (B0).",
        "Address only the issues below; everything else must stay as-is.",
        "",
    ]
    for index, issue in enumerate(issues, start=1):
        lines.append(
            f"Issue {index} [{issue.issue_type.value}] targets: "
            + ", ".join(issue.target_asset_ids)
        )
        lines.append(f"  Observation: {issue.observation}")
        lines.append(f"  Desired change: {issue.desired_change}")
    lines.append("")
    if keep_asset_ids:
        lines.append("KEEP unchanged (position, scale, z-order): " + ", ".join(keep_asset_ids))
    lines.append(
        "Do not add, remove or re-interpret assets; semantic roles and the "
        "Layout Tree are frozen."
    )
    return "\n".join(lines)


def evaluate_repair_gate(
    critic: JudgeCriticResult, known_asset_ids: List[str]
) -> RepairDecision:
    """Turn a critic result into a versioned single-revision decision."""
    validate_critic_targets(critic, known_asset_ids)
    if not critic.issues:
        return RepairDecision(outcome="no_actionable_issue")
    targets = {
        asset_id for issue in critic.issues for asset_id in issue.target_asset_ids
    }
    keep = sorted(set(known_asset_ids) - targets)
    route = (
        RepairRoute.DIRECTOR_THEN_MAPPER
        if any(
            ISSUE_ROUTING[issue.issue_type] is RepairRoute.DIRECTOR_THEN_MAPPER
            for issue in critic.issues
        )
        else RepairRoute.COORDINATE_MAPPER
    )
    return RepairDecision(
        outcome="one_targeted_repair",
        issues=list(critic.issues),
        route=route,
        keep_asset_ids=keep,
        revision_instruction=build_revision_instruction(list(critic.issues), keep),
    )


class IssueVerification(BaseModel):
    """Deterministic verdict for one gated issue after the revision."""

    model_config = ConfigDict(extra="forbid")

    issue_index: int = Field(..., ge=0)
    improved: bool
    evidence: str = Field(..., min_length=1)


class DeterministicGuardCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    reasons: List[str] = Field(default_factory=list)


def check_b1_against_b0(
    *,
    verifications: List[IssueVerification],
    issue_count: int,
    b0_violations: List[str],
    b1_violations: List[str],
    b0_completeness: Optional[float] = None,
    b1_completeness: Optional[float] = None,
) -> DeterministicGuardCheck:
    """new_plam.md section 4.7 conditions 1-3; any doubt keeps B0."""
    reasons: List[str] = []
    verified_indexes = {verification.issue_index for verification in verifications}
    if verified_indexes != set(range(issue_count)):
        reasons.append(
            f"verifier_coverage_mismatch: expected issues 0..{issue_count - 1}, "
            f"verified {sorted(verified_indexes)}"
        )
    unimproved = sorted(
        verification.issue_index
        for verification in verifications
        if not verification.improved
    )
    if unimproved:
        reasons.append(f"issues_not_improved: {unimproved}")
    new_violations = sorted(set(b1_violations) - set(b0_violations))
    if new_violations:
        reasons.append(f"new_hard_violations: {new_violations}")
    if (b0_completeness is None) != (b1_completeness is None):
        reasons.append("completeness_signal_missing")
    elif b0_completeness is not None and b1_completeness < b0_completeness:
        reasons.append(
            f"completeness_decreased: {b0_completeness} -> {b1_completeness}"
        )
    return DeterministicGuardCheck(passed=not reasons, reasons=reasons)


def resolve_winner(
    check: DeterministicGuardCheck,
    pairwise_winner: Optional[Literal["B0", "B1"]] = None,
) -> Literal["B0", "B1"]:
    """B1 needs a fully passing deterministic check; pairwise can only demote."""
    if not check.passed:
        return "B0"
    return pairwise_winner or "B1"


class B0B1GuardResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_version: Literal["a3.b0b1-guard.v1"] = A3_B0B1_GUARD_VERSION
    deterministic_passed: bool
    reasons: List[str] = Field(default_factory=list)
    pairwise_used: bool = False
    winner: Literal["B0", "B1"]
