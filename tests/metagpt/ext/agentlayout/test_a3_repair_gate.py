from __future__ import annotations

import pytest
from pydantic import ValidationError

from metagpt.ext.agentlayout.tools.judge_critic import (
    ActionableIssue,
    CriticIssueType,
    JudgeCriticResult,
)
from metagpt.ext.agentlayout.tools.repair_gate import (
    ISSUE_ROUTING,
    B0B1GuardResult,
    DeterministicGuardCheck,
    IssueVerification,
    RepairDecision,
    RepairRoute,
    check_b1_against_b0,
    evaluate_repair_gate,
    resolve_winner,
)


KNOWN_IDS = ["asset_0001", "asset_0002", "asset_0003"]


def _issue(issue_type: str = "overlap", targets=None) -> ActionableIssue:
    return ActionableIssue(
        target_asset_ids=targets or ["asset_0001"],
        issue_type=issue_type,
        observation="headline overlaps the product image",
        desired_change="move asset_0001 above asset_0003 with clear separation",
    )


def _verification(index: int = 0, improved: bool = True) -> IssueVerification:
    return IssueVerification(
        issue_index=index,
        improved=improved,
        evidence="overlap area 0.31 -> 0.00 between asset_0001 and asset_0003",
    )


def test_routing_table_covers_every_closed_issue_type_exactly():
    assert set(ISSUE_ROUTING) == set(CriticIssueType)
    assert ISSUE_ROUTING[CriticIssueType.OVERLAP] is RepairRoute.COORDINATE_MAPPER
    assert ISSUE_ROUTING[CriticIssueType.POOR_CONTRAST] is RepairRoute.COORDINATE_MAPPER
    assert ISSUE_ROUTING[CriticIssueType.HIERARCHY_ERROR] is RepairRoute.DIRECTOR_THEN_MAPPER
    assert ISSUE_ROUTING[CriticIssueType.TREE_INCONSISTENCY] is RepairRoute.DIRECTOR_THEN_MAPPER


def test_no_issue_gate_outcome_carries_no_route_or_instruction():
    decision = evaluate_repair_gate(JudgeCriticResult(issues=[]), KNOWN_IDS)
    assert decision.gate_version == "a3.l1-repair-gate.v1"
    assert decision.outcome == "no_actionable_issue"
    assert decision.issues == []
    assert decision.route is None
    assert decision.revision_instruction is None


def test_single_issue_routes_to_mapper_with_keep_constraints():
    critic = JudgeCriticResult(issues=[_issue(targets=["asset_0001", "asset_0003"])])
    decision = evaluate_repair_gate(critic, KNOWN_IDS)
    assert decision.outcome == "one_targeted_repair"
    assert decision.route is RepairRoute.COORDINATE_MAPPER
    assert decision.keep_asset_ids == ["asset_0002"]
    assert "exactly ONE revision" in decision.revision_instruction
    assert "KEEP unchanged" in decision.revision_instruction
    assert "asset_0002" in decision.revision_instruction
    assert "move asset_0001 above asset_0003" in decision.revision_instruction
    assert "Layout Tree are frozen" in decision.revision_instruction


def test_hierarchy_issues_route_through_the_director_and_dominate_mixed_sets():
    hierarchy = JudgeCriticResult(issues=[_issue("hierarchy_error")])
    assert evaluate_repair_gate(hierarchy, KNOWN_IDS).route is RepairRoute.DIRECTOR_THEN_MAPPER

    mixed = JudgeCriticResult(
        issues=[_issue("overlap"), _issue("tree_inconsistency", targets=["asset_0002"])]
    )
    decision = evaluate_repair_gate(mixed, KNOWN_IDS)
    assert decision.route is RepairRoute.DIRECTOR_THEN_MAPPER
    assert decision.keep_asset_ids == ["asset_0003"]


def test_gate_rejects_issues_targeting_unknown_assets():
    critic = JudgeCriticResult(issues=[_issue(targets=["asset_9999"])])
    with pytest.raises(ValueError, match="unknown asset IDs"):
        evaluate_repair_gate(critic, KNOWN_IDS)


def test_decision_model_enforces_outcome_consistency():
    with pytest.raises(ValidationError, match="requires at least one issue"):
        RepairDecision(outcome="one_targeted_repair")
    with pytest.raises(ValidationError, match="requires a route"):
        RepairDecision(outcome="one_targeted_repair", issues=[_issue()])
    with pytest.raises(ValidationError, match="cannot carry issues"):
        RepairDecision(outcome="no_actionable_issue", issues=[_issue()])


def test_guard_passes_when_issues_improved_and_nothing_regressed():
    check = check_b1_against_b0(
        verifications=[_verification()],
        issue_count=1,
        b0_violations=["pre-existing warning"],
        b1_violations=["pre-existing warning"],
        b0_completeness=1.0,
        b1_completeness=1.0,
    )
    assert check.passed and check.reasons == []


def test_guard_rejects_unimproved_issues_and_coverage_gaps():
    check = check_b1_against_b0(
        verifications=[_verification(improved=False)],
        issue_count=1,
        b0_violations=[],
        b1_violations=[],
    )
    assert not check.passed
    assert any("issues_not_improved" in reason for reason in check.reasons)

    gap = check_b1_against_b0(
        verifications=[_verification(index=0)],
        issue_count=2,
        b0_violations=[],
        b1_violations=[],
    )
    assert not gap.passed
    assert any("verifier_coverage_mismatch" in reason for reason in gap.reasons)


def test_guard_rejects_new_hard_violations_and_completeness_drop():
    new_violation = check_b1_against_b0(
        verifications=[_verification()],
        issue_count=1,
        b0_violations=["old"],
        b1_violations=["old", "text clipped at canvas edge"],
    )
    assert not new_violation.passed
    assert any("new_hard_violations" in reason for reason in new_violation.reasons)

    drop = check_b1_against_b0(
        verifications=[_verification()],
        issue_count=1,
        b0_violations=[],
        b1_violations=[],
        b0_completeness=1.0,
        b1_completeness=0.9,
    )
    assert not drop.passed
    assert any("completeness_decreased" in reason for reason in drop.reasons)

    missing = check_b1_against_b0(
        verifications=[_verification()],
        issue_count=1,
        b0_violations=[],
        b1_violations=[],
        b0_completeness=1.0,
        b1_completeness=None,
    )
    assert not missing.passed
    assert "completeness_signal_missing" in missing.reasons


def test_winner_resolution_is_fail_closed_and_pairwise_can_only_demote():
    passing = DeterministicGuardCheck(passed=True)
    failing = DeterministicGuardCheck(passed=False, reasons=["issues_not_improved: [0]"])
    assert resolve_winner(passing) == "B1"
    assert resolve_winner(passing, "B0") == "B0"
    assert resolve_winner(passing, "B1") == "B1"
    assert resolve_winner(failing) == "B0"
    assert resolve_winner(failing, "B1") == "B0"


def test_guard_result_is_versioned():
    result = B0B1GuardResult(deterministic_passed=True, winner="B1")
    assert result.policy_version == "a3.b0b1-guard.v1"
    with pytest.raises(ValidationError):
        B0B1GuardResult(deterministic_passed=True, winner="B2")
