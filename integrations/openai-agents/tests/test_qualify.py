"""``qualify.py`` honours the E2 harness contract, and its dry run sends nothing.

Criterion A13. The probe is loaded through ``tools/qualify_e2.py``'s own
loader -- the same path the harness takes -- and driven offline against a
mock provider standing in for Ollama. What this proves: the probe's logic
and the records it hands the harness. What it does not: that Ollama's
``/v1/responses`` answers as the mock does. That is the dry run's job on a
machine with Ollama.
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
    "integration": "openai-agents-neomundi",
    "qualification": "e2",
}
PHASES = [
    "plain 1",
    "plain 2",
    "tool run / call 1",
    "tool run / call 2",
    "chat completions",
    "no sensitive data",
]


def _harness():
    spec = importlib.util.spec_from_file_location("qualify_e2_under_test", HARNESS)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _ollama_like() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(Provider().handler))


def test_probe_loads_through_the_harness():
    module = _harness().load_probe("openai-agents")
    assert isinstance(module.PROBE_NAME, str) and module.PROBE_NAME.isascii()
    assert callable(module.probe)


def test_dry_run_records_and_sends_nothing():
    module = _harness().load_probe("openai-agents")
    recorder = Recorder()
    records = module.probe(
        make_client(recorder=recorder), submit=False, metadata=METADATA, http_client=_ollama_like()
    )
    assert recorder.count == 0
    assert [r["phase"] for r in records] == PHASES
    for record in records:
        assert CONTRACT_KEYS <= set(record), record
        assert record["submitted"] is False
    assert [r["status"] for r in records[:4]] == ["not_submitted_local_only"] * 4
    assert all(r["token_count"] == 18 for r in records[:4])
    assert records[2]["tool_calls"] == ["capital_of"]
    assert records[2]["tools_executed"] == ["capital_of"]
    assert records[4]["status"] == "not_submitted_unverified_token_count"
    assert records[5]["status"] == "not_submitted_no_provider_response"


def test_submitted_run_bills_four_and_carries_the_technical_metadata():
    module = _harness().load_probe("openai-agents")
    recorder = Recorder()
    records = module.probe(
        make_client(recorder=recorder), submit=True, metadata=METADATA, http_client=_ollama_like()
    )
    # Four billed, two refused -- the refusals are free.
    assert sum(r["submitted"] for r in records) == 4
    assert recorder.count == 4
    for body in recorder.bodies():
        for key, value in METADATA.items():
            assert body["metadata"][key] == value
