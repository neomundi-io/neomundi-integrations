"""Criteria 4, 5, L4, L5 and 8: what the bridge refuses, and what it never sends."""

from __future__ import annotations

import json
from dataclasses import dataclass

from conftest import (
    SECRET_PHRASE,
    SECRET_PROMPT,
    SECRET_RESPONSE,
    TEST_LANGFUSE_SECRET,
    TEST_NEOMUNDI_KEY,
    Recorder,
    load_fixture,
    make_langfuse,
    make_neomundi,
    measure,
    result,
)

from langfuse_neomundi import (
    DRY_RUN,
    NOT_WRITTEN_NO_TRACE,
    NOT_WRITTEN_UNSUPPORTED_INPUT,
    QUEUED,
    SUMMARY_SCORE,
    NeoMundiLangfuseBridge,
)


@dataclass(frozen=True)
class HandlerOutcome:
    """The public shape of ``langchain_neomundi.Outcome`` 0.1.0.

    Duplicated rather than imported: langchain-neomundi is not published,
    so CI cannot install it. ``test_composition.py`` runs the same path
    against the real class whenever it is installed.
    """

    run_id: str
    status: str
    submitted: bool = False
    request_id: str | None = None


def test_dry_run_opens_no_socket_and_returns_the_scores(recorder, trace_id):
    langfuse = make_langfuse(recorder=recorder)
    bridge = NeoMundiLangfuseBridge(langfuse, dry_run=True)
    outcome = bridge.write(measure(), trace_id=trace_id)
    langfuse.flush()
    langfuse.shutdown()

    assert recorder.count == 0
    assert outcome.status == DRY_RUN
    assert len(outcome.scores) == 6
    assert not outcome.written


def test_dry_run_needs_no_langfuse_client_at_all(trace_id):
    outcome = NeoMundiLangfuseBridge(None, dry_run=True).write(measure(), trace_id=trace_id)
    assert outcome.status == DRY_RUN


def test_the_bridge_never_contacts_neomundi(recorder, trace_id):
    """It transports a finished measurement; it never measures, so never bills."""
    record = measure()
    neomundi_calls = Recorder()
    make_neomundi(recorder=neomundi_calls)  # a client exists, the bridge never sees it
    NeoMundiLangfuseBridge(make_langfuse(recorder=recorder)).write(record, trace_id=trace_id)
    assert neomundi_calls.count == 0
    assert all("/v1/" not in str(r.url) for r in recorder.requests)


def test_no_trace_means_nothing_is_written(recorder):
    langfuse = make_langfuse(recorder=recorder)
    outcome = NeoMundiLangfuseBridge(langfuse).write(measure())
    langfuse.flush()
    assert outcome.status == NOT_WRITTEN_NO_TRACE
    assert recorder.score_bodies() == []


def test_the_active_langfuse_trace_is_used_when_none_is_passed(recorder):
    langfuse = make_langfuse(recorder=recorder)
    bridge = NeoMundiLangfuseBridge(langfuse, attach_to_observation=True)
    with langfuse.start_as_current_observation(name="answer"):
        active_trace = langfuse.get_current_trace_id()
        active_observation = langfuse.get_current_observation_id()
        outcome = bridge.write(measure())
    langfuse.flush()

    assert outcome.status == QUEUED
    sent = recorder.score_bodies()
    assert {s["traceId"] for s in sent} == {active_trace}
    assert {s["observationId"] for s in sent} == {active_observation}


def test_an_unknown_object_is_refused(recorder, trace_id):
    outcome = NeoMundiLangfuseBridge(make_langfuse(recorder=recorder)).write(
        {"g_score": 0.9}, trace_id=trace_id
    )
    assert outcome.status == NOT_WRITTEN_UNSUPPORTED_INPUT
    assert recorder.score_bodies() == []


def test_a_handler_outcome_writes_only_the_summary_with_its_request_id(recorder, trace_id):
    langfuse = make_langfuse(recorder=recorder)
    bridge = NeoMundiLangfuseBridge(langfuse)
    callback = bridge.as_outcome_callback(trace_id=trace_id)
    callback(HandlerOutcome(run_id="r", status="submitted", submitted=True, request_id="req-1"))
    langfuse.flush()

    sent = recorder.score_bodies()
    assert [s["name"] for s in sent] == [SUMMARY_SCORE]
    assert sent[0]["value"] == "submitted_no_result"
    assert sent[0]["metadata"]["request_id"] == "req-1"


def test_a_refused_handler_outcome_is_reported_as_not_submitted(recorder, trace_id):
    langfuse = make_langfuse(recorder=recorder)
    NeoMundiLangfuseBridge(langfuse).write(
        HandlerOutcome(run_id="r", status="not_submitted_no_token_count"), trace_id=trace_id
    )
    langfuse.flush()
    (summary,) = recorder.score_bodies()
    assert summary["value"] == "not_submitted"
    assert "not_submitted_no_token_count" in summary["metadata"]["not_submitted_because"]


def test_no_latency_score_is_written(recorder, trace_id):
    langfuse = make_langfuse(recorder=recorder)
    NeoMundiLangfuseBridge(langfuse).write(measure(), trace_id=trace_id)
    langfuse.flush()
    assert not [s for s in recorder.score_bodies() if "latency" in s["name"]]


def test_no_key_prompt_response_or_suspect_phrase_reaches_langfuse(recorder, trace_id):
    payload = load_fixture()
    payload["quality"]["suspect_phrases"] = [
        {
            "text": SECRET_PHRASE,
            "severity": "high",
            "category": "factual_error",
            "explanation": SECRET_PHRASE,
            "offset": 0,
        }
    ]
    langfuse = make_langfuse(recorder=recorder)
    bridge = NeoMundiLangfuseBridge(langfuse)
    outcomes = [
        bridge.write(measure(payload), trace_id=trace_id),
        bridge.write(result(payload), trace_id=trace_id),
    ]
    langfuse.flush()

    wire = recorder.raw()
    assert wire  # something was sent, so the check below means something
    for secret in (SECRET_PROMPT, SECRET_RESPONSE, SECRET_PHRASE, TEST_NEOMUNDI_KEY):
        assert secret not in wire
        for outcome in outcomes:
            assert secret not in repr(outcome)
    for outcome in outcomes:
        assert TEST_LANGFUSE_SECRET not in json.dumps(repr(outcome))
