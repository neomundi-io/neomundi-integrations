"""A completion whose token count the provider published is measured.

Every assertion reads the body sent to ``POST /v1/govern`` or the outcome
the logger publishes.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import litellm
from conftest import (
    MESSAGES,
    SECRET_PROMPT,
    SECRET_RESPONSE,
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
    SUBMITTED,
    TOKENS_PROVIDER_SUM,
    TOKENS_PROVIDER_TOTAL,
    NeoMundiLogger,
    verified_token_count,
)


def test_reported_usage_is_submitted_with_the_providers_total():
    recorder = Recorder()
    logger = NeoMundiLogger(make_client(recorder=recorder))
    response = complete(logger)

    (outcome,) = wait_for(logger)
    assert outcome.status == SUBMITTED
    assert outcome.submitted is True
    assert outcome.request_id  # the correlation key, from the server
    assert outcome.token_source == TOKENS_PROVIDER_TOTAL
    assert response.choices[0].message.content == SECRET_RESPONSE  # host untouched

    (body,) = recorder.bodies()
    assert body["raw_metrics"]["token_count"] == 18
    assert body["llm_prompt"] == SECRET_PROMPT
    assert body["llm_response"] == SECRET_RESPONSE
    assert body["raw_metrics"]["prompt_length"] == len(SECRET_PROMPT)
    assert body["raw_metrics"]["response_length"] == len(SECRET_RESPONSE)
    assert body["raw_metrics"]["model_name"] == "test-model"
    assert body["metadata"]["client_trace_id"] == outcome.call_id
    assert_no_secret(outcome)


def test_latency_is_the_host_call_not_the_measurement():
    """Criterion 4: the latency on the wire is LiteLLM's start..end."""
    recorder = Recorder()
    logger = NeoMundiLogger(make_client(recorder=recorder))
    before = time.monotonic()
    complete(logger)
    wall_ms = (time.monotonic() - before) * 1000.0
    wait_for(logger)
    (body,) = recorder.bodies()
    assert 0.0 <= body["raw_metrics"]["latency_ms"] <= wall_ms

    # And exactly the delta LiteLLM hands to the hook, nothing added.
    recorder = Recorder()
    logger = NeoMundiLogger(make_client(recorder=recorder))
    start = datetime(2026, 11, 26, 9, 0, 0)
    logger.log_success_event(
        kwargs={
            "litellm_call_id": "call-latency",
            "call_type": "completion",
            "model": "test-model",
            "messages": MESSAGES,
            "original_response": '{"usage": {"total_tokens": 18}}',
        },
        response_obj=SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18),
            choices=[],
        ),
        start_time=start,
        end_time=start + timedelta(milliseconds=1234.5),
    )
    (body,) = recorder.bodies()
    assert body["raw_metrics"]["latency_ms"] == 1234.5


def test_ollama_reported_counts_are_submitted():
    recorder = Recorder()
    logger = NeoMundiLogger(make_client(recorder=recorder))
    complete(
        logger,
        model="ollama_chat/llama3.2",
        api_base="https://provider.example.invalid",
        client=ollama_provider(report=True),
    )
    (outcome,) = wait_for(logger)
    assert outcome.status == SUBMITTED
    (body,) = recorder.bodies()
    assert body["raw_metrics"]["token_count"] == 18  # prompt_eval_count 11 + eval_count 7
    assert outcome.token_source == TOKENS_PROVIDER_SUM


def test_sync_stream_with_a_usage_chunk_is_submitted():
    recorder = Recorder()
    logger = NeoMundiLogger(make_client(recorder=recorder))
    complete(logger, stream=True, stream_options={"include_usage": True})
    (outcome,) = wait_for(logger)
    assert outcome.status == SUBMITTED
    (body,) = recorder.bodies()
    assert body["raw_metrics"]["token_count"] == 18
    assert body["llm_response"] == SECRET_RESPONSE


def test_async_non_streamed_completion_is_submitted():
    recorder = Recorder()
    logger = NeoMundiLogger(make_client(recorder=recorder))
    acomplete(logger)
    (outcome,) = wait_for(logger)
    assert outcome.status == SUBMITTED
    assert recorder.count == 1
    assert recorder.bodies()[0]["raw_metrics"]["token_count"] == 18


