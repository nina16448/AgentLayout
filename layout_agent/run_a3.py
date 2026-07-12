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


def _command_prepare_annotation(args: argparse.Namespace) -> int:
    """Build self-contained annotation packets (no background, no GT)."""
    import shutil

    from metagpt.ext.agentlayout.tools.annotation import (  # noqa: E402
        build_annotation_packet,
        save_annotation_packet,
    )

    store = A3RunStore(args.run_dir)
    manifest = store.manifest()
    sample_ids = json.loads(
        (store.run_dir / manifest.sample_ids_snapshot.stored_path).read_text()
    )
    rows = []
    failed = 0
    for sample_id in sample_ids:
        sample_dir = store.run_dir / "samples" / sample_id
        inputs = sample_dir / "inputs"
        destination = sample_dir / "annotation"
        try:
            pfull = PFullAssetManifest.model_validate_json(
                (inputs / "pfull" / ASSET_MANIFEST_FILENAME).read_bytes()
            )
            r3 = R3AssetManifest.model_validate_json(
                (inputs / "r3" / R3_MANIFEST_FILENAME).read_bytes()
            )
            vision_dir = inputs / "analyst_vision"
            sheets = sorted(
                path.name for path in vision_dir.glob("asset_contact_sheet_*.png")
            )
            if not sheets:
                raise FileNotFoundError("no contact sheets; run prepare-analyst-vision first")
            packet = build_annotation_packet(
                r3, build_prepared_input(pfull).user_brief, sheets
            )
            save_annotation_packet(packet, destination)
            # Copy ONLY the contact sheets: annotators never receive the
            # background overview or any GT-derived artifact.
            for sheet in sheets:
                shutil.copyfile(vision_dir / sheet, destination / sheet)
            rows.append(
                {
                    "sample_id": sample_id,
                    "status": "prepared",
                    "packet_sha256": packet.packet_sha256,
                    "contact_sheets": sheets,
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
                    stage="annotation_preparation",
                    error_type=type(error).__name__,
                    message=str(error),
                    details={"sample_id": sample_id},
                )
            )
    write_json_once(
        store.run_dir / "annotation_preparation.json",
        {"total": len(sample_ids), "failed": failed, "samples": rows},
    )
    print(json.dumps({"total": len(sample_ids), "failed": failed}, indent=2))
    return 1 if failed else 0


def _command_prepare_adjudication(args: argparse.Namespace) -> int:
    """Build a no-winner comparison queue from independent annotations."""
    from metagpt.ext.agentlayout.tools.adjudication import (  # noqa: E402
        build_adjudication_guide,
        build_adjudication_packet,
        load_annotation_submissions,
        save_adjudication_materials,
    )
    from metagpt.ext.agentlayout.tools.annotation import (  # noqa: E402
        ANNOTATION_PACKET_FILENAME,
        AnnotationPacket,
    )

    store = A3RunStore(args.run_dir)
    manifest = store.manifest()
    sample_ids = json.loads(
        (store.run_dir / manifest.sample_ids_snapshot.stored_path).read_text()
    )
    destination = store.run_dir / "adjudication"
    destination.mkdir(parents=True, exist_ok=False)
    with (destination / "ADJUDICATION_GUIDE.md").open(
        "x", encoding="utf-8"
    ) as handle:
        handle.write(build_adjudication_guide())

    rows = []
    failed = 0
    for sample_id in sample_ids:
        sample_dir = store.run_dir / "samples" / sample_id
        annotation_dir = sample_dir / "annotation"
        try:
            r3 = R3AssetManifest.model_validate_json(
                (sample_dir / "inputs" / "r3" / R3_MANIFEST_FILENAME).read_bytes()
            )
            source_packet = AnnotationPacket.model_validate_json(
                (annotation_dir / ANNOTATION_PACKET_FILENAME).read_bytes()
            )
            submissions = load_annotation_submissions(annotation_dir, r3)
            packet = build_adjudication_packet(
                submissions,
                annotation_directory=f"samples/{sample_id}/annotation",
                contact_sheet_files=source_packet.contact_sheet_files,
            )
            save_adjudication_materials(
                packet, destination / "samples" / sample_id
            )
            rows.append(
                {
                    "sample_id": sample_id,
                    "status": "prepared",
                    "annotator_ids": packet.annotator_ids,
                    "pairwise_reports": len(packet.pairwise_agreements),
                    "assets_requiring_decision": sum(
                        bool(asset.disagreement_fields) for asset in packet.assets
                    ),
                    "requires_adjudication": packet.requires_adjudication,
                }
            )
        except Exception as error:  # noqa: BLE001 -- preserve every failure
            failed += 1
            rows.append(
                {
                    "sample_id": sample_id,
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "message": str(error),
                }
            )
            error_dir = destination / "errors"
            error_dir.mkdir(parents=True, exist_ok=True)
            write_json_once(
                error_dir / f"{sample_id}.json",
                {
                    "stage": "adjudication_preparation",
                    "sample_id": sample_id,
                    "error_type": type(error).__name__,
                    "message": str(error),
                },
            )
    write_json_once(
        destination / "adjudication_queue.json",
        {
            "version": "a3.adjudication-queue.v1",
            "source_run_id": manifest.run_id,
            "total": len(sample_ids),
            "failed": failed,
            "samples": rows,
        },
    )
    print(json.dumps({"total": len(sample_ids), "failed": failed}, indent=2))
    return 1 if failed else 0


