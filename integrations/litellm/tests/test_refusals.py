"""What cannot be measured submits nothing, invents nothing, and says why.

The central case of this integration: LiteLLM fills a silent provider's
usage with zero or with its own tokenizer estimate. Both are refused.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import litellm
import pytest
from conftest import (
    MESSAGES,
    Recorder,
    acomplete,
    assert_no_secret,
    complete,
    make_client,
    ollama_provider,
    openai_provider,
    wait_for,
)

from litellm_neomundi import (
    NOT_SUBMITTED_APPLICATION_ERROR,
    NOT_SUBMITTED_CACHE_HIT,
    NOT_SUBMITTED_LOCAL_ONLY,
    NOT_SUBMITTED_NO_LATENCY,
    NOT_SUBMITTED_UNSUPPORTED_CALL,
    NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT,
    SKIPPED_SAMPLING,
    NeoMundiLogger,
)


def test_openai_compatible_provider_silent_is_refused_not_zero():
    """LiteLLM reports Usage(0, 0, 0). Zero is not a measurement."""
    recorder = Recorder()
    logger = NeoMundiLogger(make_client(recorder=recorder))
    response = complete(logger, client=openai_provider(usage=None))
    assert response.usage.total_tokens == 0  # what LiteLLM hands over
    (outcome,) = wait_for(logger)
    assert outcome.status == NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT
    assert outcome.submitted is False and outcome.token_count is None
    assert "published no token count" in outcome.reason
    assert recorder.count == 0
    assert_no_secret(outcome)


def test_ollama_silent_estimate_is_refused():
    """LiteLLM substitutes its tokenizer's estimate. It is refused."""
    recorder = Recorder()
    logger = NeoMundiLogger(make_client(recorder=recorder))
    response = complete(
        logger,
        model="ollama_chat/llama3.2",
        api_base="https://provider.example.invalid",
        client=ollama_provider(report=False),
    )
    assert response.usage.total_tokens > 0  # an estimate, indistinguishable
    (outcome,) = wait_for(logger)
    assert outcome.status == NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT
    assert recorder.count == 0


def test_sync_stream_without_a_usage_chunk_is_refused():
    recorder = Recorder()
    logger = NeoMundiLogger(make_client(recorder=recorder))
    complete(logger, client=openai_provider(usage=None), stream=True)
    (outcome,) = wait_for(logger)
    assert outcome.status == NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT
    assert recorder.count == 0


def test_async_stream_is_refused_because_nothing_can_be_verified():
    """T4: even when the provider did report, the logger cannot see it."""
    recorder = Recorder()
    logger = NeoMundiLogger(make_client(recorder=recorder))
    acomplete(logger, stream=True, stream_options={"include_usage": True})
    (outcome,) = wait_for(logger)
    assert outcome.status == NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT
    assert "async stream" in outcome.reason
    assert recorder.count == 0


def test_partial_report_is_not_completed_by_an_estimate():
    """T3: only one side published; LiteLLM estimated the other."""
    recorder = Recorder()
    logger = NeoMundiLogger(make_client(recorder=recorder))
    now = datetime(2026, 11, 26, 9, 0, 0)
    logger.log_success_event(
        kwargs={
            "litellm_call_id": "call-partial",
            "call_type": "completion",
            "messages": MESSAGES,
            "original_response": '{"message": {"content": "x"}, "eval_count": 7}',
        },
        response_obj=SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=14, completion_tokens=7, total_tokens=21),
            choices=[],
        ),
        start_time=now,
        end_time=now + timedelta(seconds=1),
    )
    assert logger.last_outcome.status == NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT
    assert recorder.count == 0


def test_cache_hit_is_not_a_model_interaction():
    """T5. Driven at the hook: ``litellm.Cache`` needs the proxy extras
    (``fastapi``), which a clean install of this package does not carry.
    ``kwargs["cache_hit"]`` is the flag observed on litellm 1.111.0."""
    recorder = Recorder()
    logger = NeoMundiLogger(make_client(recorder=recorder))
    now = datetime(2026, 11, 26, 9, 0, 0)
    logger.log_success_event(
        {
            "litellm_call_id": "call-cached",
            "call_type": "completion",
            "cache_hit": True,
            "original_response": '{"usage": {"total_tokens": 18}}',
        },
        SimpleNamespace(usage=SimpleNamespace(total_tokens=18), choices=[]),
        now,
        now + timedelta(milliseconds=2),
    )
    assert logger.last_outcome.status == NOT_SUBMITTED_CACHE_HIT
    assert recorder.count == 0


def test_failed_call_submits_nothing_and_host_keeps_its_exception():
    """T6."""
    recorder = Recorder()
    logger = NeoMundiLogger(make_client(recorder=recorder))
    with pytest.raises(litellm.exceptions.InternalServerError):
        complete(logger, client=openai_provider(status=500), num_retries=0)
    (outcome,) = wait_for(logger)
    assert outcome.status == NOT_SUBMITTED_APPLICATION_ERROR
    assert outcome.error_type == "InternalServerError"
    assert recorder.count == 0
    assert_no_secret(outcome)


