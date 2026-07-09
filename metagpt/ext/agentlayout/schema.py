"""Pydantic schemas for the AgentLayout pipeline.

All inter-agent JSON contracts live here. The schemas are framework-agnostic so
the same models are reused by Actions, Roles, the pipeline driver, and any
standalone scripts. Sections follow the dataflow order documented in
``layout_agent/README.md``:

    1. Common enums
    2. Embedding store         (CLIP preprocessor output)
    3. Background analysis     (Background Analyzer output)
    4. Design Spec             (Analyst output, enriched in place by Asset Analyzer)
    5. Layout Tree             (Asset Planner output)
    6. Candidates              (Layout Generator output)
    7. Aesthetic judgement     (Aesthetic Judge output)
    8. Pipeline state          (driver-level bookkeeping)
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ============================================================
# 1. Common enums
# ============================================================


class SemanticType(str, Enum):
    """Semantic role of a design element. Set by Analyst."""

    TITLE = "title"
    SUBTITLE = "subtitle"
    BODY_TEXT = "body_text"
    CAPTION = "caption"
    LOGO = "logo"
    PRODUCT_IMAGE = "product_image"
    BACKGROUND_IMAGE = "background_image"
    DECORATIVE_IMAGE = "decorative_image"
    ICON = "icon"
    CTA = "cta"
    PRICETAG = "pricetag"
    OTHER = "other"


class VisualType(str, Enum):
    """Render type. Derived from raw asset type, not LLM-inferred."""

    IMAGE = "image"
    TEXT = "text"


class HardConstraintRule(str, Enum):
    """Geometric constraints the Quality Checker can validate programmatically."""

    POSITION_PREFERENCE = "position_preference"
    NO_OVERLAP = "no_overlap"
    Z_ORDER = "z_order"
    SIZE_PREFERENCE = "size_preference"


class SoftConstraintRule(str, Enum):
    """Aesthetic preferences that need LLM judgement, not programmatic checks."""

    VISUAL_HIERARCHY = "visual_hierarchy"
    WHITESPACE = "whitespace"
    BALANCE = "balance"
    COLOR_HARMONY = "color_harmony"
    READABILITY = "readability"


class JudgeDecision(str, Enum):
    """Aesthetic Judge final verdict."""

    ACCEPT = "accept"
    REJECT = "reject"


class FeedbackTarget(str, Enum):
    """Which agent should receive the next round of Aesthetic Judge feedback."""

    LAYOUT_GENERATOR = "layout_generator"
    ANALYST = "analyst"
    # "先想再畫" 重構 (2026-06-25): the LayoutGenerator was split into a
    # CompositionDirector (decides the spatial concept in natural language) and
    # a CoordinateMapper (= the renamed LayoutGenerator, turns one concept into
    # pixels). Judge feedback whose worst axis is design_layout/innovation is
    # routed here so the Director re-imagines the composition from scratch,
    # instead of asking the coordinate stage to nudge a fundamentally centred
    # template by +/-10%.
    COMPOSITION_DIRECTOR = "composition_director"


class EncoderType(str, Enum):
    """Source modality of an embedding vector."""

    VISION = "vision"
    TEXT = "text"


class SuggestionKind(str, Enum):
    """Verifiable categories for Aesthetic Judge structured suggestions.

    Added 2026-05-14 alongside the prompt upgrade. ``OTHER`` is a fallback that
    the prompt explicitly discourages; tests can assert most suggestions fall
    into one of the numeric categories so a downstream Generator can act on them.
    """

    RESIZE = "resize"              # change element width/height (numeric)
    MOVE = "move"                  # change element x/y/top/left (numeric)
    SPACING = "spacing"            # gap_to:elem_id distance (numeric)
    TYPOGRAPHY = "typography"      # font_size / font_weight (numeric)
    COLOR = "color"                # hex color string
    ZORDER = "zorder"              # explicit z_index (numeric int)
    PLACE_IN_BBOX = "place_in_bbox"  # Step 44: Judge saw the image and emits an
                                   # absolute pixel target_bbox=[L,T,R,B]. Generator
                                   # sets the element's (left,top,width,height) directly,
                                   # bypassing the +/-10% refinement drift cap.
    OTHER = "other"                # fallback; prompt discourages but allows


# ============================================================
# 2. Embedding store (CLIP preprocessor output)
# ============================================================


class EmbeddingRecord(BaseModel):
    """A single CLIP-encoded vector indexed by ``embedding_key``."""

    type: EncoderType
    element_id: str
    vector: List[float] = Field(..., description="CLIP embedding, default dim 768.")
    source: str = Field(..., description="Original asset path or text content.")
    encoder: str = Field(..., description="Encoder identifier, e.g. CLIP-ViT-L/14.")


class EmbeddingStore(BaseModel):
    """Keyed lookup table for all element embeddings of a single layout job."""

    records: Dict[str, EmbeddingRecord] = Field(default_factory=dict)

    def add(self, key: str, record: EmbeddingRecord) -> None:
        self.records[key] = record

    def get(self, key: str) -> Optional[EmbeddingRecord]:
        return self.records.get(key)


# ============================================================
# 3. Background analysis (Background Analyzer / U2Net output)
# ============================================================


class SafeZone(BaseModel):
    """A region of the background where elements can be safely placed."""

    region: str = Field(..., description="Human-readable region label, e.g. 'top-left'.")
    bbox: List[int] = Field(
        ...,
        description="[left, top, right, bottom] in canvas pixel space.",
        min_length=4,
        max_length=4,
    )
    confidence: float = Field(..., ge=0.0, le=1.0)


class UnderlayRegion(BaseModel):
    """A text-underlay panel that is ALREADY part of the background image.

    Step 76 (2026-07-02, SEGA-style preprocessing): non-text Crello layers are
    composited into the background at designer GT positions BEFORE the pipeline
    runs. Underlay panels therefore stop being placeable elements; instead the
    preprocessor records where each panel sits and what colour it is, so the
    Composition Director / Coordinate Mapper can place high-contrast text ON
    the panel. This is feed-forward by construction — we composited the panel
    ourselves, so no model ever needs to *detect* it (the judge-feedback route
    was measured dead three times: Steps 20b, 59, 65).
    """

    bbox: List[int] = Field(
        ...,
        description="[left, top, right, bottom] in canvas pixel space (same convention as SafeZone).",
        min_length=4,
        max_length=4,
    )
    dominant_color: str = Field(
        ...,
        description="6-digit hex of the panel's dominant colour, sampled from opaque pixels at composite time.",
    )
    recommended_text_color: str = Field(
        ...,
        description="Luminance-contrasting hex for text placed on this panel (dark on light, light on dark).",
    )
    panel_type: str = Field(
        default="solid",
        description=(
            "'solid' = an opaque plate; dominant_color is its fill. "
            "'frame' = a mostly-transparent outlined box (Step 79): the visual "
            "backdrop behind text is the BACKGROUND showing through, so "
            "dominant_color samples the composited background inside the bbox "
            "and recommended_text_color contrasts with THAT (the Step 78 "
            "renders put dark text on a dark forest because a white outline "
            "was mistaken for a white plate)."
        ),
    )


class BackgroundAnalysis(BaseModel):
    """Background Analyzer output, consumed by Layout Generator and Aesthetic Judge.

    F2 (Step 72, 2026-06-16) added three optional continuous-saliency fields
    (saliency_map / saliency_histogram / low_saliency_regions). They are
    populated by analyze_background() when a real background image is
    available, but stay None / empty for the solid-color stub path
    (pipeline.py:_default_white_background) so backward compatibility is
    preserved.

    Step 76 (2026-07-02) added ``underlay_regions``: filled only by the
    SEGA-style Crello preprocessor (baked underlay panels), empty everywhere
    else.
    """

    safe_zones: List[SafeZone] = Field(default_factory=list)
    dominant_palette: List[str] = Field(
        default_factory=list,
        description="Hex strings, e.g. '#F5E6D3'. Order = visual prominence.",
    )
    recommended_text_color: str = Field(
        default="#111111",
        description="Suggested foreground color based on background luminance.",
    )

    # F2 (Step 72) continuous-saliency fields. All optional / default empty so
    # consumers that ignore them keep working unchanged.
    saliency_map: Optional[List[List[float]]] = Field(
        default=None,
        description=(
            "Downsampled saliency map (typically 32x32) with values in [0, 1]. "
            "Higher value = busier region. Layout Generator may inspect this "
            "to avoid placing text on hero/subject regions; QC rule "
            "TEXT_ON_HIGH_SALIENCY uses it to flag bad placements."
        ),
    )
    saliency_histogram: Optional[List[float]] = Field(
        default=None,
        description=(
            "Compact 3x3 grid (length 9, row-major: TL, TM, TR, ML, MM, MR, "
            "BL, BM, BR) of mean saliency per cell. Cheap prompt summary."
        ),
    )
    low_saliency_regions: List[SafeZone] = Field(
        default_factory=list,
        description=(
            "Top-K continuous-rank low-saliency rectangles, ranked by "
            "(1 - mean_saliency). Distinct from safe_zones which are the "
            "binary subject-avoidance bands; these are pixel-precise calm "
            "areas the Generator should prefer for text."
        ),
    )

    # Step 76 SEGA-style preprocessing: baked underlay panels. Empty unless the
    # caller ran the Crello preprocessor and merged its output in.
    underlay_regions: List[UnderlayRegion] = Field(
        default_factory=list,
        description=(
            "Underlay panels already composited into the background at designer "
            "GT positions. Text SHOULD land on these panels using the "
            "recommended contrasting colour."
        ),
    )


# ============================================================
# 4. Design Spec (Analyst output, enriched in place by Asset Analyzer)
# ============================================================


class Canvas(BaseModel):
    """Output canvas geometry plus background reference.

    Background precedence (consumed by the renderer):
      1. ``background_asset_ref`` (image file, if present and loadable)
      2. ``background_color`` (solid hex fill, when no image is supplied)
      3. Renderer default (pure white) — kept only for legacy specs that
         predate ``background_color`` (added 2026-05-14, step 7).
    """

    width: int = Field(..., gt=0)
    height: int = Field(..., gt=0)
    background_asset_ref: Optional[str] = Field(
        default=None, description="Path to background image, if any."
    )
    background_embedding_key: Optional[str] = Field(
        default=None,
        description="Filled by CLIP preprocessor; Analyst always outputs null.",
    )
    background_color: Optional[str] = Field(
        default=None,
        description=(
            "Solid 6-digit hex fill (e.g. '#F5E6D3') used by the renderer when "
            "no background_asset_ref is supplied. Analyst infers a pleasant "
            "palette-matching color; avoid pure white unless brief demands it."
        ),
    )

    @field_validator("background_color")
    @classmethod
    def _validate_hex_color(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", v):
            raise ValueError(
                f"background_color must be a 6-digit hex string like '#F5E6D3', got {v!r}."
            )
        return v.upper()


class Element(BaseModel):
    """A single design element.

    Analyst (LLM) populates everything except ``importance``,
    ``semantic_relevance`` and ``embedding_key``. Asset Analyzer (Python) fills
    ``importance`` and ``semantic_relevance`` in place. The CLIP preprocessor
    fills ``embedding_key``.
    """

    id: str
    semantic_type: SemanticType
    visual_type: VisualType
    content: Optional[str] = Field(
        default=None, description="Text content for visual_type=text."
    )
    asset_ref: Optional[str] = Field(
        default=None, description="File path for visual_type=image."
    )
    embedding_key: Optional[str] = Field(
        default=None,
        description="CLIP store key. Analyst outputs null; preprocessor fills it.",
    )
    inferred: bool = Field(
        default=False,
        description="True if Analyst inferred semantic_type instead of taking it verbatim from the user.",
    )

    importance: Optional[int] = Field(
        default=None,
        ge=1,
        le=5,
        description="Filled by Asset Analyzer via semantic_type lookup table.",
    )
    semantic_relevance: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="CLIP cosine similarity vs. style_keywords. Filled by Asset Analyzer.",
    )


class HardConstraint(BaseModel):
    """Programmatically-verifiable geometric constraint."""

    rule: HardConstraintRule
    targets: List[str] = Field(..., min_length=1, description="Element ids the rule applies to.")
    params: Dict[str, Any] = Field(
        default_factory=dict,
        description="Semantic hints only, e.g. {'hint': 'top_right'} — never pixel coordinates.",
    )


class SoftConstraint(BaseModel):
    """Aesthetic preference that requires LLM judgement."""

    rule: SoftConstraintRule
    weight: float = Field(default=0.5, ge=0.0, le=1.0)
    params: Dict[str, Any] = Field(default_factory=dict)


class CompositionDirective(BaseModel):
    """Sketch-level composition picked by the Composition Director (Step 62).

    Vocabulary matches the Step 61 GT calibration: 3x3 grid cells (TL..BR),
    photo size buckets (small/medium/large/bleed) and photo-text relations.
    ``photo_*`` fields are None for photo-less layouts.
    """

    template_id: str
    relation: Optional[str] = Field(
        default=None, description="text-on-photo | stacked | side-by-side | centered-mix"
    )
    photo_cell: Optional[str] = Field(
        default=None, description="3x3 grid cell hosting the focal photo center, e.g. 'MC'."
    )
    photo_size: Optional[str] = Field(
        default=None, description="Photo area-ratio bucket: small | medium | large | bleed."
    )
    text_cell: Optional[str] = Field(
        default=None, description="3x3 grid cell hosting the area-weighted text-mass center."
    )
    rationale: Optional[str] = Field(
        default=None, description="One-sentence reason the Director picked this template."
    )


class DesignSpec(BaseModel):
    """Structured contract produced by Analyst and enriched by Asset Analyzer."""

    canvas: Canvas
    elements: List[Element]
    composition: Optional[CompositionDirective] = Field(
        default=None,
        description="Sketch-level composition directive (Composition Director, Step 62).",
    )
    hard_constraints: List[HardConstraint] = Field(default_factory=list)
    soft_constraints: List[SoftConstraint] = Field(default_factory=list)
    style_keywords: List[str] = Field(default_factory=list)
    language: Optional[str] = Field(default=None, description="BCP-47 tag, e.g. zh-TW.")
    inferred_fields: Dict[str, bool] = Field(
        default_factory=dict,
        description=(
            "Dotted-path map. True = field was inferred by Analyst (and is therefore"
            " safe to override on Aesthetic-Judge feedback). Absent or False = the"
            " user stated this verbatim and Analyst must not change it."
        ),
    )

    def foreground_elements(self) -> List[Element]:
        """All elements except the background image — used by Asset Planner."""
        return [e for e in self.elements if e.semantic_type != SemanticType.BACKGROUND_IMAGE]

    def get_element(self, element_id: str) -> Optional[Element]:
        for element in self.elements:
            if element.id == element_id:
                return element
        return None

    def assert_enriched(self) -> None:
        """Guard called at Layout Generator entry: Asset Analyzer must have run."""
        missing = [
            e.id
            for e in self.elements
            if e.importance is None or e.semantic_relevance is None
        ]
        if missing:
            raise ValueError(
                "DesignSpec is not enriched. Asset Analyzer must fill importance and "
                f"semantic_relevance before Layout Generator. Missing element ids: {missing}"
            )


# ============================================================
# 5. Layout Tree (Asset Planner output)
# ============================================================


class LayoutTreeNode(BaseModel):
    """A node in the Layout Tree.

    Per the Asset Planner spec: only ``id`` and ``children`` are allowed.
    Every node id is a real DesignSpec element id; the only synthetic node is
    the wrapper root (``id == 'root'``).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    children: List["LayoutTreeNode"] = Field(default_factory=list)

    def iter_ids(self) -> List[str]:
        """Pre-order traversal of every id in the subtree (root included)."""
        out = [self.id]
        for child in self.children:
            out.extend(child.iter_ids())
        return out


