"""Test harness: no network, no real key, no cost.

Every span the processor sees is one **real MLflow** produced:

* the **provider** is an ``httpx.MockTransport`` behind a real ``openai``
  client, traced by the real ``mlflow.openai.autolog()`` -- and, for the
  refusals, by MLflow's real LangChain autolog and OpenAI Agents SDK tracer;
* **MLflow** writes to a throwaway sqlite store under the session's temp
  directory, so traces can be re-read (tags included) as a user would;
* the **NeoMundi API** is a real ``NeoMundiClient`` on a mock transport that
  replays a captured production response and records every request.

``https://example.invalid`` is reserved by RFC 2606: if a mock were ever
forgotten, DNS resolution would fail rather than reach a server. MLflow's
own usage telemetry is disabled before MLflow is imported: it is on by
default outside CI and would otherwise contact mlflow-telemetry.io.

The assertions read **what is sent to ``POST /v1/govern``**, the outcomes,
and the MLflow trace -- never the processor's internal state.
"""

from __future__ import annotations

import os
import tempfile

os.environ["MLFLOW_DISABLE_TELEMETRY"] = "true"
os.environ["DO_NOT_TRACK"] = "true"
_STORE = tempfile.mkdtemp(prefix="mlflow-neomundi-tests-")
os.environ["MLFLOW_TRACKING_URI"] = "sqlite:///" + os.path.join(_STORE, "mlflow.db")

import json  # noqa: E402
import threading  # noqa: E402
from collections.abc import Callable, Iterator  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import httpx  # noqa: E402
import mlflow  # noqa: E402
import pytest  # noqa: E402
from mlflow.entities import SpanType  # noqa: E402
from mlflow.tracing.config import get_config  # noqa: E402
from neomundi import NeoMundiClient  # noqa: E402
from openai import OpenAI  # noqa: E402

from mlflow_neomundi import NeoMundiMlflowProcessor, Outcome  # noqa: E402

#: Obviously fake. No real key exists in this repository.
TEST_NEOMUNDI_KEY = "ct_test_0000000000000000000000000000"
TEST_PROVIDER_KEY = "sk-provider-test-do-not-leak"
TEST_BASE_URL = "https://example.invalid"
PROVIDER_URL = "https://provider.example.invalid/v1"

#: Text that must never appear in an outcome, a trace tag or a log.
SECRET_PROMPT = "PROMPT-MUST-NOT-LEAK what is the capital of France"
SECRET_RESPONSE = "RESPONSE-MUST-NOT-LEAK Paris."

FIXTURES = Path(__file__).parent / "fixtures"

CHAT_USAGE = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
RESPONSES_USAGE = {
    "input_tokens": 11,
    "input_tokens_details": {"cached_tokens": 0},
    "output_tokens": 7,
    "output_tokens_details": {"reasoning_tokens": 0},
    "total_tokens": 18,
}

_DEFAULT = object()


def load_fixture(name: str = "rest_complete_allow.json") -> dict[str, Any]:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return {k: v for k, v in payload.items() if not k.startswith("_fixture_")}


# ------------------------------------------------------------------ NeoMundi


