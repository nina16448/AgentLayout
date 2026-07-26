from __future__ import annotations

from metagpt.ext.agentlayout.schema import Candidate
from metagpt.ext.agentlayout.tools.issue_verifier import (
    PROXY_TYPES,
    STRICT_TYPES,
    verify_issues,
)
from metagpt.ext.agentlayout.tools.judge_critic import (
    ActionableIssue,
    CriticIssueType,
    JudgeCriticResult,
)
from metagpt.ext.agentlayout.tools.repair_gate import evaluate_repair_gate


CANVAS = {"canvas_width": 800, "canvas_height": 600}
KNOWN_IDS = ["asset_0001", "asset_0002", "asset_0003"]


def _candidate(boxes: dict) -> Candidate:
    return Candidate(
        candidate_id="candidate",
        elements=[
            {
                "id": asset_id,
                "left": box[0],
                "top": box[1],
                "width": box[2],
                "height": box[3],
                "z_index": index,
            }
            for index, (asset_id, box) in enumerate(sorted(boxes.items()))
        ],
    )


def _decision(issue_type: str, targets=None):
    critic = JudgeCriticResult(
        issues=[
            ActionableIssue(
                target_asset_ids=targets or ["asset_0001"],
                issue_type=issue_type,
                observation="deterministic test issue",
                desired_change="apply the geometric fix",
            )
        ]
    )
    return evaluate_repair_gate(critic, KNOWN_IDS)


BASE = {
    "asset_0001": (100, 100, 200, 100),
    "asset_0002": (150, 150, 200, 100),  # overlaps asset_0001
    "asset_0003": (500, 400, 100, 100),
}


def test_every_issue_type_is_classified_strict_or_proxy():
    assert STRICT_TYPES | PROXY_TYPES == set(CriticIssueType)
    assert not STRICT_TYPES & PROXY_TYPES


def test_overlap_improvement_requires_intersection_to_shrink():
    b1_fixed = _candidate({**BASE, "asset_0001": (100, 300, 200, 100)})
    b1_same = _candidate(BASE)
    decision = _decision("overlap")
    improved = verify_issues(decision, _candidate(BASE), b1_fixed, **CANVAS)
    unchanged = verify_issues(decision, _candidate(BASE), b1_same, **CANVAS)
    assert improved[0].improved is True
    assert "overlap area" in improved[0].evidence
    assert unchanged[0].improved is False


def test_out_of_bounds_improvement_requires_out_area_to_shrink():
    base = {**BASE, "asset_0001": (700, 100, 200, 100)}  # 100px past right edge
    decision = _decision("out_of_bounds")
    fixed = verify_issues(
        decision,
        _candidate(base),
        _candidate({**base, "asset_0001": (590, 100, 200, 100)}),
        **CANVAS,
    )
    worse = verify_issues(
        decision,
        _candidate(base),
        _candidate({**base, "asset_0001": (750, 100, 200, 100)}),
        **CANVAS,
    )
    assert fixed[0].improved is True
    assert worse[0].improved is False


def test_text_too_small_improvement_requires_area_growth():
    decision = _decision("text_too_small")
    grown = verify_issues(
        decision,
        _candidate(BASE),
        _candidate({**BASE, "asset_0001": (100, 100, 300, 150)}),
        **CANVAS,
    )
    shrunk = verify_issues(
        decision,
        _candidate(BASE),
        _candidate({**BASE, "asset_0001": (100, 100, 100, 50)}),
        **CANVAS,
    )
    assert grown[0].improved is True
    assert shrunk[0].improved is False


def test_misalignment_improvement_requires_smaller_guide_distance():
    base = {**BASE, "asset_0001": (103, 100, 200, 100), "asset_0002": (100, 300, 200, 100)}
    decision = _decision("misalignment")
    aligned = verify_issues(
        decision,
        _candidate(base),
        _candidate({**base, "asset_0001": (100, 100, 200, 100)}),
        **CANVAS,
    )
    assert aligned[0].improved is True
    assert "alignment error" in aligned[0].evidence


def test_proxy_types_use_acted_upon_movement_check():
    decision = _decision("poor_contrast")
    moved = verify_issues(
        decision,
        _candidate(BASE),
        _candidate({**BASE, "asset_0001": (100, 350, 200, 100)}),
        **CANVAS,
    )
    untouched = verify_issues(decision, _candidate(BASE), _candidate(BASE), **CANVAS)
    assert moved[0].improved is True
    assert "acted-upon proxy" in moved[0].evidence
    assert untouched[0].improved is False
    assert "left untouched" in untouched[0].evidence


def test_missing_target_fails_closed():
    decision = _decision("overlap")
    without_target = _candidate(
        {key: value for key, value in BASE.items() if key != "asset_0001"}
    )
    result = verify_issues(decision, _candidate(BASE), without_target, **CANVAS)
    assert result[0].improved is False
    assert "targets missing" in result[0].evidence


def test_verifications_align_with_issue_indexes_for_two_issues():
    critic = JudgeCriticResult(
        issues=[
            ActionableIssue(
                target_asset_ids=["asset_0001"],
                issue_type="overlap",
                observation="headline overlaps product",
                desired_change="separate them",
            ),
            ActionableIssue(
                target_asset_ids=["asset_0003"],
                issue_type="spacing",
                observation="price crowds the corner",
                desired_change="add breathing room",
            ),
        ]
    )
    decision = evaluate_repair_gate(critic, KNOWN_IDS)
    b1 = _candidate(
        {
            **BASE,
            "asset_0001": (100, 300, 200, 100),
            "asset_0003": (450, 350, 100, 100),
        }
    )
    results = verify_issues(decision, _candidate(BASE), b1, **CANVAS)
    assert [entry.issue_index for entry in results] == [0, 1]
    assert all(entry.improved for entry in results)


def test_empty_decision_yields_no_verifications():
    decision = evaluate_repair_gate(JudgeCriticResult(issues=[]), KNOWN_IDS)
    assert verify_issues(decision, _candidate(BASE), _candidate(BASE), **CANVAS) == []


def test_verifier_output_feeds_the_guard_contract():
    from metagpt.ext.agentlayout.tools.repair_gate import check_b1_against_b0

    decision = _decision("overlap")
    verifications = verify_issues(
        decision,
        _candidate(BASE),
        _candidate({**BASE, "asset_0001": (100, 300, 200, 100)}),
        **CANVAS,
    )
    check = check_b1_against_b0(
        verifications=verifications,
        issue_count=len(decision.issues),
        b0_violations=[],
        b1_violations=[],
    )
    assert check.passed