def test_submit_false_needs_no_key_and_opens_no_socket():
    """Criterion 5, and the free dry run: every check still runs."""
    logger = NeoMundiLogger(submit=False)
    complete(logger)
    complete(logger, client=openai_provider(usage=None))
    outcomes = wait_for(logger, 2)
    assert [o.status for o in outcomes] == [
        NOT_SUBMITTED_LOCAL_ONLY,
        NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT,
    ]
    assert outcomes[0].token_count == 18 and outcomes[0].latency_ms is not None


def test_submit_false_with_a_client_opens_no_socket():
    recorder = Recorder()
    logger = NeoMundiLogger(make_client(recorder=recorder), submit=False)
    complete(logger)
    wait_for(logger)
    assert recorder.count == 0


def test_sample_rate_zero_opens_no_socket():
    recorder = Recorder()
    logger = NeoMundiLogger(make_client(recorder=recorder), sample_rate=0.0)
    complete(logger)
    (outcome,) = wait_for(logger)
    assert outcome.status == SKIPPED_SAMPLING
    assert recorder.count == 0


def test_sampling_is_deterministic_in_the_call_id():
    """T9: the same litellm_call_id always decides the same way."""

    def decisions() -> list[str]:
        logger = NeoMundiLogger(submit=False, sample_rate=0.5)
        now = datetime(2026, 11, 26, 9, 0, 0)
        for i in range(40):
            logger.log_success_event(
                {
                    "litellm_call_id": f"call-{i}",
                    "call_type": "completion",
                    "original_response": '{"usage": {"total_tokens": 18}}',
                },
                SimpleNamespace(usage=SimpleNamespace(total_tokens=18), choices=[]),
                now,
                now + timedelta(seconds=1),
            )
        return [o.status for o in logger.outcomes]

    first, second = decisions(), decisions()
    assert first == second
    assert SKIPPED_SAMPLING in first and NOT_SUBMITTED_LOCAL_ONLY in first


def test_non_chat_call_is_not_measured():
    """T11."""
    recorder = Recorder()
    logger = NeoMundiLogger(make_client(recorder=recorder))
    now = datetime(2026, 11, 26, 9, 0, 0)
    logger.log_success_event(
        {"litellm_call_id": "call-embed", "call_type": "embedding"},
        SimpleNamespace(usage=SimpleNamespace(total_tokens=5)),
        now,
        now + timedelta(seconds=1),
    )
    assert logger.last_outcome.status == NOT_SUBMITTED_UNSUPPORTED_CALL
    assert recorder.count == 0


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (None, None),
        ("yesterday", "today"),
        (datetime(2026, 11, 26, 9, 0, 1), datetime(2026, 11, 26, 9, 0, 0)),  # clock stepped back
    ],
)
def test_missing_or_negative_latency_is_refused_not_assumed(start, end):
    recorder = Recorder()
    logger = NeoMundiLogger(make_client(recorder=recorder))
    logger.log_success_event(
        {
            "litellm_call_id": "call-time",
            "call_type": "completion",
            "original_response": '{"usage": {"total_tokens": 18}}',
        },
        SimpleNamespace(usage=SimpleNamespace(total_tokens=18), choices=[]),
        start,
        end,
    )
    assert logger.last_outcome.status == NOT_SUBMITTED_NO_LATENCY
    assert recorder.count == 0


def test_constructor_refuses_an_ambiguous_or_missing_target():
    with pytest.raises(ValueError):
        NeoMundiLogger()
    with pytest.raises(ValueError):
        NeoMundiLogger(api_key="ct_test_x")
    with pytest.raises(ValueError):
        NeoMundiLogger(make_client(), api_key="ct_test_x", base_url="https://example.invalid")
    with pytest.raises(ValueError):
        NeoMundiLogger(submit=False, sample_rate=1.5)
    with pytest.raises(TypeError):
        NeoMundiLogger(submit=False, sample_rate=True)


def test_reassigning_litellm_callbacks_does_not_detach_the_logger():
    """Measured LiteLLM behaviour this package documents rather than hides.

    The logger stays registered after ``litellm.callbacks = []`` and keeps
    billing. ``sample_rate = 0.0`` stops it at once; LiteLLM's
    ``remove_callback_from_all_lists`` actually detaches it.
    """
    recorder = Recorder()
    logger = NeoMundiLogger(make_client(recorder=recorder))
    complete(logger)
    wait_for(logger, 1)

    litellm.callbacks = []
    litellm.completion(model="openai/test-model", messages=MESSAGES, client=openai_provider())
    wait_for(logger, 2)
    assert recorder.count == 2  # still measuring, still billing

    logger.sample_rate = 0.0  # the kill switch
    litellm.completion(model="openai/test-model", messages=MESSAGES, client=openai_provider())
    assert wait_for(logger, 3)[-1].status == SKIPPED_SAMPLING
    assert recorder.count == 2

    litellm.logging_callback_manager.remove_callback_from_all_lists(logger)
    litellm.completion(model="openai/test-model", messages=MESSAGES, client=openai_provider())
    time.sleep(0.5)
    assert len(logger.outcomes) == 3  # detached: not even an outcome
