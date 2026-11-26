"""No exception from the integration reaches the host application.

Criteria C3 and M11. Eight distinct API failures; each time the host's
call returns its own answer, and the failure is a reported outcome.
"""

from __future__ import annotations

import time

import httpx
import pytest
from conftest import SECRET_RESPONSE, assert_no_secret, chat, load_fixture, model_calls
from mlflow.tracing.config import get_config

from mlflow_neomundi import (
    NOT_SUBMITTED_INTERNAL_ERROR,
    NOT_SUBMITTED_PROCESSOR_CLOSED,
    NOT_SUBMITTED_QUEUE_FULL,
    NOT_SUBMITTED_TRANSPORT_ERROR,
    SUBMITTED,
)


def _status(code: int):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(code, json={"detail": "nope"})

    return handler


def _network(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("unreachable", request=request)


def _timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("slow", request=request)


def _garbage(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=b"<html>not json</html>")


FAILURES = {
    "401": _status(401),
    "402": _status(402),
    "429": _status(429),
    "500": _status(500),
    "503": _status(503),
    "network": _network,
    "timeout": _timeout,
    "invalid-json": _garbage,
}


@pytest.mark.parametrize("name", list(FAILURES))
def test_api_failure_never_reaches_the_host(attach, recorder, name):
    processor = attach(FAILURES[name])
    assert chat() == SECRET_RESPONSE
    processor.flush()

    [outcome] = model_calls(processor.outcomes)
    assert outcome.status in (NOT_SUBMITTED_TRANSPORT_ERROR, NOT_SUBMITTED_INTERNAL_ERROR)
    assert outcome.reason and outcome.error_type
    assert outcome.request_id is None
    assert outcome.trace_tagged is None
    assert_no_secret(repr(outcome))
    assert recorder.count >= 1


def test_slow_api_does_not_block_the_host(attach):
    """M11: MLflow calls the hook on the host's thread; submission must not."""

    def slow(request: httpx.Request) -> httpx.Response:
        time.sleep(1.0)
        return httpx.Response(200, json=load_fixture())

    processor = attach(slow)
    started = time.monotonic()
    chat()
    elapsed = time.monotonic() - started
    processor.flush()

    assert elapsed < 0.9, f"the host waited {elapsed:.2f}s for the measurement API"
    [outcome] = model_calls(processor.outcomes)
    assert outcome.status == SUBMITTED
    assert outcome.latency_ms < 900


def test_a_defect_in_the_processor_is_reported_not_raised(attach):
    """The hook is handed something it cannot read; the host is unaffected."""
    processor = attach()

    class Broken:
        span_id = "broken-span"

        @property
        def span_type(self):
            raise RuntimeError("boom")

    hook = get_config().span_processors[-1]
    hook(Broken())  # what MLflow would call
    [outcome] = processor.outcomes
    assert outcome.status == NOT_SUBMITTED_INTERNAL_ERROR
    assert outcome.error_type == "RuntimeError"


def test_on_outcome_exceptions_are_swallowed(attach):
    def explode(outcome):
        raise ValueError("caller bug")

    processor = attach(on_outcome=explode)
    assert chat() == SECRET_RESPONSE
    processor.flush()
    assert [o.status for o in model_calls(processor.outcomes)] == [SUBMITTED]


def test_full_queue_drops_rather_than_blocks(attach):
    def slow(request: httpx.Request) -> httpx.Response:
        time.sleep(0.5)
        return httpx.Response(200, json=load_fixture())

    processor = attach(slow, max_queue=1)
    for _ in range(4):
        chat()
    processor.flush()
    statuses = [o.status for o in model_calls(processor.outcomes)]
    assert NOT_SUBMITTED_QUEUE_FULL in statuses
    assert SUBMITTED in statuses


def test_after_shutdown_nothing_is_submitted(attach, recorder):
    processor = attach()
    processor.shutdown()
    chat()
    processor.flush()
    assert [o.status for o in model_calls(processor.outcomes)] == [NOT_SUBMITTED_PROCESSOR_CLOSED]
    assert recorder.count == 0