def test_anthropic_cached_prompt_is_a_sum_of_reported_numbers():
    """Observed on litellm 1.111.0: prompt_tokens = input + cache_read."""
    raw = (
        '{"id": "msg", "type": "message", "usage": {"input_tokens": 11, '
        '"output_tokens": 7, "cache_read_input_tokens": 5, '
        '"cache_creation_input_tokens": 0}}'
    )
    usage = SimpleNamespace(prompt_tokens=16, completion_tokens=7, total_tokens=23)
    assert verified_token_count(usage, raw) == (23, TOKENS_PROVIDER_SUM, None)


def test_one_call_is_submitted_once_whichever_hooks_fire():
    """T8: a second hook for the same litellm_call_id bills nothing more."""
    recorder = Recorder()
    logger = NeoMundiLogger(make_client(recorder=recorder))
    kwargs = {
        "litellm_call_id": "call-dup",
        "call_type": "completion",
        "model": "test-model",
        "messages": MESSAGES,
        "original_response": '{"usage": {"total_tokens": 18}}',
    }
    response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18),
        choices=[],
    )
    now = datetime(2026, 11, 26, 9, 0, 0)
    logger.log_success_event(kwargs, response, now, now + timedelta(seconds=1))
    logger.log_success_event(kwargs, response, now, now + timedelta(seconds=1))
    assert recorder.count == 1
    assert len(logger.outcomes) == 1


def test_declared_cost_is_not_sent_by_default_and_sent_on_request():
    """T7: a price-table cost is reported, and sent only when asked."""
    kwargs = {
        "litellm_call_id": "call-cost",
        "call_type": "completion",
        "model": "test-model",
        "messages": MESSAGES,
        "original_response": '{"usage": {"total_tokens": 18}}',
        "response_cost": 0.00042,
    }
    response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18),
        choices=[],
    )
    now = datetime(2026, 11, 26, 9, 0, 0)

    recorder = Recorder()
    logger = NeoMundiLogger(make_client(recorder=recorder))
    logger.log_success_event(kwargs, response, now, now + timedelta(seconds=1))
    assert "cost" not in recorder.bodies()[0]["raw_metrics"]
    assert logger.last_outcome.declared_cost == 0.00042
    assert logger.last_outcome.declared_cost_sent is False

    recorder = Recorder()
    logger = NeoMundiLogger(make_client(recorder=recorder), send_declared_cost=True)
    logger.log_success_event(kwargs, response, now, now + timedelta(seconds=1))
    assert recorder.bodies()[0]["raw_metrics"]["cost"] == 0.00042
    assert logger.last_outcome.declared_cost_sent is True

    # A zero cost (Ollama's table entry) is never sent as if it were a price.
    recorder = Recorder()
    logger = NeoMundiLogger(make_client(recorder=recorder), send_declared_cost=True)
    logger.log_success_event(
        {**kwargs, "litellm_call_id": "call-zero", "response_cost": 0.0},
        response,
        now,
        now + timedelta(seconds=1),
    )
    assert "cost" not in recorder.bodies()[0]["raw_metrics"]


def test_capture_flags_keep_text_out_of_the_payload():
    recorder = Recorder()
    logger = NeoMundiLogger(
        make_client(recorder=recorder), capture_prompt=False, capture_response=False
    )
    complete(logger)
    wait_for(logger)
    (body,) = recorder.bodies()
    assert "llm_prompt" not in body and "llm_response" not in body


def test_measurement_version_is_reported_with_its_verification_state():
    logger = NeoMundiLogger(make_client())
    complete(logger)
    (outcome,) = wait_for(logger)
    assert outcome.measurement_version == "3.3.1"
    assert outcome.measurement_version_verified is True


def test_logger_attaches_through_success_callback_too():
    """LiteLLM's other attachment surface accepts the same instance."""
    recorder = Recorder()
    logger = NeoMundiLogger(make_client(recorder=recorder))
    litellm.success_callback = [logger]
    try:
        litellm.completion(model="openai/test-model", messages=MESSAGES, client=openai_provider())
        wait_for(logger)
    finally:
        litellm.success_callback = []
    assert recorder.count == 1
