"""What a measured interaction sends, and what it writes into the trace.

Criteria C4, C8, O2, O3, O4, O5, O10 (missions/004-otel/CRITERIA.md in
neomundi-devrel). Every span here was produced by the real openai-v2
instrumentation around the real ``openai`` client.
"""

from __future__ import annotations

import asyncio
import time

import httpx
from conftest import (
    MESSAGES,
    SECRET_PROMPT,
    SECRET_RESPONSE,
    TEST_NEOMUNDI_KEY,
    TEST_PROVIDER_KEY,
    Recorder,
    assert_no_secret,
    async_openai_provider,
    call,
    declared_total,
    load_fixture,
    make_client,
    openai_provider,
)

from otel_neomundi import INTERACTION_SPAN_NAME, NOT_SUBMITTED_LOCAL_ONLY, SUBMITTED


def test_measured_interaction_submits_the_declared_count(pipeline):
    recorder = Recorder()
    processor = pipeline.attach(client=make_client(recorder=recorder))

    with processor.interaction() as interaction:
        reply = call()
        interaction.declare(token_count=declared_total(reply))

    outcome = interaction.outcome
    assert outcome.status == SUBMITTED, outcome
    assert outcome.submitted is True
    assert outcome.request_id == load_fixture()["request_id"]
    assert outcome.token_count == 18
    assert outcome.token_source == "caller_declared"
    assert outcome.model_name == "test-model-served"
    assert outcome.operation == "chat"
    assert outcome.provider == "openai"
    assert outcome.span_reported_usage is True

    assert recorder.count == 1
    sent = recorder.bodies()[0]
    assert sent["raw_metrics"]["token_count"] == 18
    assert sent["raw_metrics"]["model_name"] == "test-model-served"
    assert sent["raw_metrics"]["latency_ms"] == outcome.latency_ms
    assert sent["metadata"]["client_trace_id"] == f"{outcome.trace_id}-{outcome.span_id}"
    assert_no_secret(outcome)


def test_latency_is_the_span_not_the_measurement(pipeline):
    """C4: a slow NeoMundi API does not lengthen the reported latency."""
    payload = load_fixture()

    def slow(request: httpx.Request) -> httpx.Response:
        time.sleep(0.4)
        return httpx.Response(200, json=payload)

    processor = pipeline.attach(client=make_client(slow))
    with processor.interaction() as interaction:
        interaction.declare(token_count=declared_total(call()))

    genai = pipeline.genai_spans()[0]
    expected = (genai.end_time - genai.start_time) / 1e6
    outcome = interaction.outcome
    assert outcome.status == SUBMITTED
    assert outcome.latency_ms == expected
    assert outcome.latency_ms < 400


