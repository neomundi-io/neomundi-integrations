"""Test harness: no network, no real key, no cost.

Two mock transports, and no socket anywhere:

* the **provider** LiteLLM calls is an ``httpx.MockTransport`` behind a real
  ``openai.OpenAI`` client (or LiteLLM's own ``HTTPHandler`` for Ollama), so
  every call below goes through the real ``litellm.completion`` code path;
* the **NeoMundi API** is a real ``NeoMundiClient`` on a mock transport that
  replays a captured production response and records every request.

``https://example.invalid`` is reserved by RFC 2606: if a mock were ever
forgotten, DNS resolution would fail rather than reach a server.

The assertions read **what is sent to ``POST /v1/govern``** and the
outcome the logger publishes -- never its internal state.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import litellm
import pytest
from litellm.llms.custom_httpx.http_handler import HTTPHandler
from neomundi import NeoMundiClient
from openai import AsyncOpenAI, OpenAI

from litellm_neomundi import NeoMundiLogger, Outcome

#: Obviously fake. No real key exists in this repository.
TEST_NEOMUNDI_KEY = "ct_test_0000000000000000000000000000"
TEST_PROVIDER_KEY = "sk-provider-test-do-not-leak"
TEST_BASE_URL = "https://example.invalid"
PROVIDER_URL = "https://provider.example.invalid"

#: Text that must never appear in an outcome.
SECRET_PROMPT = "PROMPT-MUST-NOT-LEAK what is the capital of France"
SECRET_RESPONSE = "RESPONSE-MUST-NOT-LEAK Paris."
MESSAGES = [{"role": "user", "content": SECRET_PROMPT}]

FIXTURES = Path(__file__).parent / "fixtures"


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


def openai_body(usage: dict[str, int] | None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1764115200,
        "model": "test-model",
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


def openai_stream(usage: dict[str, int] | None) -> bytes:
    def chunk(delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
        return {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 1764115200,
            "model": "test-model",
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


REPORTED = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}


def provider_handler(
    usage: dict[str, int] | None = REPORTED, *, status: int = 200
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


def openai_provider(usage: dict[str, int] | None = REPORTED, *, status: int = 200) -> OpenAI:
    return OpenAI(
        api_key=TEST_PROVIDER_KEY,
        base_url=f"{PROVIDER_URL}/v1",
        max_retries=0,
        http_client=httpx.Client(
            transport=httpx.MockTransport(provider_handler(usage, status=status))
        ),
    )


def async_openai_provider(usage: dict[str, int] | None = REPORTED) -> AsyncOpenAI:
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


def ollama_provider(report: bool) -> HTTPHandler:
    def handler(request: httpx.Request) -> httpx.Response:
        body: dict[str, Any] = {
            "model": "llama3.2",
            "created_at": "2026-11-26T00:00:00Z",
            "message": {"role": "assistant", "content": SECRET_RESPONSE},
            "done": True,
            "total_duration": 812345678,
            "eval_duration": 412345678,
        }
        if report:
            body.update(prompt_eval_count=11, eval_count=7)
        return httpx.Response(200, json=body)

    return HTTPHandler(client=httpx.Client(transport=httpx.MockTransport(handler)))


# ------------------------------------------------------------------- running


@pytest.fixture(autouse=True)
def _isolated_litellm():
    """Every test starts with no callback and no cache attached.

    Reassigning ``litellm.callbacks`` is NOT enough: LiteLLM copies each
    logger into its internal success/failure lists on first use, keeps it
    there, and refuses a second instance of the same class (measured on
    1.111.0, LIMITATIONS.md). Without this reset, the first test's logger
    would receive every later test's calls.
    """
    litellm.logging_callback_manager._reset_all_callbacks()
    litellm.cache = None
    yield
    litellm.logging_callback_manager._reset_all_callbacks()
    litellm.cache = None


def wait_for(logger: NeoMundiLogger, count: int = 1, timeout: float = 10.0) -> list[Outcome]:
    """LiteLLM runs sync success hooks in a worker thread: wait for them."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        outcomes = logger.outcomes
        if len(outcomes) >= count:
            return outcomes
        time.sleep(0.02)
    raise AssertionError(f"expected {count} outcome(s), got {logger.outcomes!r}")


def complete(logger: NeoMundiLogger, **kwargs: Any) -> Any:
    """One real ``litellm.completion`` with the logger attached."""
    litellm.callbacks = [logger]
    kwargs.setdefault("model", "openai/test-model")
    kwargs.setdefault("messages", MESSAGES)
    if "client" not in kwargs:
        kwargs["client"] = openai_provider()
    response = litellm.completion(**kwargs)
    if kwargs.get("stream"):
        for _ in response:
            pass
    return response


def acomplete(logger: NeoMundiLogger, **kwargs: Any) -> Any:
    """One real ``litellm.acompletion``, driven to the end of its callbacks."""
    litellm.callbacks = [logger]
    kwargs.setdefault("model", "openai/test-model")
    kwargs.setdefault("messages", MESSAGES)
    kwargs.setdefault("client", async_openai_provider())

    async def run() -> Any:
        response = await litellm.acompletion(**kwargs)
        if kwargs.get("stream"):
            async for _ in response:
                pass
        # Let LiteLLM's logging task and our worker thread finish.
        for _ in range(200):
            if logger.outcomes:
                break
            await asyncio.sleep(0.02)
        return response

    return asyncio.run(run())


def assert_no_secret(outcome: Outcome) -> None:
    text = repr(outcome)
    for secret in (SECRET_PROMPT, SECRET_RESPONSE, TEST_NEOMUNDI_KEY, TEST_PROVIDER_KEY):
        assert secret not in text, f"{secret!r} leaked into {text}"
