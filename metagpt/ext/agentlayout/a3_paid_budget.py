"""Fail-closed paid-call budget enforcement for A3 generation."""
from __future__ import annotations

import asyncio
import base64
import fcntl
import hashlib
import io
import json
import math
import os
import threading
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, Mapping, Optional

from PIL import Image


AUTH_SCHEMA = "a3.paid-authorization.v1"
LEDGER_SCHEMA = "a3.paid-budget-ledger.v1"
INPUT_USD_PER_M = Decimal("0.75")
OUTPUT_USD_PER_M = Decimal("4.50")
IMAGE_PATCH_BUDGET = 1_536
IMAGE_TOKEN_MULTIPLIER = Decimal("1.62")
REQUEST_MARGIN_TOKENS = 1_024


class A3AuthorizationError(RuntimeError):
    pass


class A3AuthorizationCapReached(RuntimeError):
    pass


class A3ProviderCallError(RuntimeError):
    pass


@dataclass(frozen=True)
class A3PaidAuthorization:
    path: Path
    sha256: str
    run_id: str
    model: str
    tree_arm: str
    analyst_arm: str
    max_http_calls: int
    max_input_tokens: int
    max_output_tokens: int
    max_usd: Decimal
    stage_max_completion_tokens: Mapping[str, int]
    image_detail: str
    reasoning_effort: str
    service_tier: str

    def public_dict(self) -> dict:
        return {
            "schema_version": AUTH_SCHEMA,
            "authorization_sha256": self.sha256,
            "run_id": self.run_id,
            "model": self.model,
            "tree_arm": self.tree_arm,
            "analyst_arm": self.analyst_arm,
            "max_http_calls": self.max_http_calls,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "max_usd": str(self.max_usd),
            "stage_max_completion_tokens": dict(
                self.stage_max_completion_tokens
            ),
            "image_detail": self.image_detail,
            "reasoning_effort": self.reasoning_effort,
            "service_tier": self.service_tier,
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise A3AuthorizationError(f"invalid_{field}")
    return value


def load_authorization(
    path: Path,
    *,
    expected_run_id: str,
    expected_model: str,
    expected_tree_arm: str,
    expected_analyst_arm: str,
) -> A3PaidAuthorization:
    try:
        payload = json.loads(path.read_text())
    except Exception:
        raise A3AuthorizationError("authorization_receipt_unreadable") from None
    if not isinstance(payload, dict) or payload.get("schema_version") != AUTH_SCHEMA:
        raise A3AuthorizationError("authorization_schema_mismatch")
    if payload.get("authorized") is not True or payload.get("authorized_by") != "user":
        raise A3AuthorizationError("authorization_not_explicit")
    expected = {
        "run_id": expected_run_id,
        "model": expected_model,
        "tree_arm": expected_tree_arm,
        "analyst_arm": expected_analyst_arm,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise A3AuthorizationError(f"authorization_{key}_mismatch")
    limits = payload.get("limits")
    stage_caps = payload.get("stage_max_completion_tokens")
    if not isinstance(limits, dict) or not isinstance(stage_caps, dict):
        raise A3AuthorizationError("authorization_limits_missing")
    required_stages = {
        "analyst",
        "asset_planner",
        "composition_director",
        "coordinate_mapper",
        "judge_select",
    }
    if set(stage_caps) != required_stages:
        raise A3AuthorizationError("authorization_stage_caps_mismatch")
    parsed_stage_caps = {
        key: _positive_int(value, f"{key}_completion_cap")
        for key, value in stage_caps.items()
    }
    try:
        max_usd = Decimal(str(limits["max_usd"]))
    except Exception:
        raise A3AuthorizationError("invalid_max_usd") from None
    if max_usd <= 0:
        raise A3AuthorizationError("invalid_max_usd")
    if Decimal(str(payload.get("input_usd_per_m"))) != INPUT_USD_PER_M:
        raise A3AuthorizationError("input_price_mismatch")
    if Decimal(str(payload.get("output_usd_per_m"))) != OUTPUT_USD_PER_M:
        raise A3AuthorizationError("output_price_mismatch")
    if payload.get("image_detail") != "high":
        raise A3AuthorizationError("image_detail_mismatch")
    if payload.get("reasoning_effort") != "none":
        raise A3AuthorizationError("reasoning_effort_mismatch")
    if payload.get("service_tier") != "default":
        raise A3AuthorizationError("service_tier_mismatch")
    return A3PaidAuthorization(
        path=path,
        sha256=_sha256(path),
        run_id=expected_run_id,
        model=expected_model,
        tree_arm=expected_tree_arm,
        analyst_arm=expected_analyst_arm,
        max_http_calls=_positive_int(limits.get("max_http_calls"), "max_http_calls"),
        max_input_tokens=_positive_int(
            limits.get("max_input_tokens"), "max_input_tokens"
        ),
        max_output_tokens=_positive_int(
            limits.get("max_output_tokens"), "max_output_tokens"
        ),
        max_usd=max_usd,
        stage_max_completion_tokens=parsed_stage_caps,
        image_detail="high",
        reasoning_effort="none",
        service_tier="default",
    )


@dataclass(frozen=True)
class Reservation:
    reservation_id: int
    input_tokens: int
    output_tokens: int


class A3PaidBudget:
    """Reserve every cap before one HTTP call and settle fail-closed."""

    def __init__(
        self,
        authorization: A3PaidAuthorization,
        ledger_path: Path,
        *,
        code_paths: Iterable[Path] = (),
        resume: bool = False,
    ) -> None:
        self.authorization = authorization
        self.ledger_path = ledger_path
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.usage_reported_calls = 0
        self._next_id = 1
        self._active: Dict[int, Reservation] = {}
        self._reserved_input = 0
        self._reserved_output = 0
        self._lock = threading.RLock()
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        if ledger_path.exists():
            # Cumulative resume: replay the append-only ledger so the original
            # authorization envelope keeps binding across interrupted runs.
            # Caps are NEVER reset; reserve() sees prior spend in the counters.
            if not resume:
                raise A3AuthorizationError("paid_budget_ledger_already_exists") from None
            self._replay_ledger()
            self._append(
                {
                    "schema_version": LEDGER_SCHEMA,
                    "event": "resume",
                    "authorization": authorization.public_dict(),
                    "prior_calls": self.calls,
                    "prior_input_tokens": self.input_tokens,
                    "prior_output_tokens": self.output_tokens,
                    "code_sha256": {str(path): _sha256(path) for path in code_paths},
                }
            )
            return
        header = {
            "schema_version": LEDGER_SCHEMA,
            "event": "header",
            "authorization": authorization.public_dict(),
            "code_sha256": {
                str(path): _sha256(path) for path in code_paths
            },
        }
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_CLOEXEC", 0)
        try:
            descriptor = os.open(ledger_path, flags, 0o600)
        except FileExistsError:
            raise A3AuthorizationError("paid_budget_ledger_already_exists") from None
        try:
            os.write(
                descriptor,
                (json.dumps(header, sort_keys=True) + "\n").encode("utf-8"),
            )
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _replay_ledger(self) -> None:
        """Rebuild cumulative spend from the existing append-only ledger."""
        header_authorization = None
        open_reservations: Dict[int, dict] = {}
        max_reservation_id = 0
        for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            kind = event.get("event")
            if kind == "header":
                header_authorization = event.get("authorization")
            elif kind == "resume":
                if event.get("authorization") != self.authorization.public_dict():
                    raise A3AuthorizationError("paid_budget_ledger_authorization_mismatch")
            elif kind == "reserve":
                reservation_id = int(event["reservation_id"])
                open_reservations[reservation_id] = event
                max_reservation_id = max(max_reservation_id, reservation_id)
            elif kind == "settle":
                if open_reservations.pop(int(event["reservation_id"]), None) is None:
                    raise A3AuthorizationError("paid_budget_ledger_orphan_settlement")
                self.calls += 1
                self.input_tokens += int(event["input_tokens"])
                self.output_tokens += int(event["output_tokens"])
                if event.get("usage_reported"):
                    self.usage_reported_calls += 1
            else:
                raise A3AuthorizationError("paid_budget_ledger_unknown_event")
        if header_authorization != self.authorization.public_dict():
            raise A3AuthorizationError("paid_budget_ledger_authorization_mismatch")
        if open_reservations:
            # An unsettled reservation means unknown in-flight spend; a human
            # must reconcile the ledger before any further paid call.
            raise A3AuthorizationError("paid_budget_ledger_unsettled_reservations")
        self._next_id = max_reservation_id + 1

    @staticmethod
    def usd(input_tokens: int, output_tokens: int) -> Decimal:
        return (
            Decimal(input_tokens) * INPUT_USD_PER_M
            + Decimal(output_tokens) * OUTPUT_USD_PER_M
        ) / Decimal(1_000_000)

    def _append(self, event: dict) -> None:
        with self.ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def reserve(self, input_tokens: int, output_tokens: int) -> Optional[Reservation]:
        if input_tokens < 0 or output_tokens <= 0:
            raise ValueError("invalid_reservation")
        with self._lock:
            projected_calls = self.calls + len(self._active) + 1
            projected_input = self.input_tokens + self._reserved_input + input_tokens
            projected_output = (
                self.output_tokens + self._reserved_output + output_tokens
            )
            limits = self.authorization
            if (
                projected_calls > limits.max_http_calls
                or projected_input > limits.max_input_tokens
                or projected_output > limits.max_output_tokens
                or self.usd(projected_input, projected_output) > limits.max_usd
            ):
                return None
            reservation = Reservation(self._next_id, input_tokens, output_tokens)
            self._append(
                {
                    "event": "reserve",
                    "reservation_id": reservation.reservation_id,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                }
            )
            self._next_id += 1
            self._active[reservation.reservation_id] = reservation
            self._reserved_input += input_tokens
            self._reserved_output += output_tokens
            return reservation

    @staticmethod
    def _usage_value(usage: Any, name: str) -> Optional[int]:
        try:
            value = getattr(usage, name, None)
        except Exception:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value

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
                raise RuntimeError("invalid_or_settled_reservation")
            exceeded_reservation = (
                input_tokens > reservation.input_tokens
                or output_tokens > reservation.output_tokens
            )
            self._append(
                {
                    "event": "settle",
                    "reservation_id": reservation.reservation_id,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "usage_reported": usage_reported,
                    "reservation_bound_exceeded": exceeded_reservation,
                }
            )
            self._reserved_input -= reservation.input_tokens
            self._reserved_output -= reservation.output_tokens
            self.calls += 1
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens
            if usage_reported:
                self.usage_reported_calls += 1
            return {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "usage_reported": usage_reported,
                "reservation_bound_exceeded": exceeded_reservation,
            }

    def settle_response(self, reservation: Reservation, response: Any) -> dict:
        try:
            usage = response.usage
        except Exception:
            usage = None
        input_tokens = self._usage_value(usage, "prompt_tokens")
        output_tokens = self._usage_value(usage, "completion_tokens")
        if input_tokens is None or output_tokens is None:
            return self.settle_failure(reservation)
        return self._settle(
            reservation,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            usage_reported=True,
        )

    def settle_failure(self, reservation: Reservation) -> dict:
        return self._settle(
            reservation,
            input_tokens=reservation.input_tokens,
            output_tokens=reservation.output_tokens,
            usage_reported=False,
        )

    def exceeded(self) -> bool:
        limits = self.authorization
        return (
            self.calls > limits.max_http_calls
            or self.input_tokens > limits.max_input_tokens
            or self.output_tokens > limits.max_output_tokens
            or self.usd(self.input_tokens, self.output_tokens) > limits.max_usd
        )

    def snapshot(self) -> dict:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "usage_reported_calls": self.usage_reported_calls,
            "estimated_usd": str(
                self.usd(self.input_tokens, self.output_tokens).quantize(
                    Decimal("0.000001")
                )
            ),
            "active_reservations": len(self._active),
            "limits": self.authorization.public_dict(),
        }


def acquire_paid_run_lock(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    os.fchmod(descriptor, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        raise A3AuthorizationError("paid_run_lock_contended") from None
    return descriptor


def release_paid_run_lock(descriptor: int) -> None:
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _image_token_bound(data_url: str) -> int:
    try:
        _prefix, encoded = data_url.split(",", 1)
        raw = base64.b64decode(encoded, validate=True)
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
            image.verify()
    except Exception:
        raise A3AuthorizationError("invalid_image_payload") from None
    patches = math.ceil(width / 32) * math.ceil(height / 32)
    return math.ceil(
        Decimal(min(patches, IMAGE_PATCH_BUDGET)) * IMAGE_TOKEN_MULTIPLIER
    )


def _without_image_payload(value: Any) -> Any:
    if isinstance(value, list):
        return [_without_image_payload(item) for item in value]
    if isinstance(value, dict):
        return {key: _without_image_payload(item) for key, item in value.items()}
    if isinstance(value, str) and value.startswith("data:") and ";base64," in value:
        prefix, _sep, _payload = value.partition(",")
        return f"{prefix},<pinned-image>"
    return value


def request_input_token_bound(model: str, messages: list[dict], kwargs: dict) -> int:
    image_tokens = 0
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "image_url":
                continue
            image = item.get("image_url")
            if not isinstance(image, dict) or not isinstance(image.get("url"), str):
                raise A3AuthorizationError("invalid_image_message")
            image_tokens += _image_token_bound(image["url"])
    request = {
        "model": model,
        "messages": _without_image_payload(messages),
        **kwargs,
    }
    text_bound = len(
        json.dumps(
            request, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )
    return text_bound + REQUEST_MARGIN_TOKENS + image_tokens


class A3BudgetedLLM:
    """A one-request-per-aask adapter with explicit A3 authorization caps."""

    def __init__(
        self,
        underlying: Any,
        *,
        budget: A3PaidBudget,
        stage: str,
        max_completion_tokens: int,
    ) -> None:
        self.model = str(underlying.model)
        self.system_prompt = getattr(
            underlying, "system_prompt", "You are a helpful assistant."
        )
        self.use_system_prompt = bool(
            getattr(underlying, "use_system_prompt", True)
        )
        self._budget = budget
        self._stage = stage
        self._max_completion_tokens = max_completion_tokens
        self._authorization = budget.authorization
        self._client = underlying.aclient.with_options(max_retries=0)
        self._timeout = getattr(underlying.config, "timeout", 600)
        self.cost_manager = SimpleNamespace(
            total_prompt_tokens=0,
            total_completion_tokens=0,
            total_cost=0.0,
        )

    def support_image_input(self) -> bool:
        return True

    @staticmethod
    def _messages(
        prompt: str,
        images: Optional[Iterable[str]],
        *,
        system_prompt: str,
        use_system_prompt: bool,
        image_detail: str,
    ) -> list[dict]:
        messages = (
            [{"role": "system", "content": system_prompt}]
            if use_system_prompt
            else []
        )
        if images:
            content = [{"type": "text", "text": prompt}]
            content.extend(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{image}",
                        "detail": image_detail,
                    },
                }
                for image in images
            )
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": prompt})
        return messages

    async def aask(
        self,
        prompt: str,
        system_msgs: Optional[list[str]] = None,
        format_msgs: Optional[list[dict]] = None,
        images: Optional[Iterable[str]] = None,
        **_kwargs: Any,
    ) -> str:
        if system_msgs or format_msgs or not isinstance(prompt, str):
            raise A3AuthorizationError("unsupported_a3_message_shape")
        auth = self._authorization
        messages = self._messages(
            prompt,
            images,
            system_prompt=self.system_prompt,
            use_system_prompt=self.use_system_prompt,
            image_detail=auth.image_detail,
        )
        kwargs = {
            "max_completion_tokens": self._max_completion_tokens,
            "reasoning_effort": auth.reasoning_effort,
            "service_tier": auth.service_tier,
            "timeout": self._timeout,
        }
        input_bound = request_input_token_bound(self.model, messages, kwargs)
        reservation = self._budget.reserve(
            input_bound, self._max_completion_tokens
        )
        if reservation is None:
            raise A3AuthorizationCapReached("authorization_cap_reached")
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                **kwargs,
            )
        except asyncio.CancelledError:
            self._budget.settle_failure(reservation)
            raise
        except Exception as error:
            self._budget.settle_failure(reservation)
            safe = {
                "APIConnectionError": "provider_connection_error",
                "APITimeoutError": "provider_timeout",
                "AuthenticationError": "provider_authentication_error",
                "BadRequestError": "provider_bad_request",
                "RateLimitError": "provider_rate_limit",
            }.get(type(error).__name__, "provider_error")
            raise A3ProviderCallError(safe) from None
        settlement = self._budget.settle_response(reservation, response)
        if not settlement["usage_reported"]:
            raise A3ProviderCallError("provider_usage_missing")
        if settlement["reservation_bound_exceeded"] or self._budget.exceeded():
            raise A3AuthorizationCapReached("authorization_cap_exceeded")
        self.cost_manager.total_prompt_tokens += settlement["input_tokens"]
        self.cost_manager.total_completion_tokens += settlement["output_tokens"]
        self.cost_manager.total_cost += float(
            A3PaidBudget.usd(
                settlement["input_tokens"], settlement["output_tokens"]
            )
        )
        try:
            content = response.choices[0].message.content
        except Exception:
            content = None
        if not isinstance(content, str):
            raise A3ProviderCallError("provider_content_missing")
        return content

    async def aclose(self) -> None:
        result = self._client.close()
        if hasattr(result, "__await__"):
            await result