class LayoutTree(BaseModel):
    """Wrapper around the synthetic root node."""

    root: LayoutTreeNode = Field(..., description="Always has id='root'.")

    @model_validator(mode="after")
    def _check_root_id(self) -> "LayoutTree":
        if self.root.id != "root":
            raise ValueError(f"LayoutTree.root.id must be 'root', got '{self.root.id}'.")
        return self

    def all_element_ids(self) -> List[str]:
        """Every element id in the tree, excluding the synthetic root."""
        return [n for n in self.root.iter_ids() if n != "root"]


LayoutTreeNode.model_rebuild()


# ============================================================
# 5.5 Composition concepts (CompositionDirector output, 2026-06-25)
# ============================================================
#
# "先想再畫" 重構：構思階段與座標階段拆開。CompositionDirector 只用自然語言
# 描述 "東西大致放哪、視覺如何流動"，完全不碰像素。每個概念之後交給
# CoordinateMapper (= 改名後的 LayoutGenerator) 獨立翻成一組座標。
# 這裡刻意只放 "意圖" 欄位，不放任何 bbox / 數字，避免又把構思塞回座標階段。


class CompositionConcept(BaseModel):
    """One spatial composition idea, described purely in natural language.

    No pixels here on purpose: the whole point of the "先想再畫" split is that
    the Director reasons about *where things feel right* without the 20+ hard
    constraints that pushed the old monolithic Generator into a survival-mode
    "centre everything" template. The CoordinateMapper turns each concept into
    exact pixels in a separate, low-temperature call.
    """

    name: str = Field(..., description="2-4 word concept name, e.g. 'Left bleed'.")
    focal_element: str = Field(
        ..., description="Element id that anchors the design (usually the hero image)."
    )
    focal_placement: str = Field(
        ..., description="Where the focal element goes, in natural language."
    )
    text_placement: str = Field(
        ..., description="Where the text group goes, in natural language."
    )
    visual_flow: str = Field(
        ..., description="How the eye moves across the design (e.g. Z-pattern)."
    )
    whitespace: str = Field(..., description="Whitespace / breathing-room strategy.")
    typography_mood: str = Field(..., description="Font and colour direction.")
    text_photo_relation: str = Field(
        default="beside",
        description="One of: beside | overlay | above | below | mixed.",
    )
    text_assignments: Dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Step 77 feed-forward assignment: text element id -> where it goes, "
            "as 'panel N' (a numbered baked-underlay panel) or a 3x3 region "
            "word like 'bottom-left'. The CoordinateMapper resolves 'panel N' "
            "to that panel's exact bbox. Empty dict = legacy free-form concept."
        ),
    )


