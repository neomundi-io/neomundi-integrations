#!/usr/bin/env python3
"""Build (and optionally execute) the quickstart notebook.

Generated rather than hand-edited so its JSON stays valid and its cells
stay in sync with the package. Run this, then commit the executed notebook.

    pip install -e ".[examples,notebook]"
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
# OpenTelemetry × NeoMundi — quickstart

Measure a model call your application **already traces**, with one span
processor added next to your exporters — and see the one thing this
integration will not do: turn a span's token count into a measurement.

**This notebook costs nothing and opens no socket.** The provider is an
`httpx.MockTransport` behind the real `openai` client, instrumented by the
real contrib `opentelemetry-instrumentation-openai-v2`. The NeoMundi API is
a mock that replays a captured production response.

What it does **not** show: a real measurement persisted by the API. That
is outside this package's evidence (E1) — see `LIMITATIONS.md`.
"""
    ),
    md("## 1. Setup"),
    code(
        """
import json
from importlib.metadata import version
from pathlib import Path

import httpx
import neomundi
from openai import OpenAI
from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from otel_neomundi import NeoMundiSpanProcessor

for name in ("otel-neomundi", "opentelemetry-sdk", "opentelemetry-instrumentation-openai-v2", "neomundi"):
    print(f"{name:42} {version(name)}")
"""
    ),
    md(
        """
## 2. Your existing pipeline

A tracer provider with an exporter, and the openai-v2 instrumentation on
it. This is what an instrumented application already has; nothing here is
NeoMundi's.
"""
    ),
    code(
        """
provider = TracerProvider()
exporter = InMemorySpanExporter()          # stands in for your OTLP exporter
provider.add_span_processor(SimpleSpanProcessor(exporter))
OpenAIInstrumentor().instrument(tracer_provider=provider)


def model_client(report_usage: bool) -> OpenAI:
    def handle(request):
        body = {
            "id": "c", "object": "chat.completion", "created": 1, "model": "demo-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "Paris."},
                         "finish_reason": "stop"}],
        }
        if report_usage:
            body["usage"] = {"prompt_tokens": 14, "completion_tokens": 3, "total_tokens": 17}
        return httpx.Response(200, json=body)

    return OpenAI(api_key="sk-demo", base_url="https://provider.example.invalid/v1",
                  max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handle)))
"""
    ),
    md(
        """
## 3. Add the processor — next to the exporter, replacing nothing

`submit=False` first: no key, no socket, every check run locally.
"""
    ),
    code(
        """
processor = NeoMundiSpanProcessor(submit=False, tracer_provider=provider)
provider.add_span_processor(processor)

with processor.interaction() as interaction:
    reply = model_client(True).chat.completions.create(
        model="demo-model", messages=[{"role": "user", "content": "Capital of France?"}])
    interaction.declare(token_count=reply.usage.total_tokens if reply.usage else None)

o = interaction.outcome
print(o.status, "| tokens", o.token_count, o.token_source, "| latency_ms", round(o.latency_ms, 3))
"""
    ),
    md(
        """
## 4. The refusal that defines this integration

The GenAI span below carries `gen_ai.usage.input_tokens = 14`. Nothing is
declared. **Nothing is measured.** A span attribute is written by an
instrumentation, not by the provider, and on 2026-11-26 we measured an
instrumentation option that replaces a provider's published count with a
tokenizer estimate while the span looks exactly the same.
"""
    ),
    code(
        """
with processor.interaction() as interaction:
    model_client(True).chat.completions.create(
        model="demo-model", messages=[{"role": "user", "content": "Capital of France?"}])

genai = [s for s in exporter.get_finished_spans() if s.name.startswith("chat")][-1]
print("span usage :", {k: v for k, v in genai.attributes.items() if "usage" in k})
print("status     :", interaction.outcome.status)
print("reason     :", interaction.outcome.reason)
"""
    ),
    md(
        """
## 5. Measured — and the request_id lands in your trace

With a client, the interaction is submitted, and `neomundi.request_id` is
set on the `neomundi.interaction` span, which your exporter receives in the
**same trace** as the model call.
"""
    ),
    code(
        """
FIXTURE = Path("..") / "tests" / "fixtures" / "rest_complete_allow.json"
PAYLOAD = {k: v for k, v in json.loads(FIXTURE.read_text(encoding="utf-8")).items()
           if not k.startswith("_fixture_")}
SENT = []

def neomundi_api(request):
    SENT.append(json.loads(request.content))
    return httpx.Response(200, json=PAYLOAD)

client = neomundi.NeoMundiClient(api_key="ct_test_notebook_not_a_key",
                                 base_url="https://example.invalid",
                                 transport=httpx.MockTransport(neomundi_api))
measuring = NeoMundiSpanProcessor(client, tracer_provider=provider)
provider.add_span_processor(measuring)

with measuring.interaction() as interaction:
    reply = model_client(True).chat.completions.create(
        model="demo-model", messages=[{"role": "user", "content": "Capital of France?"}])
    interaction.declare(token_count=reply.usage.total_tokens)

o = interaction.outcome
marker = [s for s in exporter.get_finished_spans() if s.name == "neomundi.interaction"][-1]
print("status            :", o.status)
print("sent token_count  :", SENT[-1]["raw_metrics"]["token_count"])
print("request_id        :", o.request_id)
print("in the trace      :", marker.attributes.get("neomundi.request_id"))
print("same trace        :", f"{marker.context.trace_id:032x}" == o.trace_id)
"""
    ),
    md(
        """
## 6. What next

- Read `LIMITATIONS.md` — especially §1, why a span's count is refused.
- Point a client at the real API with `sample_rate` well below 1.0:
  **every submitted observation is billed**.
"""
    ),
    code("OpenAIInstrumentor().uninstrument()"),
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
