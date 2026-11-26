"""``qualify.py`` honours the E2 harness contract, and its dry run sends nothing.

Criterion M14. The probe is loaded through ``tools/qualify_e2.py``'s own
loader -- the same path the harness takes -- and driven offline against a
mock provider standing in for Ollama. What this proves: the probe's logic
and the records it hands the harness. What it does not: that Ollama answers
as the mock does. That is the dry run's job on a machine with Ollama.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import httpx
from conftest import Provider, Recorder, make_client

ROOT = Path(__file__).resolve().parents[3]
HARNESS = ROOT / "tools" / "qualify_e2.py"
CONTRACT_KEYS = {
    "phase",
    "submitted",
    "request_id",
    "latency_ms",
    "token_count",
    "status",
    "reason",
}
METADATA = {
    "role": "devrel-verify",
    "excluded_from": "Study2_Stage1_Stage2_Stage3",
    "integration": "mlflow-neomundi",
    "qualification": "e2",
}
PHASES = [
    "plain 1",
    "plain 2",
    "responses api",
    "stream with usage",
    "stream without usage",
    "hand-written span",
]
BILLED = {"plain 1", "plain 2", "responses api", "stream with usage"}


def _harness():
    spec = importlib.util.spec_from_file_location("qualify_e2_under_test", HARNESS)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _ollama_like() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(Provider().handler))


def test_probe_loads_through_the_harness():
    module = _harness().load_probe("mlflow")
    assert isinstance(module.PROBE_NAME, str) and module.PROBE_NAME.isascii()
    assert callable(module.probe)


def test_dry_run_records_and_sends_nothing():
    module = _harness().load_probe("mlflow")
    recorder = Recorder()
    records = module.probe(
        make_client(recorder=recorder), submit=False, metadata=METADATA, http_client=_ollama_like()
    )
    assert recorder.count == 0
    assert [r["phase"] for r in records] == PHASES
    for record in records:
        assert CONTRACT_KEYS <= set(record), record
        assert record["submitted"] is False
        assert record["request_id"] is None
    by_phase = {r["phase"]: r for r in records}
    for phase in BILLED:
        assert by_phase[phase]["status"] == "not_submitted_local_only", by_phase[phase]
        assert by_phase[phase]["token_count"] == 18
    assert by_phase["stream without usage"]["status"] == "not_submitted_no_token_count"
    assert by_phase["hand-written span"]["status"] == "not_submitted_unverified_token_count"
    assert "not_submitted_aggregate_span" in by_phase["plain 1"]["other_spans"]


def test_real_run_bills_exactly_four_and_tags_the_traces():
    module = _harness().load_probe("mlflow")
    recorder = Recorder()
    records = module.probe(
        make_client(recorder=recorder), submit=True, metadata=METADATA, http_client=_ollama_like()
    )
    billed = [r for r in records if r["submitted"]]
    assert {r["phase"] for r in billed} == BILLED
    assert recorder.count == 4
    assert all(r["request_id"] and r["trace_tagged"] is True for r in billed)
    for body in recorder.bodies():
        assert body["metadata"]["role"] == "devrel-verify"
        assert body["metadata"]["excluded_from"] == "Study2_Stage1_Stage2_Stage3"


def test_the_probe_leaves_no_hook_behind():
    from mlflow.tracing.config import get_config

    module = _harness().load_probe("mlflow")
    before = list(get_config().span_processors)
    module.probe(None, submit=False, metadata=METADATA, http_client=_ollama_like())
    assert list(get_config().span_processors) == before
