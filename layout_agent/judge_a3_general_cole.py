"""Paid blind COLE judge for A3 Crello-General N=100 versus designer GT.

The runner consumes only the pinned B0 renders from the formal General SEGA
sidecar and the same sample IDs' Crello ground-truth previews. It is
write-once, performs a full hash preflight both before and after API calls, and
requires an explicit ``--allow-api-calls`` flag.

Authorized 2026-07-12 contract: evaluation ``a3-general-n100-cole-v1``;
judge ``gpt-5.4-mini-2026-03-17``; 220 calls including probe/retries;
3,000,000 input tokens; 150,000 output tokens; US$4.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import fcntl
import hashlib
import io
import json
import math
import os
import statistics
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from PIL import Image

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "layout_agent"))
import judge_a3_cole as relation_judge  # noqa: E402

s21 = relation_judge.s21

MODEL = "gpt-5.4-mini-2026-03-17"
RUN_ID = "a3-general-n100-t2-l0-01"
SOURCE_SIDECAR_ID = "a3-general-n100-sega-v1"
SIDECAR = REPO / (
    "layout_agent/evaluations/a3-sega/a3.sega-pku-protocol.v1/"
    + SOURCE_SIDECAR_ID
)
RUNS_ROOT = REPO / "layout_agent/runs/a3"
GT_ROOT = REPO / "layout_agent/output"
OUT_ROOT = REPO / "layout_agent/evaluations/a3-cole/a3.cole-judge.v1"
EVALUATION_ID = "a3-general-n100-cole-v1"
PAID_RUN_LOCK = OUT_ROOT / f".{EVALUATION_ID}.paid-run.lock"

HARD_CALL_CAP = 220
INPUT_TOKEN_CAP = 3_000_000
OUTPUT_TOKEN_CAP = 150_000
USD_CAP = 4.0
# The first authorized launch was interrupted while its compatibility probe
# was waiting inside a network-isolated sandbox. Count it conservatively even
# though no response/usage telemetry was received, so a resumed run cannot
# exceed the user's aggregate authorization.
PRIOR_INTERRUPTED_CALLS = 1
PRIOR_INPUT_TOKEN_RESERVE = 1_000
PRIOR_OUTPUT_TOKEN_RESERVE = 600
INPUT_USD_PER_M = 0.75
OUTPUT_USD_PER_M = 4.50
MAX_COMPLETION_TOKENS = 600
CONCURRENCY = 8
PARSE_FAIL_WINDOW = 40
PARSE_FAIL_MAX_RATE = 0.05
EXPECTED_INPUT_SNAPSHOT_SHA256 = (
    "aa7c5b236bc8655bf182cfe8fc898266fbb8e136b30c3f8ae2e7e89bbcb5fa72"
)

# OpenAI vision inputs are transported as base64 data URLs but accounted as
# image tokens rather than tokenized base64 text. For high-detail accounting,
# normalize within 2048px and, when still larger, to a 768px short edge, then
# count 512px tiles. The 1,024-token base and per-tile bounds are deliberately
# much larger than the published high-detail accounting constants. The exact
# non-image request JSON is separately bounded at one token per UTF-8 byte plus
# 1,024 framing tokens. This stays conservative without pretending every
# transport byte is a text token (which would make the authorized 3M cap
# impossible for the already-pinned 31.7MB input set).
VISION_MAX_EDGE = 2048
VISION_SHORT_EDGE = 768
VISION_TILE_SIZE = 512
VISION_BASE_TOKEN_BOUND = 1_024
VISION_TILE_TOKEN_BOUND = 1_024
REQUEST_TEXT_TOKEN_MARGIN = 1_024
PINNED_BYTES_KEY = "_verified_input_bytes"
PINNED_SIZE_KEY = "_verified_image_size"

_SAFE_PROVIDER_ERROR_TYPES = {
    "APIConnectionError": "provider_connection_error",
    "APIError": "provider_api_error",
    "APITimeoutError": "provider_timeout",
    "AuthenticationError": "provider_authentication_error",
    "BadRequestError": "provider_bad_request",
    "ConnectionError": "provider_connection_error",
    "InternalServerError": "provider_internal_error",
    "PermissionDeniedError": "provider_permission_error",
    "RateLimitError": "provider_rate_limit",
    "TimeoutError": "provider_timeout",
}
_UNSUPPORTED_PARAMETER_NAMES = {"temperature", "max_tokens"}
_UNSUPPORTED_PARAMETER_CODES = {"unsupported_parameter"}


class PaidRunLockError(RuntimeError):
    """Raised when another paid instance already owns the stable lock."""


@contextmanager
def _paid_run_lock() -> Iterator[None]:
    """Acquire the stable paid-run lock without waiting and never unlink it."""

    PAID_RUN_LOCK.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(PAID_RUN_LOCK, flags, 0o600)
    except OSError:
        raise PaidRunLockError("paid_run_lock_open_failed") from None
    try:
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PaidRunLockError("paid_run_lock_contended") from None
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _pin_verified_inputs(tasks: Sequence[dict]) -> None:
    """Verify each path once and retain those exact bytes for paid requests."""

    for task in tasks:
        path = task["path"]
        if not path.is_file():
            raise RuntimeError(f"missing judge input: {path}")
        value = path.read_bytes()
        if _sha256_bytes(value) != task["sha256"]:
            raise RuntimeError(f"judge input SHA-256 mismatch: {path}")
        try:
            with Image.open(io.BytesIO(value)) as image:
                size = image.size
                image.verify()
        except Exception:
            raise RuntimeError(f"invalid judge input image: {path}") from None
        if size[0] <= 0 or size[1] <= 0:
            raise RuntimeError(f"invalid judge input dimensions: {path}")
        task[PINNED_BYTES_KEY] = value
        task[PINNED_SIZE_KEY] = size


def _verify_inputs_unchanged(tasks: Sequence[dict]) -> None:
    """Postflight check that disk still equals the exact preflight bytes."""

    for task in tasks:
        pinned = task.get(PINNED_BYTES_KEY)
        if not isinstance(pinned, bytes):
            raise RuntimeError("judge input was not pinned during preflight")
        path = task["path"]
        try:
            current = path.read_bytes()
        except OSError:
            raise RuntimeError(f"judge input disappeared during run: {path}") from None
        if current != pinned or _sha256_bytes(current) != task["sha256"]:
            raise RuntimeError(f"judge input changed during paid execution: {path}")


def _normalized_vision_tiles(size: Tuple[int, int]) -> int:
    """Return a conservative high-detail 512px tile count."""

    width, height = size
    scale = min(1.0, VISION_MAX_EDGE / max(width, height))
    width = max(1, math.ceil(width * scale))
    height = max(1, math.ceil(height * scale))
    if min(width, height) > VISION_SHORT_EDGE:
        scale = VISION_SHORT_EDGE / min(width, height)
        width = max(1, math.ceil(width * scale))
        height = max(1, math.ceil(height * scale))
    return math.ceil(width / VISION_TILE_SIZE) * math.ceil(
        height / VISION_TILE_SIZE
    )


def _without_image_payload(value):
    """Copy request data while replacing only base64 image payload bytes."""

    if isinstance(value, list):
        return [_without_image_payload(item) for item in value]
    if isinstance(value, dict):
        return {key: _without_image_payload(item) for key, item in value.items()}
    if isinstance(value, str) and value.startswith("data:") and ";base64," in value:
        prefix, _separator, _payload = value.partition(",")
        return f"{prefix},<verified-image-bytes>"
    return value


def _request_input_token_bound(
    messages: Sequence[dict],
    kwargs: dict,
    image_size: Optional[Tuple[int, int]] = None,
) -> int:
    """Bound input tokens from exact non-image JSON and verified image size."""

    request = {
        "model": MODEL,
        "messages": _without_image_payload(list(messages)),
        **kwargs,
    }
    text_bytes = len(
        json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    image_bound = 0
    if image_size is not None:
        image_bound = VISION_BASE_TOKEN_BOUND + (
            VISION_TILE_TOKEN_BOUND * _normalized_vision_tiles(image_size)
        )
    return text_bytes + REQUEST_TEXT_TOKEN_MARGIN + image_bound


def _structured_error_value(error: Exception, field: str):
    """Read structured provider metadata without ever stringifying it."""

    try:
        direct = getattr(error, field, None)
        body = getattr(error, "body", None)
    except Exception:
        return None
    if direct is not None:
        return direct
    if isinstance(body, dict):
        value = body.get(field)
        nested = body.get("error")
        if value is None and isinstance(nested, dict):
            value = nested.get(field)
        return value
    return None


def _is_unsupported_parameter(error: Exception) -> bool:
    param = _structured_error_value(error, "param")
    code = _structured_error_value(error, "code")
    return (
        isinstance(param, str)
        and isinstance(code, str)
        and param in _UNSUPPORTED_PARAMETER_NAMES
        and code in _UNSUPPORTED_PARAMETER_CODES
    )


def _safe_provider_error_code(error: Exception) -> str:
    if _is_unsupported_parameter(error):
        return "provider_unsupported_parameter"
    return _SAFE_PROVIDER_ERROR_TYPES.get(type(error).__name__, "provider_error")


def _load_tasks() -> Tuple[List[dict], List[str], str]:
    manifest = json.loads((SIDECAR / "evaluation_manifest.json").read_text())
    matched_ids = manifest["matched_samples"]["ordered_sample_ids"]
    if len(matched_ids) != 100 or len(set(matched_ids)) != 100:
        raise RuntimeError("formal sidecar must contain exactly 100 unique IDs")

    records = [
        json.loads(line)
        for line in (SIDECAR / "per_sample.jsonl").read_text().splitlines()
        if line.strip()
    ]
    evaluated = [record for record in records if record["status"] == "evaluated"]
    if len(evaluated) != 100 or any(r["run_id"] != RUN_ID for r in evaluated):
        raise RuntimeError("formal sidecar must contain 100 evaluated General rows")
    if [r["sample_id"] for r in evaluated] != matched_ids:
        raise RuntimeError("formal sidecar row order differs from matched ID snapshot")

    tasks: List[dict] = []
    b0_hashes: List[str] = []
    for record in evaluated:
        path = (
            RUNS_ROOT
            / RUN_ID
            / "samples"
            / record["sample_id"]
            / "renders"
            / f"{record['b0_slot_id']}.png"
        )
        b0_hashes.append(record["b0_render_sha256"])
        tasks.append(
            {
                "arm": "general",
                "sample_id": record["sample_id"],
                "path": path,
                "sha256": record["b0_render_sha256"],
                "mime": "image/png",
            }
        )

    gt_hashes: List[str] = []
    for sample_id in matched_ids:
        path = GT_ROOT / f"crello_{sample_id}" / "ground_truth_preview.jpg"
        if not path.is_file():
            raise RuntimeError(f"missing GT preview: {path}")
        digest = _sha256(path)
        gt_hashes.append(digest)
        tasks.append(
            {
                "arm": "gt",
                "sample_id": sample_id,
                "path": path,
                "sha256": digest,
                "mime": "image/jpeg",
            }
        )

    payload = "\n".join(matched_ids + b0_hashes + gt_hashes).encode()
    return tasks, matched_ids, hashlib.sha256(payload).hexdigest()


def _target_paths() -> Tuple[Path, Path]:
    final = OUT_ROOT / EVALUATION_ID
    return final, OUT_ROOT / f".staging-{EVALUATION_ID}"


def _preflight(tasks: Sequence[dict], snapshot_sha256: str) -> None:
    if len(tasks) != 200:
        raise RuntimeError(f"expected 200 blind scoring tasks, got {len(tasks)}")
    final, staging = _target_paths()
    if final.exists() or staging.exists():
        raise RuntimeError(f"write-once target already exists: {final} or {staging}")
    _pin_verified_inputs(tasks)
    if snapshot_sha256 != EXPECTED_INPUT_SNAPSHOT_SHA256:
        raise RuntimeError(
            f"input snapshot changed: {snapshot_sha256} != "
            f"{EXPECTED_INPUT_SNAPSHOT_SHA256}"
        )
    print(
        "[preflight OK] 100 pinned General B0 + 100 pinned GT; "
        f"snapshot={snapshot_sha256}; resumed nominal new calls=201, "
        f"aggregate including prior interrupted probe=202 <= cap {HARD_CALL_CAP}",
        flush=True,
    )


@dataclass(frozen=True)
class Reservation:
    reservation_id: int
    calls: int
    input_tokens: int
    output_tokens: int
    estimated_usd: float


class Budget:
    """Atomically reserve every cap before dispatch, then settle fail-closed."""

    def __init__(
        self,
        *,
        call_cap: int = HARD_CALL_CAP,
        input_token_cap: int = INPUT_TOKEN_CAP,
        output_token_cap: int = OUTPUT_TOKEN_CAP,
        usd_cap: float = USD_CAP,
        prior_calls: int = PRIOR_INTERRUPTED_CALLS,
        prior_input_tokens: int = PRIOR_INPUT_TOKEN_RESERVE,
        prior_output_tokens: int = PRIOR_OUTPUT_TOKEN_RESERVE,
    ) -> None:
        self.call_cap = call_cap
        self.input_token_cap = input_token_cap
        self.output_token_cap = output_token_cap
        self.usd_cap = usd_cap
        self.prior_calls = prior_calls
        self.prior_input_tokens = prior_input_tokens
        self.prior_output_tokens = prior_output_tokens
        self.calls = prior_calls
        self.input_tokens = prior_input_tokens
        self.output_tokens = prior_output_tokens
        self.usage_reported_calls = 0
        self._reserved_calls = 0
        self._reserved_input_tokens = 0
        self._reserved_output_tokens = 0
        self._next_reservation_id = 1
        self._active: Dict[int, Reservation] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _usd(input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * INPUT_USD_PER_M
            + output_tokens * OUTPUT_USD_PER_M
        ) / 1_000_000

    @property
    def estimated_usd(self) -> float:
        with self._lock:
            return self._usd(self.input_tokens, self.output_tokens)

    def reserve(
        self, input_tokens: int, output_tokens: int = MAX_COMPLETION_TOKENS
    ) -> Optional[Reservation]:
        """Reserve one attempt atomically, or return None before dispatch."""

        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("reservation bounds must be non-negative")
        with self._lock:
            projected_calls = self.calls + self._reserved_calls + 1
            projected_input = (
                self.input_tokens + self._reserved_input_tokens + input_tokens
            )
            projected_output = (
                self.output_tokens + self._reserved_output_tokens + output_tokens
            )
            if (
                projected_calls > self.call_cap
                or projected_input > self.input_token_cap
                or projected_output > self.output_token_cap
                or self._usd(projected_input, projected_output) > self.usd_cap
            ):
                return None
            reservation = Reservation(
                reservation_id=self._next_reservation_id,
                calls=1,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_usd=self._usd(input_tokens, output_tokens),
            )
            self._next_reservation_id += 1
            self._active[reservation.reservation_id] = reservation
            self._reserved_calls += reservation.calls
            self._reserved_input_tokens += reservation.input_tokens
            self._reserved_output_tokens += reservation.output_tokens
            return reservation

    def _settle(
        self,
        reservation: Reservation,
        *,
        input_tokens: int,
        output_tokens: int,
        usage_reported: bool,
    ) -> dict:
        with self._lock:
            active = self._active.pop(reservation.reservation_id, None)
            if active != reservation:
                raise RuntimeError("invalid_or_already_settled_reservation")
            self._reserved_calls -= reservation.calls
            self._reserved_input_tokens -= reservation.input_tokens
            self._reserved_output_tokens -= reservation.output_tokens
            self.calls += reservation.calls
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens
            if usage_reported:
                self.usage_reported_calls += 1
            bound_exceeded = (
                input_tokens > reservation.input_tokens
                or output_tokens > reservation.output_tokens
            )
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "usage_reported": usage_reported,
            "conservative": not usage_reported,
            "reservation_bound_exceeded": bound_exceeded,
        }

    @staticmethod
    def _usage_value(usage, primary: str, fallback: str) -> Optional[int]:
        try:
            value = getattr(usage, primary, None)
            if value is None:
                value = getattr(usage, fallback, None)
        except Exception:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value if value >= 0 else None

    def settle_response(self, reservation: Reservation, response) -> dict:
        try:
            usage = getattr(response, "usage", None)
        except Exception:
            usage = None
        if usage is None:
            return self.settle_failure(reservation)
        input_tokens = self._usage_value(usage, "prompt_tokens", "input_tokens")
        output_tokens = self._usage_value(
            usage, "completion_tokens", "output_tokens"
        )
        if input_tokens is None or output_tokens is None:
            return self.settle_failure(reservation)
        return self._settle(
            reservation,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            usage_reported=True,
        )

    def settle_failure(self, reservation: Reservation) -> dict:
        """Convert an uncertain in-flight reservation to a conservative charge."""

        return self._settle(
            reservation,
            input_tokens=reservation.input_tokens,
            output_tokens=reservation.output_tokens,
            usage_reported=False,
        )

    def exceeded(self) -> bool:
        with self._lock:
            projected_input = self.input_tokens + self._reserved_input_tokens
            projected_output = self.output_tokens + self._reserved_output_tokens
            return (
                self.calls + self._reserved_calls > self.call_cap
                or projected_input > self.input_token_cap
                or projected_output > self.output_token_cap
                or self._usd(projected_input, projected_output) > self.usd_cap
            )

    def has_active_reservations(self) -> bool:
        with self._lock:
            return bool(self._active)

    def as_dict(self) -> dict:
        with self._lock:
            reserved_usd = self._usd(
                self._reserved_input_tokens, self._reserved_output_tokens
            )
            return {
                "calls": self.calls,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "usage_reported_calls": self.usage_reported_calls,
                "estimated_usd": round(
                    self._usd(self.input_tokens, self.output_tokens), 6
                ),
                "reserved_calls": self._reserved_calls,
                "reserved_input_tokens": self._reserved_input_tokens,
                "reserved_output_tokens": self._reserved_output_tokens,
                "reserved_estimated_usd": round(reserved_usd, 6),
                "prior_interrupted_calls": self.prior_calls,
                "prior_input_token_reserve": self.prior_input_tokens,
                "prior_output_token_reserve": self.prior_output_tokens,
            }


async def _resolve_params(client, budget: Budget) -> dict:
    legacy = {"temperature": 0.0, "max_tokens": MAX_COMPLETION_TOKENS}
    modern = {"max_completion_tokens": MAX_COMPLETION_TOKENS}
    messages = [{"role": "user", "content": "Reply with the word ok."}]
    reservation = budget.reserve(
        _request_input_token_bound(messages, legacy), MAX_COMPLETION_TOKENS
    )
    if reservation is None:
        raise RuntimeError("authorization cap exhausted before parameter probe")
    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=messages,
            **legacy,
        )
    except asyncio.CancelledError:
        budget.settle_failure(reservation)
        raise
    except Exception as error:
        budget.settle_failure(reservation)
        if _is_unsupported_parameter(error):
            print("[probe] param variant=modern", flush=True)
            return modern
        code = _safe_provider_error_code(error)
        raise RuntimeError(f"parameter_probe_failed:{code}") from None
    usage = budget.settle_response(reservation, response)
    if not usage["usage_reported"]:
        raise RuntimeError("parameter_probe_failed:provider_usage_missing")
    if usage["reservation_bound_exceeded"] or budget.exceeded():
        raise RuntimeError("parameter_probe_failed:authorization_cap_exceeded")
    print("[probe] param variant=legacy", flush=True)
    return legacy


def _score_messages(task: dict) -> List[dict]:
    pinned = task.get(PINNED_BYTES_KEY)
    if not isinstance(pinned, bytes):
        raise RuntimeError("judge input was not pinned during preflight")
    encoded = base64.b64encode(pinned).decode("ascii")
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": s21.COLE_PROMPT},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{task['mime']};base64,{encoded}",
                        "detail": "high",
                    },
                },
            ],
        }
    ]


def _base_row(task: dict) -> dict:
    return {
        "arm": task["arm"],
        "sample_id": task["sample_id"],
        "path": str(task["path"].relative_to(REPO)),
        "render_sha256": task["sha256"],
    }


async def _score_task(client, task: dict, kwargs: dict, budget: Budget) -> dict:
    messages = _score_messages(task)
    image_size = task.get(PINNED_SIZE_KEY)
    if not (
        isinstance(image_size, tuple)
        and len(image_size) == 2
        and all(isinstance(value, int) for value in image_size)
    ):
        raise RuntimeError("judge input dimensions were not pinned during preflight")
    input_bound = _request_input_token_bound(messages, kwargs, image_size)
    last_error_code = "no_attempt"
    for attempt in range(2):
        reservation = budget.reserve(input_bound, MAX_COMPLETION_TOKENS)
        if reservation is None:
            return {**_base_row(task), "status": "authorization_cap_reached"}
        try:
            response = await client.chat.completions.create(
                model=MODEL,
                messages=messages,
                **kwargs,
            )
        except asyncio.CancelledError:
            budget.settle_failure(reservation)
            raise
        except Exception as error:  # noqa: BLE001
            usage = budget.settle_failure(reservation)
            last_error_code = _safe_provider_error_code(error)
        else:
            usage = budget.settle_response(reservation, response)
            if not usage["usage_reported"]:
                last_error_code = "provider_usage_missing"
            elif usage["reservation_bound_exceeded"] or budget.exceeded():
                return {
                    **_base_row(task),
                    "status": "authorization_cap_exceeded",
                    "usage": usage,
                }
            else:
                try:
                    text = (response.choices[0].message.content or "").strip()
                    parsed = s21._parse_cole_json(text)
                except Exception:  # response contents must never enter errors
                    parsed = None
                if parsed is not None:
                    return {
                        **_base_row(task),
                        "status": "ok",
                        "scores": parsed,
                        "smean4": statistics.mean(
                            parsed[key] for key in s21.REPORT_AXES
                        ),
                        "usage": usage,
                    }
                last_error_code = "response_unparseable"
        if attempt == 0:
            await asyncio.sleep(1.5)
    return {
        **_base_row(task),
        "status": "failed",
        "error_code": last_error_code,
    }


async def _run_paid(tasks: List[dict]) -> Tuple[List[dict], Budget, bool]:
    try:
        client = s21._load_openai_client()
    except Exception as error:
        code = _safe_provider_error_code(error)
        raise RuntimeError(f"provider_client_load_failed:{code}") from None
    budget = Budget()
    kwargs = await _resolve_params(client, budget)
    semaphore = asyncio.Semaphore(CONCURRENCY)
    completed: List[dict] = []
    abort = asyncio.Event()

    async def worker(task: dict) -> dict:
        async with semaphore:
            if abort.is_set():
                return {**_base_row(task), "status": "aborted"}
            row = await _score_task(client, task, kwargs, budget)
            completed.append(row)
            done_n = len(completed)
            if row["status"].startswith("authorization_cap"):
                abort.set()
            if done_n == PARSE_FAIL_WINDOW:
                failures = sum(r["status"] != "ok" for r in completed)
                if failures / done_n > PARSE_FAIL_MAX_RATE:
                    print(
                        f"[ABORT] first-window failures={failures}/{done_n}",
                        flush=True,
                    )
                    abort.set()
            if done_n % 25 == 0 or done_n == len(tasks):
                ok_n = sum(r["status"] == "ok" for r in completed)
                print(
                    f"[progress] {done_n}/{len(tasks)} scored, ok={ok_n}, "
                    f"budget={budget.as_dict()}",
                    flush=True,
                )
            return row

    rows = list(await asyncio.gather(*(worker(task) for task in tasks)))
    if budget.has_active_reservations():
        raise RuntimeError("active_budget_reservations_after_gather")
    return rows, budget, abort.is_set()


def _arm_means(rows: Sequence[dict]) -> dict:
    output: Dict[str, dict] = {}
    for arm in ("general", "gt"):
        ok = [row for row in rows if row["arm"] == arm and row["status"] == "ok"]
        if not ok:
            output[arm] = {"n": 0}
            continue
        output[arm] = {
            "n": len(ok),
            "smean4": statistics.mean(row["smean4"] for row in ok),
            **{
                axis: statistics.mean(row["scores"][axis] for row in ok)
                for axis in ("SDL", "SQL", "STV", "SGI", "SIO")
            },
        }
    if output["general"].get("n") and output["gt"].get("n"):
        output["general"]["pct_of_gt_smean4"] = (
            100.0 * output["general"]["smean4"] / output["gt"]["smean4"]
        )
    return output


def _validate_complete_rows(rows: Sequence[dict], matched_ids: Sequence[str]) -> None:
    """Require the exact 100 General + 100 GT ordered publication contract."""

    if len(matched_ids) != 100 or len(set(matched_ids)) != 100:
        raise RuntimeError("publication requires exactly 100 unique matched IDs")
    if len(rows) != 200:
        raise RuntimeError("publication requires exactly 200 scoring rows")
    if any(row.get("status") != "ok" for row in rows):
        raise RuntimeError("publication requires all 200 scoring rows to be ok")
    try:
        actual_pairs = [(row["arm"], row["sample_id"]) for row in rows]
    except (KeyError, TypeError):
        raise RuntimeError("publication rows lack required identity fields") from None
    expected_pairs = [
        *(("general", sample_id) for sample_id in matched_ids),
        *(("gt", sample_id) for sample_id in matched_ids),
    ]
    if len(set(actual_pairs)) != 200:
        raise RuntimeError("publication requires unique arm/sample pairs")
    if actual_pairs != expected_pairs:
        raise RuntimeError("publication arm/sample IDs or order differ from snapshot")


def _paired(rows: Sequence[dict]) -> dict:
    by_arm: Dict[str, Dict[str, dict]] = {}
    for row in rows:
        if row["status"] == "ok":
            by_arm.setdefault(row["arm"], {})[row["sample_id"]] = row
    common = sorted(set(by_arm.get("general", {})) & set(by_arm.get("gt", {})))
    deltas = [
        by_arm["general"][sample_id]["smean4"]
        - by_arm["gt"][sample_id]["smean4"]
        for sample_id in common
    ]
    wins = sum(delta > 0 for delta in deltas)
    losses = sum(delta < 0 for delta in deltas)
    per_axis = {}
    for axis in s21.REPORT_AXES:
        axis_wins = sum(
            by_arm["general"][sid]["scores"][axis]
            > by_arm["gt"][sid]["scores"][axis]
            for sid in common
        )
        axis_losses = sum(
            by_arm["general"][sid]["scores"][axis]
            < by_arm["gt"][sid]["scores"][axis]
            for sid in common
        )
        per_axis[axis] = {
            "wins": axis_wins,
            "losses": axis_losses,
            "ties": len(common) - axis_wins - axis_losses,
            "sign_p": relation_judge._sign_test_p(axis_wins, axis_losses),
        }
    return {
        "general_vs_gt": {
            "n_pairs": len(common),
            "smean4": {
                "wins": wins,
                "losses": losses,
                "ties": len(common) - wins - losses,
                "sign_p": relation_judge._sign_test_p(wins, losses),
                "bootstrap": relation_judge._bootstrap_ci(deltas),
            },
            "per_axis": per_axis,
        }
    }


def _publish(
    rows: List[dict],
    budget: Budget,
    wall_seconds: float,
    matched_ids: List[str],
    snapshot_sha256: str,
) -> Path:
    _validate_complete_rows(rows, matched_ids)
    if budget.exceeded() or budget.has_active_reservations():
        raise RuntimeError("publication requires a settled in-cap budget")
    final, staging = _target_paths()
    if final.exists() or staging.exists():
        raise RuntimeError("write-once target appeared before publication")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    staging.mkdir(parents=False, exist_ok=False)
    try:
        (staging / "per_sample.jsonl").write_text(
            "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n"
        )
        aggregate = {
            "schema_version": "a3.cole-judge.v1",
            "evaluation_id": EVALUATION_ID,
            "judge_model": MODEL,
            "prompt_sha256": hashlib.sha256(s21.COLE_PROMPT.encode()).hexdigest(),
            "source_sidecar": SOURCE_SIDECAR_ID,
            "input_snapshot_sha256": snapshot_sha256,
            "matched_ids_count": len(matched_ids),
            "authorization": {
                "call_cap": HARD_CALL_CAP,
                "input_token_cap": INPUT_TOKEN_CAP,
                "output_token_cap": OUTPUT_TOKEN_CAP,
                "usd_cap": USD_CAP,
            },
            "usage": budget.as_dict(),
            "wall_seconds": round(wall_seconds, 2),
            "status_counts": {
                status: sum(row["status"] == status for row in rows)
                for status in sorted({row["status"] for row in rows})
            },
            "arm_means": _arm_means(rows),
            "paired": _paired(rows),
        }
        (staging / "aggregate.json").write_text(
            json.dumps(aggregate, indent=2, sort_keys=True, allow_nan=False) + "\n"
        )
        os.rename(staging, final)
    except Exception:
        if staging.exists():
            for child in staging.iterdir():
                child.unlink()
            staging.rmdir()
        raise
    return final


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true", help="zero-cost only")
    mode.add_argument("--allow-api-calls", action="store_true")
    args = parser.parse_args()

    if args.preflight:
        tasks, _matched_ids, snapshot_sha256 = _load_tasks()
        _preflight(tasks, snapshot_sha256)
        return 0

    with _paid_run_lock():
        tasks, matched_ids, snapshot_sha256 = _load_tasks()
        _preflight(tasks, snapshot_sha256)
        started = time.time()
        rows, budget, aborted = asyncio.run(_run_paid(tasks))
        wall_seconds = time.time() - started
        if aborted or budget.exceeded() or budget.has_active_reservations():
            raise RuntimeError(
                f"paid judge aborted without publication; budget={budget.as_dict()}"
            )
        _validate_complete_rows(rows, matched_ids)
        _verify_inputs_unchanged(tasks)
        tasks_after, ids_after, snapshot_after = _load_tasks()
        _preflight(tasks_after, snapshot_after)
        if ids_after != matched_ids or snapshot_after != snapshot_sha256:
            raise RuntimeError("judge inputs changed during paid execution")
        final = _publish(
            rows, budget, wall_seconds, matched_ids, snapshot_sha256
        )
    aggregate = json.loads((final / "aggregate.json").read_text())
    print(
        json.dumps(
            {
                "published": str(final.relative_to(REPO)),
                "wall_seconds": round(wall_seconds, 1),
                "status_counts": aggregate["status_counts"],
                "usage": aggregate["usage"],
                "arm_means": aggregate["arm_means"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
