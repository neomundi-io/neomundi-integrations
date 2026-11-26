"""Offline stand-ins shared by the examples: MLflow's store, a provider, a NeoMundi API.

Nothing leaves the process:

* MLflow writes to a throwaway sqlite store in a temporary directory, and
  its own usage telemetry is disabled (it is on by default outside CI);
* the provider is an ``httpx.MockTransport`` behind a real ``openai``
  client, speaking Chat Completions, whose usage block the example chooses;
* the NeoMundi API replays a captured production ``POST /v1/govern``
  response: the suite's fixture.

Import this module **before** ``mlflow``: the environment it sets is read
when MLflow is imported.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

os.environ["MLFLOW_DISABLE_TELEMETRY"] = "true"
os.environ["DO_NOT_TRACK"] = "true"
os.environ["MLFLOW_DISABLE_AGENT_HINT"] = "1"
STORE = tempfile.mkdtemp(prefix="mlflow-neomundi-example-")
os.environ["MLFLOW_TRACKING_URI"] = "sqlite:///" + os.path.join(STORE, "mlflow.db")

import httpx  # noqa: E402
from neomundi import NeoMundiClient  # noqa: E402
from openai import OpenAI  # noqa: E402


def use_experiment(name: str) -> None:
    """``mlflow.set_experiment`` alone puts trace artifacts in ``./mlruns``
    under the current directory. Created here, they stay in the temp store."""
    import mlflow

    if mlflow.get_experiment_by_name(name) is None:
        location = Path(STORE, "artifacts").as_uri()
        mlflow.create_experiment(name, artifact_location=location)
    mlflow.set_experiment(name)


CHAT_USAGE = {"prompt_tokens": 12, "completion_tokens": 2, "total_tokens": 14}
_DEFAULT = object()


def provider_client(usage: Any = _DEFAULT, *, http_status: int = 200) -> OpenAI:
    def handler(request: httpx.Request) -> httpx.Response:
        if http_status != 200:
            return httpx.Response(http_status, json={"error": {"message": "provider down"}})
        out: dict[str, Any] = {
            "id": "chatcmpl-example",
            "object": "chat.completion",
            "created": 1764115200,
            "model": "example-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Paris."},
                    "finish_reason": "stop",
                }
            ],
        }
        chosen = CHAT_USAGE if usage is _DEFAULT else usage
        if chosen is not None:
            out["usage"] = chosen
        return httpx.Response(200, json=out)

    return OpenAI(
        api_key="sk-example-not-a-credential",
        base_url="https://provider.example.invalid/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def neomundi_client(sent: list[dict[str, Any]]) -> NeoMundiClient:
    fixture = (
        Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "rest_complete_allow.json"
    )
    payload = {
        k: v
        for k, v in json.loads(fixture.read_text(encoding="utf-8")).items()
        if not k.startswith("_fixture_")
    }

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=payload)

    return NeoMundiClient(
        api_key="ct_test_example_0000000000000000000000",
        base_url="https://example.invalid",
        transport=httpx.MockTransport(handler),
    )
