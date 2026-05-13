"""Pinned-string regression tests for ``generate_layout.PROMPT_TEMPLATE``.

These tests catch silent prompt regressions where someone removes a hard-won
ATTENTION block. They do NOT call the LLM; they only assert that specific
guidance text remains in the prompt source.

Run:
    pytest tests/metagpt/ext/agentlayout/test_generator_prompt_template.py -v --no-cov
"""
from __future__ import annotations


def test_generator_prompt_pins_canvas_vertical_coverage_rule():
    """2026-05-14 step 6: live run #3 PNG showed bottom 1/3 of the 800x1200
    canvas left empty even though all schema constraints were satisfied. The
    Aesthetic Judge correctly penalised this on visual_coherence (17/25) and
    layout_balance (17/25). The Generator prompt now spells out a vertical
    coverage rule (max(top+height) >= 0.85*canvas_height) so the LLM stops
    clustering elements in the top half."""
    from metagpt.ext.agentlayout.actions.generate_layout import PROMPT_TEMPLATE

    # The coverage thresholds appear in the prompt so the LLM sees the math.
    assert "0.85 * canvas_height" in PROMPT_TEMPLATE
    assert "0.10 * canvas_height" in PROMPT_TEMPLATE
    # The two-sided rule (lowest bottom, highest top) is named explicitly.
    assert "max(top + height)" in PROMPT_TEMPLATE
    assert "min(top)" in PROMPT_TEMPLATE
    # The worked example uses the live-run canvas size so the LLM can copy.
    assert "800x1200 canvas" in PROMPT_TEMPLATE
    assert "y >= 1020" in PROMPT_TEMPLATE
    # The negative instruction prevents top-clustering.
    assert "do NOT cluster" in PROMPT_TEMPLATE
