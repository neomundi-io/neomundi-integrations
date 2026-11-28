"""Criteria L1, L2, L3, L6 and 2: what actually reaches Langfuse."""

from __future__ import annotations

import copy

from conftest import load_fixture, make_langfuse, measure, result
from neomundi import COVERAGE_SIGNALS

from langfuse_neomundi import (
    QUEUED,
    SUMMARY_SCORE,
    NeoMundiLangfuseBridge,
)


def _write(measurement, recorder, trace_id, **kwargs):
    langfuse = make_langfuse(recorder=recorder)
    bridge = NeoMundiLangfuseBridge(langfuse)
    outcome = bridge.write(measurement, trace_id=trace_id, **kwargs)
    langfuse.flush()
    langfuse.shutdown()
    return outcome, {s["name"]: s for s in recorder.score_bodies()}


def test_each_measured_signal_becomes_one_numeric_score_with_the_server_value(recorder, trace_id):
    expected = load_fixture()["quality"]
    outcome, sent = _write(measure(), recorder, trace_id)

    assert outcome.status == QUEUED
    for signal in COVERAGE_SIGNALS:
        score = sent[f"neomundi.{signal}"]
        assert score["dataType"] == "NUMERIC"
        assert score["value"] == expected[signal]  # exactly, not rescaled
        assert score["traceId"] == trace_id
    assert len(sent) == len(COVERAGE_SIGNALS) + 1


def test_every_score_carries_the_request_id_and_measurement_version(recorder, trace_id):
    fixture = load_fixture()
    _, sent = _write(measure(), recorder, trace_id)
    for score in sent.values():
        assert score["metadata"]["request_id"] == fixture["request_id"]
        assert score["metadata"]["measurement_version"] == "3.3.1"


def test_the_summary_score_is_categorical_and_reports_the_measurement_status(recorder, trace_id):
    _, sent = _write(measure(), recorder, trace_id)
    summary = sent[SUMMARY_SCORE]
    assert summary["dataType"] == "CATEGORICAL"
    assert summary["value"] == "complete"
    assert summary["metadata"]["signals_withheld"] == {}
    assert set(summary["metadata"]["signals_written"]) == set(COVERAGE_SIGNALS)


def test_a_signal_not_declared_measured_gets_no_numeric_score(recorder, trace_id):
    """The conventional coherence 1.0 of a check that never ran is not a value."""
    payload = load_fixture()
    payload["measured_signals"] = [s for s in payload["measured_signals"] if s != "coherence_score"]
    payload["quality"]["coherence_score"] = 1.0  # what the server sends then

    outcome, sent = _write(result(payload), recorder, trace_id)

    assert "neomundi.coherence_score" not in sent
    assert "coherence_score" in outcome.signals_withheld
    assert "coherence_score" in sent[SUMMARY_SCORE]["metadata"]["signals_withheld"]


def test_an_unmeasured_signal_keeps_the_server_reason(recorder, trace_id):
    payload = load_fixture()
    payload["measured_signals"] = [
        s for s in payload["measured_signals"] if s != "factual_hallucination_score"
    ]
    payload["unmeasured_signals"] = {"factual_hallucination_score": "judge_timeout"}
    payload["quality"]["factual_hallucination_score"] = None

    _, sent = _write(result(payload), recorder, trace_id)

    withheld = sent[SUMMARY_SCORE]["metadata"]["signals_withheld"]
    assert withheld["factual_hallucination_score"] == "not_measured: judge_timeout"
    assert "neomundi.factual_hallucination_score" not in sent


def test_a_measured_signal_with_a_null_value_is_withheld_not_zeroed(recorder, trace_id):
    payload = load_fixture()
    payload["quality"]["semantic_risk"] = None
    _, sent = _write(result(payload), recorder, trace_id)
    assert "neomundi.semantic_risk" not in sent
    assert all(s["value"] is not None for s in sent.values())


def test_a_local_only_trace_writes_the_summary_and_no_signal(recorder, trace_id):
    outcome, sent = _write(measure(submit=False), recorder, trace_id)
    assert list(sent) == [SUMMARY_SCORE]
    assert sent[SUMMARY_SCORE]["value"] == "not_submitted"
    assert outcome.signals_written == ()


