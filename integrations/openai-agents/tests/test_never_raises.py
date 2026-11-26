"""No exception from the integration reaches the agent run.

Criteria C3 and A10. Eight distinct API failures; each time the run
completes with its own answer, and the failure is a reported outcome.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import pytest
from conftest import SECRET_RESPONSE, Provider, assert_no_secret, load_fixture, responses, run_agent

from openai_agents_neomundi import (
    NOT_SUBMITTED_INTERNAL_ERROR,
    NOT_SUBMITTED_QUEUE_FULL,
    NOT_SUBMITTED_TRANSPORT_ERROR,
    SUBMITTED,
    NeoMundiTraceProcessor,
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
def test_api_failure_never_reaches_the_run(attach, recorder, name):
    processor = attach(FAILURES[name])
    result = run_agent()
    processor.flush()

    assert result.final_output == SECRET_RESPONSE
    outcomes = responses(processor.outcomes)
    assert len(outcomes) == 2
    for outcome in outcomes:
        assert outcome.status in (NOT_SUBMITTED_TRANSPORT_ERROR, NOT_SUBMITTED_INTERNAL_ERROR)
        assert outcome.reason and outcome.error_type
        assert outcome.request_id is None
        assert_no_secret(outcome)
    assert recorder.count >= 2


def test_slow_api_does_not_block_the_agent(attach):
    """A10: on_span_end runs on the agent's thread; submission must not."""

    def slow(request: httpx.Request) -> httpx.Response:
        time.sleep(1.0)
        return httpx.Response(200, json=load_fixture())

    processor = attach(slow)
    start = time.monotonic()
    run_agent()  # two model calls
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, f"the run waited on the measurement API ({elapsed:.2f}s)"
    assert processor.flush(timeout=10)
    assert [o.status for o in responses(processor.outcomes)] == [SUBMITTED, SUBMITTED]


def test_full_queue_drops_rather_than_blocks(attach, recorder):
    def slow(request: httpx.Request) -> httpx.Response:
        time.sleep(0.5)
        return httpx.Response(200, json=load_fixture())

    processor = attach(slow, max_queue=1)
    start = time.monotonic()
    run_agent()
    run_agent()
    assert time.monotonic() - start < 1.0
    processor.flush(timeout=10)
    statuses = [o.status for o in responses(processor.outcomes)]
    assert NOT_SUBMITTED_QUEUE_FULL in statuses
    assert statuses.count(SUBMITTED) == recorder.count


def test_a_failing_on_outcome_callback_is_contained(attach):
    def explode(outcome: Any) -> None:
        raise RuntimeError("the application's own callback is broken")

    processor = attach(on_outcome=explode)
    result = run_agent()
    processor.flush()
    assert result.final_output == SECRET_RESPONSE
    assert [o.status for o in responses(processor.outcomes)] == [SUBMITTED, SUBMITTED]


def test_a_malformed_span_is_an_internal_error_not_an_exception():
    processor = NeoMundiTraceProcessor(submit=False)

    class Weird:
        span_id = "span_weird"
        trace_id = "trace_weird"
        parent_id = None

        @property
        def span_data(self) -> Any:
            raise RuntimeError("broken span")

    processor.on_span_start(Weird())
    processor.on_span_end(Weird())
    (outcome,) = processor.outcomes
    assert outcome.status == NOT_SUBMITTED_INTERNAL_ERROR
    assert outcome.error_type == "RuntimeError"


def test_provider_failure_is_the_hosts_not_ours(attach, recorder):
    """The run's own error propagates unchanged; ours never adds one."""
    processor = attach()
    with pytest.raises(Exception) as caught:  # noqa: B017
        run_agent(Provider(http_status=500), tools=False)
    assert "openai" in type(caught.value).__module__ or "agents" in type(caught.value).__module__
    processor.flush()
    assert recorder.count == 0


def test_shutdown_is_safe_and_idempotent(attach):
    processor = attach()
    run_agent(tools=False)
    processor.shutdown()
    processor.shutdown()
    processor.force_flush()
    assert json.dumps([o.status for o in processor.outcomes])
