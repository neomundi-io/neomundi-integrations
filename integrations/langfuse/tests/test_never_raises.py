"""Criterion 3: no exception from the integration ever reaches the host.

Five distinct Langfuse API failures, plus a client that raises outright.
For each, the host's own code path -- write, then flush, then shutdown --
must complete, and the bridge must still report an explicit outcome.
"""

from __future__ import annotations

import httpx
import pytest
from conftest import TEST_LANGFUSE_SECRET, Recorder, make_langfuse, measure

from langfuse_neomundi import (
    NOT_WRITTEN_INTERNAL_ERROR,
    QUEUED,
    NeoMundiLangfuseBridge,
)


def _status(code: int):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(code, json={"message": "nope"})

    return handler


def _raise(exc_type):
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_type("simulated", request=request)

    return handler


FAILURES = {
    "401 unauthorised": _status(401),
    "429 rate limited": _status(429),
    "500 server error": _status(500),
    "network unreachable": _raise(httpx.ConnectError),
    "timeout": _raise(httpx.ReadTimeout),
}


@pytest.mark.parametrize("name", list(FAILURES))
def test_a_langfuse_api_failure_never_reaches_the_host(name, trace_id):
    recorder = Recorder()
    langfuse = make_langfuse(FAILURES[name], recorder=recorder)
    bridge = NeoMundiLangfuseBridge(langfuse)

    # The host's code path, end to end. Any exception fails the test.
    outcome = bridge.write(measure(), trace_id=trace_id)
    langfuse.flush()
    langfuse.shutdown()

    # Handed over, and Langfuse did try: the failure was real, not skipped.
    assert outcome.status == QUEUED
    assert recorder.count >= 1


class ExplodingLangfuse:
    """A client whose every method raises, with a secret in the message."""

    def get_current_trace_id(self):
        raise RuntimeError(f"boom {TEST_LANGFUSE_SECRET}")

    def get_current_observation_id(self):
        raise RuntimeError(f"boom {TEST_LANGFUSE_SECRET}")

    def create_score(self, **kwargs):
        raise RuntimeError(f"boom {TEST_LANGFUSE_SECRET}")


@pytest.mark.parametrize("trace_id_arg", [None, "a" * 32])
def test_a_client_that_raises_is_reported_not_propagated(trace_id_arg):
    seen = []
    bridge = NeoMundiLangfuseBridge(ExplodingLangfuse(), on_outcome=seen.append)
    outcome = bridge.write(measure(), trace_id=trace_id_arg)

    assert outcome.status == NOT_WRITTEN_INTERNAL_ERROR
    assert outcome.error_type == "RuntimeError"
    assert TEST_LANGFUSE_SECRET not in repr(outcome)
    assert seen == [outcome]


def test_an_on_outcome_callback_that_raises_is_contained(trace_id):
    def bad_callback(outcome):
        raise ValueError("caller bug")

    bridge = NeoMundiLangfuseBridge(make_langfuse(), on_outcome=bad_callback)
    assert bridge.write(measure(), trace_id=trace_id).status == QUEUED


def test_raise_errors_true_does_propagate_for_debugging():
    bridge = NeoMundiLangfuseBridge(ExplodingLangfuse(), raise_errors=True)
    with pytest.raises(RuntimeError):
        bridge.write(measure(), trace_id="a" * 32)


def test_a_missing_client_is_a_configuration_error_at_construction():
    """Refused loudly at wiring time, not silently at every write."""
    with pytest.raises(ValueError):
        NeoMundiLangfuseBridge(None)
