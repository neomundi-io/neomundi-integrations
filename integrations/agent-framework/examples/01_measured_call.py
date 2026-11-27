"""One Agent Framework chat call, measured -- then the same with submit=False.

Runs offline, with no key. The chat client, its middleware pipeline and
its response parsing are the framework's real code; only the provider's
HTTP transport and the NeoMundi API are mocked. What you see here is what
you would see against the real services.

    pip install -e ".[examples]"
    python examples/01_measured_call.py
"""

from __future__ import annotations

import asyncio

import httpx
from agent_framework.openai import OpenAIChatCompletionClient
from openai import AsyncOpenAI

from agent_framework_neomundi import NeoMundiChatMiddleware

PROVIDER_REPLY = {
    "id": "chatcmpl-example",
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
    "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
}


class _Audit:
    measurement_version = "3.5.0"


class _Result:
    request_id = "11111111-2222-3333-4444-555555555555"
    audit = _Audit()


class StandInMeasurementApi:
    """What `NeoMundiClient` would do, without the socket or the bill."""

    def govern(self, **kwargs: object) -> _Result:
        print("    -> submitted to NeoMundi:")
        print(f"         token_count : {kwargs['token_count']}")
        print(f"         latency_ms  : {kwargs['latency_ms']:.1f}")
        print(f"         model_name  : {kwargs['model_name']}")
        return _Result()


def chat_client(middleware: list[object]) -> OpenAIChatCompletionClient:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=PROVIDER_REPLY))
    )
    return OpenAIChatCompletionClient(
        model="gpt-4o-mini",
        async_client=AsyncOpenAI(api_key="not-a-real-key", http_client=http),
        middleware=middleware,
    )


async def main() -> None:
    print("1. a measured call")
    measure = NeoMundiChatMiddleware(client=StandInMeasurementApi())
    reply = await chat_client([measure]).get_response("What is the capital of France?")
    outcome = measure.last_outcome
    print(f"    the host got its answer : {reply.text!r}")
    print(f"    status                  : {outcome.status}")
    print(f"    token_count             : {outcome.token_count} ({outcome.token_source})")
    print(f"    request_id              : {outcome.request_id}")
    print(f"    measurement_version     : {outcome.measurement_version}")

    print("\n2. the same call with submit=False -- nothing leaves the process")
    dry = NeoMundiChatMiddleware(client=StandInMeasurementApi(), submit=False)
    await chat_client([dry]).get_response("What is the capital of France?")
    outcome = dry.last_outcome
    print(f"    status      : {outcome.status}")
    print(f"    token_count : {outcome.token_count}   <- computed, never sent")
    print(f"    reason      : {outcome.reason}")

    print(f"\nsubmitted: {measure.submitted_count} measured, {dry.submitted_count} dry")


if __name__ == "__main__":
    asyncio.run(main())
