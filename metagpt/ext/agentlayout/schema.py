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
from typing import Any, Dict, List, Optional, Union

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


class BackgroundAnalysis(BaseModel):
    """Background Analyzer output, consumed by Layout Generator and Aesthetic Judge."""

    safe_zones: List[SafeZone] = Field(default_factory=list)
    dominant_palette: List[str] = Field(
        default_factory=list,
        description="Hex strings, e.g. '#F5E6D3'. Order = visual prominence.",
    )
    recommended_text_color: str = Field(
        default="#111111",
        description="Suggested foreground color based on background luminance.",
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


class DesignSpec(BaseModel):
    """Structured contract produced by Analyst and enriched by Asset Analyzer."""

    canvas: Canvas
    elements: List[Element]
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
        default=None, description="e.g. 'regular' / 'bold' / a numeric weight as str."
    )
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
    """Four scoring dimensions, each 0-25. Sum capped at 100 by validation on Evaluation."""

    requirement_alignment: int = Field(..., ge=0, le=25)
    info_hierarchy: int = Field(..., ge=0, le=25)
    layout_balance: int = Field(..., ge=0, le=25)
    visual_coherence: int = Field(..., ge=0, le=25)


class Evaluation(BaseModel):
    """Per-candidate evaluation entry produced by Aesthetic Judge."""

    candidate_id: str
    total: int = Field(..., ge=0, le=100)
    scores: JudgeScores
    strengths: str
    weaknesses: str

    @model_validator(mode="after")
    def _total_matches_scores(self) -> "Evaluation":
        s = self.scores
        expected = (
            s.requirement_alignment + s.info_hierarchy + s.layout_balance + s.visual_coherence
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
        SuggestionKind.TYPOGRAPHY,
        SuggestionKind.ZORDER,
    }
)


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
        return self


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


class AestheticJudgement(BaseModel):
    """Aesthetic Judge full output, consumed by the pipeline driver."""

    decision: JudgeDecision
    best_candidate_id: str
    evaluations: List[Evaluation]
    feedback: Optional[AestheticFeedback] = Field(
        default=None,
        description="Required when decision=reject; must be null when decision=accept.",
    )

    @model_validator(mode="after")
    def _feedback_matches_decision(self) -> "AestheticJudgement":
        if self.decision == JudgeDecision.ACCEPT and self.feedback is not None:
            raise ValueError("feedback must be null when decision='accept'.")
        if self.decision == JudgeDecision.REJECT and self.feedback is None:
            raise ValueError("feedback must be provided when decision='reject'.")
        return self


# ============================================================
# 8. Pipeline state (driver-level bookkeeping)
# ============================================================


ACCEPT_THRESHOLD: int = 75
"""Aesthetic Judge total score >= this value triggers ``decision='accept'``.

Calibration history (2026-05-14):
    Previously hardcoded at 80 (2026-05-07). The 2026-05-13 Judge corner-case
    verification (verify_judge_corner.py Case 2) measured the Crello designer
    ground-truth layout — taken as the realistic upper aesthetic bound — at
    only 68 / 100 under the existing prompt rubric. With ACCEPT=80 the loop
    can never accept anything resembling human-quality output and the live
    feedback loop becomes degenerate (75->72->72 observed 2026-05-10/14).
    Lowering to 75 keeps the threshold strictly above the GT baseline (68),
    leaving real headroom to discriminate, while no longer demanding scores
    no system in this fixture has produced. A full N-sample calibration on
    the Crello dataset is the next step (see layout_agent/README.md
    "ACCEPT_THRESHOLD calibration" section)."""

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
        description="Number of Aesthetic Judge rejects accumulated so far.",
    )
    feedback_target: Optional[FeedbackTarget] = None
    last_feedback: Optional[AestheticFeedback] = None

    def next_target(self) -> FeedbackTarget:
        """Decide which agent receives feedback after the most recent reject.

        Caller must increment ``iteration`` before calling.
        """
        if self.iteration <= GENERATOR_FEEDBACK_ROUNDS:
            return FeedbackTarget.LAYOUT_GENERATOR
        return FeedbackTarget.ANALYST
