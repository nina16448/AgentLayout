"""Unit tests for the Step 77 feedback verifier (compliance measurement)."""
from __future__ import annotations

from metagpt.ext.agentlayout.schema import (
    AestheticFeedback,
    Candidate,
    CompositionConcept,
    LayoutElement,
    VisualObservation,
    VisualObservationKind,
)
from metagpt.ext.agentlayout.tools.feedback_verifier import (
    check_observation,
    compliance_report,
)


def _text_el(eid: str, left: int, top: int, w: int, h: int,
             color: str = "#111111", angle: float = 0.0) -> LayoutElement:
    return LayoutElement(
        id=eid, left=left, top=top, width=w, height=h, z_index=2, angle=angle,
        font_family="sans-serif", font_size=40, font_weight="bold",
        color=color, text_align="left",
    )


def _cand(*els) -> Candidate:
    return Candidate(candidate_id="cand_01", elements=list(els))


# ------------------------------------------------------------------ per-kind


def test_off_panel_satisfied_when_inside_target_bbox():
    obs = VisualObservation(kind=VisualObservationKind.TEXT_OFF_PANEL,
                            target_id="title_1", target_bbox=[0, 0, 400, 200])
    inside = check_observation(obs, _cand(_text_el("title_1", 50, 50, 200, 100)))
    outside = check_observation(obs, _cand(_text_el("title_1", 500, 500, 200, 100)))
    assert inside.verifiable and inside.satisfied is True
    assert outside.verifiable and outside.satisfied is False


def test_illegible_satisfied_by_color_or_move():
    obs = VisualObservation(kind=VisualObservationKind.TEXT_ILLEGIBLE,
                            target_id="body_1", target_bbox=[0, 0, 300, 300],
                            target_color="#F4F4F4")
    recolored = check_observation(
        obs, _cand(_text_el("body_1", 900, 900, 100, 50, color="#f4f4f4")))
    moved = check_observation(
        obs, _cand(_text_el("body_1", 10, 10, 100, 50, color="#111111")))
    neither = check_observation(
        obs, _cand(_text_el("body_1", 900, 900, 100, 50, color="#111111")))
    assert recolored.satisfied is True   # hex match is case-insensitive
    assert moved.satisfied is True       # moved into the calm region
    assert neither.satisfied is False


def test_illegible_color_compliance_uses_tolerance_not_exact_match():
    """#FFFFFF answering a #F4F4F4 target is compliance in spirit; keeping a
    dark tone is not (Step 78 measurement-fairness fix)."""
    obs = VisualObservation(kind=VisualObservationKind.TEXT_ILLEGIBLE,
                            target_id="t", target_color="#F4F4F4")
    near = check_observation(obs, _cand(_text_el("t", 0, 0, 100, 50, color="#FFFFFF")))
    far = check_observation(obs, _cand(_text_el("t", 0, 0, 100, 50, color="#222222")))
    assert near.satisfied is True
    assert far.satisfied is False


def test_too_small_and_too_large_check_area_against_target():
    small = VisualObservation(kind=VisualObservationKind.TEXT_TOO_SMALL,
                              target_id="t", target_area_px=20_000)
    grown = check_observation(small, _cand(_text_el("t", 0, 0, 300, 100)))   # 30k
    still = check_observation(small, _cand(_text_el("t", 0, 0, 100, 100)))   # 10k
    assert grown.satisfied is True and still.satisfied is False

    large = VisualObservation(kind=VisualObservationKind.TEXT_TOO_LARGE,
                              target_id="t", target_area_px=20_000)
    shrunk = check_observation(large, _cand(_text_el("t", 0, 0, 100, 100)))
    assert shrunk.satisfied is True


def test_overlap_resolved_when_elements_separate():
    obs = VisualObservation(kind=VisualObservationKind.TEXT_OVERLAP,
                            target_id="a", second_id="b")
    apart = check_observation(
        obs, _cand(_text_el("a", 0, 0, 100, 50), _text_el("b", 500, 500, 100, 50)))
    colliding = check_observation(
        obs, _cand(_text_el("a", 0, 0, 100, 50), _text_el("b", 20, 10, 100, 50)))
    assert apart.satisfied is True
    assert colliding.satisfied is False


