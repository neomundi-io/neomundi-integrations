"""No exception from this processor ever reaches the host (criterion C3).

Every failure is driven around a real instrumented call whose result the
host must still receive unchanged, and must produce an explicit outcome
rather than silence.
"""

from __future__ import annotations

import logging

import httpx
import pytest
from conftest import (
    SECRET_PROMPT,
    SECRET_RESPONSE,
    TEST_NEOMUNDI_KEY,
    TEST_PROVIDER_KEY,
    assert_no_secret,
    call,
    declared_total,
    make_client,
)

from otel_neomundi import (
    NOT_SUBMITTED_INTERNAL_ERROR,
    NOT_SUBMITTED_TRANSPORT_ERROR,
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
    "invalid-json": _invalid_json,
}


@pytest.mark.parametrize("name", sorted(FAILURES))
def test_api_failure_never_reaches_the_host(pipeline, name, caplog):
    processor = pipeline.attach(client=make_client(FAILURES[name]))
    caplog.set_level(logging.DEBUG)

    with processor.interaction() as interaction:
        reply = call()
        interaction.declare(token_count=declared_total(reply), prompt=SECRET_PROMPT)

    # The host got its answer, untouched.
    assert reply.choices[0].message.content == SECRET_RESPONSE
    outcome = interaction.outcome
    assert outcome.status == NOT_SUBMITTED_TRANSPORT_ERROR, outcome
    assert outcome.submitted is False
    assert outcome.error_type
    assert_no_secret(outcome)
    for secret in (SECRET_PROMPT, SECRET_RESPONSE, TEST_NEOMUNDI_KEY, TEST_PROVIDER_KEY):
        assert secret not in caplog.text


def test_unexpected_client_exception_is_contained(pipeline):
    class Exploding:
        def govern(self, **kwargs):
            raise RuntimeError(f"boom {kwargs.get('llm_prompt')}")

    processor = pipeline.attach(client=Exploding())
    with processor.interaction() as interaction:
        interaction.declare(token_count=declared_total(call()), prompt=SECRET_PROMPT)
    outcome = interaction.outcome
    assert outcome.status == NOT_SUBMITTED_INTERNAL_ERROR
    assert outcome.error_type == "RuntimeError"
    assert_no_secret(outcome)


def test_on_outcome_callback_exception_is_swallowed(pipeline):
    def bad(outcome):
        raise ValueError("callback failure")

    processor = pipeline.attach(submit=False, on_outcome=bad)
    with processor.interaction() as interaction:
        interaction.declare(token_count=18)
        call()
    assert interaction.outcome is not None


def test_garbage_spans_do_not_break_the_sdk(pipeline):
    """on_start / on_end receive objects that are not spans: nothing escapes."""
    processor = pipeline.attach(submit=False)
    processor.on_start(object())
    processor.on_end(object())
    processor.on_start(None)
    processor.on_end(None)
    assert processor.force_flush() is True
    processor.shutdown()


def test_declare_never_raises(pipeline):
    processor = pipeline.attach(submit=False)
    with processor.interaction() as interaction:
        interaction.declare(token_count=object(), prompt=12, response=b"bytes")
        call()
    assert interaction.outcome.status == "not_submitted_invalid_token_count"


def test_broken_tracer_provider_is_contained():
    from otel_neomundi import NeoMundiSpanProcessor

    class Broken:
        def get_tracer(self, *args, **kwargs):
            raise RuntimeError("no tracer")

    processor = NeoMundiSpanProcessor(submit=False, tracer_provider=Broken())
    with processor.interaction() as interaction:
        interaction.declare(token_count=18)
    assert interaction.outcome.status == NOT_SUBMITTED_INTERNAL_ERROR