def _command_finalize_adjudication(args: argparse.Namespace) -> int:
    """Validate completed human decisions and publish T3 oracle trees."""
    import hashlib

    from metagpt.ext.agentlayout.tools.adjudication import (  # noqa: E402
        ADJUDICATED_ANNOTATION_FILENAME,
        ADJUDICATION_PACKET_FILENAME,
        ADJUDICATION_RECORD_FILENAME,
        AdjudicationPacket,
        validate_adjudication_submission,
    )
    from metagpt.ext.agentlayout.tools.annotation import (  # noqa: E402
        AdjudicationRecord,
        HumanAnnotation,
    )

    store = A3RunStore(args.run_dir)
    manifest = store.manifest()
    sample_ids = json.loads(
        (store.run_dir / manifest.sample_ids_snapshot.stored_path).read_text()
    )
    adjudication_root = store.run_dir / "adjudication"
    oracle_root = adjudication_root / "oracle_trees"
    summary_path = adjudication_root / "adjudication_finalization.json"
    if oracle_root.exists() or summary_path.exists():
        raise FileExistsError("adjudication finalization is write-once")

    validated = []
    errors = []
    for sample_id in sample_ids:
        sample_dir = adjudication_root / "samples" / sample_id
        try:
            packet = AdjudicationPacket.model_validate_json(
                (sample_dir / ADJUDICATION_PACKET_FILENAME).read_bytes()
            )
            annotation_path = sample_dir / ADJUDICATED_ANNOTATION_FILENAME
            record_path = sample_dir / ADJUDICATION_RECORD_FILENAME
            final_annotation = HumanAnnotation.model_validate_json(
                annotation_path.read_bytes()
            )
            record = AdjudicationRecord.model_validate_json(record_path.read_bytes())
            for source in packet.sources:
                source_path = store.run_dir / packet.annotation_directory / source.filename
                source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
                if source_hash != source.sha256:
                    raise ValueError(
                        f"source annotation changed after packet creation: {source_path}"
                    )
            tree = validate_adjudication_submission(packet, final_annotation, record)
            validated.append(
                {
                    "sample_id": sample_id,
                    "tree": tree,
                    "annotation_sha256": hashlib.sha256(
                        annotation_path.read_bytes()
                    ).hexdigest(),
                    "record_sha256": hashlib.sha256(record_path.read_bytes()).hexdigest(),
                }
            )
        except Exception as error:  # noqa: BLE001 -- report all human form errors
            errors.append(
                {
                    "sample_id": sample_id,
                    "error_type": type(error).__name__,
                    "message": str(error),
                }
            )

    # Human forms are editable until valid. Validation is all-or-nothing so a
    # partial submission never creates a misleading partial oracle set.
    if errors:
        print(
            json.dumps(
                {"total": len(sample_ids), "valid": len(validated), "errors": errors},
                indent=2,
            )
        )
        return 1

    oracle_root.mkdir(parents=True, exist_ok=False)
    rows = []
    for item in validated:
        tree_path = oracle_root / f"{item['sample_id']}.json"
        tree_payload = item["tree"].model_dump(mode="json")
        write_json_once(tree_path, tree_payload)
        rows.append(
            {
                "sample_id": item["sample_id"],
                "annotation_sha256": item["annotation_sha256"],
                "record_sha256": item["record_sha256"],
                "oracle_tree_path": str(tree_path.relative_to(store.run_dir)),
                "oracle_tree_sha256": hashlib.sha256(tree_path.read_bytes()).hexdigest(),
            }
        )
    write_json_once(
        summary_path,
        {
            "version": "a3.adjudication-finalization.v1",
            "source_run_id": manifest.run_id,
            "total": len(rows),
            "failed": 0,
            "oracle_root": str(oracle_root.relative_to(store.run_dir)),
            "samples": rows,
        },
    )
    print(json.dumps({"total": len(rows), "failed": 0}, indent=2))
    return 0


