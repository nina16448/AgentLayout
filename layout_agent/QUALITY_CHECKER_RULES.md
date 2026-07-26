# Quality Checker — Complete Rule Reference

> Compiled 2026-07-16. Source of truth: `metagpt/ext/agentlayout/tools/quality_checker.py` (1,719 lines).
> Single public entry point: `check_candidate()` (`quality_checker.py:213`). Batch wrapper: `filter_valid()` (`:269`).
> All rules always run (no fail-fast); `passed = (no violation recorded)`. `warnings` never affect `passed`.
> Every violation is a structured `Violation` record (`type` + `targets` + `detail`); the detail string carries
> concrete numbers because it feeds the Generator's retry feedback loop.

---

## Phase 1 — Element Completeness (`quality_checker.py:325`)

| Violation | Condition |
|-----------|-----------|
| `MISSING_ELEMENT` | An element ID present in the DesignSpec is absent from the candidate. |
| `EXTRA_ELEMENT` | An element ID present in the candidate does not exist in the DesignSpec (inventing elements is forbidden). |

## Phase 2 — Boundary Check (`quality_checker.py:353`)

| Violation | Condition |
|-----------|-----------|
| `OUT_OF_BOUNDS` | Any of: `left < 0`, `top < 0`, `left + width > canvas.width`, `top + height > canvas.height`. |

## Phase 3 — Hard Constraints (iterates `spec.hard_constraints`, `quality_checker.py:386`)

| Violation | Threshold | Calibration provenance |
|-----------|-----------|------------------------|
| `POSITION_PREFERENCE` (`:401`) | The element **center** must fall inside the hinted cell of a 3×3 canvas grid; each band edge is widened by **10%** of the canvas dimension (`POSITION_BAND_TOLERANCE = 0.10`), with a **16 px absolute floor**. | Step 10c: on a 600 px-tall canvas the strict center band is only 200 px, which hard-failed 5/5 candidates. |
| `NO_OVERLAP` (`:515`) | AABB intersection area / **smaller** box area must be ≤ **5%** (`NO_OVERLAP_TOLERANCE = 0.05`). Rotation is ignored. | Step 10: LLM rounding produced 1–20 px overlaps that failed 15/15 candidates under zero tolerance. |
| `Z_ORDER` (`:1598`) | Each target's `z_index` must be **strictly greater** than the reference element's. Accepts the explicit form `params={"above": <element_id>}` or a semantic hint from a 5-alias set (`above_background`, `above_bg`, `over_background`, `above_the_background`, `front_of_background`); the reference is resolved via `SemanticType.BACKGROUND_IMAGE`. If the spec has no background element the constraint is vacuously satisfied (graceful skip). | Step 12: the first content-aware live run crashed with 0 candidates passing because the Analyst emits the semantic hint form. |
| `SIZE_PREFERENCE` (`:1677`) | `element_area / canvas_area` must be ≥ the bucket's lower bound (table below). | — |
| `UNKNOWN_HINT` / `UNKNOWN_TARGET` | The hint is outside the known vocabulary, or a constraint target ID does not exist in the candidate. The position vocabulary contains 21 spellings, including reversed word-order aliases (`center_top`, `left_bottom`, …). | Step 9: the Analyst emitted `center_top` instead of `top_center`, causing `UNKNOWN_HINT` on every candidate. |

### Size bucket lower bounds (`SIZE_HINT_LOWER_BOUND`, `quality_checker.py:76`)

| Hint | Min area ratio | Note |
|------|----------------|------|
| `full-canvas` | 0.95 | |
| `hero` | 0.60 | |
| `large` | 0.30 | |
| `photo-prominent` | 0.20 | Step 60: GT-calibrated photo bucket (designer GT photo area ratio p50 = 0.213, N=2,374 elements); injected programmatically for `product_image` elements, never emitted by the Analyst. |
| `prominent` | 0.10 | 0.20 was too strict for one-line poster headlines. |
| `medium` | 0.08 | |
| `small` | 0.08 | |
| `caption` | 0.03 | |

**Deliberate asymmetry (Step 67 audit):** these values are the *QC acceptance floor*, NOT the Generator's
target. The Layout Generator prompt intentionally quotes higher numbers as stretch targets (e.g. prompt
"prominent ≥ 20%" vs QC floor 0.10) to counteract the LLM size-timidity documented in Steps 58/60. The two
sets of numbers must **not** be aligned — all Step 22–66 calibration was done against this gap.

## Step 35/36 — Visual-quality rules (2026-06-09, from the Step 34 N=20 failure audit)

