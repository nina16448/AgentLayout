"""Asset Analyzer — non-LLM enrichment tool for DesignSpec.

This tool runs between Analyst and Asset Planner. Analyst leaves every
``Element.importance`` and ``Element.semantic_relevance`` as ``None``; this
module fills them in place so that downstream agents can rely on them and
``DesignSpec.assert_enriched()`` passes at the Layout Generator boundary.

Two enrichment fields:

* ``importance`` (1–5) — looked up directly from ``semantic_type`` via the
  static :data:`IMPORTANCE_TABLE`. The table reflects a typical poster /
  marketing visual hierarchy (title / CTA / product image are top-tier;
  decorative and caption are bottom-tier; background is lowest).

* ``semantic_relevance`` (0.0–1.0) — eventually the CLIP cosine similarity
  between the element's embedding and the concatenated ``style_keywords``
  text embedding. CLIP Embedder is not implemented yet, so this milestone
  ships a constant stub (:data:`DEFAULT_SEMANTIC_RELEVANCE` = 0.5). When the
  CLIP layer lands, only :meth:`AssetAnalyzer._compute_semantic_relevance`
  needs to change.

The tool is idempotent: by default, fields that are already set are left
untouched. Pass ``override=True`` to force re-fill (useful when the upstream
agent regenerates the spec mid-iteration).
"""
from __future__ import annotations

from typing import Dict, Optional

from metagpt.ext.agentlayout.schema import DesignSpec, Element, SemanticType


IMPORTANCE_TABLE: Dict[SemanticType, int] = {
    SemanticType.TITLE: 5,
    SemanticType.CTA: 5,
    SemanticType.PRODUCT_IMAGE: 5,
    SemanticType.LOGO: 4,
    SemanticType.SUBTITLE: 4,
    SemanticType.PRICETAG: 4,
    SemanticType.BODY_TEXT: 3,
    SemanticType.ICON: 3,
    SemanticType.OTHER: 3,
    SemanticType.DECORATIVE_IMAGE: 2,
    SemanticType.CAPTION: 2,
    SemanticType.BACKGROUND_IMAGE: 1,
}
"""Static visual-hierarchy mapping. Must cover every ``SemanticType`` value."""


DEFAULT_SEMANTIC_RELEVANCE: float = 0.5
"""Neutral stub used until CLIP Embedder is wired in."""


class AssetAnalyzer:
    """Enrich every ``Element`` of a ``DesignSpec`` in place.

    Usage::

        analyzer = AssetAnalyzer()
        analyzer.run(spec)              # idempotent: skips already-filled
        spec.assert_enriched()          # passes

    Re-run after upstream regeneration::

        analyzer.run(spec, override=True)
    """

    def __init__(
        self,
        importance_table: Optional[Dict[SemanticType, int]] = None,
        default_semantic_relevance: Optional[float] = None,
    ) -> None:
        self.importance_table = (
            dict(importance_table) if importance_table is not None else dict(IMPORTANCE_TABLE)
        )
        self._validate_table_coverage()
        self.default_semantic_relevance = (
            default_semantic_relevance
            if default_semantic_relevance is not None
            else DEFAULT_SEMANTIC_RELEVANCE
        )
        if not 0.0 <= self.default_semantic_relevance <= 1.0:
            raise ValueError(
                "default_semantic_relevance must lie in [0, 1], "
                f"got {self.default_semantic_relevance!r}."
            )

    def _validate_table_coverage(self) -> None:
        """Fail fast at construction time if the table is missing any enum value."""
        missing = [st for st in SemanticType if st not in self.importance_table]
        if missing:
            raise ValueError(
                "importance_table is incomplete. Missing entries for: "
                f"{[m.value for m in missing]}"
            )
        for st, score in self.importance_table.items():
            if not (1 <= score <= 5):
                raise ValueError(
                    f"importance_table[{st.value}] = {score} out of range [1, 5]."
                )

    def run(self, spec: DesignSpec, *, override: bool = False) -> DesignSpec:
        """Fill ``importance`` and ``semantic_relevance`` on every element.

        The same ``spec`` is returned for fluent chaining; mutation happens in
        place. Elements whose fields are already populated are left untouched
        unless ``override=True``.
        """
        for element in spec.elements:
            if element.importance is None or override:
                element.importance = self._compute_importance(element)
            if element.semantic_relevance is None or override:
                element.semantic_relevance = self._compute_semantic_relevance(element, spec)
        return spec

    def _compute_importance(self, element: Element) -> int:
        """Lookup-table importance score. Pydantic guarantees a valid enum."""
        return self.importance_table[element.semantic_type]

    def _compute_semantic_relevance(self, element: Element, spec: DesignSpec) -> float:
        """Stub. To be replaced by CLIP cosine when the embedder ships.

        Future implementation will:
          1. Resolve ``element.embedding_key`` against the embedding store.
          2. Encode ``" ".join(spec.style_keywords)`` with the CLIP text encoder.
          3. Return the cosine similarity, clamped to [0, 1].
        """
        del element, spec  # arguments kept for future signature stability
        return self.default_semantic_relevance


def enrich(spec: DesignSpec, *, override: bool = False) -> DesignSpec:
    """Module-level convenience wrapper around :class:`AssetAnalyzer`."""
    return AssetAnalyzer().run(spec, override=override)
