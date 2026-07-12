import asyncio
import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from metagpt.ext.agentlayout.a3_paid_budget import (
    A3AuthorizationCapReached,
    A3BudgetedLLM,
    A3PaidBudget,
    load_authorization,
)


def _receipt(path: Path, *, calls=10, input_tokens=100_000, output_tokens=50_000):
    payload = {
        "schema_version": "a3.paid-authorization.v1",
        "authorized": True,
        "authorized_by": "user",
        "authorization_text": "authorized test",
        "run_id": "run-1",
        "model": "gpt-5.4-mini-2026-03-17",
        "tree_arm": "T2",
        "analyst_arm": "vision",
        "limits": {
            "max_http_calls": calls,
            "max_input_tokens": input_tokens,
            "max_output_tokens": output_tokens,
            "max_usd": "7.00",
        },
        "input_usd_per_m": "0.75",
        "output_usd_per_m": "4.50",
        "stage_max_completion_tokens": {
            "analyst": 4096,
            "asset_planner": 4096,
            "composition_director": 2048,
            "coordinate_mapper": 2048,
            "judge_select": 512,
        },
        "image_detail": "high",
        "reasoning_effort": "none",
        "service_tier": "default",
    }
    path.write_text(json.dumps(payload))
    return load_authorization(
        path,
        expected_run_id="run-1",
        expected_model="gpt-5.4-mini-2026-03-17",
        expected_tree_arm="T2",
        expected_analyst_arm="vision",
    )


class _Completions:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class _Client:
    def __init__(self, response):
        self.chat = SimpleNamespace(completions=_Completions(response))
        self.max_retries = None
        self.closed = False

    def with_options(self, *, max_retries):
        self.max_retries = max_retries
        return self

    async def close(self):
        self.closed = True


def _underlying(response):
    client = _Client(response)
    return (
        SimpleNamespace(
            model="gpt-5.4-mini-2026-03-17",
            aclient=client,
            config=SimpleNamespace(timeout=30),
            system_prompt="system",
            use_system_prompt=True,
        ),
        client,
    )


def _image_b64():
    image = Image.new("RGB", (64, 64), "white")
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode()


def test_budgeted_llm_enforces_request_shape_and_settles_usage(tmp_path):
    auth = _receipt(tmp_path / "auth.json")
    budget = A3PaidBudget(auth, tmp_path / "ledger.jsonl")
    response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=123, completion_tokens=45),
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))],
    )
    underlying, client = _underlying(response)
    llm = A3BudgetedLLM(
        underlying, budget=budget, stage="analyst", max_completion_tokens=4096
    )
    result = asyncio.run(llm.aask("prompt", images=[_image_b64()]))
    asyncio.run(llm.aclose())
    assert result == '{"ok":true}'
    assert client.max_retries == 0
    assert len(client.chat.completions.calls) == 1
    request = client.chat.completions.calls[0]
    assert request["max_completion_tokens"] == 4096
    assert request["reasoning_effort"] == "none"
    assert request["service_tier"] == "default"
    assert request["messages"][-1]["content"][1]["image_url"]["detail"] == "high"
    assert budget.snapshot()["calls"] == 1
    assert budget.snapshot()["input_tokens"] == 123
    assert budget.snapshot()["output_tokens"] == 45


def test_cap_refuses_before_dispatch(tmp_path):
    auth = _receipt(tmp_path / "auth.json", calls=1, output_tokens=100)
    budget = A3PaidBudget(auth, tmp_path / "ledger.jsonl")
    response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
    )
    underlying, client = _underlying(response)
    llm = A3BudgetedLLM(
        underlying, budget=budget, stage="analyst", max_completion_tokens=4096
    )
    with pytest.raises(A3AuthorizationCapReached):
        asyncio.run(llm.aask("prompt"))
    assert client.chat.completions.calls == []


def test_missing_usage_is_conservatively_settled(tmp_path):
    auth = _receipt(tmp_path / "auth.json")
    budget = A3PaidBudget(auth, tmp_path / "ledger.jsonl")
    response = SimpleNamespace(
        usage=None,
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
    )
    underlying, _client = _underlying(response)
    llm = A3BudgetedLLM(
        underlying, budget=budget, stage="judge_select", max_completion_tokens=512
    )
    with pytest.raises(RuntimeError, match="provider_usage_missing"):
        asyncio.run(llm.aask("prompt"))
    snapshot = budget.snapshot()
    assert snapshot["calls"] == 1
    assert snapshot["output_tokens"] == 512
    assert snapshot["usage_reported_calls"] == 0


def test_receipt_rejects_wrong_run(tmp_path):
    path = tmp_path / "auth.json"
    _receipt(path)
    with pytest.raises(RuntimeError, match="run_id_mismatch"):
        load_authorization(
            path,
            expected_run_id="other-run",
            expected_model="gpt-5.4-mini-2026-03-17",
            expected_tree_arm="T2",
            expected_analyst_arm="vision",
        )
