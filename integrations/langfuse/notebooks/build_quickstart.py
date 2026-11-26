#!/usr/bin/env python3
"""Build (and optionally execute) the quickstart notebook.

Generated rather than hand-edited so its JSON stays valid and its cells
stay in sync with the package. Run this, then commit the executed notebook.

    python notebooks/build_quickstart.py --execute
"""

from __future__ import annotations

import argparse
from pathlib import Path

import nbformat as nbf

HERE = Path(__file__).resolve().parent
OUT = HERE / "quickstart.ipynb"


def md(text: str) -> nbf.NotebookNode:
    return nbf.v4.new_markdown_cell(text.strip())


def code(text: str) -> nbf.NotebookNode:
    return nbf.v4.new_code_cell(text.strip())


CELLS = [
    md(
        """
# Langfuse × NeoMundi — quickstart

Put NeoMundi's signed behavioural measurement **on the Langfuse trace you
already have open**, as scores, with the NeoMundi `request_id` attached.

**This notebook costs nothing and opens no socket.** The NeoMundi API is
replaced by a mock that replays a captured production response, and the
Langfuse client's transport is a recorder: every score printed below is
the exact body Langfuse would send to `POST /api/public/scores`.

What it does **not** show: that a Langfuse server persists the scores.
That is outside this package's evidence (E1) — see `LIMITATIONS.md`.
"""
    ),
    md("## 1. Setup"),
    code(
        """
import json
import uuid
from pathlib import Path

import httpx
import neomundi
from langfuse import Langfuse
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import langfuse_neomundi
from langfuse_neomundi import NeoMundiLangfuseBridge

print(f"langfuse-neomundi {langfuse_neomundi.__version__}")
print(f"neomundi          {neomundi.__version__}")
print(json.dumps(langfuse_neomundi.INTEGRATION_CONTRACT, indent=2, default=str))
"""
    ),
    md(
        """
## 2. Two clients that cannot reach the network

In your application you already have a `Langfuse()` client and a
`NeoMundiClient(api_key=..., base_url=...)`. Here both are real objects
with mock transports.
"""
    ),
    code(
        """
FIXTURE = Path("..") / "tests" / "fixtures" / "rest_complete_allow.json"
PAYLOAD = {
    k: v
    for k, v in json.loads(FIXTURE.read_text(encoding="utf-8")).items()
    if not k.startswith("_fixture_")
}

nm = neomundi.NeoMundiClient(
    api_key="ct_test_0000000000000000000000000000",
    base_url="https://example.invalid",  # RFC 2606: cannot resolve
    transport=httpx.MockTransport(lambda request: httpx.Response(200, json=PAYLOAD)),
)

SENT = []


def langfuse_wire(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/api/public/scores"):
        SENT.extend(json.loads(request.content))
    return httpx.Response(200, json={})


lf = Langfuse(
    public_key=f"pk-lf-notebook-{uuid.uuid4().hex}",
    secret_key="sk-lf-notebook",
    base_url="https://example.invalid",
    httpx_client=httpx.Client(transport=httpx.MockTransport(langfuse_wire)),
    span_exporter=InMemorySpanExporter(),
)
print("clients ready — no network, no key, no cost")
"""
    ),
    md(
        """
## 3. Measure, then write back onto the active trace

The bridge does not measure. NeoMundi measures; the bridge transports the
result onto whichever Langfuse trace is active when `write()` is called.
"""
    ),
    code(
        """
bridge = NeoMundiLangfuseBridge(lf)

with lf.start_as_current_observation(name="answer-question"):
    with nm.trace(model_name="local-model") as trace:
        answer = "Paris."  # your LLM call
        trace.record(prompt="Capital of France?", response=answer, token_count=9)
    outcome = bridge.write(trace.close())

lf.flush()
print("status          :", outcome.status)
print("request_id      :", outcome.request_id)
print("signals written :", outcome.signals_written)
print()
for score in SENT:
    print(f"{score['name']:<40} {score['dataType']:<12} {score['value']}")
"""
    ),
    md(
        """
## 4. What is refused: a check that never ran

When a check is not declared *measured* by the server, it gets **no
numeric score** — even if the server published a conventional value such
as `coherence_score: 1.0`. The summary score says what was withheld and why.
"""
    ),
    code(
        """
partial = json.loads(json.dumps(PAYLOAD))
partial["measured_signals"].remove("coherence_score")
partial["quality"]["coherence_score"] = 1.0

SENT.clear()
outcome = bridge.write(neomundi.GovernResult.from_dict(partial), trace_id=lf.create_trace_id())
lf.flush()
print("written  :", outcome.signals_written)
print("withheld :", outcome.signals_withheld)
print("on wire  :", [s["name"] for s in SENT])
assert "neomundi.coherence_score" not in [s["name"] for s in SENT]
"""
    ),
    md(
        """
## 5. Wire it up for free: `dry_run=True`

Computes the scores, hands nothing to Langfuse, opens no socket.
"""
    ),
    code(
        """
SENT.clear()
dry = NeoMundiLangfuseBridge(lf, dry_run=True).write(
    neomundi.GovernResult.from_dict(PAYLOAD), trace_id=lf.create_trace_id()
)
lf.flush()
print("status            :", dry.status)
print("scores computed   :", len(dry.scores))
print("scores on the wire:", len(SENT))
lf.shutdown()
nm.close()
"""
    ),
    md(
        """
## Where to go next

- **[LIMITATIONS.md](../LIMITATIONS.md)** — read it before relying on a
  score in Langfuse, in particular §1: `queued` is not a delivery receipt.
- **[examples/02_refusals.py](../examples/02_refusals.py)** — the refusals
  and the Langfuse failure modes as a standalone script.
"""
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="run the notebook and embed outputs")
    args = parser.parse_args()

    notebook = nbf.v4.new_notebook(cells=CELLS)
    notebook.metadata.update(
        {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
        }
    )

    if args.execute:
        from nbclient import NotebookClient

        client = NotebookClient(
            notebook,
            timeout=600,
            kernel_name="python3",
            resources={"metadata": {"path": str(HERE)}},
        )
        client.execute()

    nbf.write(notebook, OUT)
    print(f"wrote {OUT} ({len(CELLS)} cells, executed={args.execute})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
