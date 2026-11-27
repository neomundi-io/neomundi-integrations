"""Every refusal, on a real call, and what reached the wire: nothing.

The refusals are the part worth reading. A measurement product that
quietly fills a gap is worse than one that says it cannot measure.

    pip install -e ".[examples]"
    python examples/02_refusals.py
"""

from __future__ import annotations

import asyncio

import httpx
from agent_framework.openai import OpenAIChatCompletionClient
from neomundi import NeoMundiError
from openai import AsyncOpenAI

from agent_framework_neomundi import NeoMundiChatMiddleware


def reply(usage: dict | None) -> dict:
    body = {
        "id": "chatcmpl-refusals",
        "object": "chat.completion",
        "created": 1764115200,
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Paris."},
                "finish_reason": "stop",
            }
        ],
    }
    if usage is not None:
        body["usage"] = usage
    return body


class CountingApi:
    """Counts what would have been billed. Nothing should be, below."""

    def __init__(self) -> None:
        self.billed = 0

    def govern(self, **kwargs: object) -> object:
        self.billed += 1
        result = type("R", (), {})()
        result.request_id = "would-have-been-billed"
        result.audit = type("A", (), {"measurement_version": "3.5.0"})()
        return result


class FailingApi(CountingApi):
    def govern(self, **kwargs: object) -> object:
        raise NeoMundiError("the measurement API is unreachable")


def client(body: dict, middleware: list, *, status: int = 200, sse=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if sse is not None:
            return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
        return httpx.Response(status, json=body)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAIChatCompletionClient(
        model="gpt-4o-mini",
        async_client=AsyncOpenAI(api_key="not-a-real-key", http_client=http),
        middleware=middleware,
    )


def show(label: str, middleware: NeoMundiChatMiddleware, api: CountingApi) -> None:
    outcome = middleware.last_outcome
    print(f"\n{label}")
    print(f"    status : {outcome.status}")
    print(f"    reason : {outcome.reason}")
    print(f"    billed : {api.billed}")


async def main() -> None:
    print("Each case below makes a REAL call through the framework.")
    print("None of them produces a measurement, and none is billed.")

    # 1. The provider published no count.
    api = CountingApi()
    mw = NeoMundiChatMiddleware(client=api)
    await client(reply(usage=None), [mw]).get_response("q")
    show("1. the provider published no token count", mw, api)

    # 2. The count is there but it is zero.
    api = CountingApi()
    mw = NeoMundiChatMiddleware(client=api)
    await client(
        reply({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}), [mw]
    ).get_response("q")
    show("2. the count is present but zero -- a zero is not a measurement", mw, api)

    # 3. Streaming.
    api = CountingApi()
    mw = NeoMundiChatMiddleware(client=api)
    chunk = (
        'data: {"id":"c","object":"chat.completion.chunk","created":1,'
        '"model":"gpt-4o-mini","choices":[{"index":0,"delta":{"content":"Paris."},'
        '"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
    )
    streamed = client(reply(None), [mw], sse=chunk)
    async for _ in streamed.get_response("q", stream=True):
        pass
    show("3. a streaming invocation", mw, api)

    # 4. The measurement API itself is down. The host must not notice.
    api = FailingApi()
    mw = NeoMundiChatMiddleware(client=api)
    answer = await client(
        reply({"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}), [mw]
    ).get_response("q")
    show("4. the measurement API is unreachable", mw, api)
    print(f"    and the host still got its answer: {answer.text!r}")

    print("\nNothing was billed in any of the four.")


if __name__ == "__main__":
    asyncio.run(main())
