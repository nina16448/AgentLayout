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
    PFullAssetManifest,
    build_prepared_input,
    prepare_pfull_sample,
)
from metagpt.ext.agentlayout.tools.text_bitmap_normalizer import (  # noqa: E402
    R3_MANIFEST_FILENAME,
    R3AssetManifest,
    R3NormalizationConfig,
    prepare_r3_sample,
)
from metagpt.ext.agentlayout.tools.analyst_vision import (  # noqa: E402
    build_vision_packet,
    save_vision_packet,
)


DEFAULT_RUNS_ROOT = REPO_ROOT / "layout_agent" / "runs" / "a3"


def _call_budget(loop: str, tree_arm: str, sample_count: int) -> dict:
    """System-call budget assuming no reliability retries (each stage may 3x)."""
    per_sample = 1 + 1 + 3 + 1  # analyst + director + 3 mappers + judge_select
    if tree_arm == "T2":
        per_sample += 1  # planner
    if loop == "L1-Gated":
        per_sample += 2  # judge_critic + at most one revision mapper call
    return {
        "loop": loop,
        "tree_arm": tree_arm,
        "samples": sample_count,
        "model_calls_per_sample_max": per_sample,
        "model_calls_total_max": per_sample * sample_count,
        "note": "excludes schema-retry attempts (up to 3x per stage) and assumes "
        "every L1 sample triggers one revision",
    }


