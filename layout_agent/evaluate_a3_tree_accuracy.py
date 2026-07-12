#!/usr/bin/env python3
"""Read-only tree-prediction accuracy evaluation for a persisted A3 run.

No LLM/API code path.  The only writes are new files below
``--output-root/a3.tree-accuracy.v1/<evaluation-id>``; an existing evaluation
directory is never overwritten.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from metagpt.ext.agentlayout.evaluation.a3_tree_accuracy import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
