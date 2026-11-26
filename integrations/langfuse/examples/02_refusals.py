"""What the bridge refuses to write, and what it never lets escape.

Offline, like example 01: real SDK and real Langfuse client, mock
transports, no key, no cost. Each section prints the bridge's outcome and
the score names Langfuse actually sent, so the refusal is visible on the
wire rather than asserted.

    python examples/02_refusals.py
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import httpx
from langfuse import Langfuse
from neomundi import GovernResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from langfuse_neomundi import NeoMundiLangfuseBridge

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "rest_complete_allow.json"
TRACE = "0" * 31 + "1"


def payload() -> dict:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_fixture_")}


def langfuse(sent: list[str], status: int | None = 200) -> Langfuse:
    def handle(request: httpx.Request) -> httpx.Response:
        if status is None:
            raise httpx.ConnectError("unreachable", request=request)
        if request.url.path.endswith("/api/public/scores") and status < 300:
            sent.extend(s["name"] for s in json.loads(request.content))
        return httpx.Response(status, json={})

    return Langfuse(
        public_key=f"pk-lf-example-{uuid.uuid4().hex}",
        secret_key="sk-lf-example",
        base_url="https://example.invalid",
        httpx_client=httpx.Client(transport=httpx.MockTransport(handle)),
        span_exporter=InMemorySpanExporter(),
    )


def section(title: str, measurement, *, trace_id: str | None = TRACE, status: int | None = 200):
    sent: list[str] = []
    client = langfuse(sent, status)
    outcome = NeoMundiLangfuseBridge(client).write(measurement, trace_id=trace_id)
    started = time.perf_counter()
    client.flush()
    flush_s = time.perf_counter() - started
    client.shutdown()
    print(f"## {title}")
    print(f"   bridge status : {outcome.status}")
    if outcome.signals_withheld:
        for signal, why in outcome.signals_withheld.items():
            print(f"   withheld      : {signal} ({why})")
    print(f"   scores landed : {sent or '(none)'}")
    print(f"   host continued: yes (flush took {flush_s:.1f}s)")
    print()


def main() -> int:
    unmeasured = payload()
    unmeasured["measured_signals"].remove("coherence_score")
    unmeasured["quality"]["coherence_score"] = 1.0  # the server's convention value

    section(
        "a check that never ran: its conventional 1.0 is not written",
        GovernResult.from_dict(unmeasured),
    )
    section(
        "no Langfuse trace to attach to: nothing is written",
        GovernResult.from_dict(payload()),
        trace_id=None,
    )
    section(
        "Langfuse answered 500: scores are dropped by Langfuse, host untouched",
        GovernResult.from_dict(payload()),
        status=500,
    )
    section(
        "Langfuse was unreachable: same, and flush blocks while it retries",
        GovernResult.from_dict(payload()),
        status=None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
