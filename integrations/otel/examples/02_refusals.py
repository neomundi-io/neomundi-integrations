"""Each refusal, on a real instrumented call, and what reached the wire: nothing.

The point of this integration is as much what it refuses as what it sends.
Every case below has a GenAI span produced by the real openai-v2
instrumentation; the NeoMundi API is a mock that counts requests.

    python examples/02_refusals.py
"""

from __future__ import annotations

import httpx
import openai
from neomundi import NeoMundiClient
from openai import OpenAI
from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor
from opentelemetry.sdk.trace import TracerProvider

from otel_neomundi import NeoMundiSpanProcessor

USAGE = {"prompt_tokens": 12, "completion_tokens": 2, "total_tokens": 14}


def provider_client(usage=USAGE, status=200) -> OpenAI:
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "provider down"}})
        body = {
            "id": "chatcmpl-example",
            "object": "chat.completion",
            "created": 1764115200,
            "model": "llama3.2:3b",
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
        return httpx.Response(200, json=body)

    return OpenAI(
        api_key="not-a-key",
        base_url="https://provider.example.invalid/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def ask(client: OpenAI):
    return client.chat.completions.create(
        model="llama3.2:3b", messages=[{"role": "user", "content": "Capital of France?"}]
    )


def main() -> None:
    provider = TracerProvider()
    OpenAIInstrumentor().instrument(tracer_provider=provider)
    sent: list[httpx.Request] = []
    neomundi = NeoMundiClient(
        api_key="ct_test_example",
        base_url="https://example.invalid",
        transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(500)),
    )
    processor = NeoMundiSpanProcessor(neomundi, tracer_provider=provider)
    provider.add_span_processor(processor)

    def show(title, interaction):
        o = interaction.outcome
        print(f"## {title}")
        print(f"status        : {o.status}")
        usage = {True: "present", False: "absent", None: "not examined"}[o.span_reported_usage]
        print(f"span usage    : {usage} (never submitted)")
        print(f"reason        : {o.reason}")
        print(f"requests sent : {len(sent)}\n")

    # 1. The span carries the provider's 12 / 2, and nothing is declared.
    with processor.interaction() as i:
        ask(provider_client())
    show("span usage, nothing declared", i)

    # 2. The provider published no usage; the application has nothing to declare.
    with processor.interaction() as i:
        reply = ask(provider_client(usage=None))
        i.declare(token_count=reply.usage.total_tokens if reply.usage else None)
    show("silent provider", i)

    # 3. Two model calls in one interaction: which one is meant is not guessed.
    with processor.interaction() as i:
        ask(provider_client())
        reply = ask(provider_client())
        i.declare(token_count=reply.usage.total_tokens)
    show("two calls, one interaction", i)

    # 4. The provider failed; the application caught it.
    with processor.interaction() as i:
        try:
            ask(provider_client(status=500))
        except openai.InternalServerError:
            pass
        i.declare(token_count=14)
    show("failed model call", i)

    # 5. Sampling at zero: no socket, nothing billed.
    processor.sample_rate = 0.0
    with processor.interaction() as i:
        reply = ask(provider_client())
        i.declare(token_count=reply.usage.total_tokens)
    show("sample_rate = 0", i)

    OpenAIInstrumentor().uninstrument()


if __name__ == "__main__":
    main()
