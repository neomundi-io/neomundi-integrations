"""R2 and R5: the four refusals, each reported and each billing nothing.

These tests are the point of the package. An instrumentation layer that
quietly estimates a missing token count, or that silently drops a failure,
produces fabricated measurements -- which is worse than measuring nothing.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from conftest import chat_result, messages, run_chat

from langchain_neomundi import (
    NOT_SUBMITTED_APPLICATION_ERROR,
    NOT_SUBMITTED_LOCAL_ONLY,
    NOT_SUBMITTED_NO_TOKEN_COUNT,
    SKIPPED_SAMPLING,
    NeoMundiCallbackHandler,
)


def test_no_token_count_submits_nothing(client, recorder):
    """R2: the provider reported no usage. Nothing is invented."""
    handler = NeoMundiCallbackHandler(client, raise_errors=True)

    run_chat(handler, result=chat_result(input_tokens=None, output_tokens=None))

    assert recorder.count == 0, "no socket, so nothing billed"
    outcome = handler.last_outcome
    assert outcome.status == NOT_SUBMITTED_NO_TOKEN_COUNT
    assert outcome.submitted is False
    assert outcome.token_count is None
    assert "fabricated" in outcome.reason
    assert handler.submitted_count == 0


def test_no_token_count_still_measures_latency_locally(client):
    """A refusal is not a blind spot: what was observed is still reported."""
    handler = NeoMundiCallbackHandler(client, raise_errors=True)

    run_chat(handler, result=chat_result(input_tokens=None, output_tokens=None))

    assert handler.last_outcome.latency_ms is not None


def test_submit_false_opens_no_socket(client, recorder):
    """R5: wire the integration up at zero cost."""
    handler = NeoMundiCallbackHandler(client, submit=False, raise_errors=True)

    run_chat(handler)

    assert recorder.count == 0
    outcome = handler.last_outcome
    assert outcome.status == NOT_SUBMITTED_LOCAL_ONLY
    assert outcome.submitted is False
    assert outcome.latency_ms is not None, "measured, just not submitted"


def test_sample_rate_zero_opens_no_socket(client, recorder):
    """R5: the budget control that actually stops spend."""
    handler = NeoMundiCallbackHandler(client, sample_rate=0.0, raise_errors=True)

    for _ in range(10):
        run_chat(handler)

    assert recorder.count == 0
    assert handler.submitted_count == 0
    assert all(o.status == SKIPPED_SAMPLING for o in handler.outcomes)


def test_sampling_is_deterministic_in_the_run_id(client):
    """The same run always decides the same way, so a sample is reproducible."""
    handler = NeoMundiCallbackHandler(client, sample_rate=0.5, raise_errors=True)
    run_id = uuid4()

    first = handler._should_sample(str(run_id))
    for _ in range(50):
        assert handler._should_sample(str(run_id)) is first


def test_sampling_rate_is_approximately_honoured(client, recorder):
    handler = NeoMundiCallbackHandler(client, sample_rate=0.5, raise_errors=True)

    for _ in range(400):
        run_chat(handler)

    # Deterministic hashing over random uuid4s: expect ~200, allow slack.
    assert 140 <= recorder.count <= 260, recorder.count


def test_application_error_submits_nothing(client, recorder):
    """R2: the host's own LLM call failed. There is no interaction to measure."""
    handler = NeoMundiCallbackHandler(client, raise_errors=True)
    rid = uuid4()

    handler.on_chat_model_start({"kwargs": {"model": "m"}}, messages(), run_id=rid)
    handler.on_llm_error(TimeoutError("provider timed out"), run_id=rid)

    assert recorder.count == 0
    outcome = handler.last_outcome
    assert outcome.status == NOT_SUBMITTED_APPLICATION_ERROR
    assert outcome.submitted is False
    assert outcome.error_type == "TimeoutError"
    assert outcome.latency_ms is not None


def test_error_message_is_not_kept(client):
    """An error message can carry a prompt. Only the class name is kept."""
    handler = NeoMundiCallbackHandler(client, raise_errors=True)
    rid = uuid4()

    handler.on_chat_model_start({"kwargs": {"model": "m"}}, messages(), run_id=rid)
    handler.on_llm_error(ValueError("secret prompt text leaked here"), run_id=rid)

    outcome = handler.last_outcome
    assert outcome.error_type == "ValueError"
    assert "secret prompt text" not in repr(outcome)


def test_unknown_run_is_ignored_not_crashed(client, recorder):
    """LangChain can emit an end without a start the handler ever saw."""
    handler = NeoMundiCallbackHandler(client, raise_errors=True)

    handler.on_llm_end(chat_result(), run_id=uuid4())
    handler.on_llm_error(ValueError("x"), run_id=uuid4())

    assert recorder.count == 0
    assert handler.outcomes == []


@pytest.mark.parametrize("bad", [-0.1, 1.1, 2.0])
def test_invalid_sample_rate_is_rejected_at_construction(client, bad):
    """Fail at wiring time, not silently at measurement time."""
    with pytest.raises(ValueError):
        NeoMundiCallbackHandler(client, sample_rate=bad)


def test_client_and_credentials_are_mutually_exclusive(client):
    with pytest.raises(ValueError):
        NeoMundiCallbackHandler(client, api_key="ct_test_x", base_url="https://example.invalid")


def test_base_url_is_mandatory():
    """The SDK never assumes a host, and neither does this package."""
    with pytest.raises(ValueError):
        NeoMundiCallbackHandler(api_key="ct_test_0000000000000000000000000000")