class Recorder:
    """Every request the NeoMundi mock saw. ``count == 0`` proves no socket."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._lock = threading.Lock()

    def add(self, request: httpx.Request) -> None:
        with self._lock:
            self.requests.append(request)

    @property
    def count(self) -> int:
        return len(self.requests)

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.content.decode("utf-8")) for r in self.requests]


def make_client(
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
    *,
    recorder: Recorder | None = None,
) -> NeoMundiClient:
    payload = load_fixture()

    def default(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    inner = handler or default

    def transport(request: httpx.Request) -> httpx.Response:
        if recorder is not None:
            recorder.add(request)
        return inner(request)

    return NeoMundiClient(
        api_key=TEST_NEOMUNDI_KEY,
        base_url=TEST_BASE_URL,
        transport=httpx.MockTransport(transport),
    )


# ------------------------------------------------------------------ provider


def chat_payload(usage: Any, n: int) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": f"chatcmpl-{n}",
        "object": "chat.completion",
        "created": 1764115200,
        "model": "test-model-served",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": SECRET_RESPONSE},
                "finish_reason": "stop",
            }
        ],
    }
    if usage is not None:
        out["usage"] = usage
    return out


def chat_stream(usage: Any) -> bytes:
    chunks: list[dict[str, Any]] = [
        {
            "id": "chatcmpl-s",
            "object": "chat.completion.chunk",
            "created": 1764115200,
            "model": "test-model-served",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": SECRET_RESPONSE},
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chatcmpl-s",
            "object": "chat.completion.chunk",
            "created": 1764115200,
            "model": "test-model-served",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        },
    ]
    if usage is not None:
        chunks.append(
            {
                "id": "chatcmpl-s",
                "object": "chat.completion.chunk",
                "created": 1764115200,
                "model": "test-model-served",
                "choices": [],
                "usage": usage,
            }
        )
    text = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return text.encode("utf-8")


def responses_payload(usage: Any, n: int) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": f"resp_{n}",
        "object": "response",
        "created_at": 1764115200,
        "model": "test-model-served",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "id": f"msg_{n}",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": SECRET_RESPONSE, "annotations": []}],
            }
        ],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
    }
    if usage is not None:
        out["usage"] = usage
    return out


class Provider:
    """The model provider. Records each request; answers as scripted.

    ``usage`` is what the provider publishes: the default is a full block
    (11/7/18); ``None`` publishes no usage at all; any dict is sent as is.
    """

    def __init__(self, usage: Any = _DEFAULT, *, http_status: int = 200) -> None:
        self.usage = usage
        self.http_status = http_status
        self.paths: list[str] = []

    def _answer(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        self.paths.append(request.url.path)
        n = len(self.paths)
        if self.http_status != 200:
            return httpx.Response(self.http_status, json={"error": {"message": "provider down"}})
        if request.url.path.endswith("/responses"):
            usage = RESPONSES_USAGE if self.usage is _DEFAULT else self.usage
            return httpx.Response(200, json=responses_payload(usage, n))
        usage = CHAT_USAGE if self.usage is _DEFAULT else self.usage
        if body.get("stream"):
            include = (body.get("stream_options") or {}).get("include_usage")
            return httpx.Response(
                200,
                content=chat_stream(usage if include else None),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json=chat_payload(usage, n))

    def handler(self, request: httpx.Request) -> httpx.Response:
        request.read()
        return self._answer(request)

    async def async_handler(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        return self._answer(request)

    def client(self) -> OpenAI:
        return OpenAI(
            api_key=TEST_PROVIDER_KEY,
            base_url=PROVIDER_URL,
            max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(self.handler)),
        )

    def async_client(self) -> Any:
        from openai import AsyncOpenAI

        return AsyncOpenAI(
            api_key=TEST_PROVIDER_KEY,
            base_url=PROVIDER_URL,
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(self.async_handler)),
        )


def chat(provider: Provider | None = None, **kwargs: Any) -> str | None:
    """One Chat Completions call inside an application span, as an app would."""
    client = (provider or Provider()).client()

    @mlflow.trace(name="answer", span_type=SpanType.AGENT)
    def answer() -> str | None:
        reply = client.chat.completions.create(
            model="test-model",
            messages=[{"role": "user", "content": SECRET_PROMPT}],
            **kwargs,
        )
        if kwargs.get("stream"):
            text = []
            for chunk in reply:
                if chunk.choices and chunk.choices[0].delta.content:
                    text.append(chunk.choices[0].delta.content)
            return "".join(text)
        return reply.choices[0].message.content

    return answer()


def use_experiment(name: str) -> None:
    """Select an experiment whose artifacts live in the session's temp store.

    ``mlflow.set_experiment`` alone creates the experiment with MLflow's
    default artifact root, ``./mlruns`` in the CURRENT directory, and the
    trace artifacts land there. Created explicitly, they stay in the store.
    """
    if mlflow.get_experiment_by_name(name) is None:
        location = Path(_STORE, "artifacts", name).as_uri()
        mlflow.create_experiment(name, artifact_location=location)
    mlflow.set_experiment(name)


def respond(provider: Provider | None = None) -> str:
    """One Responses API call, with no parent span."""
    client = (provider or Provider()).client()
    reply = client.responses.create(model="test-model", input=SECRET_PROMPT)
    return reply.output_text


# ------------------------------------------------------------------ fixtures


@pytest.fixture(scope="session", autouse=True)
def autolog() -> Iterator[None]:
    use_experiment("mlflow-neomundi-tests")
    mlflow.openai.autolog()
    yield
    mlflow.openai.autolog(disable=True)


@pytest.fixture(autouse=True)
def isolated_hooks() -> Iterator[None]:
    """Restore MLflow's span-processor list after each test, exactly."""
    saved = list(get_config().span_processors)
    try:
        yield
    finally:
        mlflow.tracing.configure(span_processors=saved)


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def attach(recorder: Recorder) -> Callable[..., NeoMundiMlflowProcessor]:
    """Build a processor on the mock NeoMundi API and install it."""

    def build(
        handler: Callable[[httpx.Request], httpx.Response] | None = None, **kwargs: Any
    ) -> NeoMundiMlflowProcessor:
        if kwargs.get("submit", True) and "client" not in kwargs:
            kwargs["client"] = make_client(handler, recorder=recorder)
        return NeoMundiMlflowProcessor(**kwargs).install()

    return build


def model_calls(outcomes: list[Outcome]) -> list[Outcome]:
    return [o for o in outcomes if o.span_type in ("LLM", "CHAT_MODEL")]


def by_writer(outcomes: list[Outcome], writer: str | None) -> list[Outcome]:
    return [o for o in model_calls(outcomes) if o.writer == writer]


def last_trace() -> Any:
    trace_id = mlflow.get_last_active_trace_id()
    return mlflow.get_trace(trace_id, flush=True)


def assert_no_secret(text: str) -> None:
    for secret in (SECRET_PROMPT, SECRET_RESPONSE, TEST_NEOMUNDI_KEY, TEST_PROVIDER_KEY):
        assert secret not in text, f"{secret!r} leaked into {text}"
