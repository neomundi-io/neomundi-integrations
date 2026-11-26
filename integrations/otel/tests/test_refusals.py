"""Every refusal submits nothing, invents nothing, and says why.

Criteria C2, C5, O1, O6, O7, O8, O9. Each test asserts **zero requests** on
the NeoMundi mock -- the observable proof that nothing was billed.
"""

from __future__ import annotations

import openai
import pytest
from conftest import (
    Recorder,
    assert_no_secret,
    call,
    declared_total,
    make_client,
    openai_provider,
)
from opentelemetry.sdk.trace import TracerProvider

from otel_neomundi import (
    NOT_SUBMITTED_AMBIGUOUS_SPANS,
    NOT_SUBMITTED_APPLICATION_ERROR,
    NOT_SUBMITTED_INVALID_TOKEN_COUNT,
    NOT_SUBMITTED_LOCAL_ONLY,
    NOT_SUBMITTED_NO_GENAI_SPAN,
    NOT_SUBMITTED_NO_TOKEN_COUNT,
    NOT_SUBMITTED_UNSUPPORTED_OPERATION,
    SKIPPED_SAMPLING,
    NeoMundiSpanProcessor,
)


def _refused(outcome, status, recorder):
    assert outcome.status == status, outcome
    assert outcome.submitted is False
    assert outcome.request_id is None
    assert outcome.reason
    assert recorder.count == 0
    assert_no_secret(outcome)


def test_span_usage_alone_is_refused(pipeline):
    """O1, the invariant: the span carries 11/7, nothing is declared -> nothing sent."""
    recorder = Recorder()
    processor = pipeline.attach(client=make_client(recorder=recorder))
    with processor.interaction() as interaction:
        call()

    span = pipeline.genai_spans()[0]
    assert span.attributes["gen_ai.usage.input_tokens"] == 11  # precondition
    _refused(interaction.outcome, NOT_SUBMITTED_NO_TOKEN_COUNT, recorder)
    assert interaction.outcome.span_reported_usage is True
    assert "cannot be traced to the provider" in interaction.outcome.reason
    assert interaction.outcome.token_count is None


def test_silent_provider_is_refused(pipeline):
    """The provider published no usage: the app has nothing to declare."""
    recorder = Recorder()
    processor = pipeline.attach(client=make_client(recorder=recorder))
    with processor.interaction() as interaction:
        reply = call(openai_provider(None))
        interaction.declare(token_count=declared_total(reply))
    assert declared_total(reply) is None
    _refused(interaction.outcome, NOT_SUBMITTED_NO_TOKEN_COUNT, recorder)
    assert interaction.outcome.span_reported_usage is False


@pytest.mark.parametrize("bad", [0, -1, True, "18", 18.0, [18]])
def test_invalid_declared_count_is_refused(pipeline, bad):
    recorder = Recorder()
    processor = pipeline.attach(client=make_client(recorder=recorder))
    with processor.interaction() as interaction:
        call()
        interaction.declare(token_count=bad)
    _refused(interaction.outcome, NOT_SUBMITTED_INVALID_TOKEN_COUNT, recorder)


def test_no_genai_span_is_refused(pipeline):
    recorder = Recorder()
    processor = pipeline.attach(client=make_client(recorder=recorder))
    with processor.interaction() as interaction:
        interaction.declare(token_count=18)  # a count with nothing observed
    _refused(interaction.outcome, NOT_SUBMITTED_NO_GENAI_SPAN, recorder)


def test_processor_on_another_provider_is_refused(pipeline):
    """Attached to a provider the instrumentation does not use: said, not guessed."""
    recorder = Recorder()
    other = TracerProvider()
    processor = NeoMundiSpanProcessor(make_client(recorder=recorder), tracer_provider=other)
    with processor.interaction() as interaction:
        interaction.declare(token_count=declared_total(call()))
    _refused(interaction.outcome, NOT_SUBMITTED_NO_GENAI_SPAN, recorder)
    assert "not attached" in interaction.outcome.reason


def test_trace_dropped_by_the_application_sampler_is_refused():
    """Head sampling is the application's: an unsampled trace is never measured."""
    from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor
    from opentelemetry.sdk.trace.sampling import ALWAYS_OFF

    provider = TracerProvider(sampler=ALWAYS_OFF)
    recorder = Recorder()
    processor = NeoMundiSpanProcessor(make_client(recorder=recorder), tracer_provider=provider)
    provider.add_span_processor(processor)
    OpenAIInstrumentor().instrument(tracer_provider=provider)
    try:
        with processor.interaction() as interaction:
            interaction.declare(token_count=declared_total(call()))
    finally:
        OpenAIInstrumentor().uninstrument()
    _refused(interaction.outcome, NOT_SUBMITTED_NO_GENAI_SPAN, recorder)
    assert "sampler dropped the trace" in interaction.outcome.reason


