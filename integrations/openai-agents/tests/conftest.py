"""Test harness: no network, no real key, no cost.

Two mock transports, and no socket anywhere:

* the **provider** is an ``httpx.MockTransport`` behind a real
  ``openai.AsyncOpenAI`` client, driven by the **real** Agents SDK: a real
  ``Agent``, the real ``Runner``, the real ``OpenAIResponsesModel`` or
  ``OpenAIChatCompletionsModel``. Every span the processor sees is one the
  SDK produced, not one built by hand;
* the **NeoMundi API** is a real ``NeoMundiClient`` on a mock transport that
  replays a captured production response and records every request.

``https://example.invalid`` is reserved by RFC 2606: if a mock were ever
forgotten, DNS resolution would fail rather than reach a server.

The assertions read **what is sent to ``POST /v1/govern``** and the
outcomes -- never the processor's internal state.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from agents import (
    Agent,
    OpenAIChatCompletionsModel,
    OpenAIResponsesModel,
    RunConfig,
    Runner,
    function_tool,
    set_trace_processors,
)
from agents.tracing import get_trace_provider
from neomundi import NeoMundiClient
from openai import AsyncOpenAI

from openai_agents_neomundi import NeoMundiTraceProcessor, Outcome

#: Obviously fake. No real key exists in this repository.
TEST_NEOMUNDI_KEY = "ct_test_0000000000000000000000000000"
TEST_PROVIDER_KEY = "sk-provider-test-do-not-leak"
TEST_BASE_URL = "https://example.invalid"
PROVIDER_URL = "https://provider.example.invalid/v1"

#: Text that must never appear in an outcome or a log.
SECRET_PROMPT = "PROMPT-MUST-NOT-LEAK what is the weather in Paris"
SECRET_RESPONSE = "RESPONSE-MUST-NOT-LEAK sunny, 21 degrees."
SECRET_TOOL_OUTPUT = "TOOL-OUTPUT-MUST-NOT-LEAK sunny"
SECRET_INSTRUCTIONS = "INSTRUCTIONS-MUST-NOT-LEAK answer briefly"

FIXTURES = Path(__file__).parent / "fixtures"

USAGE = {
    "input_tokens": 11,
    "input_tokens_details": {"cached_tokens": 0},
    "output_tokens": 7,
    "output_tokens_details": {"reasoning_tokens": 0},
    "total_tokens": 18,
}
CHAT_USAGE = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}

_ABSENT = object()


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


def _tool_output_sent(body: dict[str, Any]) -> bool:
    items = body.get("input")
    if isinstance(items, list):
        return any(isinstance(i, dict) and i.get("type") == "function_call_output" for i in items)
    return any(m.get("role") == "tool" for m in body.get("messages", []))


def responses_payload(
    body: dict[str, Any], usage: Any, n: int, *, status: str = "completed"
) -> dict[str, Any]:
    if body.get("tools") and not _tool_output_sent(body):
        output: list[dict[str, Any]] = [
            {
                "type": "function_call",
                "id": f"fc_{n}",
                "call_id": f"call_{n}",
                "name": body["tools"][0]["name"],
                "arguments": json.dumps({"city": "Paris"}),
                "status": "completed",
            }
        ]
    else:
        output = [
            {
                "type": "message",
                "id": f"msg_{n}",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": SECRET_RESPONSE, "annotations": []}],
            }
        ]
    out: dict[str, Any] = {
        "id": f"resp_{n}",
        "object": "response",
        "created_at": 1764115200,
        "model": "test-model-served",
        "status": status,
        "output": output,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
    }
    if usage is not None:
        out["usage"] = usage
    return out


def chat_payload(body: dict[str, Any], usage: Any, n: int) -> dict[str, Any]:
    if body.get("tools") and not _tool_output_sent(body):
        message: dict[str, Any] = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": f"call_{n}",
                    "type": "function",
                    "function": {
                        "name": body["tools"][0]["function"]["name"],
                        "arguments": json.dumps({"city": "Paris"}),
                    },
                }
            ],
        }
        finish = "tool_calls"
    else:
        message = {"role": "assistant", "content": SECRET_RESPONSE}
        finish = "stop"
    out: dict[str, Any] = {
        "id": f"chatcmpl-{n}",
        "object": "chat.completion",
        "created": 1764115200,
        "model": "test-model-served",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
    }
    if usage is not None:
        out["usage"] = usage
    return out


def responses_stream(payload: dict[str, Any]) -> bytes:
    created = dict(payload, status="in_progress", output=[])
    created.pop("usage", None)
    events = [
        {"type": "response.created", "response": created, "sequence_number": 0},
        {"type": "response.completed", "response": payload, "sequence_number": 1},
    ]
    text = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    return text.encode("utf-8")


class Provider:
    """The model provider. Records each request; answers as scripted."""

    def __init__(
        self,
        usage: Any = _ABSENT,
        *,
        http_status: int = 200,
        response_status: str = "completed",
        delay: float = 0.0,
    ) -> None:
        self.usage = usage
        self.http_status = http_status
        self.response_status = response_status
        self.delay = delay
        self.paths: list[str] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        if self.delay:
            await asyncio.sleep(self.delay)
        body = json.loads(request.content.decode("utf-8"))
        self.paths.append(request.url.path)
        n = len(self.paths)
        if self.http_status != 200:
            return httpx.Response(self.http_status, json={"error": {"message": "provider down"}})
        if request.url.path.endswith("/responses"):
            usage = USAGE if self.usage is _ABSENT else self.usage
            payload = responses_payload(body, usage, n, status=self.response_status)
            if body.get("stream"):
                return httpx.Response(
                    200,
                    content=responses_stream(payload),
                    headers={"content-type": "text/event-stream"},
                )
            return httpx.Response(200, json=payload)
        usage = CHAT_USAGE if self.usage is _ABSENT else self.usage
        return httpx.Response(200, json=chat_payload(body, usage, n))

    def client(self) -> AsyncOpenAI:
        return AsyncOpenAI(
            api_key=TEST_PROVIDER_KEY,
            base_url=PROVIDER_URL,
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(self.handler)),
        )


@function_tool
def weather(city: str) -> str:
    """Return the weather for a city."""
    return SECRET_TOOL_OUTPUT


def make_agent(provider: Provider, *, api: str = "responses", tools: bool = True) -> Agent:
    client = provider.client()
    model: Any = (
        OpenAIResponsesModel(model="test-model", openai_client=client)
        if api == "responses"
        else OpenAIChatCompletionsModel(model="test-model", openai_client=client)
    )
    return Agent(
        name="weather-agent",
        instructions=SECRET_INSTRUCTIONS,
        tools=[weather] if tools else [],
        model=model,
    )


def run_agent(
    provider: Provider | None = None,
    *,
    api: str = "responses",
    tools: bool = True,
    stream: bool = False,
    **config: Any,
) -> Any:
    """One real agent run. Returns the SDK's RunResult."""
    agent = make_agent(provider or Provider(), api=api, tools=tools)
    run_config = RunConfig(**config)

    async def go() -> Any:
        if stream:
            result = Runner.run_streamed(agent, SECRET_PROMPT, run_config=run_config)
            async for _ in result.stream_events():
                pass
            return result
        return await Runner.run(agent, SECRET_PROMPT, run_config=run_config)

    return asyncio.run(go())