class ConceptBatch(BaseModel):
    """CompositionDirector output: 1-5 spatially diverse composition concepts.

    Each concept becomes exactly one CoordinateMapper candidate. We keep the
    list small (default target 3) because three *genuinely* different layouts
    beat fifteen variations of the same centred template — the empirical
    finding that motivated this refactor.
    """

    concepts: List[CompositionConcept] = Field(..., min_length=1, max_length=5)


# ============================================================
# 6. Candidates (Layout Generator output)
# ============================================================


class LayoutElement(BaseModel):
    """An element with concrete pixel coordinates and (for text) visual style.

    Crello-aligned: top-left origin, ``angle`` in degrees clockwise, ``z_index``
    increases toward the viewer.
    """

    id: str
    left: int
    top: int
    width: int = Field(..., gt=0)
    height: int = Field(..., gt=0)
    angle: float = Field(default=0.0, description="Rotation in degrees, 0 = upright.")
    z_index: int = Field(..., ge=0)

    font_family: Optional[str] = None
    font_size: Optional[int] = Field(default=None, gt=0)
    font_weight: Optional[str] = Field(
        default=None,
        description=(
            "e.g. 'regular' / 'bold' / a numeric weight as str. LLMs sometimes "
            "emit numeric int (e.g. 700); the validator coerces int->str."
        ),
    )

    @field_validator("font_weight", mode="before")
    @classmethod
    def _coerce_font_weight_int_to_str(cls, v):
        if isinstance(v, int) and not isinstance(v, bool):
            return str(v)
        return v
    color: Optional[str] = Field(default=None, description="Hex string, e.g. '#1B3A6B'.")
    text_align: Optional[str] = Field(
        default=None, description="'left' / 'center' / 'right' / 'justify'."
    )