def _command_snapshot_text_bitmaps(args: argparse.Namespace) -> int:
    """Write A3 text-bitmap sidecars from the Crello dataset (no LLM calls)."""
    from metagpt.ext.agentlayout.tools.pfull_preprocessor import (  # noqa: E402
        A3_TEXT_BITMAP_SIDECAR_VERSION,
        TEXT_BITMAP_SIDECAR_FILENAME,
    )

    payload = json.loads(args.ids.read_text(encoding="utf-8"))
    id_pool = payload["ids"] if isinstance(payload, dict) else payload
    targets = set()
    for sample_id in id_pool:
        sample_dir = args.crello_root / f"crello_{sample_id}"
        if not (sample_dir / "meta.json").exists():
            print(f"skip {sample_id}: no cached meta.json", file=sys.stderr)
            continue
        if (sample_dir / TEXT_BITMAP_SIDECAR_FILENAME).exists():
            continue  # incremental: sidecar is write-once per sample
        targets.add(sample_id)
    print(f"targets: {len(targets)} samples (of {len(id_pool)} requested)")
    if not targets:
        return 0

    from datasets import load_dataset  # noqa: E402

    dataset = load_dataset(args.hf_dataset, split=args.split, streaming=True)
    done = set()
    saved = 0
    mismatches = 0
    for scanned, sample in enumerate(dataset, start=1):
        sample_id = sample["id"]
        if sample_id not in targets or sample_id in done:
            if len(done) == len(targets):
                break
            continue
        sample_dir = args.crello_root / f"crello_{sample_id}"
        meta = json.loads((sample_dir / "meta.json").read_text(encoding="utf-8"))
        images = sample["image"]
        types = sample["type"]
        bitmaps = {}
        for position, element in enumerate(meta["elements"]):
            index = int(element.get("idx", position))
            if not (element.get("type_code") == 1 or element.get("kind") == "text"):
                continue
            if index >= len(images) or types[index] != 1:
                mismatches += 1
                print(f"  [WARN] {sample_id} idx={index}: dataset/meta mismatch", file=sys.stderr)
                continue
            filename = f"a3_text_{index:04d}.png"
            # Raw dataset render, deliberately NOT resized to GT geometry.
            images[index].convert("RGBA").save(sample_dir / filename, format="PNG")
            bitmaps[str(index)] = filename
            saved += 1
        write_json_once(
            sample_dir / TEXT_BITMAP_SIDECAR_FILENAME,
            {
                "version": A3_TEXT_BITMAP_SIDECAR_VERSION,
                "sample_id": sample_id,
                "bitmaps": bitmaps,
            },
        )
        done.add(sample_id)
        print(f"  [{len(done)}/{len(targets)}] {sample_id} (scanned={scanned})")
        if len(done) == len(targets):
            break
    missing = sorted(targets - done)
    print(
        json.dumps(
            {"done": len(done), "bitmaps_saved": saved, "mismatches": mismatches, "missing": missing}
        )
    )
    return 0 if not missing else 1