def test_tilted_requires_upright_within_tolerance():
    obs = VisualObservation(kind=VisualObservationKind.TEXT_TILTED, target_id="t")
    upright = check_observation(obs, _cand(_text_el("t", 0, 0, 100, 50, angle=1.5)))
    tilted = check_observation(obs, _cand(_text_el("t", 0, 0, 100, 50, angle=12.0)))
    assert upright.satisfied is True
    assert tilted.satisfied is False


def test_step85_rubric_kinds_verify_as_inside_bbox():
    for kind in (VisualObservationKind.TITLE_MISPLACED, VisualObservationKind.LOCKUP_BROKEN):
        obs = VisualObservation(kind=kind, target_id="t", target_bbox=[0, 0, 400, 200])
        inside = check_observation(obs, _cand(_text_el("t", 50, 50, 200, 100)))
        outside = check_observation(obs, _cand(_text_el("t", 500, 500, 200, 100)))
        assert inside.satisfied is True and outside.satisfied is False
        # missing target_bbox -> unverifiable, never a free pass
        bare = VisualObservation(kind=kind, target_id="t")
        assert check_observation(bare, _cand(_text_el("t", 0, 0, 10, 10))).verifiable is False


# ------------------------------------------------------------------ edge cases


def test_missing_element_and_missing_target_are_unverifiable():
    gone = check_observation(
        VisualObservation(kind=VisualObservationKind.TEXT_TILTED, target_id="ghost"),
        _cand(_text_el("t", 0, 0, 100, 50)),
    )
    no_target = check_observation(
        VisualObservation(kind=VisualObservationKind.TEXT_TOO_SMALL, target_id="t"),
        _cand(_text_el("t", 0, 0, 100, 50)),
    )
    assert gone.verifiable is False and gone.satisfied is None
    assert no_target.verifiable is False


def test_compliance_report_rate_excludes_unverifiable():
    cand = _cand(_text_el("t", 50, 50, 200, 100))
    obs = [
        VisualObservation(kind=VisualObservationKind.TEXT_OFF_PANEL,
                          target_id="t", target_bbox=[0, 0, 400, 200]),   # satisfied
        VisualObservation(kind=VisualObservationKind.TEXT_TILTED,
                          target_id="ghost"),                             # unverifiable
        VisualObservation(kind=VisualObservationKind.TEXT_TOO_SMALL,
                          target_id="t", target_area_px=999_999),         # violated
    ]
    report = compliance_report(obs, cand)
    assert report.n_total == 3
    assert report.n_verifiable == 2
    assert report.n_satisfied == 1
    assert report.rate == 0.5


def test_empty_observations_yield_none_rate():
    report = compliance_report([], _cand(_text_el("t", 0, 0, 10, 10)))
    assert report.n_total == 0 and report.rate is None


# ------------------------------------------------------------------ Step 78 decoupled observer


def test_observe_prompt_lists_catalogue_layout_and_panels():
    """Step 78: the inspector prompt is a SEPARATE call -- it carries the
    candidate layout, the panel bboxes and the closed catalogue, and it
    explicitly de-scopes judging (verdict contamination fix from 77d)."""
    from metagpt.ext.agentlayout.actions.judge_aesthetic import JudgeAesthetic
    from metagpt.ext.agentlayout.schema import (
        BackgroundAnalysis,
        Canvas,
        DesignSpec,
        Element,
        SemanticType,
        UnderlayRegion,
        VisualType,
    )

    spec = DesignSpec(
        canvas=Canvas(width=1080, height=1080),
        elements=[Element(id="title_1", semantic_type=SemanticType.TITLE,
                          visual_type=VisualType.TEXT, content="X",
                          inferred=False, importance=5, semantic_relevance=0.5)],
        hard_constraints=[], style_keywords=[], language="en",
    )
    bg = BackgroundAnalysis(
        underlay_regions=[
            UnderlayRegion(bbox=[36, 30, 1064, 1049], dominant_color="#090102",
                           recommended_text_color="#F4F4F4")
        ]
    )
    cand = _cand(_text_el("title_1", 50, 50, 200, 100))
    prompt = JudgeAesthetic._build_observe_prompt(cand, spec, bg)
    for kind in ("text_off_panel", "text_illegible", "text_too_small",
                 "text_too_large", "text_overlap", "text_tilted"):
        assert kind in prompt
    assert "panel 1: bbox [36, 30, 1064, 1049]" in prompt
    assert "title_1: bbox [50, 50, 250, 150]" in prompt
    assert "your job is NOT to\njudge it" in prompt  # decoupling statement
    # Step 87 anti-hallucination guard: per-element 'ok' is a common verdict
    assert "COMMON\n    verdict" in prompt or "COMMON" in prompt
    assert "do NOT invent defects" in prompt
    assert "ONE object per element" in prompt


