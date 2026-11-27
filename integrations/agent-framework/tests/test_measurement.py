"""What the middleware measures, and what it refuses.

Every test here drives the framework's real middleware pipeline and the
real connector. Assertions are on observable output -- the `Outcome` the
host can read, and what reached `govern` -- never on internal state.
"""

from __future__ import annotations

import pytest
from conftest import RecordingClient, chat_client, completion_body

from agent_framework_neomundi import (
    NOT_SUBMITTED_APPLICATION_ERROR,
    NOT_SUBMITTED_INTERNAL_ERROR,
    NOT_SUBMITTED_INVALID_TOKEN_COUNT,
    NOT_SUBMITTED_LOCAL_ONLY,
    NOT_SUBMITTED_NO_TOKEN_COUNT,
    NOT_SUBMITTED_STREAMING,
    NOT_SUBMITTED_TRANSPORT_ERROR,
    OUTCOMES,
    SUBMITTED,
    TOKENS_FRAMEWORK_SUM,
    TOKENS_FRAMEWORK_TOTAL,
    NeoMundiChatMiddleware,
    usage_token_count,
)

USAGE = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}


async def run_once(middleware, **client_kwargs):
    client = chat_client(middleware=[middleware], **client_kwargs)
    return await client.get_response("What is the capital of France?")


# ------------------------------------------------------------- the happy path


async def test_a_published_count_is_measured_and_submitted(recording_client):
    mw = NeoMundiChatMiddleware(client=recording_client)
    await run_once(mw, body=completion_body(usage=USAGE))

    outcome = mw.last_outcome
    assert outcome.status == SUBMITTED
    assert outcome.submitted is True
    assert outcome.token_count == 18
    assert outcome.token_source == TOKENS_FRAMEWORK_TOTAL
    assert outcome.request_id == "req-1"
    assert outcome.model_name == "gpt-4o-mini"

    assert len(recording_client.calls) == 1
    sent = recording_client.calls[0]
    assert sent["token_count"] == 18
    assert sent["latency_ms"] > 0


async def test_the_count_submitted_is_the_providers_number(recording_client):
    """The provider said 18. Nothing else may be sent."""
    mw = NeoMundiChatMiddleware(client=recording_client)
    await run_once(mw, body=completion_body(usage=USAGE))
    assert recording_client.calls[0]["token_count"] == 18


async def test_latency_brackets_the_call(recording_client):
    mw = NeoMundiChatMiddleware(client=recording_client)
    await run_once(mw, body=completion_body(usage=USAGE))
    latency = mw.last_outcome.latency_ms
    assert latency is not None and latency > 0
    # Sanity: it is a duration in milliseconds, not seconds mislabelled.
    assert latency < 60_000


# ------------------------------------------------------------ the refusals


async def test_a_silent_provider_yields_no_measurement(recording_client):
    """The whole point. No usage block, no measurement, nothing billed."""
    mw = NeoMundiChatMiddleware(client=recording_client)
    await run_once(mw, body=completion_body(usage=None))

    outcome = mw.last_outcome
    assert outcome.status == NOT_SUBMITTED_NO_TOKEN_COUNT
    assert outcome.submitted is False
    assert outcome.token_count is None
    assert "fabricated" in outcome.reason
    assert recording_client.calls == []


async def test_a_zero_count_is_refused_not_submitted(recording_client):
    """A zero is not a measurement, whoever wrote it."""
    mw = NeoMundiChatMiddleware(client=recording_client)
    await run_once(
        mw,
        body=completion_body(usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}),
    )
    assert mw.last_outcome.status == NOT_SUBMITTED_INVALID_TOKEN_COUNT
    assert recording_client.calls == []


async def test_a_dry_run_opens_no_socket_and_says_so():
    mw = NeoMundiChatMiddleware(client=None)
    await run_once(mw, body=completion_body(usage=USAGE))
    outcome = mw.last_outcome
    assert outcome.status == NOT_SUBMITTED_LOCAL_ONLY
    assert outcome.token_count == 18  # computed, not sent
    assert outcome.submitted is False


async def test_submit_false_computes_without_sending(recording_client):
    mw = NeoMundiChatMiddleware(client=recording_client, submit=False)
    await run_once(mw, body=completion_body(usage=USAGE))
    assert mw.last_outcome.status == NOT_SUBMITTED_LOCAL_ONLY
    assert recording_client.calls == []


async def test_streaming_is_refused_by_name_not_half_measured(recording_client):
    mw = NeoMundiChatMiddleware(client=recording_client)
    chunk = {
        "id": "c",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "gpt-4o-mini",
        "choices": [{"index": 0, "delta": {"content": "Paris."}, "finish_reason": "stop"}],
    }
    client = chat_client(sse_chunks=[chunk], middleware=[mw])
    async for _ in client.get_response("q", stream=True):
        pass

    outcome = mw.last_outcome
    assert outcome.status == NOT_SUBMITTED_STREAMING
    assert outcome.stream is True
    assert recording_client.calls == []


