"""Pipeline-level routing tests for the "先想再畫" two-LLM architecture.

No real LLM: fake Action objects with the same ``.run()`` shape are installed
onto a ``LayoutPipeline``. The tests assert the compose -> per-concept generate
-> judge -> feedback-routing control flow:

  * happy path: one candidate per concept, accept terminates;
  * design_layout-low reject -> CompositionDirector (re-compose next round);
  * typography-low reject -> CoordinateMapper (concepts reused, feedback fed).

Run:
    pytest tests/metagpt/ext/agentlayout/test_pipeline_compose_routing.py -v --no-cov
"""
from __future__ import annotations

from typing import List, Optional

import pytest

from metagpt.ext.agentlayout.pipeline import LayoutPipeline, PipelineConfig
from metagpt.ext.agentlayout.schema import (
    AestheticFeedback,
    AestheticJudgement,
    BackgroundAnalysis,
    Candidate,
    CandidatesBatch,
    Canvas,
    CompositionConcept,
    ConceptBatch,
    DesignSpec,
    Element,
    Evaluation,
    JudgeDecision,
    JudgeScores,
    LayoutElement,
    LayoutTree,
    LayoutTreeNode,
    SemanticType,
    VisualType,
)


def _spec() -> DesignSpec:
    return DesignSpec(
        canvas=Canvas(width=1080, height=1920, background_color="#FFFFFF"),
        elements=[
            Element(
                id="title_1",
                semantic_type=SemanticType.TITLE,
                visual_type=VisualType.TEXT,
                content="Hello",
                inferred=False,
                importance=5,
                semantic_relevance=0.9,
            )
        ],
        hard_constraints=[],
        style_keywords=["bold"],
        language="en",
    )


def _concept(name: str) -> CompositionConcept:
    return CompositionConcept(
        name=name,
        focal_element="title_1",
        focal_placement="x",
        text_placement="y",
        visual_flow="z",
        whitespace="w",
        typography_mood="m",
    )


def _candidate() -> Candidate:
    return Candidate(
        candidate_id="cand_01",
        elements=[
            LayoutElement(
                id="title_1",
                left=40,
                top=40,
                width=1000,
                height=300,
                z_index=2,
                font_family="sans-serif",
                font_size=80,
                font_weight="bold",
                color="#111111",
                text_align="center",
            )
        ],
    )


def _scores(**overrides) -> JudgeScores:
    base = dict(
        design_layout=8,
        content_relevance=8,
        typography_color=8,
        graphics_images=8,
        innovation_originality=8,
    )
    base.update(overrides)
    return JudgeScores(**base)


class _FakeAnalyze:
    def __init__(self, spec: DesignSpec):
        self._spec = spec
        self.calls = 0

    async def run(self, **kwargs):
        self.calls += 1
        return self._spec


class _FakeAssetAnalyzer:
    def run(self, spec):  # sync, like the real one
        return spec


class _FakePlan:
    async def run(self, *, spec):
        return LayoutTree(root=LayoutTreeNode(id="root", children=[LayoutTreeNode(id="title_1")]))


class _FakeCompose:
    def __init__(self, n: int = 3):
        self.n = n
        self.calls = 0
        self.n_seen = []  # Step 83: records the n kwarg the pipeline passed
        self.feedback_seen = []  # Step 84: records the feedback kwarg

    async def run(self, *, spec, bg=None, n=None, feedback=None, prev_concepts=None):
        self.calls += 1
        self.n_seen.append(n)
        self.feedback_seen.append(feedback)
        count = n if n is not None else self.n
        return ConceptBatch(concepts=[_concept(f"concept_{i}") for i in range(count)])


class _FakeGenerate:
    def __init__(self):
        self.calls = 0
        self.feedback_seen: List[Optional[AestheticFeedback]] = []
        self.revision_seen: List[bool] = []  # Step 84b
        self.prev_layout_seen: List[Optional[dict]] = []

    async def run(self, *, spec, tree, bg, concept, feedback=None,
                  prev_best_layout=None, revision=False, **kwargs):
        self.calls += 1
        self.feedback_seen.append(feedback)
        self.revision_seen.append(revision)
        self.prev_layout_seen.append(prev_best_layout)
        return CandidatesBatch(candidates=[_candidate()])


