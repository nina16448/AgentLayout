from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from metagpt.ext.agentlayout.tools.director_contract import (
    A3ConceptSet,
    build_director_request,
    parse_concept_set,
    validate_concepts_against_assets,
)
from metagpt.ext.agentlayout.tools.mapper_contract import (
    build_mapper_request,
    parse_candidate,
    validate_candidate_coverage,
)
from metagpt.ext.agentlayout.layout_tree_v3 import (
    condition_prompt_payload,
    make_tree_condition,
)
from metagpt.ext.agentlayout.schema import Candidate, CompositionConcept
from metagpt.ext.agentlayout.tools.analyst_vision import (
    A3AnalystOutput,
    A3AssetUnderstanding,
)
from metagpt.ext.agentlayout.tools.text_bitmap_normalizer import (
    R3Asset,
    R3AssetManifest,
    R3NormalizationConfig,
)


def _analyst() -> A3AnalystOutput:
    return A3AnalystOutput(
        background_summary="Quiet blue background",
        design_intent="Promote a summer sale",
        style_keywords=["bright"],
        assets=[
            A3AssetUnderstanding(
                asset_id="asset_0001",
                semantic_type="title",
                description="Main sale heading",
                semantic_role="primary message",
            ),
            A3AssetUnderstanding(
                asset_id="asset_0002",
                semantic_type="product_image",
                description="Featured shoe",
                semantic_role="focal product",
            ),
        ],
    )


def _concept(name: str = "Left bleed", focal: str = "asset_0002") -> CompositionConcept:
    return CompositionConcept(
        name=name,
        focal_element=focal,
        focal_placement="hero shoe bleeding off the left edge",
        text_placement="headline stacked in the right third",
        visual_flow="left-to-right Z pattern",
        whitespace="generous right-side margin",
        typography_mood="bold condensed sans",
    )


def _concept_set(focal: str = "asset_0002") -> A3ConceptSet:
    return A3ConceptSet(
        concepts=[
            _concept("Left bleed", focal),
            _concept("Top banner", focal),
            _concept("Centered stage", focal),
        ]
    )


def _manifest(tmp_path: Path) -> R3AssetManifest:
    def _png(name: str, size=(64, 32)) -> str:
        path = tmp_path / name
        if not path.exists():
            Image.new("RGBA", size, (200, 40, 40, 255)).save(path)
        return str(path)

    return R3AssetManifest(
        sample_id="sample01",
        canvas_width=800,
        canvas_height=600,
        normalization=R3NormalizationConfig(),
        source_pfull_manifest_sha256="2" * 64,
        assets=[
            R3Asset(
                asset_id="asset_0001",
                role="placeable",
                media_type="text_bitmap",
                content="SUMMER SALE",
                asset_ref=_png("asset_0001_r3_text.png", (96, 18)),
                sha256="0" * 64,
                bitmap_width=96,
                bitmap_height=18,
                bitmap_aspect_ratio=96 / 18,
            ),
            R3Asset(
                asset_id="asset_0002",
                role="placeable",
                media_type="raster",
                content=None,
                asset_ref=_png("asset_0002.png", (64, 64)),
                sha256="1" * 64,
                bitmap_width=64,
                bitmap_height=64,
                bitmap_aspect_ratio=1.0,
            ),
        ],
    )


def _candidate(ids=("asset_0001", "asset_0002")) -> Candidate:
    return Candidate(
        candidate_id="candidate",
        elements=[
            {
                "id": asset_id,
                "left": 20 + 100 * index,
                "top": 30,
                "width": 200,
                "height": 100,
                "z_index": index,
            }
            for index, asset_id in enumerate(ids)
        ],
    )


def test_concept_set_requires_exactly_three_distinct_concepts():
    assert len(_concept_set().concepts) == 3
    with pytest.raises(ValidationError):
        A3ConceptSet(concepts=[_concept(), _concept("Top banner")])
    with pytest.raises(ValidationError, match="distinct names"):
        A3ConceptSet(concepts=[_concept(), _concept(), _concept("Top banner")])


def test_focal_elements_must_be_known_assets():
    condition = make_tree_condition("T0", _analyst())
    validate_concepts_against_assets(_concept_set(), condition)
    with pytest.raises(ValueError, match="unknown asset IDs"):
        validate_concepts_against_assets(_concept_set(focal="asset_9999"), condition)


