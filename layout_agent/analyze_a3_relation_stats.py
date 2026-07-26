#!/usr/bin/env python3
"""Read-only statistical reanalysis of Relation N=100 T0/T2/T3 SGC/TLC/PCA.

No LLM/API code path and no layout regeneration: per-sample metrics are
recomputed deterministically from frozen run artifacts and the human oracle
trees.  The only writes are new files below
``--output-root/a3.relation-stats.v1/<evaluation-id>``; an existing evaluation
directory is never overwritten.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from metagpt.ext.agentlayout.evaluation.a3_relation_stats import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