def _command_run_l1_tail(args: argparse.Namespace) -> int:
    """Gate C L1 arm: critic + at most one revision on a persisted L0 run."""
    store = A3RunStore(args.run_dir)
    manifest = store.manifest()
    config = manifest.config
    if config.loop != "L1-Gated":
        print("run-l1-tail requires a run whose config.loop is 'L1-Gated'", file=sys.stderr)
        return 1
    sample_ids = json.loads(
        (store.run_dir / manifest.sample_ids_snapshot.stored_path).read_text()
    )
    source_root = args.reuse_r0_from.resolve()
    budget = {
        "loop": "L1-Gated (tail only, R0 reused)",
        "tree_arm": args.tree_arm,
        "samples": len(sample_ids),
        "model_calls_per_sample_max": 2,
        "model_calls_total_max": 2 * len(sample_ids),
        "reuse_r0_from": str(source_root),
        "note": "judge_critic + at most one revision; excludes schema-retry "
        "attempts (up to 3x per stage)",
    }
    if not args.allow_api_calls:
        print(json.dumps({"authorized": False, "budget": budget}, indent=2))
        print(
            "refusing to make paid model calls without --allow-api-calls",
            file=sys.stderr,
        )
        return 2

    import asyncio

    from metagpt.ext.agentlayout.a3_pipeline import R0Bundle, R0PhaseOutcome  # noqa: E402
    from metagpt.ext.agentlayout.a3_pipeline_l1 import A3L1GatedPipeline  # noqa: E402
    from metagpt.ext.agentlayout.a3_stage_binding import A3StageBinding  # noqa: E402
    from metagpt.ext.agentlayout.actions.generate_layout_a3 import GenerateLayoutA3  # noqa: E402
    from metagpt.ext.agentlayout.actions.judge_critic_a3 import JudgeCriticA3  # noqa: E402
    from metagpt.ext.agentlayout.layout_tree_v3 import TreeCondition  # noqa: E402
    from metagpt.ext.agentlayout.schema import CompositionConcept  # noqa: E402
    from metagpt.ext.agentlayout.tools.analyst_vision import A3AnalystOutput  # noqa: E402
    from metagpt.ext.agentlayout.tools.judge_select import JudgeSelectResult  # noqa: E402

    async def _unused(*_args, **_kwargs):
        raise RuntimeError("this stage is frozen from the reused L0 run")

    rows = []
    failed = 0
    for sample_id in sample_ids:
        sample_dir = store.run_dir / "samples" / sample_id
        source_sample = source_root / "samples" / sample_id
        source_pipeline = source_sample / "pipeline"
        binding = None
        try:
            r3 = R3AssetManifest.model_validate_json(
                (source_sample / "inputs" / "r3" / R3_MANIFEST_FILENAME).read_bytes()
            )
            analyst_output = A3AnalystOutput.model_validate_json(
                (source_pipeline / "analyst_output.json").read_bytes()
            )
            concept_payload = json.loads(
                (source_sample / "stages" / "director" / "concept_set.json").read_text()
            )
            concepts = [
                CompositionConcept.model_validate(concept)
                for concept in concept_payload["concepts"]
            ]
            l0_result = json.loads((source_pipeline / "l0_result.json").read_text())
            outcome = R0PhaseOutcome(
                analyst_output=analyst_output,
                condition=TreeCondition.model_validate_json(
                    (source_pipeline / "tree_condition.json").read_bytes()
                ),
                bundle=R0Bundle.model_validate_json(
                    (source_pipeline / "r0_bundle.json").read_bytes()
                ),
                degradations=l0_result["degradations"],
                selection=JudgeSelectResult.model_validate_json(
                    (source_pipeline / "judge_select_result.json").read_bytes()
                ),
            )
            binding = A3StageBinding(
                r3_manifest=r3,
                background_overview_path=source_sample
                / "inputs"
                / "analyst_vision"
                / "background_overview.png",
                renders_dir=sample_dir / "renders",
                stages_dir=sample_dir / "stages",
                analyst_action=None,
                planner_action=None,
                director_action=None,
                mapper_action=GenerateLayoutA3(
                    expected_model=config.models["coordinate_mapper"].model
                ),
                judge_select_action=None,
                judge_critic_action=JudgeCriticA3(
                    expected_model=config.models["judge_critic"].model
                ),
            )
            binding.hydrate_from_r0(analyst_output=analyst_output, concepts=concepts)
            pipeline = A3L1GatedPipeline(
                config=config,
                analyst=_unused,
                planner=_unused,
                director=_unused,
                mapper=binding.mapper,
                renderer=binding.renderer,
                qc=binding.qc,
                judge_select=_unused,
                judge_critic=binding.judge_critic,
                repair=binding.repair,
                verifier=binding.verifier,
                artifacts_dir=sample_dir / "pipeline",
            )
            result = asyncio.run(
                pipeline.run_from_r0(outcome=outcome, tree_arm=args.tree_arm)
            )
            rows.append(
                {
                    "sample_id": sample_id,
                    "status": "completed",
                    "b0": result.b0_slot_id,
                    "final": result.final_slot_id,
                    "repair_attempted": result.repair_attempted,
                    "winner": result.guard.winner if result.guard else None,
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
                    stage="a3_l1_tail_run",
                    error_type=type(error).__name__,
                    message=str(error),
                    details={"sample_id": sample_id, "reuse_r0_from": str(source_root)},
                )
            )
        finally:
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


