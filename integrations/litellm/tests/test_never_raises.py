"""No exception from this logger ever reaches the host (criterion 3).

Every failure is driven through a real ``litellm.completion`` whose result
the host must still receive unchanged, and must produce an explicit
outcome rather than silence.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from conftest import (
    MESSAGES,
    SECRET_RESPONSE,
    Recorder,
    assert_no_secret,
    complete,
    make_client,
    wait_for,
)

from litellm_neomundi import (
    NOT_SUBMITTED_INTERNAL_ERROR,
    NOT_SUBMITTED_TRANSPORT_ERROR,
    NeoMundiLogger,
)


def _status(code: int, body: str = '{"detail": "refused"}'):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(code, content=body, headers={"content-type": "application/json"})

    return handler


def _network_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def _timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


def _invalid_json(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=b"<html>not json</html>")


FAILURES = {
    "401": _status(401),
    "402": _status(402),
    "429": _status(429),
    "500": _status(500),
    "503": _status(503),
    "network": _network_error,
    "timeout": _timeout,
    "invalid_json": _invalid_json,
}


@pytest.mark.parametrize("name", sorted(FAILURES))
def test_api_failure_is_reported_and_the_host_is_untouched(name):
    recorder = Recorder()
    logger = NeoMundiLogger(make_client(FAILURES[name], recorder=recorder))
    response = complete(logger)
    assert response.choices[0].message.content == SECRET_RESPONSE
    (outcome,) = wait_for(logger)
    assert outcome.status == NOT_SUBMITTED_TRANSPORT_ERROR
    assert outcome.submitted is False and outcome.request_id is None
    assert outcome.error_type  # the class name, never the message
    assert recorder.count >= 1  # it did try: this is not a pre-network refusal
    assert_no_secret(outcome)


class _BrokenClient:
    def govern(self, **kwargs):
        raise RuntimeError(f"defect carrying {kwargs.get('llm_prompt')}")


def test_a_defect_below_the_logger_is_an_internal_error_without_its_message():
    logger = NeoMundiLogger(_BrokenClient())  # type: ignore[arg-type]
    response = complete(logger)
    assert response.choices[0].message.content == SECRET_RESPONSE
    (outcome,) = wait_for(logger)
    assert outcome.status == NOT_SUBMITTED_INTERNAL_ERROR
    assert outcome.error_type == "RuntimeError"
    assert_no_secret(outcome)


@pytest.mark.parametrize(
    ("kwargs", "response_obj"),
    [
        (None, None),
        ("not a mapping", object()),
        ({"call_type": "completion"}, None),
        ({"call_type": "completion", "original_response": "{not json"}, object()),
        ({"call_type": "completion", "original_response": 42}, SimpleNamespace(usage=42)),
        (
            {"call_type": "completion", "original_response": '{"usage": [1, [2, [3]]]}'},
            SimpleNamespace(usage={"total_tokens": "eighteen"}, choices="??"),
        ),
    ],
)
def test_malformed_hook_inputs_never_raise(kwargs, response_obj):
    logger = NeoMundiLogger(make_client())
    now = datetime(2026, 11, 26, 9, 0, 0)
    logger.log_success_event(kwargs, response_obj, now, now + timedelta(seconds=1))
    logger.log_failure_event(kwargs, response_obj, now, now + timedelta(seconds=1))
    asyncio.run(logger.async_log_success_event(kwargs, response_obj, now, now))
    asyncio.run(logger.async_log_failure_event(kwargs, response_obj, now, now))
    assert logger.outcomes  # never silent
    assert all(o.submitted is False for o in logger.outcomes)


def test_a_raising_on_outcome_callback_is_contained():
    def explode(outcome):
        raise ValueError("caller bug")

    logger = NeoMundiLogger(make_client(), on_outcome=explode)
    response = complete(logger)
    assert response.choices[0].message.content == SECRET_RESPONSE
    wait_for(logger)


def test_async_hook_does_not_block_the_event_loop():
    """T10: a slow API stalls a worker thread, not the proxy's loop."""

    def slow(request: httpx.Request) -> httpx.Response:
        time.sleep(0.5)
        return httpx.Response(503, json={"detail": "slow"})

    recorder = Recorder()
    logger = NeoMundiLogger(make_client(slow, recorder=recorder))
    kwargs = {
        "litellm_call_id": "call-loop",
        "call_type": "acompletion",
        "messages": MESSAGES,
        "original_response": '{"usage": {"total_tokens": 18}}',
    }
    response = SimpleNamespace(usage=SimpleNamespace(total_tokens=18), choices=[])
    now = datetime(2026, 11, 26, 9, 0, 0)

    async def scenario() -> int:
        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.01)

        task = asyncio.create_task(ticker())
        await logger.async_log_success_event(kwargs, response, now, now + timedelta(seconds=1))
        task.cancel()
        return ticks

    ticks = asyncio.run(scenario())
    assert recorder.count == 1
    assert ticks >= 10, f"event loop starved: {ticks} ticks during a 0.5 s submission"
    assert logger.last_outcome.status == NOT_SUBMITTED_TRANSPORT_ERROR