class Candidate(BaseModel):
    """One full layout proposal — every element id from the DesignSpec must appear."""

    candidate_id: str
    elements: List[LayoutElement]


class CandidatesBatch(BaseModel):
    """Layout Generator output. Target size = K_VALID after Quality Checker filtering."""

    candidates: List[Candidate] = Field(default_factory=list)


# ============================================================
# 7. Aesthetic judgement (Aesthetic Judge output)
# ============================================================


class JudgeScores(BaseModel):
    """COLE 5-axis scoring (Step 30 migration, 2026-06-09).

    Aligns the in-pipeline Aesthetic Judge with the offline Phase B COLE eval
    (layout_agent/output/step21_phaseb_eval.py). Each axis 1-10, total 5-50.
    `content_relevance` absorbs the old `requirement_alignment` semantics so
    brief fidelity is still optimised (see judge_aesthetic.py rubric B).
    """

    design_layout: int = Field(..., ge=1, le=10)
    content_relevance: int = Field(..., ge=1, le=10)
    typography_color: int = Field(..., ge=1, le=10)
    graphics_images: int = Field(..., ge=1, le=10)
    innovation_originality: int = Field(..., ge=1, le=10)


class Evaluation(BaseModel):
    """Per-candidate evaluation entry produced by Aesthetic Judge."""

    candidate_id: str
    total: int = Field(..., ge=5, le=50)
    scores: JudgeScores
    strengths: str
    weaknesses: str

    @model_validator(mode="after")
    def _total_matches_scores(self) -> "Evaluation":
        s = self.scores
        expected = (
            s.design_layout
            + s.content_relevance
            + s.typography_color
            + s.graphics_images
            + s.innovation_originality
        )
        if self.total != expected:
            raise ValueError(
                f"Evaluation.total ({self.total}) does not equal sum of scores ({expected})."
            )
        return self


