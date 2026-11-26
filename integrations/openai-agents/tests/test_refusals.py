"""Every refusal submits nothing, invents nothing, and says why.

Criteria C2, C5, A2, A5, A6, A7, A8, A11. Each test asserts **zero
requests** on the NeoMundi mock, not merely a status.
"""

from __future__ import annotations

import sys
import types

import pytest
from conftest import CHAT_USAGE, Provider, assert_no_secret, by_type, responses, run_agent

from openai_agents_neomundi import (
    ANY_LLM_MODULE,
    NOT_SUBMITTED_AGGREGATE_SPAN,
    NOT_SUBMITTED_APPLICATION_ERROR,
    NOT_SUBMITTED_NO_PROVIDER_RESPONSE,
    NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT,
    SKIPPED_SAMPLING,
    SUBMITTED,
    NeoMundiTraceProcessor,
)


def _refused(processor, recorder, status, *, kind="ResponseSpanData", count=None):
    processor.flush()
    assert recorder.count == 0, "a refusal must open no socket"
    outcomes = by_type(processor.outcomes, kind)
    assert outcomes, f"no outcome for {kind}"
    if count is not None:
        assert len(outcomes) == count
    for outcome in outcomes:
        assert outcome.status == status, outcome
        assert outcome.reason
        assert not outcome.submitted and outcome.request_id is None
        assert outcome.token_count is None
        assert_no_secret(outcome)
    return outcomes


def test_generation_span_is_refused(attach, recorder):
    """A2: Chat Completions, full provider usage 11/7/18 -- still refused, by design."""
    processor = attach()
    result = run_agent(Provider(CHAT_USAGE), api="chat")
    assert result.final_output
    (first, _second) = _refused(
        processor,
        recorder,
        NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT,
        kind="GenerationSpanData",
        count=2,
    )
    assert "estimate" in first.reason and "zero-filled" in first.reason
    assert first.agent_name == "weather-agent"


def test_aggregates_refused(attach, recorder):
    """A3: turn and task spans are named as aggregates, even when nothing else is measured."""
    processor = attach(submit=False)
    run_agent()
    processor.flush()
    turns = by_type(processor.outcomes, "TurnSpanData")
    tasks = by_type(processor.outcomes, "TaskSpanData")
    assert len(turns) == 2 and len(tasks) == 1
    assert {o.status for o in turns + tasks} == {NOT_SUBMITTED_AGGREGATE_SPAN}
    assert recorder.count == 0


@pytest.mark.parametrize(
    ("usage", "why"),
    [
        (None, "published no usage"),
        ({"input_tokens": None, "output_tokens": 7, "total_tokens": None}, "partial"),
        (
            {
                "input_tokens": 0,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": 0,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 0,
            },
            "positive",
        ),
    ],
    ids=["absent", "partial", "zero"],
)
def test_unpublished_count_is_refused(attach, recorder, usage, why):
    """A5: the span's own usage dict says 0/0/0 or 0/7/0 here -- never used, never rebuilt."""
    processor = attach()
    run_agent(Provider(usage), tools=False)
    (outcome,) = _refused(processor, recorder, NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT)
    assert why in outcome.reason
    assert outcome.latency_ms is not None  # observed, but not enough to measure


def test_run_without_sensitive_data_is_refused(attach, recorder):
    """A6: trace_include_sensitive_data=False -> no provider Response on the span."""
    processor = attach()
    run_agent(trace_include_sensitive_data=False)
    (outcome, _) = _refused(processor, recorder, NOT_SUBMITTED_NO_PROVIDER_RESPONSE, count=2)
    assert "trace_include_sensitive_data" in outcome.reason


def test_env_without_sensitive_data_is_refused(attach, recorder, monkeypatch):
    """A6, the same through the environment variable the SDK reads at RunConfig creation."""
    monkeypatch.setenv("OPENAI_AGENTS_TRACE_INCLUDE_SENSITIVE_DATA", "false")
    processor = attach()
    run_agent(tools=False)
    _refused(processor, recorder, NOT_SUBMITTED_NO_PROVIDER_RESPONSE, count=1)


def test_any_llm_loaded_refuses_every_response_span(attach, recorder, monkeypatch):
    """A7: once AnyLLMModel is importable in the process, origin cannot be established."""
    monkeypatch.setitem(sys.modules, ANY_LLM_MODULE, types.ModuleType(ANY_LLM_MODULE))
    processor = attach()
    run_agent()
    (outcome, _) = _refused(processor, recorder, NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT, count=2)
    assert "AnyLLMModel" in outcome.reason


def test_incomplete_response_is_refused(attach, recorder):
    """A8: the provider answered with status 'incomplete' -- the SDK raises, nothing measured."""
    processor = attach()
    with pytest.raises(Exception):  # noqa: B017 - the SDK's own error, whatever its class
        run_agent(Provider(response_status="incomplete"), tools=False)
    processor.flush()
    assert recorder.count == 0
    for outcome in responses(processor.outcomes):
        assert outcome.status == NOT_SUBMITTED_APPLICATION_ERROR
        assert outcome.reason


def test_failed_model_call_is_refused(attach, recorder):
    """A8: the provider returned 500; the span carries the error; nothing measured."""
    processor = attach()
    with pytest.raises(Exception):  # noqa: B017 - the host's own failure, not ours
        run_agent(Provider(http_status=500), tools=False)
    (outcome,) = _refused(processor, recorder, NOT_SUBMITTED_APPLICATION_ERROR)
    assert "failed" in outcome.reason


def test_sample_rate_zero_opens_no_socket(attach, recorder):
    """C5."""
    processor = attach(sample_rate=0.0)
    run_agent()
    _refused(processor, recorder, SKIPPED_SAMPLING, count=2)


def test_kill_switch_at_runtime(attach, recorder):
    processor = attach()
    run_agent(tools=False)
    processor.flush()
    assert recorder.count == 1
    processor.sample_rate = 0.0
    run_agent(tools=False)
    processor.flush()
    assert recorder.count == 1
    assert responses(processor.outcomes)[-1].status == SKIPPED_SAMPLING


def test_sampling_is_deterministic_in_span_id():
    """A11: the same span id is always in, or always out."""
    processor = NeoMundiTraceProcessor(submit=False, sample_rate=0.5)
    ids = [f"span_{i:024x}" for i in range(400)]
    first = [processor._should_sample(i) for i in ids]  # noqa: SLF001 - pure function
    second = [processor._should_sample(i) for i in ids]  # noqa: SLF001
    assert first == second
    assert 120 < sum(first) < 280


def test_a_span_is_measured_once(attach, recorder):
    """Ending the same span twice must not bill twice."""
    from agents import TracingProcessor, add_trace_processor

    processor = attach()
    seen = []

    class Again(TracingProcessor):
        def on_trace_start(self, t): ...
        def on_trace_end(self, t): ...
        def on_span_start(self, s): ...
        def on_span_end(self, s):
            if type(s.span_data).__name__ == "ResponseSpanData":
                seen.append(s)

        def shutdown(self): ...
        def force_flush(self): ...

    add_trace_processor(Again())
    run_agent(tools=False)
    for span in seen:
        processor.on_span_end(span)
    processor.flush()
    assert recorder.count == 1
    assert [o.status for o in responses(processor.outcomes)] == [SUBMITTED]
