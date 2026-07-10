from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from metagpt.ext.agentlayout.a3_config import A3RunConfig
from metagpt.ext.agentlayout.run_manifest import (
    A3RunStore,
    RUN_MANIFEST_SCHEMA_VERSION,
    capture_provenance,
    load_sample_ids,
    write_json_once,
)


def _config(loop: str = "L0") -> dict:
    stages = {
        name: {"model": "gpt-test-snapshot"}
        for name in (
            "analyst",
            "asset_planner",
            "composition_director",
            "coordinate_mapper",
            "judge_select",
        )
    }
    if loop == "L1-Gated":
        stages["judge_critic"] = {"model": "gpt-test-snapshot"}
    return {
        "loop": loop,
        "internal_judge": "gpt-test-snapshot",
        "dataset_split": "synthetic-test",
        "models": stages,
    }


def _inputs(tmp_path: Path):
    config = tmp_path / "config.json"
    ids = tmp_path / "ids.json"
    config.write_text(json.dumps(_config()))
    ids.write_text(json.dumps(["sample-a", "sample-b"]))
    return config, ids


def test_config_requires_all_l1_stages():
    bad = _config("L1-Gated")
    del bad["models"]["judge_critic"]
    with pytest.raises(ValueError, match="judge_critic"):
        A3RunConfig.model_validate(bad)


def test_sample_ids_reject_duplicates(tmp_path: Path):
    path = tmp_path / "ids.json"
    path.write_text(json.dumps(["same", "same"]))
    with pytest.raises(ValueError, match="duplicates"):
        load_sample_ids(path)


def test_sample_ids_reject_path_traversal(tmp_path: Path):
    path = tmp_path / "ids.json"
    path.write_text(json.dumps(["../escape"]))
    with pytest.raises(ValueError, match="unsafe"):
        load_sample_ids(path)


def test_write_json_once_refuses_overwrite(tmp_path: Path):
    target = tmp_path / "record.json"
    write_json_once(target, {"version": 1})
    with pytest.raises(FileExistsError):
        write_json_once(target, {"version": 2})
    assert json.loads(target.read_text()) == {"version": 1}


def test_create_run_writes_frozen_snapshots_and_sample_records(tmp_path: Path):
    config, ids = _inputs(tmp_path)
    store = A3RunStore.create(
        runs_root=tmp_path / "runs",
        run_id="a3-test-001",
        config_path=config,
        sample_ids_path=ids,
        repo_root=tmp_path,
    )
    manifest = store.manifest()
    assert manifest.schema_version == RUN_MANIFEST_SCHEMA_VERSION
    assert manifest.sample_ids_snapshot.count == 2
    assert manifest.completion == {
        "total": 2,
        "pending": 2,
        "completed": 0,
        "failed": 0,
        "skipped": 0,
    }
    assert (store.run_dir / "run_config.json").exists()
    assert (store.run_dir / "sample_ids.json").exists()
    assert (store.run_dir / "schemas" / "run_manifest.schema.json").exists()
    for sample_id in ("sample-a", "sample-b"):
        record = json.loads(
            (store.run_dir / "samples" / sample_id / "sample_record.json").read_text()
        )
        assert record["sample_id"] == sample_id
        assert record["status"] == "pending"


def test_create_run_refuses_reusing_run_id(tmp_path: Path):
    config, ids = _inputs(tmp_path)
    kwargs = dict(
        runs_root=tmp_path / "runs",
        run_id="a3-test-duplicate",
        config_path=config,
        sample_ids_path=ids,
        repo_root=tmp_path,
    )
    A3RunStore.create(**kwargs)
    with pytest.raises(FileExistsError):
        A3RunStore.create(**kwargs)


def test_provenance_hashes_untracked_contents(tmp_path: Path):
    # A non-git directory degrades safely instead of blocking manifest creation.
    result = capture_provenance(tmp_path)
    assert result["git"] == {"available": False}
    assert result["runtime"]["python"]


def test_provenance_records_untracked_content_hash(tmp_path: Path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    watched = tmp_path / "metagpt" / "ext" / "agentlayout"
    watched.mkdir(parents=True)
    source = watched / "new_module.py"
    source.write_text("VALUE = 1\n")

    result = capture_provenance(tmp_path)
    hashes = result["git"]["untracked_file_sha256"]
    assert hashes["metagpt/ext/agentlayout/new_module.py"]


def test_cli_plan_is_zero_cost_and_does_not_create_run(tmp_path: Path):
    config, ids = _inputs(tmp_path)
    runs = tmp_path / "runs"
    proc = subprocess.run(
        [
            sys.executable,
            "layout_agent/run_a3.py",
            "plan",
            "--config",
            str(config),
            "--sample-ids",
            str(ids),
            "--run-id",
            "a3-cli-plan",
            "--runs-root",
            str(runs),
        ],
        cwd=Path(__file__).resolve().parents[4],
        capture_output=True,
        text=True,
        check=True,
    )
    plan = json.loads(proc.stdout)
    assert plan["api_calls"] == 0
    assert plan["sample_count"] == 2
    assert not (runs / "a3-cli-plan").exists()