_NUMERIC_KINDS = frozenset(
    {
        SuggestionKind.RESIZE,
        SuggestionKind.MOVE,
        SuggestionKind.SPACING,
        SuggestionKind.ZORDER,
        # TYPOGRAPHY is handled separately (Step 45): font_size requires numeric
        # but font_family / text_align / named font_weight are strings.
    }
)

_TYPOGRAPHY_METRICS = frozenset({"font_size", "font_weight", "font_family", "text_align"})
_TEXT_ALIGN_VALUES = frozenset({"left", "center", "right", "justify"})


class Suggestion(BaseModel):
    """A single verifiable improvement suggestion emitted by Aesthetic Judge.

    Added 2026-05-14 to make ``AestheticFeedback`` machine-actionable. Each
    suggestion is a tuple ``(target_id, metric, op, value)`` plus a ``kind``
    enum and an optional ``rationale``. ``op`` is a free string so the LLM can
    emit ``>=``, ``<=``, ``==``, ``set_to``, ``increase_by``, etc., without
    triggering Pydantic ValidationError. The ``model_validator`` below enforces
    numeric ``value`` for numeric ``kind``s so we still get a hard contract.
    """

    kind: SuggestionKind
    target_id: str = Field(..., description="Element id this suggestion refers to.")
    metric: str = Field(
        ...,
        description=(
            "What is being constrained, e.g. 'width', 'height', 'x', 'y', "
            "'font_size', 'gap_to:elem_id', 'z_index', 'color'."
        ),
    )
    op: str = Field(
        ...,
        description="Comparator or action, e.g. '>=', '<=', '==', 'set_to', 'increase_by'.",
    )
    value: Union[int, float, str] = Field(
        ..., description="Numeric target, hex color, or other typed value matching `kind`."
    )
    target_bbox: Optional[List[int]] = Field(
        default=None,
        description=(
            "Step 44: absolute pixel bbox [L, T, R, B] in canvas coords. "
            "REQUIRED iff kind=place_in_bbox; ignored otherwise. Generator sets "
            "the target element to (left=L, top=T, width=R-L, height=B-T)."
        ),
        min_length=4,
        max_length=4,
    )
    rationale: Optional[str] = Field(
        default=None,
        description="Short explanation of why this change helps; optional.",
    )

    @model_validator(mode="after")
    def _value_matches_kind(self) -> "Suggestion":
        if self.kind in _NUMERIC_KINDS and not isinstance(self.value, (int, float)):
            raise ValueError(
                f"Suggestion(kind={self.kind.value}, metric={self.metric}) requires "
                f"a numeric value; got {type(self.value).__name__}={self.value!r}."
            )
        if self.kind == SuggestionKind.COLOR:
            ok = (
                isinstance(self.value, str)
                and self.value.startswith("#")
                and len(self.value) in (4, 7, 9)
                and all(c in "0123456789abcdefABCDEF" for c in self.value[1:])
            )
            if not ok:
                raise ValueError(
                    f"Suggestion(kind=color, target_id={self.target_id}) requires a "
                    f"hex string like '#RGB' / '#RRGGBB' / '#RRGGBBAA'; got {self.value!r}."
                )
        if self.kind == SuggestionKind.TYPOGRAPHY:
            if self.metric not in _TYPOGRAPHY_METRICS:
                raise ValueError(
                    f"Suggestion(kind=typography, target_id={self.target_id}) "
                    f"metric must be one of {sorted(_TYPOGRAPHY_METRICS)}; "
                    f"got {self.metric!r}."
                )
            if self.metric == "font_size":
                if not isinstance(self.value, (int, float)):
                    raise ValueError(
                        f"Suggestion(kind=typography, metric=font_size, "
                        f"target_id={self.target_id}) requires numeric value; "
                        f"got {type(self.value).__name__}={self.value!r}."
                    )
            elif self.metric == "font_weight":
                if not isinstance(self.value, (int, float, str)):
                    raise ValueError(
                        f"Suggestion(kind=typography, metric=font_weight, "
                        f"target_id={self.target_id}) requires numeric or named "
                        f"weight (e.g. 700 or 'bold'); got {self.value!r}."
                    )
            elif self.metric == "font_family":
                if not isinstance(self.value, str) or not self.value.strip():
                    raise ValueError(
                        f"Suggestion(kind=typography, metric=font_family, "
                        f"target_id={self.target_id}) requires a non-empty "
                        f"string (e.g. 'serif', 'Inter'); got {self.value!r}."
                    )
            elif self.metric == "text_align":
                if not isinstance(self.value, str) or self.value not in _TEXT_ALIGN_VALUES:
                    raise ValueError(
                        f"Suggestion(kind=typography, metric=text_align, "
                        f"target_id={self.target_id}) value must be one of "
                        f"{sorted(_TEXT_ALIGN_VALUES)}; got {self.value!r}."
                    )
        if self.kind == SuggestionKind.PLACE_IN_BBOX:
            if self.target_bbox is None:
                raise ValueError(
                    f"Suggestion(kind=place_in_bbox, target_id={self.target_id}) "
                    f"requires target_bbox=[L, T, R, B]; got None."
                )
            l, t, r, b = self.target_bbox
            if not (isinstance(l, int) and isinstance(t, int)
                    and isinstance(r, int) and isinstance(b, int)):
                raise ValueError(
                    f"Suggestion(kind=place_in_bbox, target_id={self.target_id}) "
                    f"target_bbox must be 4 ints; got {self.target_bbox!r}."
                )
            if r <= l or b <= t:
                raise ValueError(
                    f"Suggestion(kind=place_in_bbox, target_id={self.target_id}) "
                    f"target_bbox=[L,T,R,B] requires R>L and B>T; got {self.target_bbox!r}."
                )
            if l < 0 or t < 0:
                raise ValueError(
                    f"Suggestion(kind=place_in_bbox, target_id={self.target_id}) "
                    f"target_bbox must have L>=0 and T>=0; got {self.target_bbox!r}."
                )
        return self