def _command_run(args: argparse.Namespace) -> int:
    store = A3RunStore(args.run_dir)
    manifest = store.manifest()
    config = manifest.config
    sample_ids = json.loads(
        (store.run_dir / manifest.sample_ids_snapshot.stored_path).read_text()
    )
    oracle_trees = {}
    if args.tree_arm == "T3":
        if args.oracle_trees_from is None:
            print("T3 requires --oracle-trees-from", file=sys.stderr)
            return 1
        from metagpt.ext.agentlayout.tools.adjudication import (  # noqa: E402
            load_oracle_tree,
        )

        try:
            for sample_id in sample_ids:
                tree = load_oracle_tree(args.oracle_trees_from, sample_id)
                r3 = R3AssetManifest.model_validate_json(
                    (
                        store.run_dir
                        / "samples"
                        / sample_id
                        / "inputs"
                        / "r3"
                        / R3_MANIFEST_FILENAME
                    ).read_bytes()
                )
                expected_ids = {asset.asset_id for asset in r3.foreground_assets()}
                tree_ids = {node.asset_id for node in tree.nodes}
                if tree_ids != expected_ids:
                    raise ValueError(
                        f"{sample_id} oracle coverage mismatch: "
                        f"missing={sorted(expected_ids - tree_ids)}, "
                        f"extra={sorted(tree_ids - expected_ids)}"
                    )
                oracle_trees[sample_id] = tree
        except Exception as error:  # noqa: BLE001 -- fail before any paid call
            print(f"invalid T3 oracle tree set: {error}", file=sys.stderr)
            return 1
    elif args.oracle_trees_from is not None:
        print("--oracle-trees-from is only valid with --tree-arm T3", file=sys.stderr)
        return 1
    budget = _call_budget(config.loop, args.tree_arm, len(sample_ids))
    if args.oracle_trees_from is not None:
        budget["oracle_trees_from"] = str(args.oracle_trees_from.resolve())
    if not args.allow_api_calls:
        print(json.dumps({"authorized": False, "budget": budget}, indent=2))
        print(
            "refusing to make paid model calls without --allow-api-calls",
            file=sys.stderr,
        )
        return 2

    if args.authorization_receipt is None:
        print(
            "refusing paid calls without --authorization-receipt",
            file=sys.stderr,
        )
        return 2

    # Paid path: import the LLM machinery only after explicit authorization.
    import asyncio

    from metagpt.ext.agentlayout.a3_paid_budget import (  # noqa: E402
        A3AuthorizationCapReached,
        A3BudgetedLLM,
        A3PaidBudget,
        acquire_paid_run_lock,
        load_authorization,
        release_paid_run_lock,
    )
    from metagpt.ext.agentlayout.a3_pipeline import A3L0Pipeline  # noqa: E402
    from metagpt.ext.agentlayout.a3_pipeline_l1 import A3L1GatedPipeline  # noqa: E402
    from metagpt.ext.agentlayout.a3_stage_binding import A3StageBinding  # noqa: E402
    from metagpt.ext.agentlayout.actions.analyze_a3 import AnalyzeA3Brief  # noqa: E402
    from metagpt.ext.agentlayout.actions.analyze_a3_text_only import AnalyzeA3TextOnly  # noqa: E402
    from metagpt.ext.agentlayout.actions.compose_concept_a3 import ComposeConceptA3  # noqa: E402
    from metagpt.ext.agentlayout.actions.generate_layout_a3 import GenerateLayoutA3  # noqa: E402
    from metagpt.ext.agentlayout.actions.judge_critic_a3 import JudgeCriticA3  # noqa: E402
    from metagpt.ext.agentlayout.actions.judge_select_a3 import JudgeSelectA3  # noqa: E402
    from metagpt.ext.agentlayout.actions.plan_assets_a3 import PlanAssetsA3  # noqa: E402

    expected_models = {entry.model for entry in config.models.values()}
    if len(expected_models) != 1:
        print("paid run requires one frozen model across all stages", file=sys.stderr)
        return 2
    expected_model = next(iter(expected_models))
    try:
        authorization = load_authorization(
            args.authorization_receipt,
            expected_run_id=manifest.run_id,
            expected_model=expected_model,
            expected_tree_arm=args.tree_arm,
            expected_analyst_arm=args.analyst_arm,
        )
    except Exception as error:
        print(f"authorization receipt rejected: {type(error).__name__}", file=sys.stderr)
        return 2

    lock_descriptor = acquire_paid_run_lock(store.run_dir / ".a3_paid_run.lock")
    paid_budget = A3PaidBudget(
        authorization,
        store.run_dir / "a3_paid_budget_ledger.jsonl",
        code_paths=[
            Path(__file__),
            REPO_ROOT / "metagpt/ext/agentlayout/a3_paid_budget.py",
        ],
        resume=args.resume_ledger,
    )

    def _expected(stage: str) -> str:
        return config.models[stage].model

    rows = []
    failed = 0
    consecutive_failures = 0
    stop_reason = None
    for sample_id in sample_ids:
        sample_dir = store.run_dir / "samples" / sample_id
        # Resume: a durable l0_result means this sample already completed in a
        # previous authorized attempt; reuse it verbatim, never re-spend.
        prior_result_path = sample_dir / "pipeline" / "l0_result.json"
        if prior_result_path.exists():
            prior = json.loads(prior_result_path.read_text())
            rows.append(
                {
                    "sample_id": sample_id,
                    "status": "completed",
                    "final": prior.get("b0_slot_id"),
                    "resumed_existing": True,
                }
            )
            continue
        if sample_id in args.skip_sample:
            failed += 1
            rows.append(
                {
                    "sample_id": sample_id,
                    "status": "failed",
                    "error_type": "OperatorSkip",
                    "message": "operator-declared skip on resume; prior attempt "
                    "exhausted validation retries and retry was not authorized",
                }
            )
            continue
        inputs = sample_dir / "inputs"
        binding = None
        budgeted_llms = []
        try:
            r3 = R3AssetManifest.model_validate_json(
                (inputs / "r3" / R3_MANIFEST_FILENAME).read_bytes()
            )
            pfull = PFullAssetManifest.model_validate_json(
                (inputs / "pfull" / ASSET_MANIFEST_FILENAME).read_bytes()
            )
            def _paid(action, stage):
                wrapped = A3BudgetedLLM(
                    action.llm,
                    budget=paid_budget,
                    stage=stage,
                    max_completion_tokens=authorization.stage_max_completion_tokens[
                        stage
                    ],
                )
                action.set_llm(wrapped, override=True)
                budgeted_llms.append(wrapped)
                return action

            binding = A3StageBinding(
                r3_manifest=r3,
                background_overview_path=inputs / "analyst_vision" / "background_overview.png",
                renders_dir=sample_dir / "renders",
                stages_dir=sample_dir / "stages",
                analyst_action=(
                    _paid(
                        AnalyzeA3TextOnly(expected_model=_expected("analyst")),
                        "analyst",
                    )
                    if args.analyst_arm == "text-only"
                    else _paid(
                        AnalyzeA3Brief(expected_model=_expected("analyst")),
                        "analyst",
                    )
                ),
                planner_action=_paid(
                    PlanAssetsA3(expected_model=_expected("asset_planner")),
                    "asset_planner",
                ),
                director_action=_paid(
                    ComposeConceptA3(
                        expected_model=_expected("composition_director")
                    ),
                    "composition_director",
                ),
                mapper_action=_paid(
                    GenerateLayoutA3(
                        expected_model=_expected("coordinate_mapper")
                    ),
                    "coordinate_mapper",
                ),
                judge_select_action=_paid(
                    JudgeSelectA3(expected_model=_expected("judge_select")),
                    "judge_select",
                ),
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
            async def _execute_and_close():
                try:
                    return await pipeline.run(
                        user_brief=build_prepared_input(pfull).user_brief,
                        tree_arm=args.tree_arm,
                        oracle_tree=oracle_trees.get(sample_id),
                    )
                finally:
                    await asyncio.gather(
                        *(llm.aclose() for llm in budgeted_llms),
                        return_exceptions=True,
                    )

            result = asyncio.run(_execute_and_close())
            consecutive_failures = 0
            rows.append(
                {
                    "sample_id": sample_id,
                    "status": "completed",
                    "final": getattr(result, "final_slot_id", result.b0_slot_id),
                    "stage_calls": len(binding.call_records),
                }
            )
        except A3AuthorizationCapReached:
            failed += 1
            stop_reason = "authorization_cap_reached"
            rows.append(
                {
                    "sample_id": sample_id,
                    "status": "failed",
                    "error_type": "A3AuthorizationCapReached",
                    "message": stop_reason,
                }
            )
            break
        except Exception as error:  # noqa: BLE001 -- failure must be persisted
            failed += 1
            consecutive_failures += 1
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
            if failed > 5 or consecutive_failures >= 3:
                stop_reason = (
                    "failed_sample_limit"
                    if failed > 5
                    else "consecutive_failure_limit"
                )
                break
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
            "analyst_arm": args.analyst_arm,
            "oracle_trees_from": (
                str(args.oracle_trees_from.resolve())
                if args.oracle_trees_from is not None
                else None
            ),
            "budget": budget,
            "paid_authorization": authorization.public_dict(),
            "paid_usage": paid_budget.snapshot(),
            "stop_reason": stop_reason,
            "total": len(sample_ids),
            "processed": len(rows),
            "failed": failed,
            "samples": rows,
        },
    )
    release_paid_run_lock(lock_descriptor)
    print(
        json.dumps(
            {
                "total": len(sample_ids),
                "processed": len(rows),
                "failed": failed,
                "stop_reason": stop_reason,
                "paid_usage": paid_budget.snapshot(),
            },
            indent=2,
        )
    )
    return 1 if failed or len(rows) != len(sample_ids) else 0


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
        "--oracle-trees-from",
        type=Path,
        help="Directory containing <sample_id>.json human-oracle trees; required for T3.",
    )
    run.add_argument(
        "--analyst-arm",
        choices=["vision", "text-only"],
        default="vision",
        help="Gate A ablation: 'text-only' swaps in AnalyzeA3TextOnly (no images).",
    )
    run.add_argument(
        "--allow-api-calls",
        action="store_true",
        help="Explicitly authorize paid model calls. Without it, only the call "
        "budget is printed and the command exits with status 2.",
    )
    run.add_argument(
        "--authorization-receipt",
        type=Path,
        help="Exact user authorization receipt; required with --allow-api-calls.",
    )
    run.add_argument(
        "--skip-sample",
        action="append",
        default=[],
        metavar="SAMPLE_ID",
        help="Operator-declared explicit skip on resume (e.g. a sample that "
        "exhausted validation retries in a prior attempt and must not be "
        "retried). Recorded as an explicit failed row, never silent.",
    )
    run.add_argument(
        "--resume-ledger",
        action="store_true",
        help="Resume an interrupted paid run against its existing append-only "
        "budget ledger. Prior spend keeps counting against the ORIGINAL "
        "authorization envelope; caps are never reset.",
    )
    tail = sub.add_parser(
        "run-l1-tail",
        help="Run only the L1 gated-revision tail, reusing R0 candidates and "
        "B0 selection from a completed L0 run (Gate C protocol).",
    )
    tail.add_argument("--run-dir", type=Path, required=True)
    tail.add_argument("--reuse-r0-from", type=Path, required=True)
    tail.add_argument("--tree-arm", choices=["T0", "T1", "T2", "T3"], default="T2")
    tail.add_argument("--allow-api-calls", action="store_true")
    snapshot = sub.add_parser(
        "snapshot-text-bitmaps",
        help="Stream the Crello dataset and write A3 text-bitmap sidecars "
        "(a3_text_bitmaps.json + raw-size PNGs) into cached sample dirs "
        "WITHOUT touching meta.json. No LLM calls; downloads dataset data.",
    )
    snapshot.add_argument("--ids", type=Path, required=True,
                          help="JSON array of sample IDs, or an object with an 'ids' key")
    snapshot.add_argument("--crello-root", type=Path, required=True)
    snapshot.add_argument("--hf-dataset", default="cyberagent/crello")
    snapshot.add_argument("--split", default="test")
    annotation = sub.add_parser(
        "prepare-annotation",
        help="Build self-contained human-annotation packets (packet + empty "
        "form + contact sheets, WITHOUT the background) for a prepared run.",
    )
    annotation.add_argument("--run-dir", type=Path, required=True)
    adjudication = sub.add_parser(
        "prepare-adjudication",
        help="Build a zero-cost, write-once human adjudication queue from "
        "independent annotation_*.json files. Never chooses a winner.",
    )
    adjudication.add_argument("--run-dir", type=Path, required=True)
    finalize = sub.add_parser(
        "finalize-adjudication",
        help="Validate completed human adjudication forms all-or-nothing and "
        "publish write-once T3 oracle trees. No API calls.",
    )
    finalize.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "prepare-annotation":
        return _command_prepare_annotation(args)
    if args.command == "prepare-adjudication":
        return _command_prepare_adjudication(args)
    if args.command == "finalize-adjudication":
        return _command_finalize_adjudication(args)

    if args.command == "run":
        return _command_run(args)
    if args.command == "run-l1-tail":
        return _command_run_l1_tail(args)
    if args.command == "snapshot-text-bitmaps":
        return _command_snapshot_text_bitmaps(args)

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
