"""Offline guard tests for the paid General-N100 COLE runner.

All provider objects are fakes, and an autouse fixture makes network and real
client construction fail immediately.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import socket
import stat
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

import layout_agent.judge_a3_general_cole as runner


@pytest.fixture(autouse=True)
def _forbid_real_provider(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("network/provider access is forbidden in this module")

    monkeypatch.setattr(runner.s21, "_load_openai_client", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def _png_bytes(size=(32, 24), color=(10, 20, 30)) -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, format="PNG")
    return stream.getvalue()


def _task(tmp_path: Path, value: bytes | None = None) -> tuple[dict, bytes]:
    value = value or _png_bytes()
    path = tmp_path / "input.png"
    path.write_bytes(value)
    return (
        {
            "arm": "general",
            "sample_id": "sample-000",
            "path": path,
            "sha256": hashlib.sha256(value).hexdigest(),
            "mime": "image/png",
        },
        value,
    )


def _usage_response(content: str, input_tokens=20, output_tokens=10):
    return SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
        ),
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
    )


class _FakeCompletions:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    async def create(self, **request):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _fake_client(*outcomes):
    completions = _FakeCompletions(outcomes)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


def _zero_prior_budget(**overrides) -> runner.Budget:
    values = {
        "call_cap": 20,
        "input_token_cap": 1_000_000,
        "output_token_cap": 20_000,
        "usd_cap": 100.0,
        "prior_calls": 0,
        "prior_input_tokens": 0,
        "prior_output_tokens": 0,
    }
    values.update(overrides)
    return runner.Budget(**values)


def test_paid_run_lock_is_nonblocking_private_and_never_unlinked(
    tmp_path, monkeypatch
):
    lock_path = tmp_path / "stable-paid.lock"
    monkeypatch.setattr(runner, "PAID_RUN_LOCK", lock_path)

    with runner._paid_run_lock():
        assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
        with pytest.raises(runner.PaidRunLockError, match="paid_run_lock_contended"):
            with runner._paid_run_lock():
                pass

    assert lock_path.is_file()
    with runner._paid_run_lock():
        assert lock_path.is_file()

    with pytest.raises(RuntimeError, match="synthetic lock-body failure"):
        with runner._paid_run_lock():
            raise RuntimeError("synthetic lock-body failure")
    with runner._paid_run_lock():
        assert lock_path.is_file()


def test_offline_preflight_main_never_acquires_paid_lock(monkeypatch):
    calls = []

    def lock_forbidden():
        raise AssertionError("offline preflight must not acquire paid lock")

    monkeypatch.setattr(sys, "argv", ["judge_a3_general_cole.py", "--preflight"])
    monkeypatch.setattr(
        runner, "_load_tasks", lambda: ([], [], "synthetic-snapshot")
    )
    monkeypatch.setattr(
        runner,
        "_preflight",
        lambda tasks, snapshot: calls.append((tasks, snapshot)),
    )
    monkeypatch.setattr(runner, "_paid_run_lock", lock_forbidden)

    assert runner.main() == 0
    assert calls == [([], "synthetic-snapshot")]


def test_concurrent_reservations_cannot_oversubscribe_any_cap():
    budget = _zero_prior_budget(
        call_cap=8,
        input_token_cap=800,
        output_token_cap=80,
        usd_cap=1.0,
    )

    with ThreadPoolExecutor(max_workers=16) as executor:
        reservations = list(
            executor.map(lambda _index: budget.reserve(100, 10), range(16))
        )

    accepted = [reservation for reservation in reservations if reservation is not None]
    assert len(accepted) == 8
    state = budget.as_dict()
    assert state["reserved_calls"] == 8
    assert state["reserved_input_tokens"] == 800
    assert state["reserved_output_tokens"] == 80
    assert budget.reserve(1, 1) is None

    for reservation in accepted:
        budget.settle_failure(reservation)
    assert budget.as_dict()["calls"] == 8
    assert budget.as_dict()["input_tokens"] == 800
    assert budget.as_dict()["output_tokens"] == 80


def test_concurrent_reservations_cannot_oversubscribe_usd_cap():
    unit_usd = runner.Budget._usd(100, 10)
    budget = _zero_prior_budget(
        call_cap=20,
        input_token_cap=10_000,
        output_token_cap=1_000,
        usd_cap=unit_usd * 3.5,
    )

    with ThreadPoolExecutor(max_workers=10) as executor:
        reservations = list(
            executor.map(lambda _index: budget.reserve(100, 10), range(10))
        )

    accepted = [reservation for reservation in reservations if reservation]
    assert len(accepted) == 3
    state = budget.as_dict()
    assert state["reserved_estimated_usd"] <= budget.usd_cap
    assert budget.reserve(100, 10) is None


def test_budget_preserves_historical_reserves_and_settles_actual_usage():
    historical = runner.Budget().as_dict()
    assert historical["calls"] == runner.PRIOR_INTERRUPTED_CALLS
    assert historical["input_tokens"] == runner.PRIOR_INPUT_TOKEN_RESERVE
    assert historical["output_tokens"] == runner.PRIOR_OUTPUT_TOKEN_RESERVE

    budget = _zero_prior_budget()
    reservation = budget.reserve(1_000, 600)
    usage = budget.settle_response(
        reservation, _usage_response("ok", input_tokens=17, output_tokens=3)
    )
    assert usage == {
        "input_tokens": 17,
        "output_tokens": 3,
        "usage_reported": True,
        "conservative": False,
        "reservation_bound_exceeded": False,
    }
    assert budget.as_dict()["input_tokens"] == 17
    assert budget.as_dict()["output_tokens"] == 3


def test_missing_usage_retains_full_conservative_charge():
    budget = _zero_prior_budget()
    reservation = budget.reserve(321, 123)
    usage = budget.settle_response(
        reservation,
        SimpleNamespace(usage=None, choices=[]),
    )
    assert usage["conservative"] is True
    assert usage["usage_reported"] is False
    assert budget.as_dict()["input_tokens"] == 321
    assert budget.as_dict()["output_tokens"] == 123
    assert budget.as_dict()["reserved_calls"] == 0


def test_missing_usage_response_never_becomes_a_score(tmp_path, monkeypatch):
    async def no_sleep(_seconds):
        return None

    task, _value = _task(tmp_path)
    runner._pin_verified_inputs([task])
    monkeypatch.setattr(runner, "REPO", tmp_path)
    monkeypatch.setattr(runner.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(
        runner.s21,
        "_parse_cole_json",
        lambda _text: {"SDL": 5, "SQL": 5, "STV": 5, "SGI": 5, "SIO": 5},
    )
    missing_usage = SimpleNamespace(
        usage=None,
        choices=[SimpleNamespace(message=SimpleNamespace(content="valid"))],
    )
    client, _completions = _fake_client(missing_usage, missing_usage)
    budget = _zero_prior_budget()

    row = asyncio.run(
        runner._score_task(
            client,
            task,
            {"max_completion_tokens": runner.MAX_COMPLETION_TOKENS},
            budget,
        )
    )

    assert row["status"] == "failed"
    assert row["error_code"] == "provider_usage_missing"
    assert budget.as_dict()["calls"] == 2
    assert budget.as_dict()["usage_reported_calls"] == 0


def test_request_bound_uses_exact_payload_and_verified_image_tiles(tmp_path):
    task, _value = _task(tmp_path, _png_bytes((3000, 2000)))
    runner._pin_verified_inputs([task])
    messages = runner._score_messages(task)
    kwargs = {"max_completion_tokens": runner.MAX_COMPLETION_TOKENS}
    bound = runner._request_input_token_bound(
        messages, kwargs, task[runner.PINNED_SIZE_KEY]
    )
    no_image_bound = runner._request_input_token_bound(messages, kwargs)
    tile_bound = (
        runner.VISION_BASE_TOKEN_BOUND
        + runner.VISION_TILE_TOKEN_BOUND
        * runner._normalized_vision_tiles((3000, 2000))
    )
    assert bound == no_image_bound + tile_bound
    assert no_image_bound > len(runner.s21.COLE_PROMPT.encode("utf-8"))


def test_malformed_image_bytes_fail_before_reservation_or_fake_create(tmp_path):
    task, _value = _task(tmp_path, b"not-an-image")
    client, completions = _fake_client()
    budget = _zero_prior_budget()

    with pytest.raises(RuntimeError, match="invalid judge input image"):
        runner._pin_verified_inputs([task])

    assert client.chat.completions is completions
    assert completions.requests == []
    assert budget.as_dict()["calls"] == 0
    assert budget.as_dict()["reserved_calls"] == 0
    assert budget.has_active_reservations() is False


def test_invalid_image_dimensions_fail_before_reservation_or_fake_create(
    tmp_path, monkeypatch
):
    class ZeroWidthImage:
        size = (0, 10)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def verify(self):
            return None

    task, _value = _task(tmp_path)
    client, completions = _fake_client()
    budget = _zero_prior_budget()
    monkeypatch.setattr(
        runner.Image, "open", lambda _stream: ZeroWidthImage()
    )

    with pytest.raises(RuntimeError, match="invalid judge input dimensions"):
        runner._pin_verified_inputs([task])

    assert client.chat.completions is completions
    assert completions.requests == []
    assert budget.as_dict()["calls"] == 0
    assert budget.as_dict()["reserved_calls"] == 0
    assert budget.has_active_reservations() is False


def test_scoring_uses_preflight_bytes_and_postflight_rejects_mutation(
    tmp_path, monkeypatch
):
    task, original = _task(tmp_path)
    runner._pin_verified_inputs([task])
    task["path"].write_bytes(_png_bytes(color=(200, 10, 20)))
    monkeypatch.setattr(runner, "REPO", tmp_path)
    scores = {"SDL": 6, "SQL": 7, "STV": 8, "SGI": 5, "SIO": 9}
    monkeypatch.setattr(runner.s21, "_parse_cole_json", lambda _text: scores)
    client, completions = _fake_client(_usage_response("valid-json"))

    row = asyncio.run(
        runner._score_task(
            client,
            task,
            {"max_completion_tokens": runner.MAX_COMPLETION_TOKENS},
            _zero_prior_budget(),
        )
    )

    url = completions.requests[0]["messages"][0]["content"][1]["image_url"]["url"]
    assert base64.b64decode(url.partition(",")[2]) == original
    assert row["status"] == "ok"
    assert runner.PINNED_BYTES_KEY not in row
    assert original not in json.dumps(row, sort_keys=True).encode()
    with pytest.raises(RuntimeError, match="changed during paid execution"):
        runner._verify_inputs_unchanged([task])


def test_secret_bearing_provider_exception_is_sanitized(tmp_path, monkeypatch):
    class SecretBearingError(Exception):
        pass

    async def no_sleep(_seconds):
        return None

    secret = "Authorization: Bearer sk-test-never-persist"
    task, _value = _task(tmp_path)
    runner._pin_verified_inputs([task])
    monkeypatch.setattr(runner, "REPO", tmp_path)
    monkeypatch.setattr(runner.asyncio, "sleep", no_sleep)
    client, _completions = _fake_client(
        SecretBearingError(secret), SecretBearingError(secret)
    )

    row = asyncio.run(
        runner._score_task(
            client,
            task,
            {"max_completion_tokens": runner.MAX_COMPLETION_TOKENS},
            _zero_prior_budget(),
        )
    )

    persisted = json.dumps(row, sort_keys=True)
    assert row["error_code"] == "provider_error"
    assert secret not in persisted
    assert "sk-test" not in persisted


def test_cancellation_conservatively_commits_in_flight_reservation(
    tmp_path, monkeypatch
):
    class BlockingCompletions:
        def __init__(self):
            self.started = None
            self.requests = []

        async def create(self, **request):
            self.requests.append(request)
            assert self.started is not None
            self.started.set()
            await asyncio.Future()

    task, _value = _task(tmp_path)
    runner._pin_verified_inputs([task])
    monkeypatch.setattr(runner, "REPO", tmp_path)
    kwargs = {"max_completion_tokens": runner.MAX_COMPLETION_TOKENS}
    messages = runner._score_messages(task)
    input_bound = runner._request_input_token_bound(
        messages, kwargs, task[runner.PINNED_SIZE_KEY]
    )
    budget = _zero_prior_budget(
        call_cap=2,
        input_token_cap=input_bound,
        output_token_cap=runner.MAX_COMPLETION_TOKENS,
    )
    completions = BlockingCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    async def cancel_in_flight():
        completions.started = asyncio.Event()
        scoring = asyncio.create_task(
            runner._score_task(client, task, kwargs, budget)
        )
        await completions.started.wait()
        scoring.cancel()
        with pytest.raises(asyncio.CancelledError):
            await scoring

    asyncio.run(cancel_in_flight())

    state = budget.as_dict()
    assert len(completions.requests) == 1
    assert state["calls"] == 1
    assert state["input_tokens"] == input_bound
    assert state["output_tokens"] == runner.MAX_COMPLETION_TOKENS
    assert state["usage_reported_calls"] == 0
    assert state["reserved_calls"] == 0
    assert state["reserved_input_tokens"] == 0
    assert state["reserved_output_tokens"] == 0
    assert budget.has_active_reservations() is False
    assert budget.reserve(1, 1) is None


def test_secret_bearing_unparseable_response_is_sanitized(tmp_path, monkeypatch):
    async def no_sleep(_seconds):
        return None

    secret = "Bearer sk-test-response-secret"
    task, _value = _task(tmp_path)
    runner._pin_verified_inputs([task])
    monkeypatch.setattr(runner, "REPO", tmp_path)
    monkeypatch.setattr(runner.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(runner.s21, "_parse_cole_json", lambda _text: None)
    client, _completions = _fake_client(
        _usage_response(secret), _usage_response(secret)
    )

    row = asyncio.run(
        runner._score_task(
            client,
            task,
            {"max_completion_tokens": runner.MAX_COMPLETION_TOKENS},
            _zero_prior_budget(),
        )
    )

    persisted = json.dumps(row, sort_keys=True)
    assert row["error_code"] == "response_unparseable"
    assert secret not in persisted
    assert "sk-test" not in persisted


def test_probe_failure_commits_conservative_reservation_before_fallback(capsys):
    class BadRequestError(Exception):
        code = "unsupported_parameter"
        param = "max_tokens"

    secret = "Authorization Bearer sk-test-probe-secret"
    client, _completions = _fake_client(BadRequestError(secret))
    budget = _zero_prior_budget()
    legacy = {"temperature": 0.0, "max_tokens": runner.MAX_COMPLETION_TOKENS}
    messages = [{"role": "user", "content": "Reply with the word ok."}]
    expected_input = runner._request_input_token_bound(messages, legacy)
    expected_usd = runner.Budget._usd(
        expected_input, runner.MAX_COMPLETION_TOKENS
    )

    kwargs = asyncio.run(_resolve(client, budget))

    assert kwargs == {"max_completion_tokens": runner.MAX_COMPLETION_TOKENS}
    assert secret not in capsys.readouterr().out
    state = budget.as_dict()
    assert state["calls"] == 1
    assert state["input_tokens"] == expected_input
    assert state["output_tokens"] == runner.MAX_COMPLETION_TOKENS
    assert state["usage_reported_calls"] == 0
    assert state["estimated_usd"] == round(expected_usd, 6)
    assert state["reserved_calls"] == 0
    assert state["reserved_input_tokens"] == 0
    assert state["reserved_output_tokens"] == 0
    assert state["reserved_estimated_usd"] == 0
    assert budget.has_active_reservations() is False


async def _resolve(client, budget):
    return await runner._resolve_params(client, budget)


def _complete_rows(ids):
    scores = {"SDL": 5, "SQL": 5, "STV": 5, "SGI": 5, "SIO": 5}
    return [
        {
            "arm": arm,
            "sample_id": sample_id,
            "status": "ok",
            "scores": scores,
            "smean4": 5.0,
        }
        for arm in ("general", "gt")
        for sample_id in ids
    ]


@pytest.mark.parametrize(
    "bad_status", ["failed", "aborted", "authorization_cap_reached"]
)
def test_incomplete_or_cap_rows_never_create_staging_or_final(
    tmp_path, monkeypatch, bad_status
):
    ids = [f"sample-{index:03d}" for index in range(100)]
    rows = _complete_rows(ids)
    rows[37] = {**rows[37], "status": bad_status}
    monkeypatch.setattr(runner, "OUT_ROOT", tmp_path / "cole")

    with pytest.raises(RuntimeError, match="all 200 scoring rows"):
        runner._publish(rows, _zero_prior_budget(), 1.0, ids, "snapshot")

    final, staging = runner._target_paths()
    assert not final.exists()
    assert not staging.exists()


def test_publication_contract_rejects_duplicate_or_reordered_pairs():
    ids = [f"sample-{index:03d}" for index in range(100)]
    rows = _complete_rows(ids)
    runner._validate_complete_rows(rows, ids)

    duplicate = list(rows)
    duplicate[1] = duplicate[0]
    with pytest.raises(RuntimeError, match="unique arm/sample pairs"):
        runner._validate_complete_rows(duplicate, ids)

    reordered = list(rows)
    reordered[0], reordered[1] = reordered[1], reordered[0]
    with pytest.raises(RuntimeError, match="IDs or order"):
        runner._validate_complete_rows(reordered, ids)
