"""Test harness: no network, no real key, no cost.

Two mock transports, and no socket anywhere:

* the **provider** is an ``httpx.MockTransport`` behind a real
  ``openai.OpenAI`` client, instrumented by the **real** contrib
  instrumentation ``opentelemetry-instrumentation-openai-v2``, on a real
  OpenTelemetry SDK ``TracerProvider`` -- so every GenAI span below is one
  the instrumentation actually produced;
* the **NeoMundi API** is a real ``NeoMundiClient`` on a mock transport that
  replays a captured production response and records every request.

``https://example.invalid`` is reserved by RFC 2606: if a mock were ever
forgotten, DNS resolution would fail rather than reach a server.

The assertions read **what is sent to ``POST /v1/govern``**, the outcome,
and the spans the application's own exporter receives -- never the
processor's internal state.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from neomundi import NeoMundiClient
from openai import AsyncOpenAI, OpenAI
from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from otel_neomundi import NeoMundiSpanProcessor, Outcome

#: Obviously fake. No real key exists in this repository.
TEST_NEOMUNDI_KEY = "ct_test_0000000000000000000000000000"
TEST_PROVIDER_KEY = "sk-provider-test-do-not-leak"
TEST_BASE_URL = "https://example.invalid"
PROVIDER_URL = "https://provider.example.invalid"

#: Text that must never appear in an outcome, a span we add, or a request.
SECRET_PROMPT = "PROMPT-MUST-NOT-LEAK what is the capital of France"
SECRET_RESPONSE = "RESPONSE-MUST-NOT-LEAK Paris."
MESSAGES = [{"role": "user", "content": SECRET_PROMPT}]

FIXTURES = Path(__file__).parent / "fixtures"

REPORTED = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}


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


def openai_body(usage: dict[str, Any] | None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "chatcmpl-test",
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
        body["usage"] = usage
    return body


def openai_stream(usage: dict[str, Any] | None) -> bytes:
    def chunk(delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
        return {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 1764115200,
            "model": "test-model-served",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }

    head, _, tail = SECRET_RESPONSE.partition(" ")
    chunks = [chunk({"role": "assistant", "content": w}) for w in (head + " ", tail)]
    chunks.append(chunk({}, "stop"))
    if usage is not None:
        last = chunk({})
        last["choices"] = []
        last["usage"] = usage
        chunks.append(last)
    text = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return text.encode("utf-8")


def provider_handler(
    usage: dict[str, Any] | None = REPORTED, *, status: int = 200
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "provider down"}})
        body = json.loads(request.content.decode("utf-8"))
        if body.get("stream"):
            return httpx.Response(
                200,
                content=openai_stream(usage),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json=openai_body(usage))

    return handler


def openai_provider(usage: dict[str, Any] | None = REPORTED, *, status: int = 200) -> OpenAI:
    return OpenAI(
        api_key=TEST_PROVIDER_KEY,
        base_url=f"{PROVIDER_URL}/v1",
        max_retries=0,
        http_client=httpx.Client(
            transport=httpx.MockTransport(provider_handler(usage, status=status))
        ),
    )


def async_openai_provider(usage: dict[str, Any] | None = REPORTED) -> AsyncOpenAI:
    handler = provider_handler(usage)

    async def ahandler(request: httpx.Request) -> httpx.Response:
        await request.aread()
        return handler(request)

    return AsyncOpenAI(
        api_key=TEST_PROVIDER_KEY,
        base_url=f"{PROVIDER_URL}/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(ahandler)),
    )


# ------------------------------------------------------------------ pipeline


@dataclass
class Pipeline:
    """An application's existing OTel pipeline: a provider and an exporter."""

    provider: TracerProvider
    exporter: InMemorySpanExporter

    def spans(self, name: str | None = None) -> list[Any]:
        spans = list(self.exporter.get_finished_spans())
        return [s for s in spans if name is None or s.name == name]

    def genai_spans(self) -> list[Any]:
        return [s for s in self.spans() if "gen_ai.operation.name" in (s.attributes or {})]

    def attach(self, **kwargs: Any) -> NeoMundiSpanProcessor:
        kwargs.setdefault("tracer_provider", self.provider)
        processor = NeoMundiSpanProcessor(**kwargs)
        self.provider.add_span_processor(processor)
        return processor


@pytest.fixture
def pipeline(monkeypatch: pytest.MonkeyPatch) -> Iterator[Pipeline]:
    """A fresh provider, its exporter, and openai-v2 instrumenting onto it."""
    monkeypatch.delenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", raising=False)
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    OpenAIInstrumentor().instrument(tracer_provider=provider)
    try:
        yield Pipeline(provider, exporter)
    finally:
        OpenAIInstrumentor().uninstrument()


@pytest.fixture
def content_pipeline(monkeypatch: pytest.MonkeyPatch) -> Iterator[Pipeline]:
    """Same, with the instrumentation capturing prompts and completions."""
    monkeypatch.setenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "SPAN_AND_EVENT")
    monkeypatch.setenv("OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental")
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    OpenAIInstrumentor().instrument(tracer_provider=provider)
    try:
        yield Pipeline(provider, exporter)
    finally:
        OpenAIInstrumentor().uninstrument()


def call(provider: OpenAI | None = None, *, stream: bool = False, **kwargs: Any) -> Any:
    """One real, instrumented chat completion. Returns the reply (or the text, streamed)."""
    client = provider or openai_provider()
    kwargs.setdefault("model", "test-model")
    kwargs.setdefault("messages", MESSAGES)
    if stream:
        kwargs.setdefault("stream_options", {"include_usage": True})
        parts = []
        for chunk in client.chat.completions.create(stream=True, **kwargs):
            for choice in chunk.choices or ():
                if choice.delta and choice.delta.content:
                    parts.append(choice.delta.content)
        return "".join(parts)
    return client.chat.completions.create(**kwargs)


def declared_total(reply: Any) -> int | None:
    """What an application does: take the provider's own count, or nothing."""
    usage = getattr(reply, "usage", None)
    return usage.total_tokens if usage is not None else None


def assert_no_secret(outcome: Outcome) -> None:
    text = repr(outcome)
    for secret in (SECRET_PROMPT, SECRET_RESPONSE, TEST_NEOMUNDI_KEY, TEST_PROVIDER_KEY):
        assert secret not in text, f"{secret!r} leaked into {text}"