class _FakeJudge:
    """Returns a scripted verdict per round; best candidate = first kept."""

    def __init__(self, scripted):
        # scripted: list of (decision, scores) per round
        self.scripted = scripted
        self.round = 0

    async def run(self, *, candidates, spec, tree, bg):
        decision, scores = self.scripted[min(self.round, len(self.scripted) - 1)]
        self.round += 1
        best = candidates[0].candidate_id
        total = (
            scores.design_layout
            + scores.content_relevance
            + scores.typography_color
            + scores.graphics_images
            + scores.innovation_originality
        )
        return AestheticJudgement(
            decision=decision,
            best_candidate_id=best,
            evaluations=[
                Evaluation(
                    candidate_id=best,
                    total=total,
                    scores=scores,
                    strengths="s",
                    weaknesses="w",
                )
            ],
            feedback=AestheticFeedback(common_issues="x", suggestions=["y"]),
            best_candidate_layout={"title_1": (40.0, 40.0, 1000.0, 300.0)},
        )


def _make_pipeline(compose, generate, judge, spec, max_rounds=5):
    pipe = LayoutPipeline(config=PipelineConfig(max_total_rounds=max_rounds))
    pipe.analyze = _FakeAnalyze(spec)
    pipe.asset_analyzer = _FakeAssetAnalyzer()
    pipe.plan = _FakePlan()
    pipe.compose = compose
    pipe.generate = generate
    pipe.judge = judge
    return pipe


@pytest.mark.asyncio
async def test_happy_path_one_candidate_per_concept_then_accept():
    spec = _spec()
    compose = _FakeCompose(n=3)
    generate = _FakeGenerate()
    # Two accepts in a row -> converged (ACCEPT_CONSECUTIVE_STOP = 2).
    judge = _FakeJudge([(JudgeDecision.ACCEPT, _scores()), (JudgeDecision.ACCEPT, _scores())])
    pipe = _make_pipeline(compose, generate, judge, spec)

    result = await pipe.run(user_brief="x", asset_list=[], bg=BackgroundAnalysis())

    assert result.accepted_candidate is not None
    assert compose.calls == 1  # composed once, reused for the refinement round
    # 3 concepts/round * 2 rounds (initial + refinement) = 6 generate calls
    assert generate.calls == 6


@pytest.mark.asyncio
async def test_design_layout_reject_recomposes():
    spec = _spec()
    compose = _FakeCompose(n=2)
    generate = _FakeGenerate()
    # Round 0: reject with design_layout the worst axis -> re-compose.
    # Round 1: accept twice to terminate.
    judge = _FakeJudge(
        [
            (JudgeDecision.REJECT, _scores(design_layout=2)),
            (JudgeDecision.ACCEPT, _scores()),
            (JudgeDecision.ACCEPT, _scores()),
        ]
    )
    pipe = _make_pipeline(compose, generate, judge, spec)

    result = await pipe.run(user_brief="x", asset_list=[], bg=BackgroundAnalysis())

    assert result is not None
    # design_layout-low reject forces a second compose call.
    assert compose.calls >= 2
    # the re-compose path must clear coordinate feedback (cold concepts)
    assert generate.feedback_seen[0] is None
    # Step 84: the SECOND compose call must carry the judge's feedback
    # (design-reject loop closure); the first (cold) call must not.
    assert compose.feedback_seen[0] is None
    assert compose.feedback_seen[1] is not None
    assert compose.feedback_seen[1].common_issues == "x"
    # Step 84b: the round AFTER the design reject runs the mapper in revision
    # mode with the rejected layout as contrast reference; the cold round and
    # later rounds do not.
    assert generate.revision_seen[0] is False
    # n=2 concepts per round -> calls 0-1 are round 0, calls 2-3 are round 1.
    assert generate.revision_seen[2] is True
    assert generate.prev_layout_seen[2] is not None
    assert generate.feedback_seen[2] is not None


