"""What is measured, and exactly what reaches the wire.

Criteria C4, C8, A1, A3, A4, A9, A14 (missions/005-openai-agents/CRITERIA.md).
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import httpx
from agents import TracingProcessor
from agents.tracing import get_trace_provider
from conftest import (
    SECRET_INSTRUCTIONS,
    SECRET_PROMPT,
    SECRET_RESPONSE,
    SECRET_TOOL_OUTPUT,
    Provider,
    assert_no_secret,
    by_type,
    load_fixture,
    responses,
    run_agent,
)

from openai_agents_neomundi import (
    NOT_SUBMITTED_AGGREGATE_SPAN,
    NOT_SUBMITTED_LOCAL_ONLY,
    NOT_SUBMITTED_NOT_A_MODEL_CALL,
    SUBMITTED,
    NeoMundiTraceProcessor,
)


class SpanLog(TracingProcessor):
    """Somebody else's processor, already registered by the application."""

    def __init__(self) -> None:
        self.ended: list[Any] = []

    def on_trace_start(self, trace: Any) -> None:
        pass

    def on_trace_end(self, trace: Any) -> None:
        pass

    def on_span_start(self, span: Any) -> None:
        pass

    def on_span_end(self, span: Any) -> None:
        self.ended.append(span)

    def shutdown(self) -> None:
        pass

    def force_flush(self) -> None:
        pass


def _names(processors: Any) -> list[str]:
    return [type(p).__name__ for p in processors]


def test_existing_processors_kept(attach):
    """A1: install() appends. The SDK's exporter and the app's processor stay, and see every span."""
    from agents import add_trace_processor

    theirs = SpanLog()
    add_trace_processor(theirs)
    before = _names(get_trace_provider()._multi_processor._processors)  # noqa: SLF001
    assert "BatchTraceProcessor" in before

    processor = attach()
    after = _names(get_trace_provider()._multi_processor._processors)  # noqa: SLF001
    assert after == [*before, "NeoMundiTraceProcessor"]

    run_agent()
    processor.flush()
    assert len(theirs.ended) == 7  # 2 responses, 1 function, 2 turns, 1 agent, 1 task
    assert len(processor.outcomes) == 7


def test_tool_run_submits_one_per_model_call(attach, recorder):
    """A3: two model calls around one tool -> exactly two observations, aggregates refused."""
    processor = attach()
    result = run_agent()
    processor.flush()

    assert result.final_output == SECRET_RESPONSE
    assert recorder.count == 2
    assert [o.status for o in responses(processor.outcomes)] == [SUBMITTED, SUBMITTED]
    for kind in ("TurnSpanData", "TaskSpanData"):
        assert {o.status for o in by_type(processor.outcomes, kind)} == {
            NOT_SUBMITTED_AGGREGATE_SPAN
        }
    # The task span alone says 36: measuring it would have doubled the bill.
    assert sum(b["raw_metrics"]["token_count"] for b in recorder.bodies()) == 36


def test_provider_total_is_what_is_sent(attach, recorder):
    """A4: the provider published 11/7/18; 18 is on the wire, labelled as the provider's."""
    processor = attach()
    run_agent(tools=False)
    processor.flush()

    (outcome,) = responses(processor.outcomes)
    assert outcome.status == SUBMITTED
    assert outcome.token_count == 18
    assert outcome.token_source == "provider_response_usage_total"
    assert outcome.model_name == "test-model-served"
    assert outcome.request_id == load_fixture()["request_id"]
    (body,) = recorder.bodies()
    assert body["raw_metrics"]["token_count"] == 18
    assert body["raw_metrics"]["model_name"] == "test-model-served"
    assert body["llm_prompt"] == SECRET_PROMPT
    assert body["llm_response"] == SECRET_RESPONSE


def test_latency_is_the_span_not_the_measurement(attach, recorder):
    """C4: latency == ended_at - started_at; a 300 ms NeoMundi API does not enter it."""
    spans: list[Any] = []

    class Keep(SpanLog):
        def on_span_end(self, span: Any) -> None:
            if type(span.span_data).__name__ == "ResponseSpanData":
                spans.append(span)

    from agents import add_trace_processor

    add_trace_processor(Keep())

    def slow(request: httpx.Request) -> httpx.Response:
        time.sleep(0.3)
        return httpx.Response(200, json=load_fixture())

    processor = attach(slow)
    run_agent(Provider(delay=0.05), tools=False)
    processor.flush()

    (outcome,) = responses(processor.outcomes)
    (span,) = spans
    expected = (
        datetime.fromisoformat(span.ended_at) - datetime.fromisoformat(span.started_at)
    ).total_seconds() * 1000.0
    assert outcome.latency_ms == expected
    assert 50.0 <= outcome.latency_ms < 300.0
    assert abs(recorder.bodies()[0]["raw_metrics"]["latency_ms"] - expected) < 0.01


