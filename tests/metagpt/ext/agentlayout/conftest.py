"""Local pytest conftest for the AgentLayout extension test suite.

Registers the ``requires_llm`` marker and applies default-skip semantics:

  * Without any marker filter, tests decorated with ``@pytest.mark.requires_llm``
    are SKIPPED. This keeps offline CI deterministic and free.
  * To execute them, pass ``-m requires_llm`` explicitly (and ensure
    OPENAI_API_KEY is configured in ~/.metagpt/config2.yaml).

Scoped to this directory only — does not affect the project-wide
``tests/conftest.py`` MockLLM fixture or the provider-level conftest.
"""
from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Register the agentlayout-local marker so unknown-marker warnings are silenced."""
    config.addinivalue_line(
        "markers",
        "requires_llm: test consumes real LLM tokens; default-skipped, run with `-m requires_llm`.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items) -> None:
    """Default-skip every requires_llm-marked test unless the user explicitly asked.

    The 'explicit ask' signal is ``-m requires_llm`` (or ``-m 'requires_llm or ...'``)
    in the pytest invocation. We probe this via ``config.getoption('markexpr')``.
    """
    markexpr = config.getoption("markexpr", default="") or ""
    if "requires_llm" in markexpr:
        return  # user opted in — let pytest run these
    skip_llm = pytest.mark.skip(
        reason="requires_llm marker; pass `-m requires_llm` to run (consumes LLM tokens)."
    )
    for item in items:
        if "requires_llm" in item.keywords:
            item.add_marker(skip_llm)
