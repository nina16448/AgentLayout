"""A3 run planner and immutable run initializer (A3-01; makes no API calls)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from metagpt.ext.agentlayout.a3_config import A3RunConfig  # noqa: E402
from metagpt.ext.agentlayout.run_manifest import (  # noqa: E402
    A3RunStore,
    load_sample_ids,
    validate_run_id,
)


DEFAULT_RUNS_ROOT = REPO_ROOT / "layout_agent" / "runs" / "a3"


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--sample-ids", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS_ROOT)


def _load(args: argparse.Namespace):
    config = A3RunConfig.model_validate_json(args.config.read_bytes())
    ids = load_sample_ids(args.sample_ids)
    return config, ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="Validate and print a zero-cost run plan.")
    _common(plan)
    init = sub.add_parser("init", help="Create an immutable A3 run skeleton.")
    _common(init)
    args = parser.parse_args()

    config, ids = _load(args)
    validate_run_id(args.run_id)
    run_dir = args.runs_root.resolve() / args.run_id
    if args.command == "plan":
        print(
            json.dumps(
                {
                    "api_calls": 0,
                    "run_id": args.run_id,
                    "run_directory": str(run_dir),
                    "sample_count": len(ids),
                    "config": config.model_dump(mode="json"),
                    "exists": run_dir.exists(),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    store = A3RunStore.create(
        runs_root=args.runs_root,
        run_id=args.run_id,
        config_path=args.config,
        sample_ids_path=args.sample_ids,
        repo_root=REPO_ROOT,
    )
    print(store.run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