def test_two_calls_in_one_interaction_are_refused(pipeline):
    """O6: which call a count belongs to is never guessed."""
    recorder = Recorder()
    processor = pipeline.attach(client=make_client(recorder=recorder))
    with processor.interaction() as interaction:
        call()
        reply = call()
        interaction.declare(token_count=declared_total(reply))
    _refused(interaction.outcome, NOT_SUBMITTED_AMBIGUOUS_SPANS, recorder)
    # No single span was examined: the outcome must not claim "no usage".
    assert interaction.outcome.span_reported_usage is None


def test_failed_model_call_caught_by_the_app_is_refused(pipeline):
    """O7: the app swallowed the provider's 500; the span says ERROR."""
    recorder = Recorder()
    processor = pipeline.attach(client=make_client(recorder=recorder))
    with processor.interaction() as interaction:
        try:
            call(openai_provider(status=500))
        except openai.InternalServerError:
            pass
        interaction.declare(token_count=18)
    _refused(interaction.outcome, NOT_SUBMITTED_APPLICATION_ERROR, recorder)


def test_application_exception_is_re_raised_and_refused(pipeline):
    recorder = Recorder()
    processor = pipeline.attach(client=make_client(recorder=recorder))
    with pytest.raises(openai.InternalServerError):
        with processor.interaction() as interaction:
            call(openai_provider(status=500))
    _refused(interaction.outcome, NOT_SUBMITTED_APPLICATION_ERROR, recorder)
    assert interaction.outcome.error_type == "InternalServerError"


def test_unsupported_operation_is_refused(pipeline):
    recorder = Recorder()
    processor = pipeline.attach(client=make_client(recorder=recorder))
    tracer = pipeline.provider.get_tracer("someone.else")
    with processor.interaction() as interaction:
        with tracer.start_as_current_span("embeddings m") as span:
            span.set_attribute("gen_ai.operation.name", "embeddings")
            span.set_attribute("gen_ai.provider.name", "openai")
        interaction.declare(token_count=18)
    _refused(interaction.outcome, NOT_SUBMITTED_UNSUPPORTED_OPERATION, recorder)


def test_span_without_operation_name_is_refused(pipeline):
    """An old instrumentation that writes gen_ai.system but no operation."""
    recorder = Recorder()
    processor = pipeline.attach(client=make_client(recorder=recorder))
    tracer = pipeline.provider.get_tracer("opentelemetry.instrumentation.openai.v1", "0.30.0")
    with processor.interaction() as interaction:
        with tracer.start_as_current_span("openai.chat") as span:
            span.set_attribute("gen_ai.system", "OpenAI")
            span.set_attribute("gen_ai.usage.prompt_tokens", 7)
        interaction.declare(token_count=18)
    _refused(interaction.outcome, NOT_SUBMITTED_UNSUPPORTED_OPERATION, recorder)


def test_sample_rate_zero_opens_no_socket(pipeline):
    """C5."""
    recorder = Recorder()
    processor = pipeline.attach(client=make_client(recorder=recorder), sample_rate=0.0)
    with processor.interaction() as interaction:
        interaction.declare(token_count=declared_total(call()))
    _refused(interaction.outcome, SKIPPED_SAMPLING, recorder)


def test_sampling_is_deterministic_in_trace_id(pipeline):
    """O9: the same trace id gives the same decision, every time."""
    processor = pipeline.attach(submit=False, sample_rate=0.5)
    ids = [int(f"{i:032x}", 16) + (i << 64) for i in range(1, 200)]
    first = [processor._should_sample(t) for t in ids]
    again = [processor._should_sample(t) for t in ids]
    assert first == again
    assert 0 < sum(first) < len(ids)


def test_submit_false_needs_no_key_and_opens_no_socket(pipeline):
    processor = pipeline.attach(submit=False)
    with processor.interaction() as interaction:
        interaction.declare(token_count=declared_total(call()))
    outcome = interaction.outcome
    assert outcome.status == NOT_SUBMITTED_LOCAL_ONLY
    assert outcome.token_count == 18
    assert outcome.latency_ms is not None and outcome.latency_ms >= 0


def test_construction_refuses_a_missing_client():
    with pytest.raises(ValueError):
        NeoMundiSpanProcessor()
    with pytest.raises(ValueError):
        NeoMundiSpanProcessor(api_key="k")
    with pytest.raises(ValueError):
        NeoMundiSpanProcessor(submit=False, sample_rate=1.5)
