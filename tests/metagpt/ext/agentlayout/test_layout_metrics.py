"""Unit tests for Step 83 layout metrics + judge integration."""
from __future__ import annotations

from metagpt.ext.agentlayout.schema import (
    BackgroundAnalysis,
    Candidate,
    Canvas,
    DesignSpec,
    Element,
    LayoutElement,
    SemanticType,
    UnderlayRegion,
    VisualType,
)
from metagpt.ext.agentlayout.tools.layout_metrics import (
    GT_METRICS,
    format_metrics_block,
    measure_layout,
)


def _el(eid: str, left: int, top: int, w: int, h: int) -> LayoutElement:
    return LayoutElement(id=eid, left=left, top=top, width=w, height=h, z_index=2)


def _spec(n_texts: int = 4) -> DesignSpec:
    els = [
        Element(id=f"text_{i}", semantic_type=SemanticType.BODY_TEXT,
                visual_type=VisualType.IMAGE, content=f"t{i}",
                asset_ref=f"/x/asset_{i:02d}_text.png", inferred=False,
                importance=3, semantic_relevance=0.5)
        for i in range(1, n_texts + 1)
    ]
    els.append(Element(id="photo_1", semantic_type=SemanticType.PRODUCT_IMAGE,
                       visual_type=VisualType.IMAGE, asset_ref="/x/p.png",
                       inferred=False, importance=3, semantic_relevance=0.5))
    return DesignSpec(canvas=Canvas(width=1000, height=1000), elements=els,
                      hard_constraints=[], style_keywords=[], language="en")


def test_gt_constants_pinned():
    """Re-run the Step 83 calibration one-liner before changing these."""
    assert GT_METRICS["lockup_gap_p50"] == 0.014
    assert GT_METRICS["left_groups_p50"] == 3
    assert GT_METRICS["fully_centered_rate"] == 0.223


def test_centered_alignment_and_lockup():
    # title (largest) at top; one text right below (tight lockup); two far.
    cand = Candidate(candidate_id="c", elements=[
        _el("text_1", 100, 100, 800, 200),   # title, centered (cx=500)
        _el("text_2", 300, 310, 400, 50),    # centered, 10px below title
        _el("text_3", 50, 700, 300, 40),     # left-ish
        _el("text_4", 50, 800, 300, 40),     # same left edge as text_3
        _el("photo_1", 0, 0, 10, 10),        # non-text: ignored
    ])
    m = measure_layout(cand, _spec())
    assert m.n_texts == 4
    assert m.centered_count == 2 and m.centered_fraction == 0.5
    # left edges: 100, 300, 50, 50 -> groups {50,50},{100},{300} = 3
    assert m.n_left_groups == 3
    assert m.lockup_gap_frac == 0.01  # 10px / 1000
    assert m.n_overlap_pairs == 0


def test_overlap_pairs_and_panel_utilization():
    cand = Candidate(candidate_id="c", elements=[
        _el("text_1", 100, 100, 400, 100),
        _el("text_2", 150, 150, 400, 100),   # overlaps text_1
        _el("text_3", 600, 600, 200, 100),   # inside panel
    ])
    regions = [UnderlayRegion(bbox=[600, 600, 1000, 800], dominant_color="#000000",
                              recommended_text_color="#F4F4F4")]
    m = measure_layout(cand, _spec(3), regions)
    assert m.n_overlap_pairs == 1
    # panel 400x200=80k; text_3 fully inside = 20k -> 0.25
    assert m.panel_utilization == 0.25


def test_format_block_is_neutral_and_cites_gt():
    cand = Candidate(candidate_id="c", elements=[
        _el("text_1", 100, 100, 800, 200),
        _el("text_2", 300, 310, 400, 50),
    ])
    block = format_metrics_block(measure_layout(cand, _spec(2)))
    assert "NEUTRAL measurements" in block
    assert "legitimate style" in block          # centering not framed as a flaw
    assert "designer p50=0.014" in block
    assert "22%" in block


def test_canvas_resources_block_lists_panels_zones_and_frame_semantics():
    """Step 86: judge + observer get panels (frame/solid) AND CV placeable
    regions -- the terrain for image-specific target_bbox estimates."""
    from metagpt.ext.agentlayout.actions.judge_aesthetic import JudgeAesthetic
    from metagpt.ext.agentlayout.schema import SafeZone

    bg = BackgroundAnalysis(
        safe_zones=[SafeZone(region="top-left", bbox=[0, 0, 400, 300], confidence=0.9)],
        underlay_regions=[
            UnderlayRegion(bbox=[104, 408, 836, 717], dominant_color="#28312A",
                           recommended_text_color="#F4F4F4", panel_type="frame"),
            UnderlayRegion(bbox=[0, 900, 500, 1000], dominant_color="#000000",
                           recommended_text_color="#F4F4F4"),
        ],
    )
    block = JudgeAesthetic._canvas_resources_block(bg)
    assert "panel 1: bbox [104, 408, 836, 717], transparent frame, backdrop ~#28312A" in block
    assert "panel 2: bbox [0, 900, 500, 1000], solid fill #000000" in block
    assert "top-left: bbox [0, 0, 400, 300] (confidence 0.90)" in block
    assert "anchors for target_bbox estimates" in block
    assert JudgeAesthetic._canvas_resources_block(BackgroundAnalysis()) == \
        "(no panel / region data available)"


def test_judge_prompt_gets_anchors_and_geometry(tmp_path):
    from metagpt.ext.agentlayout.actions.judge_aesthetic import (
        JudgeAesthetic,
        _SCORING_ANCHORS,
    )

    assert "Scoring anchors" in _SCORING_ANCHORS
    assert "INVALID weaknesses" in _SCORING_ANCHORS

    cand = Candidate(candidate_id="cand_01", elements=[
        _el("text_1", 100, 100, 800, 200),
        _el("text_2", 300, 310, 400, 50),
    ])
    block = JudgeAesthetic._geometry_facts_block(
        [cand], _spec(2), BackgroundAnalysis()
    )
    assert "Machine-measured geometry" in block
    assert "Candidate cand_01" in block
    assert "lockup" in block
