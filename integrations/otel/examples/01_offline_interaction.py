"""One traced model call, measured -- then the same thing with submit=False.

Runs offline, with no key: the provider is a mock transport behind the real
``openai`` client, instrumented by the real contrib ``openai-v2``
instrumentation, and the NeoMundi API is a mock replaying a captured
response. What it shows is what you would see against the real services.

    pip install -e ".[examples]"
    python examples/01_offline_interaction.py
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from neomundi import NeoMundiClient
from openai import OpenAI
from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from otel_neomundi import NeoMundiSpanProcessor


def provider_client() -> OpenAI:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
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
                # What the provider published. The span will carry 12 / 2 too,
                # but the span is not where the count is taken from.
                "usage": {"prompt_tokens": 12, "completion_tokens": 2, "total_tokens": 14},
            },
        )

    return OpenAI(
        api_key="not-a-key",
        base_url="https://provider.example.invalid/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def neomundi_client(sent: list[dict]) -> NeoMundiClient:
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=_full_response())

    return NeoMundiClient(
        api_key="ct_test_example",
        base_url="https://example.invalid",
        transport=httpx.MockTransport(handler),
    )


def _full_response() -> dict:
    # A captured production POST /v1/govern response: the suite's fixture.
    fixture = (
        Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "rest_complete_allow.json"
    )
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    return {k: v for k, v in payload.items() if not k.startswith("_fixture_")}


def run(submit: bool) -> None:
    # The application's existing pipeline: a provider and an exporter.
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    OpenAIInstrumentor().instrument(tracer_provider=provider)

    sent: list[dict] = []
    processor = NeoMundiSpanProcessor(
        neomundi_client(sent) if submit else None,
        tracer_provider=provider,
        submit=submit,
    )
    provider.add_span_processor(processor)

    client = provider_client()
    with processor.interaction() as interaction:
        reply = client.chat.completions.create(
            model="llama3.2:3b",
            messages=[{"role": "user", "content": "Capital of France, one word."}],
        )
        # The count the PROVIDER returned, or None if it returned none.
        interaction.declare(token_count=reply.usage.total_tokens if reply.usage else None)

    OpenAIInstrumentor().uninstrument()

    outcome = interaction.outcome
    print(f"status        : {outcome.status}")
    print(f"token_count   : {outcome.token_count} ({outcome.token_source})")
    print(f"latency_ms    : {outcome.latency_ms:.3f} (from the GenAI span)")
    print(f"model_name    : {outcome.model_name}")
    print(f"request_id    : {outcome.request_id}")
    print(f"requests sent : {len(sent)}")
    if sent:
        print(f"sent          : token_count={sent[0]['raw_metrics']['token_count']}")
    marker = [s for s in exporter.get_finished_spans() if s.name == "neomundi.interaction"][0]
    genai = [s for s in exporter.get_finished_spans() if s.name.startswith("chat")][0]
    print(f"in the trace  : neomundi.request_id={marker.attributes.get('neomundi.request_id')}")
    print(f"same trace    : {marker.context.trace_id == genai.context.trace_id}")


if __name__ == "__main__":
    print("## measured (mock NeoMundi API)")
    run(submit=True)
    print("\n## submit=False (no key, no socket)")
    run(submit=False)
