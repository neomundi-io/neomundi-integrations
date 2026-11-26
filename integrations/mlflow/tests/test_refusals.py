"""What is refused, and that a refusal submits nothing and says why.

Criteria C2, C5, C8, M5, M6, M8, M9, M12, M15. Each refusal asserts zero
requests on the NeoMundi mock and a non-empty reason.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging

import mlflow
import openai
import pytest
from conftest import (
    SECRET_PROMPT,
    SECRET_RESPONSE,
    Provider,
    assert_no_secret,
    by_writer,
    chat,
    last_trace,
    model_calls,
    respond,
)
from mlflow.entities import SpanType

from mlflow_neomundi import (
    NOT_SUBMITTED_AGGREGATE_SPAN,
    NOT_SUBMITTED_APPLICATION_ERROR,
    NOT_SUBMITTED_INVALID_TOKEN_COUNT,
    NOT_SUBMITTED_LOCAL_ONLY,
    NOT_SUBMITTED_NO_TOKEN_COUNT,
    NOT_SUBMITTED_NOT_A_MODEL_CALL,
    NOT_SUBMITTED_UNSUPPORTED_SPAN,
    NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT,
    OUTCOMES,
    SKIPPED_SAMPLING,
    SUBMITTED,
    NeoMundiMlflowProcessor,
)


def _refused(processor, recorder, status: str, writer: str | None = "openai"):
    processor.flush()
    calls = by_writer(processor.outcomes, writer)
    assert calls, processor.outcomes
    for outcome in calls:
        assert outcome.status == status, outcome
        assert outcome.reason
        assert not outcome.submitted and outcome.request_id is None
        assert outcome.token_count is None
    assert recorder.count == 0
    return calls


# ------------------------------------------------------- the provider's count


def test_no_usage_is_refused_not_zeroed(attach, recorder):
    """M5 / #33 Q2: a silent provider gives no attribute, and no measurement."""
    processor = attach()
    assert chat(Provider(usage=None)) == SECRET_RESPONSE
    [outcome] = _refused(processor, recorder, NOT_SUBMITTED_NO_TOKEN_COUNT)
    assert "never estimated" in outcome.reason


def test_no_usage_on_responses_is_refused(attach, recorder):
    processor = attach()
    respond(Provider(usage=None))
    _refused(processor, recorder, NOT_SUBMITTED_NO_TOKEN_COUNT)