| Violation | Threshold | Rationale |
|-----------|-----------|-----------|
| `TEXT_OBSCURED_BY_OVERLAY` (`:659`) | A non-text element with `z_index` **≥** the text's covers ≥ **20%** of the text bbox (`TEXT_OBSCURED_RATIO_THRESHOLD = 0.20`). | Step 37 tightened 0.30 → 0.20 and added the same-z case (render order undefined at equal z), after "FIND" rendered as "F.YD" under a mountain shape. |
| `LOW_TEXT_CONTRAST` (`:1531`) | WCAG 2.1 contrast ratio between the text color and the canvas background color < **4.5** (AA, `MIN_TEXT_CONTRAST_RATIO`). Skipped when the element carries no `color`. v1 compares against the canvas plate only; resolving the effective background through under-z elements is a known v2 item. | Light-gray-on-white unreadable body copy. |
| `DECORATIVE_IMAGE_OVERSIZED` (`:733`) | A single `decorative_image` covers > **40%** of the canvas area (`DECORATIVE_IMAGE_MAX_AREA_RATIO = 0.40`). | Dominant Step 34 failure mode (7/17): underlay shapes inflated to canvas size, burying the photo/text. True full-canvas plates use `background_image`, so 0.40 sits safely above legitimate underlays. |
| `TITLE_UNDERSIZED` (`:759`) | A `title` bbox smaller than **2.5%** of the canvas area (`TITLE_MIN_AREA_RATIO = 0.025`). | Titles rendered as tiny corner captions. |
| `TITLE_PERIPHERAL` (`:782`) | The `title` bbox center has `x ∉ [0.10, 0.90]`, or `y > 0.85` (pinned to the bottom), or `y < 0.05` (pinned to the top edge, likely cropped). | Step 36c added the top cutoff after a title landed in the extreme top-right corner. |

## Step 43 / F2 — Safe-zone & saliency rules — **downgraded to WARNINGS** (they no longer block acceptance)

Since the "think-then-draw" refactor (2026-06-25, `CheckResult` docstring `:192`), both rules are recorded in
`warnings` for analytics/feedback only: the art director may deliberately place text off the calm bands, and
rejecting that would kill the bold, asymmetric compositions the refactor targets.

| Warning | Threshold | Preconditions |
|---------|-----------|---------------|
| `PRIMARY_OUTSIDE_SAFE_ZONE` (`:948`) | A primary element (`title`/`subtitle`/`body_text`/`product_image`/`logo`) overlaps its best safe zone by < **50%** of its own area (`PRIMARY_SAFE_ZONE_MIN_OVERLAP = 0.50`). Safe-zone bboxes are decoded as **LTRB** (a Step 43 bug-fix — the first cut mis-read them as LTWH). | Requires a `BackgroundAnalysis`; **fully defers** when `spec.composition` is present (Step 63 deference contract — enforcing both gates reproduced the Step 62 double-bind). |
| `TEXT_ON_HIGH_SALIENCY` (`:888`) | Mean background saliency inside a text primary's bbox > **0.5** (`TEXT_ON_HIGH_SALIENCY_TAU`). Calibrated on Crello N=100 designer GT: text mean-saliency p95 = 0.38, only 1.3% of designer text exceeds 0.5 → ~1.3% false-positive rate. | Gated by feature flag `AGENTLAYOUT_F2_SALIENCY`, **default OFF** (Step 72 N=100 showed net-negative aesthetic impact). Also skips when the saliency map is absent or a composition directive exists. |

## Step 57 — Degenerate-layout guardrails (GT-calibrated degradation guards, not aesthetic rules)

| Violation | Threshold | GT calibration |
|-----------|-----------|----------------|
| `CANVAS_COVERAGE_LOW` (`:1088`) | Union coverage of foreground elements (everything except `background_image`), estimated on a **100×100** raster, < **10%** of the canvas (`CANVAS_COVERAGE_MIN = 0.10`). | Designer minimum is 0.129 (minimalist layouts are legitimate), so 0.10 passes every GT layout with margin; 10/70 Step 56 candidates fell below it. |
| `DEAD_BAND_EXCESSIVE` | Largest contiguous blank band (no foreground element) along either axis, leading/trailing margins included, > **60%** of that axis (`DEAD_BAND_MAX = 0.60`). | Designer GT maxima are v=0.503 / h=0.548; Step 56 candidates regularly hit 0.66–0.79 ("bottom half blank" degenerate compositions). |

## Step 59 — Text on busy texture (`quality_checker.py:1195`)

| Violation | Threshold |
|-----------|-----------|
| `TEXT_ON_BUSY_TEXTURE` | Mean normalised Sobel gradient of the background under a text element's **exposed pixels** (pixels not shielded by a `decorative_image` underlay bbox) > **0.065** (`TEXT_GRADIENT_MAX`). GT-calibrated: all 20 designer GT layouts pass — the worst exposed GT element is 0.0454, threshold = GT max + 0.02 margin (Step 58 lesson: gates hugging the GT max kill GT-style solutions). At 0.065 the rule catches 23% (74/327) of replayed live candidates. Text classification uses `visual_type == text` (not the semantic-type set) so CTA buttons are covered. Under a `text-on-photo` directive the focal photo's bbox counts as an underlay (the background gradient beneath it is invisible). The per-background Sobel map is cached per `(asset_ref, w, h)`; missing images or an unavailable `cv2` degrade to a silent skip (the "never crash" philosophy). |