class VisualObservationKind(str, Enum):
    """Closed catalogue of render-level defects the Judge may report (Step 77).

    Every kind pairs with a machine-verifiable geometric predicate in
    ``tools/feedback_verifier.py`` — after a retry we can compute exactly
    which observations were acted on (compliance rate). This is the metric
    Step 59 lacked: it localises a loop failure to perception vs execution.
    """

    TEXT_OFF_PANEL = "text_off_panel"      # text should sit ON a given panel bbox
    TEXT_ILLEGIBLE = "text_illegible"      # low contrast / busy backdrop
    TEXT_TOO_SMALL = "text_too_small"      # below the GT area prior
    TEXT_TOO_LARGE = "text_too_large"
    TEXT_OVERLAP = "text_overlap"          # two elements collide
    TEXT_TILTED = "text_tilted"            # unintended rotation
    # Step 85 rubric items (both verify as "move INTO target_bbox"): the judge
    # LOOKS at the render and estimates where the element should go -- the
    # per-image call a population prior cannot make.
    TITLE_MISPLACED = "title_misplaced"    # dominant text in the wrong spot
    LOCKUP_BROKEN = "lockup_broken"        # subtitle drifted from the title


class VisualObservation(BaseModel):
    """One discrete, verifiable defect observed on the RENDERED candidate.

    The Judge is the only component that sees the finished render; Step 77
    lets it report what it sees — but only in this closed, checkable
    vocabulary (discrete choices, not free-form pixel prose, which Step 59
    proved the generator ignores).
    """

    kind: VisualObservationKind
    target_id: str = Field(..., description="Element id the observation is about.")
    second_id: Optional[str] = Field(
        default=None, description="Second element id (only for text_overlap)."
    )
    target_bbox: Optional[List[int]] = Field(
        default=None,
        description="[left, top, right, bottom] the target should move INTO "
        "(panel bbox for text_off_panel, calm region for text_illegible).",
        min_length=4,
        max_length=4,
    )
    target_color: Optional[str] = Field(
        default=None, description="Concrete hex the text should switch to (text_illegible)."
    )
    target_area_px: Optional[int] = Field(
        default=None, description="Area target in px^2 (text_too_small / text_too_large)."
    )
    note: str = Field(default="", description="One-line human-readable rationale.")