def test_outcome_names_agent_and_tools(attach):
    """A9: the call names its agent and the tool it asked for; the tool run is reported by name."""
    processor = attach()
    run_agent()
    processor.flush()

    first, second = responses(processor.outcomes)
    assert first.agent_name == "weather-agent"
    assert first.tool_calls == ("weather",)
    assert second.tool_calls == ()
    (tool,) = by_type(processor.outcomes, "FunctionSpanData")
    assert tool.status == NOT_SUBMITTED_NOT_A_MODEL_CALL
    assert tool.tool_name == "weather"
    assert tool.agent_name == "weather-agent"
    assert tool.reason
    (agent,) = by_type(processor.outcomes, "AgentSpanData")
    assert agent.status == NOT_SUBMITTED_NOT_A_MODEL_CALL
    assert agent.agent_name == "weather-agent"


def test_streamed_run_is_measured(attach, recorder):
    """A14: Runner.run_streamed measures exactly as Runner.run does."""
    processor = attach()
    result = run_agent(stream=True)
    processor.flush()

    assert result.final_output == SECRET_RESPONSE
    assert [o.status for o in responses(processor.outcomes)] == [SUBMITTED, SUBMITTED]
    assert [b["raw_metrics"]["token_count"] for b in recorder.bodies()] == [18, 18]


def test_nothing_secret_in_outcomes_or_logs(attach, caplog):
    """C8: no prompt, response, tool output, instructions or key in outcomes or logs."""
    caplog.set_level("DEBUG")
    processor = attach()
    run_agent()
    processor.flush()
    for outcome in processor.outcomes:
        assert_no_secret(outcome)
    ours = [r for r in caplog.records if "neomundi" in r.name.lower()]
    for record in ours:
        message = record.getMessage()
        for secret in (SECRET_PROMPT, SECRET_RESPONSE, SECRET_TOOL_OUTPUT, SECRET_INSTRUCTIONS):
            assert secret not in message


def test_capture_off_sends_no_text(attach, recorder):
    """capture_prompt/capture_response=False: lengths and text both absent."""
    processor = attach(capture_prompt=False, capture_response=False)
    run_agent(tools=False)
    processor.flush()
    (body,) = recorder.bodies()
    assert "llm_prompt" not in body and "llm_response" not in body
    assert SECRET_PROMPT not in str(body) and SECRET_RESPONSE not in str(body)


def test_submit_false_needs_no_key_and_opens_no_socket(attach, recorder):
    processor = attach(submit=False)
    run_agent()
    processor.flush()
    assert recorder.count == 0
    statuses = [o.status for o in responses(processor.outcomes)]
    assert statuses == [NOT_SUBMITTED_LOCAL_ONLY, NOT_SUBMITTED_LOCAL_ONLY]
    assert all(o.token_count == 18 for o in responses(processor.outcomes))


def test_user_metadata_is_forwarded_with_the_span_id(attach, recorder):
    processor = attach(metadata={"role": "devrel-verify", "excluded_from": "x"})
    run_agent(tools=False)
    processor.flush()
    (body,) = recorder.bodies()
    (outcome,) = responses(processor.outcomes)
    assert body["metadata"]["role"] == "devrel-verify"
    assert body["metadata"]["client_trace_id"] == outcome.span_id


def test_constructor_contract():
    import pytest

    with pytest.raises(ValueError):
        NeoMundiTraceProcessor()
    with pytest.raises(ValueError):
        NeoMundiTraceProcessor(api_key="k")
    with pytest.raises(ValueError):
        NeoMundiTraceProcessor(submit=False, sample_rate=1.5)
    with pytest.raises(TypeError):
        NeoMundiTraceProcessor(submit=False, sample_rate=True)
    assert NeoMundiTraceProcessor(submit=False).sample_rate == 1.0


def test_installing_twice_does_not_bill_twice(recorder):
    """Found by running example 01: a second install left the first one billing.

    Re-running a notebook cell must not double the bill, and must not evict
    the SDK's exporter either.
    """
    from conftest import Recorder, make_client

    first_calls = Recorder()
    first = NeoMundiTraceProcessor(make_client(recorder=first_calls)).install()
    second = NeoMundiTraceProcessor(make_client(recorder=recorder)).install()
    assert second.install() is second  # idempotent for the same instance

    run_agent()
    first.flush()
    second.flush()

    assert first.retired and not second.retired
    assert first_calls.count == 0
    assert recorder.count == 2
    names = _names(get_trace_provider()._multi_processor._processors)  # noqa: SLF001
    assert names.count("BatchTraceProcessor") == 1
    assert names.count("NeoMundiTraceProcessor") == 2  # still listed, but inert