def test_an_older_server_without_provenance_lists_writes_no_signal(recorder, trace_id):
    """No measured_signals and no unmeasured_signals: state unknown, so nothing."""
    payload = load_fixture()
    payload.pop("measured_signals")
    payload.pop("unmeasured_signals")
    _, sent = _write(result(payload), recorder, trace_id)
    assert list(sent) == [SUMMARY_SCORE]


def test_score_ids_are_deterministic_in_trace_request_and_name(trace_id):
    from conftest import Recorder

    first, second = Recorder(), Recorder()
    _write(measure(), first, trace_id)
    _write(measure(), second, trace_id)
    ids_a = sorted(s["id"] for s in first.score_bodies())
    ids_b = sorted(s["id"] for s in second.score_bodies())
    assert ids_a == ids_b
    assert len(set(ids_a)) == len(ids_a)

    other = Recorder()
    _write(measure(), other, "f" * 32)
    assert not set(ids_a) & {s["id"] for s in other.score_bodies()}


def test_observation_id_is_forwarded(recorder, trace_id):
    _, sent = _write(measure(), recorder, trace_id, observation_id="0123456789abcdef")
    assert {s["observationId"] for s in sent.values()} == {"0123456789abcdef"}


def test_the_input_result_is_not_mutated(recorder, trace_id):
    payload = load_fixture()
    before = copy.deepcopy(payload)
    _write(result(payload), recorder, trace_id)
    assert payload == before


# --- An aggregate that was required and never attempted is not a value ------
#
# The SDK documents both conventions: `measurement.coverage` is 1.0 when
# nothing was attempted, and a check that never ran can carry a reassuring
# value. Nothing numeric may reach Langfuse from such an observation.

_NOTHING_ATTEMPTED = {
    "status": "not_attempted",
    "checks_attempted": 0,
    "checks_succeeded": 0,
    "coverage": 1.0,
    "aggregates_publishable": False,
}
_PERFECT = {
    "stability_score": 1.0,
    "coherence_score": 1.0,
    "semantic_risk": 0.0,
    "factual_hallucination_score": 0.0,
    "semantic_instability_score": 0.0,
}


def test_nothing_attempted_writes_no_number_and_withholds_the_conventional_coverage(
    recorder, trace_id
):
    payload = load_fixture()
    payload["measurement"].update(_NOTHING_ATTEMPTED)
    payload["measured_signals"] = []
    payload["quality"].update(_PERFECT)

    _, sent = _write(result(payload), recorder, trace_id)

    assert list(sent) == [SUMMARY_SCORE]
    summary = sent[SUMMARY_SCORE]
    assert summary["value"] == "not_attempted"
    assert summary["metadata"]["coverage"] is None  # not the conventional 1.0
    assert summary["metadata"]["coverage_withheld"].startswith("not_attempted")


def test_nothing_attempted_overrides_signals_the_server_still_lists_as_measured(recorder, trace_id):
    """The EVPACK-033 shape: no check ran, yet five signals are declared measured."""
    payload = load_fixture()
    payload["measurement"].update(_NOTHING_ATTEMPTED)
    payload["quality"].update(_PERFECT)  # measured_signals left as served: all five

    outcome, sent = _write(result(payload), recorder, trace_id)

    assert list(sent) == [SUMMARY_SCORE]
    assert set(outcome.signals_withheld) == set(COVERAGE_SIGNALS)
    for reason in outcome.signals_withheld.values():
        assert reason.startswith("not_attempted")


def test_zero_checks_attempted_is_enough_even_if_the_status_says_otherwise(recorder, trace_id):
    payload = load_fixture()
    payload["measurement"]["checks_attempted"] = 0
    payload["quality"].update(_PERFECT)

    _, sent = _write(result(payload), recorder, trace_id)

    assert list(sent) == [SUMMARY_SCORE]
    assert sent[SUMMARY_SCORE]["metadata"]["coverage"] is None


def test_a_complete_measurement_still_carries_its_coverage_number(recorder, trace_id):
    _, sent = _write(measure(), recorder, trace_id)
    assert sent[SUMMARY_SCORE]["metadata"]["coverage"] == load_fixture()["measurement"]["coverage"]
    assert "coverage_withheld" not in sent[SUMMARY_SCORE]["metadata"]
