"""E2 probe for the OpenTelemetry integration: real traced calls to Ollama.

Driven by ``tools/qualify_e2.py``, which owns the budget, the account
guard, the re-read and the evidence. This file owns one thing: how to make
an OTel-instrumented application do real work, and declare the count its
provider published.

The host is what a developer would run: the real ``openai`` client,
instrumented by the contrib ``opentelemetry-instrumentation-openai-v2``,
pointed at Ollama's **OpenAI-compatible** endpoint, with
``NeoMundiSpanProcessor`` added to the tracer provider.

Phases, on purpose:

* four interactions whose count the application declares from
  ``reply.usage.total_tokens`` -- **the observations the E2 claim rests
  on** (billed when ``submit=True``);
* one interaction where the span carries the provider's usage and the
  application declares **nothing** -- a refusal on a *real* span, the
  invariant this integration exists for. Nothing is submitted.
* one streamed call without ``include_usage``: the provider publishes no
  count, the application has nothing to declare, and it is refused.

🔴 **The environment must be scrubbed by the caller** (``tools/probe.sh``
or ``env -i``): the ``openai`` client reads ``OPENAI_API_KEY`` and
``OPENAI_BASE_URL`` from the environment, and a probe must never leave for
a paid provider.

ASCII only in what is printed: an ``env -i`` shell on Windows loses its
UTF-8 console encoding.
"""

from __future__ import annotations

import os
from typing import Any

PROBE_NAME = "openai client + openai-v2 instrumentation -> ollama (caller declares the count)"

MODEL = os.environ.get("NEOMUNDI_QUALIFY_MODEL", "llama3.2:3b")
OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

#: Ollama ignores the key, but the OpenAI client requires one to be present.
#: Obviously not a credential.
DUMMY_KEY = "ollama-local-no-credential"

QUESTIONS = (
    "In one sentence, what is the capital of France?",
    "In one sentence, what does the HTTP status code 404 mean?",
    "In one sentence, what is a SQL primary key?",
    "In one sentence, who wrote the play Hamlet?",
)


def _record(phase: str, outcome: Any, *, expectation: str) -> dict[str, Any]:
    if outcome is None:
        return {
            "phase": phase,
            "expectation": expectation,
            "status": "no_outcome_recorded",
            "submitted": False,
            "reason": "the interaction produced no outcome",
        }
    return {
        "phase": phase,
        "expectation": expectation,
        "status": outcome.status,
        "submitted": bool(outcome.submitted),
        "request_id": outcome.request_id,
        "latency_ms": outcome.latency_ms,
        "token_count": outcome.token_count,
        "token_source": outcome.token_source,
        "span_reported_usage": outcome.span_reported_usage,
        "model_name": outcome.model_name,
        "trace_id": outcome.trace_id,
        "measurement_version": outcome.measurement_version,
        "reason": outcome.reason,
    }


def _failed(phase: str, expectation: str, exc: BaseException) -> dict[str, Any]:
    return {
        "phase": phase,
        "expectation": expectation,
        "status": "host_call_failed",
        "submitted": False,
        "reason": f"the model call itself failed: {type(exc).__name__}",
    }


def probe(client, *, submit: bool, metadata: dict, http_client: Any = None) -> list[dict[str, Any]]:
    """Drive the host.

    ``http_client`` is for the offline test only: an ``httpx.Client`` on a
    mock transport, so the probe's logic is exercised without Ollama. The
    harness never passes it.
    """
    from openai import OpenAI
    from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor
    from opentelemetry.sdk.trace import TracerProvider

    from otel_neomundi import NeoMundiSpanProcessor

    provider = TracerProvider()
    OpenAIInstrumentor().instrument(tracer_provider=provider)
    processor = NeoMundiSpanProcessor(
        client if submit else None,
        tracer_provider=provider,
        submit=submit,
        metadata=metadata,
    )
    provider.add_span_processor(processor)
    model = OpenAI(
        base_url=f"{OLLAMA}/v1" if http_client is None else "https://ollama.example.invalid/v1",
        api_key=DUMMY_KEY,
        max_retries=0,
        timeout=120.0,
        http_client=http_client,
    )

    records: list[dict[str, Any]] = []
    try:
        # 1. The measured route: the application declares the provider's count.
        for index, question in enumerate(QUESTIONS, start=1):
            phase = f"declared {index}"
            expectation = "submitted, count declared from the provider's usage block"
            try:
                with processor.interaction() as interaction:
                    reply = model.chat.completions.create(
                        model=MODEL, messages=[{"role": "user", "content": question}]
                    )
                    interaction.declare(
                        token_count=reply.usage.total_tokens if reply.usage else None,
                        prompt=question,
                        response=reply.choices[0].message.content if reply.choices else None,
                    )
                records.append(_record(phase, interaction.outcome, expectation=expectation))
            except Exception as exc:  # noqa: BLE001
                records.append(_failed(phase, expectation, exc))

        # 2. The invariant on a real span: usage on the span, nothing declared.
        phase = "span usage only"
        expectation = "refused: a span's count is never submitted"
        try:
            with processor.interaction() as interaction:
                model.chat.completions.create(
                    model=MODEL,
                    messages=[{"role": "user", "content": "Name one European capital, one word."}],
                )
            records.append(_record(phase, interaction.outcome, expectation=expectation))
        except Exception as exc:  # noqa: BLE001
            records.append(_failed(phase, expectation, exc))

        # 3. A stream without include_usage: the provider publishes no count.
        phase = "stream without usage"
        expectation = "refused: the provider published no count, nothing to declare"
        try:
            with processor.interaction() as interaction:
                stream = model.chat.completions.create(
                    model=MODEL,
                    messages=[{"role": "user", "content": "Say hello in one word."}],
                    stream=True,
                )
                usage = None
                for chunk in stream:
                    usage = getattr(chunk, "usage", None) or usage
                interaction.declare(token_count=usage.total_tokens if usage else None)
            records.append(_record(phase, interaction.outcome, expectation=expectation))
        except Exception as exc:  # noqa: BLE001
            records.append(_failed(phase, expectation, exc))
    finally:
        OpenAIInstrumentor().uninstrument()

    return records