def test_director_prompt_carries_condition_but_no_geometry_inputs():
    analyst = _analyst()
    t0_request = build_director_request(analyst, make_tree_condition("T0", analyst), "800x600")
    t0 = t0_request.prompt
    t1 = build_director_request(analyst, make_tree_condition("T1", analyst), "800x600").prompt
    assert t0_request.version == "a3.director-request.v1"
    assert len(t0_request.prompt_sha256) == 64
    assert t0_request.tree_arm == "T0"
    assert "Composition Director" in t0
    assert '"tree_condition": "T0"' in t0
    assert "flat_roles" not in t0
    assert "flat_roles" in t1
    assert "Do NOT output coordinates" in t0
    assert '"left"' not in t0 and '"top"' not in t0
    assert "ACCEPT" not in t0 and "threshold" not in t0


def test_condition_payload_exposes_exactly_the_arm_information():
    analyst = _analyst()
    t0 = condition_prompt_payload(make_tree_condition("T0", analyst))
    t1 = condition_prompt_payload(make_tree_condition("T1", analyst))
    assert set(t0) == {"tree_condition", "asset_ids"}
    assert set(t1) == {"tree_condition", "asset_ids", "flat_roles"}
    assert t0["asset_ids"] == ["asset_0001", "asset_0002"]


def test_concept_parser_accepts_fenced_json():
    concept_set = _concept_set()
    parsed = parse_concept_set("```json\n" + concept_set.model_dump_json() + "\n```")
    assert parsed == concept_set


def test_mapper_prompt_uses_aspect_ratio_only_no_bitmap_pixel_sizes(tmp_path):
    manifest = _manifest(tmp_path)
    condition = make_tree_condition("T0", _analyst())
    request = build_mapper_request(
        concept=_concept(), condition=condition, manifest=manifest
    )
    prompt = request.prompt
    assert request.version == "a3.mapper-request.v1"
    assert request.mode == "r0"
    assert "Coordinate Mapper" in prompt
    assert "bitmap_aspect_ratio" in prompt
    assert "5.333333" in prompt
    # Normalized bitmap pixel sizes must not leak; only the canvas is in pixels.
    assert '"bitmap_width"' not in prompt and '"bitmap_height"' not in prompt
    assert '"96"' not in prompt and ": 96" not in prompt
    assert "natural size" not in prompt
    assert "0.8x" not in prompt
    assert "800x600" in prompt


def test_mapper_prompt_revision_mode_embeds_base_and_instruction(tmp_path):
    manifest = _manifest(tmp_path)
    condition = make_tree_condition("T0", _analyst())
    base = [element.model_dump(mode="json") for element in _candidate().elements]
    request = build_mapper_request(
        concept=_concept(),
        condition=condition,
        manifest=manifest,
        revision_instruction="move asset_0001 above asset_0002",
        base_elements=base,
    )
    prompt = request.prompt
    assert request.mode == "revision"
    assert "Revision mode" in prompt
    assert "ONLY the requested change" in prompt
    assert "move asset_0001 above asset_0002" in prompt
    assert json.dumps(base[0]["left"]) in prompt

    r0_prompt = build_mapper_request(
        concept=_concept(), condition=condition, manifest=manifest
    ).prompt
    assert "Revision mode" not in r0_prompt


def test_candidate_coverage_is_exact():
    validate_candidate_coverage(_candidate(), ["asset_0001", "asset_0002"])
    with pytest.raises(ValueError, match="coverage mismatch"):
        validate_candidate_coverage(_candidate(("asset_0001",)), ["asset_0001", "asset_0002"])
    duplicated = _candidate(("asset_0001", "asset_0001"))
    with pytest.raises(ValueError, match="more than once"):
        validate_candidate_coverage(duplicated, ["asset_0001", "asset_0002"])


def test_candidate_parser_accepts_fenced_json():
    candidate = _candidate()
    parsed = parse_candidate("```json\n" + candidate.model_dump_json() + "\n```")
    assert parsed.candidate_id == "candidate"
    assert [element.id for element in parsed.elements] == ["asset_0001", "asset_0002"]


def test_actions_enforce_vision_exact_model_and_error_aware_retry():
    repo = Path(__file__).resolve().parents[4]
    for filename in (
        "metagpt/ext/agentlayout/actions/compose_concept_a3.py",
        "metagpt/ext/agentlayout/actions/generate_layout_a3.py",
    ):
        source = (repo / filename).read_text()
        assert "support_image_input" in source
        assert "actual_model != self.expected_model" in source
        assert "Previous response validation error" in source
        assert "images=[background_image_b64]" in source
