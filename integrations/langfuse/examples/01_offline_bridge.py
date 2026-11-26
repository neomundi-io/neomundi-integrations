"""Write a NeoMundi measurement onto a Langfuse trace -- fully offline.

What this demonstrates, honestly:

* the measurement is produced by the **real** ``neomundi`` SDK, but its API
  is replaced by a mock transport replaying a captured production response.
  No key, no socket, no cost;
* the Langfuse client is **real**, but its transport is a recorder too, so
  what is printed is the exact body Langfuse would send to
  ``POST /api/public/scores``.

It does **not** demonstrate that Langfuse persists the scores. That needs a
Langfuse instance and is not part of this package's evidence (E1).

    python examples/01_offline_bridge.py
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import httpx
from langfuse import Langfuse
from neomundi import NeoMundiClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from langfuse_neomundi import NeoMundiLangfuseBridge

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "rest_complete_allow.json"


def offline_neomundi() -> NeoMundiClient:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload = {k: v for k, v in payload.items() if not k.startswith("_fixture_")}
    return NeoMundiClient(
        api_key="ct_test_0000000000000000000000000000",
        base_url="https://example.invalid",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    )


def offline_langfuse(sent: list[dict]) -> Langfuse:
    def record(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/public/scores"):
            sent.extend(json.loads(request.content))
        return httpx.Response(200, json={})

    return Langfuse(
        public_key=f"pk-lf-example-{uuid.uuid4().hex}",
        secret_key="sk-lf-example",
        base_url="https://example.invalid",
        httpx_client=httpx.Client(transport=httpx.MockTransport(record)),
        span_exporter=InMemorySpanExporter(),
    )


def main() -> int:
    sent: list[dict] = []
    langfuse = offline_langfuse(sent)
    neomundi = offline_neomundi()
    bridge = NeoMundiLangfuseBridge(langfuse)

    # The application's own call, traced by Langfuse and measured by NeoMundi.
    with langfuse.start_as_current_observation(name="answer-question"):
        with neomundi.trace(model_name="local-model") as trace:
            answer = "Paris."  # your LLM call goes here
            trace.record(prompt="Capital of France?", response=answer, token_count=9)
        outcome = bridge.write(trace.close())  # the active trace is used

    langfuse.flush()
    langfuse.shutdown()

    print(f"bridge status    : {outcome.status}")
    print(f"request_id       : {outcome.request_id}")
    print(f"signals written  : {', '.join(outcome.signals_written) or '(none)'}")
    print(f"signals withheld : {outcome.signals_withheld or '(none)'}")
    print(f"\nwhat Langfuse sent to POST /api/public/scores ({len(sent)} scores):")
    for score in sent:
        print(f"  {score['name']:<40} {score['dataType']:<12} {score['value']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
