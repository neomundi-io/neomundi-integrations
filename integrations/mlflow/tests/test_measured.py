"""What is measured, and exactly what is sent.

Criteria C1 (offline half), C4, M4, M7, M10, M15. Every span comes from the
real ``mlflow.openai.autolog()`` around a real ``openai`` client.
"""

from __future__ import annotations

import asyncio

from conftest import (
    SECRET_PROMPT,
    SECRET_RESPONSE,
    Provider,
    by_writer,
    chat,
    last_trace,
    model_calls,
    respond,
    use_experiment,
)

from mlflow_neomundi import (
    NOT_SUBMITTED_AGGREGATE_SPAN,
    NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT,
    SUBMITTED,
    TAG_MEASUREMENT_VERSION,
    TAG_REQUEST_ID_PREFIX,
)


def test_chat_call_is_measured_with_the_provider_total(attach, recorder):
    processor = attach()
    assert chat() == SECRET_RESPONSE
    processor.flush()

    [outcome] = model_calls(processor.outcomes)
    assert outcome.status == SUBMITTED
    assert outcome.writer == "openai"
    assert outcome.token_count == 18
    assert outcome.request_id
    assert outcome.model_name == "test-model-served"

    [body] = recorder.bodies()
    assert body["raw_metrics"]["token_count"] == 18
    assert body["raw_metrics"]["model_name"] == "test-model-served"
    assert body["llm_prompt"] == SECRET_PROMPT
    assert body["llm_response"] == SECRET_RESPONSE


def test_latency_is_the_span_mlflow_recorded(attach, recorder):
    """C4: the latency on the wire is end - start of the MLflow span."""
    processor = attach()
    chat()
    processor.flush()

    [outcome] = model_calls(processor.outcomes)
    span = next(s for s in last_trace().data.spans if s.span_id == outcome.span_id)
    expected = (span.end_time_ns - span.start_time_ns) / 1e6
    assert outcome.latency_ms == expected
    assert recorder.bodies()[0]["raw_metrics"]["latency_ms"] == expected


def test_responses_api_is_measured(attach, recorder):
    processor = attach()
    assert respond() == SECRET_RESPONSE
    processor.flush()

    [outcome] = model_calls(processor.outcomes)
    assert outcome.status == SUBMITTED
    assert outcome.token_count == 18
    assert recorder.bodies()[0]["llm_response"] == SECRET_RESPONSE


def test_stream_with_include_usage_is_measured(attach, recorder):
    """M15."""
    processor = attach()
    text = chat(stream=True, stream_options={"include_usage": True})
    processor.flush()

    assert text == SECRET_RESPONSE
    [outcome] = model_calls(processor.outcomes)
    assert outcome.status == SUBMITTED
    assert outcome.token_count == 18
    assert recorder.count == 1


def test_parent_application_span_is_an_aggregate(attach, recorder):
    processor = attach()
    chat()
    processor.flush()

    parents = [o for o in processor.outcomes if o.span_type == "AGENT"]
    assert [o.status for o in parents] == [NOT_SUBMITTED_AGGREGATE_SPAN]
    assert recorder.count == 1


def test_request_id_written_on_the_trace(attach):
    """M10: the trace leads to its observation, re-read from the store."""
    processor = attach()
    chat()
    processor.flush()

    [outcome] = model_calls(processor.outcomes)
    assert outcome.trace_tagged is True
    tags = last_trace().info.tags
    assert tags[f"{TAG_REQUEST_ID_PREFIX}{outcome.span_id}"] == outcome.request_id
    assert tags[TAG_MEASUREMENT_VERSION] == outcome.measurement_version


def test_tagging_can_be_turned_off(attach):
    processor = attach(tag_traces=False)
    chat()
    processor.flush()

    [outcome] = model_calls(processor.outcomes)
    assert outcome.status == SUBMITTED
    assert outcome.trace_tagged is None
    assert not [k for k in last_trace().info.tags if k.startswith("neomundi.")]


def test_agents_run_not_double_counted(attach, recorder):
    """M7: under mlflow.openai.autolog(), an Agents SDK run yields two
    CHAT_MODEL spans per provider call -- the SDK tracer's and the openai
    client's. Exactly one observation per call is billed."""
    from agents import Agent, OpenAIChatCompletionsModel, OpenAIResponsesModel, Runner

    for model_cls in (OpenAIChatCompletionsModel, OpenAIResponsesModel):
        recorder.requests.clear()
        processor = attach()
        provider = Provider()
        agent = Agent(
            name="capital-agent",
            instructions="Answer briefly.",
            model=model_cls(model="test-model", openai_client=provider.async_client()),
        )
        result = asyncio.run(Runner.run(agent, SECRET_PROMPT))
        processor.flush()

        assert result.final_output == SECRET_RESPONSE
        assert len(provider.paths) == 1
        assert recorder.count == 1, model_cls.__name__
        assert [o.status for o in by_writer(processor.outcomes, "openai")] == [SUBMITTED]
        outer = by_writer(processor.outcomes, "openai-agent")
        assert [o.status for o in outer] == [NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT]


def test_submitted_count_counts_billed_calls(attach):
    processor = attach()
    chat()
    chat()
    processor.flush()
    assert processor.submitted_count == 2


def test_metadata_reaches_the_api(attach, recorder):
    processor = attach(metadata={"role": "devrel-verify", "excluded_from": "x"})
    chat()
    processor.flush()
    metadata = recorder.bodies()[0]["metadata"]
    assert metadata["role"] == "devrel-verify"
    assert metadata["excluded_from"] == "x"
    assert metadata["client_trace_id"].startswith("tr-")


def test_measured_after_set_experiment(attach, recorder):
    """The hook survives the call that loses an OTel processor (probe 03)."""
    processor = attach()
    use_experiment("mlflow-neomundi-tests-other")
    try:
        chat()
        processor.flush()
    finally:
        use_experiment("mlflow-neomundi-tests")
    assert processor.installed
    assert recorder.count == 1


def test_nothing_is_written_in_the_working_directory(attach):
    """MLflow's default artifact root is ./mlruns under the current directory;
    the suite, like the examples, keeps everything in its temp store."""
    from pathlib import Path

    processor = attach()
    chat()
    processor.flush()
    last_trace()
    assert not (Path.cwd() / "mlruns").exists()