class AestheticFeedback(BaseModel):
    """Common-issue feedback emitted when no candidate hits the threshold.

    ``structured_suggestions`` was added 2026-05-14 alongside the prompt
    upgrade. Existing JSON without that key still parses (default empty list).
    The free-text ``suggestions: List[str]`` is preserved for prompts that
    cannot easily produce structured output (e.g. older Crello driver runs).
    """

    common_issues: str
    suggestions: List[str] = Field(default_factory=list)
    structured_suggestions: List[Suggestion] = Field(
        default_factory=list,
        description=(
            "Machine-readable, verifiable suggestions. Each entry is a typed "
            "Suggestion (kind / target_id / metric / op / value). The Aesthetic "
            "Judge prompt requires at least one entry on reject; the schema "
            "itself accepts an empty list so legacy JSON still parses."
        ),
    )
    visual_observations: List[VisualObservation] = Field(
        default_factory=list,
        description=(
            "Step 77 closed-catalogue render-level defects. Populated only "
            "when the visual-loop feature flag is on; default empty keeps "
            "legacy JSON parsing."
        ),
    )
    keep_constraints: List[VisualObservation] = Field(
        default_factory=list,
        description=(
            "Step 89: RETIRED ledger issues -- targets already satisfied that "
            "the next mapper call must NOT undo (the 88b trace showed accept-"
            "round polish repeatedly un-fixing the title). Populated by the "
            "pipeline from the retired ledger; default empty."
        ),
    )


class AestheticJudgement(BaseModel):
    """Aesthetic Judge full output, consumed by the pipeline driver.

    Refinement Loop (2026-05-20): feedback is now REQUIRED on both accept and
    reject paths. On accept the suggestions are small-step polish ideas
    consumed by the mandatory one-more refinement round. The
    ``best_candidate_layout`` carries the winning candidate's bbox dict so the
    next refinement round can anchor its edits without re-deriving from id.
    """

    decision: JudgeDecision
    best_candidate_id: str
    evaluations: List[Evaluation]
    feedback: AestheticFeedback = Field(
        ...,
        description=(
            "Required on BOTH accept and reject. On reject it lists concrete "
            "fixes for failing dimensions; on accept it lists small-step polish "
            "suggestions for the next mandatory refinement round."
        ),
    )
    best_candidate_layout: Optional[Dict[str, Tuple[float, float, float, float]]] = Field(
        default=None,
        description=(
            "bbox dict {element_id: (left, top, width, height)} of the chosen "
            "best candidate. Populated by JudgeAesthetic.run() after parsing the "
            "verdict by looking up the input Candidates. None for legacy JSON "
            "that pre-dates the Refinement Loop architecture."
        ),
    )