@pytest.mark.asyncio
async def test_compliance_measured_on_round_after_observations():
    """Step 77: observations fed to the CoordinateMapper are machine-checked
    against the NEXT round's best candidate and recorded in the trace."""
    from metagpt.ext.agentlayout.schema import VisualObservation, VisualObservationKind

    class _ObservingJudge(_FakeJudge):
        async def run(self, *, candidates, spec, tree, bg):
            judgement = await super().run(candidates=candidates, spec=spec, tree=tree, bg=bg)
            if judgement.decision == JudgeDecision.REJECT:
                judgement.feedback.visual_observations = [
                    # _candidate() puts title_1 at (40,40,1000,300): fully inside
                    # this bbox -> the retry candidate satisfies the observation.
                    VisualObservation(
                        kind=VisualObservationKind.TEXT_OFF_PANEL,
                        target_id="title_1",
                        target_bbox=[0, 0, 1080, 400],
                    ),
                    VisualObservation(
                        kind=VisualObservationKind.TEXT_TOO_SMALL,
                        target_id="title_1",
                        target_area_px=999_999_999,  # unreachable -> violated
                    ),
                ]
            return judgement

    spec = _spec()
    judge = _ObservingJudge(
        [
            (JudgeDecision.REJECT, _scores(typography_color=2)),  # -> CoordinateMapper
            (JudgeDecision.ACCEPT, _scores()),
            (JudgeDecision.ACCEPT, _scores()),
        ]
    )
    pipe = _make_pipeline(_FakeCompose(n=1), _FakeGenerate(), judge, spec)

    result = await pipe.run(user_brief="x", asset_list=[], bg=BackgroundAnalysis())

    assert result.trace[0].compliance is None  # no observations pending in round 0
    comp = result.trace[1].compliance
    assert comp is not None
    assert comp["n_verifiable"] == 2
    assert comp["n_satisfied"] == 1  # off_panel satisfied, too_small violated
    assert comp["rate"] == 0.5


@pytest.mark.asyncio
async def test_n_concepts_config_reaches_compose():
    """Step 83 single-candidate mode: PipelineConfig.n_concepts overrides the
    Director's concept count; None keeps the action default."""
    spec = _spec()
    compose = _FakeCompose()
    judge = _FakeJudge([(JudgeDecision.ACCEPT, _scores()), (JudgeDecision.ACCEPT, _scores())])
    pipe = _make_pipeline(compose, _FakeGenerate(), judge, spec)
    pipe.config = PipelineConfig(max_total_rounds=5, n_concepts=1)

    result = await pipe.run(user_brief="x", asset_list=[], bg=BackgroundAnalysis())
    assert result.accepted_candidate is not None
    assert compose.n_seen == [1]  # composed once, with the override

    compose2 = _FakeCompose()
    pipe2 = _make_pipeline(compose2, _FakeGenerate(), judge.__class__(
        [(JudgeDecision.ACCEPT, _scores()), (JudgeDecision.ACCEPT, _scores())]), spec)
    await pipe2.run(user_brief="x", asset_list=[], bg=BackgroundAnalysis())
    assert compose2.n_seen == [None]  # default config leaves n to the action


@pytest.mark.asyncio
async def test_issue_ledger_persists_dedups_and_retires():
    """Step 85: an opened issue keeps its ORIGINAL target across rounds (no
    re-litigation) until the verifier retires it geometrically; judge
    re-raising the same (kind, target_id) with a new bbox is ignored."""
    from metagpt.ext.agentlayout.schema import VisualObservation, VisualObservationKind

    class _RubricJudge(_FakeJudge):
        async def run(self, *, candidates, spec, tree, bg):
            judgement = await super().run(candidates=candidates, spec=spec, tree=tree, bg=bg)
            if judgement.decision == JudgeDecision.REJECT:
                judgement.feedback.visual_observations = [
                    # _candidate(): title_1 at (40,40,1000,300).
                    VisualObservation(  # NOT satisfied by the static candidate
                        kind=VisualObservationKind.TITLE_MISPLACED,
                        target_id="title_1",
                        target_bbox=[0, 700, 1080, 1100],
                        note="round-specific bbox that must NOT overwrite round 0's",
                    ),
                    VisualObservation(  # satisfied immediately -> retires next round
                        kind=VisualObservationKind.LOCKUP_BROKEN,
                        target_id="title_1",
                        target_bbox=[0, 0, 1080, 400],
                    ),
                ]
            return judgement

    spec = _spec()
    judge = _RubricJudge([
        (JudgeDecision.REJECT, _scores(typography_color=2)),  # -> mapper path
        (JudgeDecision.REJECT, _scores(typography_color=2)),
        (JudgeDecision.ACCEPT, _scores()),
        (JudgeDecision.ACCEPT, _scores()),
    ])
    generate = _FakeGenerate()
    pipe = _make_pipeline(_FakeCompose(n=1), generate, judge, spec)

    result = await pipe.run(user_brief="x", asset_list=[], bg=BackgroundAnalysis())

    # Round 0 opened 2 issues; round 1 verification retires lockup (satisfied)
    # and keeps title_misplaced open with the ORIGINAL bbox.
    assert result.trace[0].ledger_open == 2
    assert result.trace[1].ledger_open == 1
    fed_round1 = generate.feedback_seen[1].visual_observations
    kinds_round1 = {o.kind.value for o in fed_round1}
    assert kinds_round1 == {"title_misplaced", "lockup_broken"}
    # Round 2's fed view: only the persistent title issue, original target.
    fed_round2 = generate.feedback_seen[2].visual_observations
    assert [o.kind.value for o in fed_round2] == ["title_misplaced"]
    assert fed_round2[0].target_bbox == [0, 700, 1080, 1100]
    assert "round-specific" in fed_round2[0].note  # round 0's original object