def test_judgement_prompt_has_no_catalogue_even_when_flag_on(monkeypatch):
    """77d fix: the main judgement prompt must stay byte-identical regardless
    of the flag -- the catalogue lives only in the observer call."""
    from metagpt.ext.agentlayout.actions import judge_aesthetic as ja

    monkeypatch.setenv("AGENTLAYOUT_VISUAL_LOOP", "1")
    assert "text_off_panel" not in ja.PROMPT_TEMPLATE
    assert "visual_observations" not in ja.PROMPT_TEMPLATE


def test_parse_observations_tolerates_wrappers_and_drops_invalid():
    from metagpt.ext.agentlayout.actions.judge_aesthetic import JudgeAesthetic

    good = ('[{"kind": "text_off_panel", "target_id": "title_1", '
            '"target_bbox": [0, 0, 100, 100], "note": "x"}]')
    assert len(JudgeAesthetic._parse_observations(good)) == 1
    # dict wrapper form
    wrapped = '{"visual_observations": %s}' % good
    assert len(JudgeAesthetic._parse_observations(wrapped)) == 1
    # invalid kind dropped, valid one kept
    mixed = ('[{"kind": "nonsense", "target_id": "a"}, '
             '{"kind": "text_tilted", "target_id": "b"}]')
    parsed = JudgeAesthetic._parse_observations(mixed)
    assert len(parsed) == 1 and parsed[0].kind.value == "text_tilted"
    # garbage / empty
    assert JudgeAesthetic._parse_observations("not json") == []
    assert JudgeAesthetic._parse_observations("[]") == []


def test_parse_per_element_reviews_skips_ok_and_maps_fields():
    """Step 87: per-element form -- 'ok' entries confirm coverage without an
    observation; issue entries map element_id/verdict/comment onto the
    VisualObservation schema."""
    from metagpt.ext.agentlayout.actions.judge_aesthetic import JudgeAesthetic

    rsp = """[
      {"element_id": "text_1", "verdict": "ok", "comment": "fine"},
      {"element_id": "text_2", "verdict": "text_illegible",
       "target_color": "#F4F4F4", "target_bbox": [160, 50, 700, 200],
       "comment": "sits on the misty subject"},
      {"element_id": "text_3", "verdict": "OK"}
    ]"""
    parsed = JudgeAesthetic._parse_observations(
        rsp, expected_ids={"text_1", "text_2", "text_3"}
    )
    assert len(parsed) == 1
    obs = parsed[0]
    assert obs.kind.value == "text_illegible"
    assert obs.target_id == "text_2"
    assert obs.target_bbox == [160, 50, 700, 200]
    assert obs.note == "sits on the misty subject"
    # all-ok answer parses to no observations
    assert JudgeAesthetic._parse_observations(
        '[{"element_id": "a", "verdict": "ok"}]', expected_ids={"a"}
    ) == []


def test_visual_loop_flag_default_off(monkeypatch):
    from metagpt.ext.agentlayout.feature_flags import visual_loop_enabled

    monkeypatch.delenv("AGENTLAYOUT_VISUAL_LOOP", raising=False)
    assert visual_loop_enabled() is False
    monkeypatch.setenv("AGENTLAYOUT_VISUAL_LOOP", "1")
    assert visual_loop_enabled() is True


# ------------------------------------------------------------------ schema defaults


def test_feedback_visual_observations_default_empty():
    fb = AestheticFeedback(common_issues="x")
    assert fb.visual_observations == []


def test_concept_text_assignments_default_empty():
    concept = CompositionConcept(
        name="n", focal_element="e", focal_placement="p", text_placement="t",
        visual_flow="v", whitespace="w", typography_mood="m",
    )
    assert concept.text_assignments == {}