def _command_run(args: argparse.Namespace) -> int:
    store = A3RunStore(args.run_dir)
    manifest = store.manifest()
    config = manifest.config
    sample_ids = json.loads(
        (store.run_dir / manifest.sample_ids_snapshot.stored_path).read_text()
    )
    budget = _call_budget(config.loop, args.tree_arm, len(sample_ids))
    if not args.allow_api_calls:
        print(json.dumps({"authorized": False, "budget": budget}, indent=2))
        print(
            "refusing to make paid model calls without --allow-api-calls",
            file=sys.stderr,
        )
        return 2

    # Paid path: import the LLM machinery only after explicit authorization.
    import asyncio

    from metagpt.ext.agentlayout.a3_pipeline import A3L0Pipeline  # noqa: E402
    from metagpt.ext.agentlayout.a3_pipeline_l1 import A3L1GatedPipeline  # noqa: E402
    from metagpt.ext.agentlayout.a3_stage_binding import A3StageBinding  # noqa: E402
    from metagpt.ext.agentlayout.actions.analyze_a3 import AnalyzeA3Brief  # noqa: E402
    from metagpt.ext.agentlayout.actions.compose_concept_a3 import ComposeConceptA3  # noqa: E402
    from metagpt.ext.agentlayout.actions.generate_layout_a3 import GenerateLayoutA3  # noqa: E402
    from metagpt.ext.agentlayout.actions.judge_critic_a3 import JudgeCriticA3  # noqa: E402
    from metagpt.ext.agentlayout.actions.judge_select_a3 import JudgeSelectA3  # noqa: E402
    from metagpt.ext.agentlayout.actions.plan_assets_a3 import PlanAssetsA3  # noqa: E402

    def _expected(stage: str) -> str:
        return config.models[stage].model

    rows = []
    failed = 0
    for sample_id in sample_ids:
        sample_dir = store.run_dir / "samples" / sample_id
        inputs = sample_dir / "inputs"
        binding = None
        try:
            r3 = R3AssetManifest.model_validate_json(
                (inputs / "r3" / R3_MANIFEST_FILENAME).read_bytes()
            )
            pfull = PFullAssetManifest.model_validate_json(
                (inputs / "pfull" / ASSET_MANIFEST_FILENAME).read_bytes()
            )
            binding = A3StageBinding(
                r3_manifest=r3,
                background_overview_path=inputs / "analyst_vision" / "background_overview.png",
                renders_dir=sample_dir / "renders",
                stages_dir=sample_dir / "stages",
                analyst_action=AnalyzeA3Brief(expected_model=_expected("analyst")),
                planner_action=PlanAssetsA3(expected_model=_expected("asset_planner")),
                director_action=ComposeConceptA3(
                    expected_model=_expected("composition_director")
                ),
                mapper_action=GenerateLayoutA3(
                    expected_model=_expected("coordinate_mapper")
                ),
                judge_select_action=JudgeSelectA3(expected_model=_expected("judge_select")),
                judge_critic_action=JudgeCriticA3(expected_model=_expected("judge_critic"))
                if config.loop == "L1-Gated"
                else None,
            )
            common = dict(
                config=config,
                analyst=binding.analyst,
                planner=binding.planner,
                director=binding.director,
                mapper=binding.mapper,
                renderer=binding.renderer,
                qc=binding.qc,
                judge_select=binding.judge_select,
                artifacts_dir=sample_dir / "pipeline",
            )
            if config.loop == "L1-Gated":
                pipeline = A3L1GatedPipeline(
                    judge_critic=binding.judge_critic,
                    repair=binding.repair,
                    verifier=binding.verifier,
                    **common,
                )
            else:
                pipeline = A3L0Pipeline(**common)
            result = asyncio.run(
                pipeline.run(
                    user_brief=build_prepared_input(pfull).user_brief,
                    tree_arm=args.tree_arm,
                )
            )
            rows.append(
                {
                    "sample_id": sample_id,
                    "status": "completed",
                    "final": getattr(result, "final_slot_id", result.b0_slot_id),
                    "stage_calls": len(binding.call_records),
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
                    stage="a3_pipeline_run",
                    error_type=type(error).__name__,
                    message=str(error),
                    details={"sample_id": sample_id, "tree_arm": args.tree_arm},
                )
            )
        finally:
            # Paid calls happened even when the sample failed; the cost trail
            # must survive either way (A3-08 smoke finding).
            if binding is not None and binding.call_records:
                try:
                    binding.write_call_records(sample_dir / "stage_calls.json")
                except FileExistsError:
                    pass
    write_json_once(
        store.run_dir / "a3_run_summary.json",
        {
            "tree_arm": args.tree_arm,
            "budget": budget,
            "total": len(sample_ids),
            "failed": failed,
            "samples": rows,
        },
    )
    print(json.dumps({"total": len(sample_ids), "failed": failed}, indent=2))
    return 1 if failed else 0


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
    vision = sub.add_parser(
        "prepare-analyst-vision",
        help="Build background overview and foreground contact sheets without API calls.",
    )
    vision.add_argument("--run-dir", type=Path, required=True)
    run = sub.add_parser(
        "run",
        help="Execute the A3 pipeline for an initialized, fully prepared run. "
        "Refuses to spend money unless --allow-api-calls is given.",
    )
    run.add_argument("--run-dir", type=Path, required=True)
    run.add_argument("--tree-arm", choices=["T0", "T1", "T2", "T3"], default="T2")
    run.add_argument(
        "--allow-api-calls",
        action="store_true",
        help="Explicitly authorize paid model calls. Without it, only the call "
        "budget is printed and the command exits with status 2.",
    )
    args = parser.parse_args()

    if args.command == "run":
        return _command_run(args)

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

    if args.command == "prepare-analyst-vision":
        store = A3RunStore(args.run_dir)
        manifest = store.manifest()
        sample_ids = json.loads(
            (store.run_dir / manifest.sample_ids_snapshot.stored_path).read_text()
        )
        rows = []
        failed = 0
        for sample_id in sample_ids:
            inputs = store.run_dir / "samples" / sample_id / "inputs"
            pfull_path = inputs / "pfull" / ASSET_MANIFEST_FILENAME
            r3_path = inputs / "r3" / R3_MANIFEST_FILENAME
            destination = inputs / "analyst_vision"
            try:
                pfull = PFullAssetManifest.model_validate_json(pfull_path.read_bytes())
                r3 = R3AssetManifest.model_validate_json(r3_path.read_bytes())
                brief = build_prepared_input(pfull).user_brief
                packet = build_vision_packet(r3, brief)
                save_vision_packet(packet, destination)
                rows.append(
                    {
                        "sample_id": sample_id,
                        "status": "prepared",
                        "prompt_sha256": packet.prompt_sha256,
                        "image_count": len(packet.images),
                        "image_labels": packet.image_labels,
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
                        stage="analyst_vision_preparation",
                        error_type=type(error).__name__,
                        message=str(error),
                        details={"sample_id": sample_id},
                    )
                )
        write_json_once(
            store.run_dir / "analyst_vision_preparation.json",
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