@pytest.mark.asyncio
async def test_retired_ledger_issue_reopens_on_geometric_regression():
    """Step 88b: retirement is not immunity -- when a fixed defect comes back,
    the issue re-opens with its ORIGINAL target (the 88-trace failure: title
    fixed in R1 drifted back to the top in R2-R4 unpoliced)."""
    from metagpt.ext.agentlayout.schema import VisualObservation, VisualObservationKind

    class _FlipGenerate(_FakeGenerate):
        """Round 0: violating position; round 1: satisfied; round 2: regressed."""

        async def run(self, *, spec, tree, bg, concept, feedback=None,
                      prev_best_layout=None, revision=False, **kwargs):
            await super().run(spec=spec, tree=tree, bg=bg, concept=concept,
                              feedback=feedback, prev_best_layout=prev_best_layout,
                              revision=revision, **kwargs)
            top = 40 if self.calls != 2 else 40  # calls counts up in super()
            # call 1 (round 0): top=800 violates; call 2 (round 1): top=100
            # satisfies; call 3 (round 2): top=800 regresses.
            top = {1: 800, 2: 100, 3: 800, 4: 800}.get(self.calls, 800)
            cand = _candidate()
            cand.elements[0].top = top
            return CandidatesBatch(candidates=[cand])

    class _ObsJudge(_FakeJudge):
        async def run(self, *, candidates, spec, tree, bg):
            judgement = await super().run(candidates=candidates, spec=spec, tree=tree, bg=bg)
            if judgement.decision == JudgeDecision.REJECT:
                judgement.feedback.visual_observations = [
                    VisualObservation(
                        kind=VisualObservationKind.TITLE_MISPLACED,
                        target_id="title_1",
                        target_bbox=[0, 0, 1080, 500],  # 滿足條件: top 區
                    )
                ]
            return judgement

    spec = _spec()
    judge = _ObsJudge([
        (JudgeDecision.REJECT, _scores(typography_color=2)),  # R0: 開帳
        (JudgeDecision.REJECT, _scores(typography_color=2)),  # R1: 修好->銷帳
        (JudgeDecision.REJECT, _scores(typography_color=2)),  # R2: 回歸->重開
        (JudgeDecision.ACCEPT, _scores()),
        (JudgeDecision.ACCEPT, _scores()),
    ])
    generate = _FlipGenerate()
    pipe = _make_pipeline(_FakeCompose(n=1), generate, judge, spec)
    pipe.config = PipelineConfig(max_total_rounds=5)

    result = await pipe.run(user_brief="x", asset_list=[], bg=BackgroundAnalysis())

    # 用 trace 的帳本計數驗證（對 Analyst 預算路由穩健）:
    #   R0 開帳 1 -> R1 候選滿足銷帳 0 -> R2 候選回歸重開 1。
    assert result.trace[0].ledger_open == 1
    assert result.trace[1].ledger_open == 0
    assert result.trace[2].ledger_open == 1  # Step 88b: 回歸即重開（原目標）
    # Step 89: R1 銷帳後 keep_constraints 帶著已修好的目標（餵給下一輪）。
    fed_after_r1 = generate.feedback_seen[2]
    assert fed_after_r1 is not None
    assert [o.target_bbox for o in fed_after_r1.keep_constraints] == [[0, 0, 1080, 500]]