## Step 62 — Composition Director contract (active only when `spec.composition` is set; otherwise bit-identical no-op)

| Violation | Threshold |
|-----------|-----------|
| `COMPOSITION_MISMATCH` (`:1384`) | Four sub-checks: (1) the focal photo's center must fall inside the directive's 3×3 grid cell, with **5%**-per-axis slack (`COMPOSITION_CELL_TOLERANCE = 0.05`); (2) the photo's area ratio must be inside the directive's size bucket ± **0.02** (`COMPOSITION_SIZE_MARGIN`); (3) the area-weighted **text mass center** must fall inside the directive's text cell (same 5% slack); (4) the candidate's photo-text relation must classify as the directive's relation. Relation classifier (mirrors the Step 61 GT signature): ≥ **30%** of text area overlapping the photo → `text-on-photo`; centroid offset < **1/6** of the axis → `centered-mix`; otherwise `stacked` (vertical) or `side-by-side` (horizontal). The focal photo = the largest `product_image` element. |
| `TEXT_ON_PHOTO_NO_UNDERLAY` (`:1482`) | Under a `text-on-photo` directive, every text element riding the focal photo (≥ 30% overlap) must have a `decorative_image` underlay covering ≥ **80%** of its own bbox (`TEXT_ON_PHOTO_UNDERLAY_MIN_COVER = 0.80`; 100% is not demanded because GT underlays often inset slightly — 8/20 GT layouts shield every text element this way). Skipped entirely when the spec contains no `decorative_image`: completeness forbids inventing elements, so the demand would be unsatisfiable. This is the conditional-exemption deal from Step 62: text riding the photo is no longer rejected by safe-zone/busy-texture rules, but readability must then be protected the designer way. |

## Graceful degradation (`rank_candidates_by_violations`, `quality_checker.py:298`)

When **no** candidate passes QC (e.g. an out-of-vocabulary hint failing every candidate identically, or a
genuinely over-constrained spec), the legacy pipeline does not hard-crash: candidates are ranked
fewest-violations-first (stable sort) and the least-broken layouts still reach the Aesthetic Judge, keeping the
reject loop alive so feedback can route the spec back to the Analyst.

---

## ⚠️ What actually fires in the A3 pipeline (the current thesis architecture)

A3 binds QC at `metagpt/ext/agentlayout/a3_stage_binding.py:207` — it calls the same
`check_candidate(parsed, spec)` but **without `bg`**, and the A3 spec is built by
`analyst_output_to_design_spec` (`tools/analyst_vision.py:319`), which produces **no `hard_constraints`, no
`composition`, and sets every foreground element's `visual_type` to `IMAGE`** (R3 text is a bitmap).
Consequently:

**Rules that fire in A3** (semantic types still come from the Analyst, so type-keyed rules work):

- Phase 1 completeness (`MISSING_ELEMENT` / `EXTRA_ELEMENT`)
- Phase 2 boundary (`OUT_OF_BOUNDS`)
- `TEXT_OBSCURED_BY_OVERLAY`
- `DECORATIVE_IMAGE_OVERSIZED`
- `TITLE_UNDERSIZED`
- `TITLE_PERIPHERAL`
- `CANVAS_COVERAGE_LOW`
- `DEAD_BAND_EXCESSIVE`

**Rules that never fire in A3:**

- All Phase 3 hard constraints — the A3 spec carries no `hard_constraints`.
- `PRIMARY_OUTSIDE_SAFE_ZONE` / `TEXT_ON_HIGH_SALIENCY` — no `BackgroundAnalysis` is passed (and F2 is flag-off anyway).
- `TEXT_ON_BUSY_TEXTURE` — requires `visual_type == text`; every A3 foreground element is `IMAGE`.
- `COMPOSITION_MISMATCH` / `TEXT_ON_PHOTO_NO_UNDERLAY` — the A3 spec has no `composition` directive.
- `LOW_TEXT_CONTRAST` — in practice never: the A3 Mapper contract does not ask for a `color` field, and the rule skips colorless elements.

**QC's role is different in A3.** The legacy pipeline used `filter_valid` to **reject** candidates and drive
retry/top-up rounds. The A3 pipeline **never rejects on QC**: all three rendered R0 candidates go to
Judge-Select regardless, and the QC outcome is provided as structured context inside the Judge-Select prompt
(`deterministic_qc_passed` / `deterministic_qc_violations`) plus run provenance. If all three fail QC, the run
only records a `DEGRADATION_ALL_QC_FAILED` flag (`a3_pipeline.py:292`) — nothing is silently promoted. The A3
binding additionally computes `qc_completeness` = fraction of expected non-background elements present.

**One-line summary:** `quality_checker.py` is a rule library of 22 violation types accumulated across
Steps 10–72, every threshold backed by GT calibration or a live-run post-mortem; the A3 architecture demotes
it from a rejection gate to deterministic evidence handed to Judge-Select, and — given the shape of the A3
spec — only 8 rule categories can actually fire.