def test_declared_count_wins_over_span(pipeline):
    """O2: the span says 7+5, the caller declares 18 -- 18 is what is sent."""
    recorder = Recorder()
    processor = pipeline.attach(client=make_client(recorder=recorder))
    provider = openai_provider({"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12})

    with processor.interaction() as interaction:
        call(provider)
        interaction.declare(token_count=18)

    usage = {k: v for k, v in pipeline.genai_spans()[0].attributes.items() if "usage" in k}
    assert usage == {"gen_ai.usage.input_tokens": 7, "gen_ai.usage.output_tokens": 5}
    assert recorder.bodies()[0]["raw_metrics"]["token_count"] == 18


def test_request_id_is_written_into_the_trace(pipeline):
    """O4: the application's own exporter receives the request_id, same trace."""
    processor = pipeline.attach(client=make_client())
    with processor.interaction() as interaction:
        interaction.declare(token_count=declared_total(call()))

    [marker] = pipeline.spans(INTERACTION_SPAN_NAME)
    [genai] = pipeline.genai_spans()
    assert marker.attributes["neomundi.request_id"] == interaction.outcome.request_id
    assert marker.attributes["neomundi.status"] == SUBMITTED
    assert marker.context.trace_id == genai.context.trace_id
    assert genai.parent.span_id == marker.context.span_id
    assert f"{genai.context.trace_id:032x}" == interaction.outcome.trace_id


def test_existing_pipeline_unchanged(pipeline):
    """O5: the GenAI span the exporter receives is the same with or without us."""
    call()
    without = pipeline.genai_spans()[0]
    pipeline.exporter.clear()

    processor = pipeline.attach(submit=False)
    with processor.interaction() as interaction:
        interaction.declare(token_count=declared_total(call()))
    with_ = pipeline.genai_spans()[0]

    def shape(span):
        attrs = dict(span.attributes)
        return span.name, span.kind, span.status.status_code, attrs

    assert shape(without) == shape(with_)
    assert interaction.outcome.status == NOT_SUBMITTED_LOCAL_ONLY


def test_span_content_never_reaches_the_wire(content_pipeline):
    """O3 and C8: content capture ON in the instrumentation, nothing leaks."""
    recorder = Recorder()
    processor = content_pipeline.attach(client=make_client(recorder=recorder))
    with processor.interaction() as interaction:
        interaction.declare(token_count=declared_total(call()))

    genai = content_pipeline.genai_spans()[0]
    # Precondition: the instrumentation really did capture the content.
    assert SECRET_PROMPT in str(genai.attributes.get("gen_ai.input.messages"))
    assert interaction.outcome.status == SUBMITTED

    wire = recorder.requests[0].content.decode("utf-8")
    for secret in (SECRET_PROMPT, SECRET_RESPONSE, TEST_PROVIDER_KEY):
        assert secret not in wire
    assert "llm_prompt" not in recorder.bodies()[0] or recorder.bodies()[0]["llm_prompt"] is None
    [marker] = content_pipeline.spans(INTERACTION_SPAN_NAME)
    for secret in (SECRET_PROMPT, SECRET_RESPONSE, TEST_NEOMUNDI_KEY):
        assert secret not in str(dict(marker.attributes))
    assert_no_secret(interaction.outcome)


def test_declared_text_is_sent_only_when_declared(pipeline):
    recorder = Recorder()
    processor = pipeline.attach(client=make_client(recorder=recorder))
    with processor.interaction() as interaction:
        reply = call()
        interaction.declare(
            token_count=declared_total(reply),
            prompt=MESSAGES[0]["content"],
            response=reply.choices[0].message.content,
        )
    sent = recorder.bodies()[0]
    assert sent["llm_prompt"] == SECRET_PROMPT
    assert sent["llm_response"] == SECRET_RESPONSE
    assert sent["raw_metrics"]["prompt_length"] == len(SECRET_PROMPT)
    assert_no_secret(interaction.outcome)


def test_streamed_call_is_measured_with_the_declared_count(pipeline):
    recorder = Recorder()
    processor = pipeline.attach(client=make_client(recorder=recorder))
    with processor.interaction() as interaction:
        call(stream=True)
        interaction.declare(token_count=18)
    assert interaction.outcome.status == SUBMITTED
    assert recorder.bodies()[0]["raw_metrics"]["token_count"] == 18


def test_nested_application_span_is_followed(pipeline):
    """The GenAI span need not be a direct child: an app span in between is fine."""
    processor = pipeline.attach(submit=False)
    tracer = pipeline.provider.get_tracer("app")
    with processor.interaction() as interaction:
        with tracer.start_as_current_span("app.step"):
            reply = call()
        interaction.declare(token_count=declared_total(reply))
    assert interaction.outcome.status == NOT_SUBMITTED_LOCAL_ONLY
    assert interaction.outcome.token_count == 18


def test_async_interaction(pipeline):
    recorder = Recorder()
    processor = pipeline.attach(client=make_client(recorder=recorder))

    async def run():
        client = async_openai_provider()
        async with processor.interaction() as interaction:
            reply = await client.chat.completions.create(model="test-model", messages=MESSAGES)
            interaction.declare(token_count=declared_total(reply))
        return interaction

    interaction = asyncio.run(run())
    assert interaction.outcome.status == SUBMITTED, interaction.outcome
    assert recorder.count == 1
