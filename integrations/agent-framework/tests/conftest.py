"""A real Agent Framework client over a mocked transport. No socket.

The point of building the fixture this way, rather than faking a
`ChatContext`, is that the middleware pipeline, the connector's response
parsing and `usage_details` are all the framework's real code. A test
against a hand-made context would prove that our dataclass works and
nothing about the host.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from agent_framework.openai import OpenAIChatCompletionClient
from neomundi import NeoMundiError
from openai import AsyncOpenAI


def completion_body(
    *,
    usage: dict[str, Any] | None = None,
    content: str = "Paris.",
    model: str = "gpt-4o-mini",
) -> dict[str, Any]:
    """A chat.completion payload, with or without a usage block."""
    body: dict[str, Any] = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1764300000,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }
    if usage is not None:
        body["usage"] = usage
    return body


def sse_body(chunks: list[dict[str, Any]]) -> str:
    import json

    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def chat_client(
    *,
    body: dict[str, Any] | None = None,
    sse_chunks: list[dict[str, Any]] | None = None,
    status: int = 200,
    middleware: list[Any] | None = None,
) -> OpenAIChatCompletionClient:
    """A real client whose transport returns exactly what we hand it."""

    def handler(request: httpx.Request) -> httpx.Response:
        if sse_chunks is not None:
            return httpx.Response(
                status,
                text=sse_body(sse_chunks),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(status, json=body if body is not None else {})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAIChatCompletionClient(
        model="gpt-4o-mini",
        async_client=AsyncOpenAI(api_key="not-a-real-key", http_client=http),
        middleware=middleware or [],
    )


class FakeAudit:
    def __init__(self, version: str | None) -> None:
        self.measurement_version = version


class FakeResult:
    def __init__(self, request_id: str = "req-1", version: str | None = "3.5.0") -> None:
        self.request_id = request_id
        self.audit = FakeAudit(version)


class RecordingClient:
    """Stands in for `NeoMundiClient`, records what `govern` received.

    It is a double for the *measurement* API only. The host's own call is
    always the framework's real code.
    """

    def __init__(self, *, raises: BaseException | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._raises = raises

    def govern(self, **kwargs: Any) -> FakeResult:
        """Refuse what the real API refuses.

        🔴 The first real run returned HTTP 422 because `source_type` and
        `mode` were forwarded as explicit `None`. The offline suite missed
        it because this double accepted anything. A double that is more
        permissive than the thing it stands for does not test the contract;
        it tests itself.
        """
        for field, allowed in (("source_type", str), ("mode", str)):
            if field in kwargs and not isinstance(kwargs[field], allowed):
                raise NeoMundiError(
                    f"HTTP 422 | validation_error | body.{field}: Input should be a valid string"
                )
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return FakeResult()


@pytest.fixture
def recording_client() -> RecordingClient:
    return RecordingClient()