# ============================================================
# 8. Pipeline state (driver-level bookkeeping)
# ============================================================


ACCEPT_THRESHOLD: int = 35
"""Aesthetic Judge total score >= this value triggers ``decision='accept'``.

Calibration history:
    2026-05-07: 80 (4 axes * 25 max = 100).
    2026-05-14: 75 (lowered after corner-case verification measured Crello
                 designer GT at 68/100 under the old rubric).
    2026-06-09 (Step 30): 35 on the new COLE 5-axis 1-10 scale (total 5-50).
                 35 = 5 * 7 i.e. mean axis 7/10, the COLE rubric's "mediocre
                 design" anchor (judge_aesthetic.py PROMPT_TEMPLATE rule 2).
                 This preserves the prior 75/100 = 0.75 acceptance ratio while
                 mapping cleanly onto COLE's documented quality anchors. A
                 full N-sample calibration on the Crello dataset is the next
                 step after the Step 30 migration smoke run."""

K_VALID: int = 5
"""Target number of Quality-Checker-passing candidates per generation round."""

GENERATOR_FEEDBACK_ROUNDS: int = 2
"""Reject 1..N -> Layout Generator. Reject N+1 onwards -> Analyst."""


class IterationState(BaseModel):
    """Bookkeeping carried by the pipeline driver across rounds.

    Routing rule (per the design doc):
      * After 1st..N-th Aesthetic Judge reject -> Layout Generator
      * After (N+1)-th reject and beyond       -> Analyst
      where N = ``GENERATOR_FEEDBACK_ROUNDS``.
    """

    iteration: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of completed Aesthetic Judge rounds (any verdict). "
            "Refinement Loop (2026-05-20): incremented on BOTH accept and reject "
            "because every verdict triggers a mandatory next refinement pass."
        ),
    )
    consecutive_accepts: int = Field(
        default=0,
        ge=0,
        description=(
            "Refinement Loop termination counter. Incremented on each consecutive "
            "ACCEPT verdict, reset to 0 on REJECT. Pipeline stops when this hits 2 "
            "(coarse accept + post-refinement accept => the refinement actually held)."
        ),
    )
    reject_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Cumulative REJECT verdicts (independent of accept-driven refinement "
            "iterations). Drives next_target() routing: first GENERATOR_FEEDBACK_ROUNDS "
            "rejects go to LAYOUT_GENERATOR, subsequent rejects escalate to ANALYST. "
            "Refinement passes triggered by ACCEPT do NOT consume this budget."
        ),
    )
    feedback_target: Optional[FeedbackTarget] = None
    last_feedback: Optional[AestheticFeedback] = None

    best_so_far_total: Optional[int] = Field(
        default=None,
        ge=5,
        le=50,
        description=(
            "Step 31 (2026-06-09): refinement-loop best-so-far guard. The "
            "highest total score observed across ALL judgement rounds in this "
            "pipeline run. Updated only when a new round's best STRICTLY "
            "exceeds this value; otherwise the loop keeps using best-so-far as "
            "the Generator's anchor, preventing the score from regressing on "
            "noisy re-judges (root cause #1 of the loop's negative result in "
            "Step 20b / Step 30 N=5)."
        ),
    )
    best_so_far_layout: Optional[Dict[str, Tuple[float, float, float, float]]] = Field(
        default=None,
        description=(
            "Bbox dict of the best-so-far candidate. Replaces "
            "judgement.best_candidate_layout when routing refinement feedback "
            "to LayoutGenerator. None until the first ACCEPT/REJECT round."
        ),
    )
    best_so_far_subscores: Optional[Dict[str, int]] = Field(
        default=None,
        description=(
            "Per-axis sub-scores (5 COLE axes 1-10) of the best-so-far "
            "candidate. Passed to the Generator alongside best_so_far_layout."
        ),
    )

    def next_target(self) -> FeedbackTarget:
        """Decide which agent receives feedback after the most recent verdict.

        Refinement Loop (2026-05-20): accept verdicts route to LAYOUT_GENERATOR
        unconditionally (mandatory one-more refinement). Reject verdicts route to
        LAYOUT_GENERATOR for the first GENERATOR_FEEDBACK_ROUNDS rejects, then
        ANALYST. Caller must increment ``reject_count`` on each REJECT before
        calling.
        """
        if self.reject_count <= GENERATOR_FEEDBACK_ROUNDS:
            return FeedbackTarget.LAYOUT_GENERATOR
        return FeedbackTarget.ANALYST