@pytest.mark.parametrize(
    "usage",
    [
        {"prompt_tokens": 11, "completion_tokens": 7},
        {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": None},
        {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    ],
    ids=["no-total", "null-total", "zero-total"],
)
def test_partial_or_zero_usage_is_never_rebuilt(attach, recorder, usage):
    processor = attach()
    chat(Provider(usage=usage))
    [outcome] = _refused(processor, recorder, NOT_SUBMITTED_INVALID_TOKEN_COUNT)
    assert "rebuilt" in outcome.reason


def test_stream_without_include_usage_is_refused(attach, recorder):
    """M15."""
    processor = attach()
    assert chat(stream=True) == SECRET_RESPONSE
    _refused(processor, recorder, NOT_SUBMITTED_NO_TOKEN_COUNT)


# --------------------------------------------------------- other writers (M6)


def test_agents_tracer_zero_fill_is_refused(attach, recorder):
    """The Agents SDK tracer writes 0/0/0 for a silent provider. Refused."""
    from agents import Agent, OpenAIChatCompletionsModel, Runner

    processor = attach()
    provider = Provider(usage=None)
    agent = Agent(
        name="capital-agent",
        instructions="Answer briefly.",
        model=OpenAIChatCompletionsModel(model="test-model", openai_client=provider.async_client()),
    )
    asyncio.run(Runner.run(agent, SECRET_PROMPT))
    processor.flush()

    spans = {s.span_id: s for s in last_trace().data.spans}
    [outer] = by_writer(processor.outcomes, "openai-agent")
    # The fact the refusal rests on, re-read from the stored trace:
    assert spans[outer.span_id].attributes["mlflow.chat.tokenUsage"]["total_tokens"] == 0
    assert outer.status == NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT
    assert "zero-fills" in outer.reason
    [inner] = by_writer(processor.outcomes, "openai")
    assert inner.status == NOT_SUBMITTED_NO_TOKEN_COUNT
    assert recorder.count == 0


def test_langchain_estimate_is_refused(attach, recorder):
    """A LangChain chat model that computes its own usage_metadata."""
    pytest.importorskip("langchain_core")
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class Estimating(FakeListChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            words = sum(len(str(m.content).split()) for m in messages)
            message = AIMessage(
                content=SECRET_RESPONSE,
                usage_metadata={
                    "input_tokens": words,
                    "output_tokens": 1,
                    "total_tokens": words + 1,
                },
            )
            return ChatResult(generations=[ChatGeneration(message=message)])

    mlflow.langchain.autolog()
    try:
        processor = attach()
        Estimating(responses=[SECRET_RESPONSE]).invoke(SECRET_PROMPT)
        processor.flush()
    finally:
        mlflow.langchain.autolog(disable=True)

    spans = {s.span_id: s for s in last_trace().data.spans}
    [outcome] = by_writer(processor.outcomes, "langchain")
    assert spans[outcome.span_id].attributes["mlflow.chat.tokenUsage"]["total_tokens"] > 0
    assert outcome.status == NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT
    assert "LangChain" in outcome.reason
    assert recorder.count == 0


def test_manual_span_with_a_count_is_refused(attach, recorder):
    """A span with no writer: LiteLLM's logger writes this way (estimate)."""
    processor = attach()
    with mlflow.start_span(name="litellm-completion", span_type=SpanType.LLM) as span:
        span.set_attribute(
            "mlflow.chat.tokenUsage",
            {"input_tokens": 14, "output_tokens": 7, "total_tokens": 21},
        )
    [outcome] = _refused(processor, recorder, NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT, writer=None)
    assert "LiteLLM" in outcome.reason


# ------------------------------------------------------------- span types (M8)

EXPECTED_BY_TYPE = {
    "AGENT": NOT_SUBMITTED_AGGREGATE_SPAN,
    "CHAIN": NOT_SUBMITTED_AGGREGATE_SPAN,
    "WORKFLOW": NOT_SUBMITTED_AGGREGATE_SPAN,
    "TASK": NOT_SUBMITTED_AGGREGATE_SPAN,
    "TOOL": NOT_SUBMITTED_NOT_A_MODEL_CALL,
    "RETRIEVER": NOT_SUBMITTED_NOT_A_MODEL_CALL,
    "RERANKER": NOT_SUBMITTED_NOT_A_MODEL_CALL,
    "EMBEDDING": NOT_SUBMITTED_NOT_A_MODEL_CALL,
    "GUARDRAIL": NOT_SUBMITTED_NOT_A_MODEL_CALL,
    "PARSER": NOT_SUBMITTED_NOT_A_MODEL_CALL,
    "MEMORY": NOT_SUBMITTED_NOT_A_MODEL_CALL,
    "EVALUATOR": NOT_SUBMITTED_NOT_A_MODEL_CALL,
    "UNKNOWN": NOT_SUBMITTED_UNSUPPORTED_SPAN,
    # A model-call type written by hand names no writer: its count is unverified.
    "LLM": NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT,
    "CHAT_MODEL": NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT,
}

REAL_SPAN_TYPES = sorted(k for k in vars(SpanType) if not k.startswith("_"))


def test_the_table_covers_every_real_span_type():
    """If MLflow adds a span type, this fails until it is classified."""
    assert sorted(EXPECTED_BY_TYPE) == REAL_SPAN_TYPES


@pytest.mark.parametrize("span_type", [*REAL_SPAN_TYPES, "SOMETHING_NEW"])
def test_every_span_type_has_a_named_status(attach, recorder, span_type):
    processor = attach()
    with mlflow.start_span(name=f"span-{span_type}", span_type=span_type):
        pass
    processor.flush()
    [outcome] = processor.outcomes
    expected = EXPECTED_BY_TYPE.get(span_type, NOT_SUBMITTED_UNSUPPORTED_SPAN)
    assert outcome.status == expected
    assert outcome.status in OUTCOMES
    assert outcome.reason
    assert recorder.count == 0


# ------------------------------------------------------------- failed call (M9)


def test_failed_model_call_is_not_measured(attach, recorder):
    processor = attach()
    with pytest.raises(openai.InternalServerError):
        chat(Provider(http_status=500))
    _refused(processor, recorder, NOT_SUBMITTED_APPLICATION_ERROR)


# ------------------------------------------------------------ sampling (C5, M12)


def test_sample_rate_zero_opens_no_socket(attach, recorder):
    processor = attach(sample_rate=0.0)
    chat()
    _refused(processor, recorder, SKIPPED_SAMPLING)


def test_kill_switch(attach, recorder):
    processor = attach()
    processor.sample_rate = 0.0
    chat()
    _refused(processor, recorder, SKIPPED_SAMPLING)


def test_sampling_is_deterministic_in_the_span_id(attach):
    rate = 0.5
    processor = attach(sample_rate=rate)
    for _ in range(12):
        chat()
    processor.flush()
    for outcome in model_calls(processor.outcomes):
        digest = hashlib.sha256(outcome.span_id.encode("utf-8")).digest()[:8]
        kept = int.from_bytes(digest, "big") / float(1 << 64) < rate
        assert (outcome.status == SUBMITTED) == kept


def test_bad_sample_rate_is_refused_at_construction():
    with pytest.raises(ValueError):
        NeoMundiMlflowProcessor(submit=False, sample_rate=1.5)
    with pytest.raises(TypeError):
        NeoMundiMlflowProcessor(submit=False, sample_rate=True)


def test_a_key_is_required_to_submit():
    with pytest.raises(ValueError):
        NeoMundiMlflowProcessor()
    with pytest.raises(ValueError):
        NeoMundiMlflowProcessor(api_key="ct_test_x")


# --------------------------------------------------------------- local only


def test_submit_false_needs_no_key_and_opens_no_socket(recorder):
    processor = NeoMundiMlflowProcessor(submit=False).install()
    chat()
    processor.flush()
    [outcome] = model_calls(processor.outcomes)
    # The checks a submission would make all ran; only the socket did not.
    assert outcome.status == NOT_SUBMITTED_LOCAL_ONLY
    assert outcome.reason == "submit=False: nothing left the process"
    assert not outcome.submitted and outcome.request_id is None
    assert outcome.token_count == 18  # what WOULD have been sent, from the provider
    assert outcome.latency_ms is not None
    assert recorder.count == 0


# --------------------------------------------------------------- secrets (C8)


def test_nothing_secret_in_outcomes_tags_or_logs(attach, caplog):
    caplog.set_level(logging.DEBUG)
    processor = attach()
    chat()
    chat(Provider(usage=None))
    processor.flush()
    for outcome in processor.outcomes:
        assert_no_secret(repr(outcome))
    tags = {k: v for k, v in last_trace().info.tags.items() if k.startswith("neomundi.")}
    assert_no_secret(repr(tags))
    neomundi_logs = [r for r in caplog.records if "neomundi" in r.name]
    assert_no_secret("\n".join(r.getMessage() for r in neomundi_logs))
