"""Test harness: no network, no real key, no cost.

Both clients are built on ``httpx.MockTransport``, which opens no socket.
Langfuse's span exporter is replaced by an in-memory one, so its OTel side
cannot reach the network either.

``https://example.invalid`` is reserved by RFC 2606: if a mock were ever
forgotten, DNS resolution would fail rather than reach a server.

The assertions read **what Langfuse puts on the wire** -- the body of
``POST /api/public/scores`` -- never the bridge's internal state.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from langfuse import Langfuse
from neomundi import GovernResult, NeoMundiClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

#: Obviously fake. No real key exists in this repository.
TEST_NEOMUNDI_KEY = "ct_test_0000000000000000000000000000"
TEST_LANGFUSE_SECRET = "sk-lf-test-0000-do-not-leak"
TEST_BASE_URL = "https://example.invalid"

#: Text that must never reach Langfuse: the prompt, the response, and the
#: suspect-phrase extract that the server copies out of the response.
SECRET_PROMPT = "PROMPT-MUST-NOT-LEAK what is the capital of France"
SECRET_RESPONSE = "RESPONSE-MUST-NOT-LEAK Paris."
SECRET_PHRASE = "PHRASE-MUST-NOT-LEAK"

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str = "rest_complete_allow.json") -> dict[str, Any]:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return {k: v for k, v in payload.items() if not k.startswith("_fixture_")}


class Recorder:
    """Every request a mock transport saw. ``count == 0`` proves no socket."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    @property
    def count(self) -> int:
        return len(self.requests)

    def score_bodies(self) -> list[dict[str, Any]]:
        """The scores Langfuse sent, flattened across batches."""
        out: list[dict[str, Any]] = []
        for request in self.requests:
            if not request.url.path.endswith("/api/public/scores"):
                continue
            body = json.loads(request.content.decode("utf-8"))
            out.extend(body if isinstance(body, list) else [body])
        return out

    def raw(self) -> str:
        return "\n".join(r.content.decode("utf-8", "replace") for r in self.requests)


def make_langfuse(
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
    *,
    recorder: Recorder | None = None,
) -> Langfuse:
    """A real Langfuse client that cannot reach the network.

    The public key is unique per call: Langfuse shares resources between
    clients with the same key, which would let one test's transport leak
    into another's.
    """

    def default(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    inner = handler or default

    def recording(request: httpx.Request) -> httpx.Response:
        if recorder is not None:
            recorder.requests.append(request)
        return inner(request)

    return Langfuse(
        public_key=f"pk-lf-test-{uuid.uuid4().hex}",
        secret_key=TEST_LANGFUSE_SECRET,
        base_url=TEST_BASE_URL,
        httpx_client=httpx.Client(transport=httpx.MockTransport(recording)),
        span_exporter=InMemorySpanExporter(),
        flush_at=1,
    )


def make_neomundi(
    payload: dict[str, Any] | None = None, *, recorder: Recorder | None = None
) -> NeoMundiClient:
    body = payload if payload is not None else load_fixture()

    def handler(request: httpx.Request) -> httpx.Response:
        if recorder is not None:
            recorder.requests.append(request)
        return httpx.Response(200, json=body)

    return NeoMundiClient(
        api_key=TEST_NEOMUNDI_KEY,
        base_url=TEST_BASE_URL,
        transport=httpx.MockTransport(handler),
    )


def measure(payload: dict[str, Any] | None = None, *, submit: bool = True):
    """A real ``TraceRecord``, produced by the real SDK through a mock."""
    client = make_neomundi(payload)
    with client.trace(submit=submit, model_name="local-model") as trace:
        trace.record(prompt=SECRET_PROMPT, response=SECRET_RESPONSE, token_count=12)
    return trace.close()


def result(payload: dict[str, Any] | None = None) -> GovernResult:
    return GovernResult.from_dict(payload if payload is not None else load_fixture())


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def trace_id() -> str:
    return Langfuse.create_trace_id(seed="neomundi-test")