# ------------------------------------- nothing escapes: four distinct failures


async def test_a_transport_failure_of_ours_does_not_reach_the_host():
    from neomundi import NeoMundiError

    client = RecordingClient(raises=NeoMundiError("api down"))
    mw = NeoMundiChatMiddleware(client=client)
    reply = await run_once(mw, body=completion_body(usage=USAGE))

    assert reply.text == "Paris."  # the host got its answer
    assert mw.last_outcome.status == NOT_SUBMITTED_TRANSPORT_ERROR
    assert mw.last_outcome.error_type == "NeoMundiError"


async def test_an_unexpected_error_in_our_code_does_not_reach_the_host():
    class Exploding(RecordingClient):
        def govern(self, **kwargs):
            raise ZeroDivisionError("boom")

    mw = NeoMundiChatMiddleware(client=Exploding())
    reply = await run_once(mw, body=completion_body(usage=USAGE))

    assert reply.text == "Paris."
    assert mw.last_outcome.status == NOT_SUBMITTED_INTERNAL_ERROR
    assert mw.last_outcome.error_type == "ZeroDivisionError"


async def test_a_host_failure_is_recorded_and_re_raised_untouched(recording_client):
    """🔴 The host's own error belongs to the host. We record, then re-raise.

    Swallowing it would be worse than useless: the application would
    believe a failed call succeeded.
    """
    mw = NeoMundiChatMiddleware(client=recording_client)
    with pytest.raises(Exception):  # noqa: B017 - the connector's own error type
        await run_once(mw, status=500, body={"error": "upstream"})

    assert mw.last_outcome.status == NOT_SUBMITTED_APPLICATION_ERROR
    assert recording_client.calls == []


async def test_a_malformed_usage_block_is_refused_not_guessed(recording_client):
    mw = NeoMundiChatMiddleware(client=recording_client)
    await run_once(
        mw,
        body=completion_body(usage={"prompt_tokens": "eleven", "completion_tokens": None}),
    )
    assert mw.last_outcome.status == NOT_SUBMITTED_INVALID_TOKEN_COUNT
    assert recording_client.calls == []


async def test_source_type_and_mode_are_not_forwarded_when_unset(recording_client):
    """🔴 Regression, found by the first REAL run, not by this suite.

    Passing them as explicit `None` overrode the SDK's defaults and the
    API answered HTTP 422: "body.source_type: Input should be a valid
    string; body.mode: Input should be 'OBS'". Nothing was billed -- the
    refusal path held -- but no measurement happened either.
    """
    mw = NeoMundiChatMiddleware(client=recording_client)
    await run_once(mw, body=completion_body(usage=USAGE))

    assert mw.last_outcome.status == SUBMITTED
    sent = recording_client.calls[0]
    assert "source_type" not in sent
    assert "mode" not in sent


async def test_source_type_and_mode_are_forwarded_when_given(recording_client):
    mw = NeoMundiChatMiddleware(client=recording_client, source_type="llm", mode="OBS")
    await run_once(mw, body=completion_body(usage=USAGE))

    sent = recording_client.calls[0]
    assert sent["source_type"] == "llm"
    assert sent["mode"] == "OBS"


# ------------------------------------------------------------- the unit itself


def test_usage_token_count_prefers_the_total():
    count, source, reason = usage_token_count(
        {"total_token_count": 18, "input_token_count": 11, "output_token_count": 7}
    )
    assert (count, source, reason) == (18, TOKENS_FRAMEWORK_TOTAL, None)


def test_usage_token_count_sums_when_there_is_no_total():
    count, source, reason = usage_token_count({"input_token_count": 11, "output_token_count": 7})
    assert (count, source, reason) == (18, TOKENS_FRAMEWORK_SUM, None)


def test_usage_token_count_refuses_absent_usage():
    count, source, reason = usage_token_count(None)
    assert count is None and source is None
    assert "fabricated" in reason


def test_usage_token_count_refuses_a_bool_posing_as_an_int():
    """`True` is an `int` in Python. It is not a token count."""
    count, _, _ = usage_token_count({"total_token_count": True})
    assert count is None


def test_usage_token_count_refuses_a_partial_pair():
    count, _, reason = usage_token_count({"input_token_count": 11})
    assert count is None
    assert "usage_details was present" in reason


def test_an_unknown_status_cannot_be_constructed():
    from agent_framework_neomundi import Outcome

    with pytest.raises(ValueError, match="OUTCOMES is closed"):
        Outcome(call_id="x", status="invented_status")


def test_every_declared_outcome_is_unique():
    assert len(set(OUTCOMES)) == len(OUTCOMES)
