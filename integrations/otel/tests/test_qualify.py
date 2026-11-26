"""``qualify.py`` honours the E2 harness contract, and its dry run sends nothing.

Criterion O11. The probe is loaded through ``tools/qualify_e2.py``'s own
loader -- the same path the harness takes -- and driven offline against a
mock OpenAI-compatible provider standing in for Ollama. What this proves:
the probe's logic and the records it hands the harness. What it does not:
that Ollama answers as the mock does. That is the dry run's job on a
machine with Ollama.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx
from conftest import REPORTED, Recorder, make_client, openai_body, openai_stream

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


def _harness():
    spec = importlib.util.spec_from_file_location("qualify_e2_under_test", HARNESS)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _ollama_like() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        if body.get("stream"):
            usage = REPORTED if (body.get("stream_options") or {}).get("include_usage") else None
            return httpx.Response(
                200, content=openai_stream(usage), headers={"content-type": "text/event-stream"}
            )
        return httpx.Response(200, json=openai_body(REPORTED))

    return httpx.Client(transport=httpx.MockTransport(handler))


METADATA = {
    "role": "devrel-verify",
    "excluded_from": "Study2_Stage1_Stage2_Stage3",
    "integration": "otel-neomundi",
    "qualification": "e2",
}


def test_probe_loads_through_the_harness():
    module = _harness().load_probe("otel")
    assert isinstance(module.PROBE_NAME, str) and module.PROBE_NAME.isascii()
    assert callable(module.probe)


def test_dry_run_records_and_sends_nothing():
    module = _harness().load_probe("otel")
    recorder = Recorder()
    records = module.probe(
        make_client(recorder=recorder), submit=False, metadata=METADATA, http_client=_ollama_like()
    )
    assert recorder.count == 0
    assert [r["phase"] for r in records] == [
        "declared 1",
        "declared 2",
        "declared 3",
        "declared 4",
        "span usage only",
        "stream without usage",
    ]
    for record in records:
        assert CONTRACT_KEYS <= set(record), record
        assert record["submitted"] is False
    assert [r["status"] for r in records[:4]] == ["not_submitted_local_only"] * 4
    assert all(
        r["token_count"] == 18 and r["token_source"] == "caller_declared" for r in records[:4]
    )
    assert records[4]["status"] == "not_submitted_no_token_count"
    assert records[4]["span_reported_usage"] is True
    assert records[5]["status"] == "not_submitted_no_token_count"


def test_submitted_run_carries_the_technical_metadata():
    module = _harness().load_probe("otel")
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