# ------------------------------------------------------------------ fixtures


@pytest.fixture(autouse=True)
def isolated_tracing(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Restore the global processor list after each test, exactly.

    The SDK's default exporter only exports with an OpenAI key; there is
    none here, so it never makes a request.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_AGENTS_TRACE_INCLUDE_SENSITIVE_DATA", raising=False)
    provider = get_trace_provider()
    saved = list(provider._multi_processor._processors)  # noqa: SLF001 - test only
    try:
        yield
    finally:
        set_trace_processors(saved)


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def attach(recorder: Recorder) -> Callable[..., NeoMundiTraceProcessor]:
    """Build a processor on the mock NeoMundi API and install it."""

    def build(
        handler: Callable[[httpx.Request], httpx.Response] | None = None, **kwargs: Any
    ) -> NeoMundiTraceProcessor:
        if kwargs.get("submit", True) and "client" not in kwargs:
            kwargs["client"] = make_client(handler, recorder=recorder)
        return NeoMundiTraceProcessor(**kwargs).install()

    return build


def by_type(outcomes: list[Outcome], span_type: str) -> list[Outcome]:
    return [o for o in outcomes if o.span_type == span_type]


def responses(outcomes: list[Outcome]) -> list[Outcome]:
    return by_type(outcomes, "ResponseSpanData")


def assert_no_secret(outcome: Outcome) -> None:
    text = repr(outcome)
    for secret in (
        SECRET_PROMPT,
        SECRET_RESPONSE,
        SECRET_TOOL_OUTPUT,
        SECRET_INSTRUCTIONS,
        TEST_NEOMUNDI_KEY,
        TEST_PROVIDER_KEY,
    ):
        assert secret not in text, f"{secret!r} leaked into {text}"