@pytest.mark.asyncio
async def test_round_callback_called_every_round_and_errors_swallowed():
    """Step 82: the observer fires once per judge round; its crash never
    breaks the run."""
    spec = _spec()
    judge = _FakeJudge([(JudgeDecision.ACCEPT, _scores()), (JudgeDecision.ACCEPT, _scores())])
    pipe = _make_pipeline(_FakeCompose(n=2), _FakeGenerate(), judge, spec)

    calls = []

    def cb(round_idx, kept, judgement, cb_spec):
        calls.append((round_idx, len(kept), judgement.decision, cb_spec is spec))
        raise RuntimeError("observer crash must be swallowed")

    result = await pipe.run(user_brief="x", asset_list=[], bg=BackgroundAnalysis(),
                            round_callback=cb)
    assert result.accepted_candidate is not None
    assert [c[0] for c in calls] == [0, 1]  # once per round
    assert all(c[3] for c in calls)


@pytest.mark.asyncio
async def test_exhausted_pipeline_error_carries_last_best_candidate():
    """Step 76b selection-effect fix: when every round is rejected, the raised
    PipelineError must carry the last round's judge-preferred candidate so
    evaluation drivers can still render and blind-judge the run."""
    from metagpt.ext.agentlayout.pipeline import PipelineError

    spec = _spec()
    judge = _FakeJudge([(JudgeDecision.REJECT, _scores(typography_color=2))])
    pipe = _make_pipeline(_FakeCompose(n=2), _FakeGenerate(), judge, spec, max_rounds=2)

    with pytest.raises(PipelineError) as excinfo:
        await pipe.run(user_brief="x", asset_list=[], bg=BackgroundAnalysis())

    err = excinfo.value
    assert err.best_candidate is not None
    assert err.best_candidate.candidate_id.endswith("cand_01")
    assert err.spec is spec
    assert err.judgement is not None
    assert err.judgement.decision == JudgeDecision.REJECT
    assert err.trace is not None and len(err.trace) == 2  # compliance rows survive


@pytest.mark.asyncio
async def test_underlay_regions_param_merges_into_bg():
    """Step 76: pipe.run(underlay_regions=...) must surface in the bg every
    downstream stage sees (Director and CoordinateMapper)."""
    from metagpt.ext.agentlayout.schema import UnderlayRegion

    spec = _spec()

    class _BgCapturingGenerate(_FakeGenerate):
        def __init__(self):
            super().__init__()
            self.bg_seen = []

        async def run(self, *, spec, tree, bg, concept, feedback=None, **kwargs):
            self.bg_seen.append(bg)
            return await super().run(
                spec=spec, tree=tree, bg=bg, concept=concept, feedback=feedback, **kwargs
            )

    generate = _BgCapturingGenerate()
    judge = _FakeJudge([(JudgeDecision.ACCEPT, _scores()), (JudgeDecision.ACCEPT, _scores())])
    pipe = _make_pipeline(_FakeCompose(n=1), generate, judge, spec)

    region = UnderlayRegion(
        bbox=[10, 10, 200, 100], dominant_color="#1A2B3C",
        recommended_text_color="#F4F4F4",
    )
    await pipe.run(
        user_brief="x", asset_list=[], bg=BackgroundAnalysis(),
        underlay_regions=[region],
    )

    assert generate.bg_seen
    assert all(b.underlay_regions == [region] for b in generate.bg_seen)


@pytest.mark.asyncio
async def test_typography_reject_reuses_concepts_and_feeds_coordinate_mapper():
    spec = _spec()
    compose = _FakeCompose(n=2)
    generate = _FakeGenerate()
    # Round 0: reject with typography_color the worst axis -> CoordinateMapper,
    # concepts reused. Round 1+: accept to terminate.
    judge = _FakeJudge(
        [
            (JudgeDecision.REJECT, _scores(typography_color=2)),
            (JudgeDecision.ACCEPT, _scores()),
            (JudgeDecision.ACCEPT, _scores()),
        ]
    )
    pipe = _make_pipeline(compose, generate, judge, spec)

    await pipe.run(user_brief="x", asset_list=[], bg=BackgroundAnalysis())

    # Typography reject must NOT re-compose: concepts are reused.
    assert compose.calls == 1
    # The second round's generate calls must carry the judge feedback.
    assert any(fb is not None for fb in generate.feedback_seen)
