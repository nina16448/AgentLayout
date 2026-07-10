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
    ErrorRecord,
    load_sample_ids,
    validate_run_id,
    write_json_once,
)
from metagpt.ext.agentlayout.tools.pfull_preprocessor import (  # noqa: E402
    ASSET_MANIFEST_FILENAME,
    prepare_pfull_sample,
)
from metagpt.ext.agentlayout.tools.text_bitmap_normalizer import (  # noqa: E402
    R3NormalizationConfig,
    prepare_r3_sample,
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
    prepare = sub.add_parser(
        "prepare-pfull", help="Snapshot P-Full inputs into an initialized A3 run."
    )
    prepare.add_argument("--run-dir", type=Path, required=True)
    prepare.add_argument("--crello-root", type=Path, required=True)
    normalize = sub.add_parser(
        "normalize-r3", help="Normalize every P-Full text bitmap for an initialized run."
    )
    normalize.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "prepare-pfull":
        store = A3RunStore(args.run_dir)
        manifest = store.manifest()
        sample_ids = json.loads(
            (store.run_dir / manifest.sample_ids_snapshot.stored_path).read_text()
        )
        rows = []
        failed = 0
        for sample_id in sample_ids:
            source = args.crello_root / f"crello_{sample_id}"
            destination = store.run_dir / "samples" / sample_id / "inputs" / "pfull"
            try:
                prepared = prepare_pfull_sample(source, destination)
                rows.append(
                    {
                        "sample_id": sample_id,
                        "status": "prepared",
                        "asset_count": len(prepared.assets),
                        "foreground_count": len(prepared.foreground_assets()),
                        "background_asset_id": prepared.background_asset_id,
                    }
                )
            except Exception as error:  # noqa: BLE001 -- failure must be persisted
                failed += 1
                rows.append(
                    {
                        "sample_id": sample_id,
                        "status": "failed",
                        "error_type": type(error).__name__,
                        "message": str(error),
                    }
                )
                store.record_run_error(
                    ErrorRecord(
                        stage="pfull_preprocessing",
                        error_type=type(error).__name__,
                        message=str(error),
                        details={"sample_id": sample_id, "source": str(source)},
                    )
                )
        write_json_once(
            store.run_dir / "pfull_preparation.json",
            {"total": len(sample_ids), "failed": failed, "samples": rows},
        )
        print(json.dumps({"total": len(sample_ids), "failed": failed}, indent=2))
        return 1 if failed else 0

    if args.command == "normalize-r3":
        store = A3RunStore(args.run_dir)
        manifest = store.manifest()
        sample_ids = json.loads(
            (store.run_dir / manifest.sample_ids_snapshot.stored_path).read_text()
        )
        image_config = manifest.config.image_normalization
        normalization = R3NormalizationConfig(
            long_edge_px=image_config.text_long_edge_px,
            padding_px=image_config.text_padding_px,
            alpha_threshold=image_config.alpha_threshold,
            resize_filter=image_config.resize_filter,
        )
        rows = []
        failed = 0
        for sample_id in sample_ids:
            sample_inputs = store.run_dir / "samples" / sample_id / "inputs"
            pfull_manifest = sample_inputs / "pfull" / ASSET_MANIFEST_FILENAME
            destination = sample_inputs / "r3"
            try:
                prepared = prepare_r3_sample(pfull_manifest, destination, normalization)
                rows.append(
                    {
                        "sample_id": sample_id,
                        "status": "normalized",
                        "asset_count": len(prepared.assets),
                        "text_bitmap_count": sum(
                            asset.media_type == "text_bitmap" for asset in prepared.assets
                        ),
                    }
                )
            except Exception as error:  # noqa: BLE001 -- failure must be persisted
                failed += 1
                rows.append(
                    {
                        "sample_id": sample_id,
                        "status": "failed",
                        "error_type": type(error).__name__,
                        "message": str(error),
                    }
                )
                store.record_run_error(
                    ErrorRecord(
                        stage="r3_normalization",
                        error_type=type(error).__name__,
                        message=str(error),
                        details={"sample_id": sample_id, "source": str(pfull_manifest)},
                    )
                )
        write_json_once(
            store.run_dir / "r3_normalization.json",
            {"total": len(sample_ids), "failed": failed, "samples": rows},
        )
        print(json.dumps({"total": len(sample_ids), "failed": failed}, indent=2))
        return 1 if failed else 0

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
