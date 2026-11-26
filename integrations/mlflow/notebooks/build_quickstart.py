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
# MLflow × NeoMundi — quickstart

Measure the model calls **MLflow already traces**, with one function
appended to MLflow's own span processors — and find the NeoMundi
observation on the MLflow trace afterwards. Then see what this integration
refuses to turn into a measurement.

**This notebook costs nothing and opens no socket.** It uses the real
`openai` client traced by the real `mlflow.openai.autolog()` into a
throwaway sqlite store. The provider is an `httpx.MockTransport`. The
NeoMundi API is a mock that replays a captured production response.
MLflow's usage telemetry is switched off before MLflow is imported.

What it does **not** show: a real measurement persisted by the API. That
is outside this package's evidence (E1). See `LIMITATIONS.md`.
"""
    ),
    md("## 1. Setup — before importing MLflow"),
    code(
        """
import json
import os
import tempfile
from importlib.metadata import version
from pathlib import Path

os.environ["MLFLOW_DISABLE_TELEMETRY"] = "true"  # on by default outside CI
os.environ["MLFLOW_DISABLE_AGENT_HINT"] = "1"  # MLflow's coding-assistant hints
os.environ["MLFLOW_TRACKING_URI"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "mlflow.db")

import httpx
import mlflow
import neomundi
from mlflow.entities import SpanType
from openai import OpenAI

from mlflow_neomundi import NeoMundiMlflowProcessor

store = Path(os.environ["MLFLOW_TRACKING_URI"].removeprefix("sqlite:///")).parent
print("mlflow-tracing ", version("mlflow-tracing"))
print("neomundi       ", neomundi.__version__)
print("mlflow-neomundi", version("mlflow-neomundi"))
"""
    ),
    md(
        """
## 2. A provider and a NeoMundi API, both offline

The provider answers Chat Completions. `usage=None` makes it publish **no**
token count, which is what many OpenAI-compatible servers do.
"""
    ),
    code(
        """
FULL = {"prompt_tokens": 12, "completion_tokens": 2, "total_tokens": 14}


def provider(usage=FULL):
    def handler(request):
        out = {
            "id": "c1",
            "object": "chat.completion",
            "created": 1764115200,
            "model": "notebook-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Paris."},
                    "finish_reason": "stop",
                }
            ],
        }
        if usage is not None:
            out["usage"] = usage
        return httpx.Response(200, json=out)

    return OpenAI(
        api_key="sk-notebook-not-a-credential",
        base_url="https://provider.example.invalid/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


fixture = Path("..") / "tests" / "fixtures" / "rest_complete_allow.json"
payload = {
    k: v
    for k, v in json.loads(fixture.read_text(encoding="utf-8")).items()
    if not k.startswith("_fixture_")
}
sent = []


def neomundi_api(request):
    sent.append(json.loads(request.content))
    return httpx.Response(200, json=payload)


api = neomundi.NeoMundiClient(
    api_key="ct_test_notebook_000000000000000000000",
    base_url="https://example.invalid",
    transport=httpx.MockTransport(neomundi_api),
)
"""
    ),
    md(
        """
## 3. Trace as usual, then install — after the tracking URI is set

`install()` **appends** one function through MLflow's public hook,
`mlflow.tracing.configure(span_processors=[...])`. A new tracking URI
resets that hook, so install after `set_tracking_uri`. `installed` tells
you whether MLflow still calls it.
"""
    ),
    code(
        """
# Artifacts next to the store, not in ./mlruns under the current directory.
mlflow.create_experiment("neomundi-quickstart", artifact_location=Path(store, "artifacts").as_uri())
mlflow.set_experiment("neomundi-quickstart")
mlflow.openai.autolog()
processor = NeoMundiMlflowProcessor(api).install()
client = provider()


@mlflow.trace(name="answer", span_type=SpanType.AGENT)
def answer(question):
    reply = client.chat.completions.create(
        model="notebook-model", messages=[{"role": "user", "content": question}]
    )
    return reply.choices[0].message.content


print(answer("What is the capital of France?"))
processor.flush()
for o in processor.outcomes:
    print(
        f"{o.span_type:<11} {o.span_name:<12} {o.status:<30} tokens={o.token_count} "
        f"latency_ms={o.latency_ms}"
    )
print("installed:", processor.installed, "| requests sent:", len(sent))
"""
    ),
    md(
        """
The model call is measured with the provider's own `total_tokens` and the
latency MLflow recorded on its span. The application's `AGENT` span is an
aggregate: measuring it too would count the call twice.

## 4. The observation is on the MLflow trace
"""
    ),
    code(
        """
trace = mlflow.get_trace(mlflow.get_last_active_trace_id(), flush=True)
for key, value in sorted(trace.info.tags.items()):
    if key.startswith("neomundi."):
        print(key, "=", value)
submitted = [o for o in processor.outcomes if o.submitted][0]
print("measurement_version verified by the SDK:", submitted.measurement_version_verified)
"""
    ),
    md(
        """
Compare two runs only when their `neomundi.measurement_version` matches —
the same rule you already apply to model versions.

## 5. What is refused, and what it costs: nothing
"""
    ),
    code(
        """
processor.reset()
sent.clear()
silent = provider(usage=None)
silent.chat.completions.create(
    model="notebook-model", messages=[{"role": "user", "content": "Capital of Japan?"}]
)

with mlflow.start_span(name="litellm-completion", span_type=SpanType.LLM) as span:
    # How LiteLLM's MLflow logger writes its own (possibly estimated) count.
    span.set_attribute(
        "mlflow.chat.tokenUsage", {"input_tokens": 14, "output_tokens": 7, "total_tokens": 21}
    )

with mlflow.start_span(name="search", span_type=SpanType.RETRIEVER):
    pass

processor.flush()
for o in processor.outcomes:
    print(f"{o.span_type:<10} writer={o.writer!s:<7} {o.status}")
    print(f"           {o.reason}")
print("requests sent:", len(sent))
"""
    ),
    md(
        """
A provider that publishes no usage leaves MLflow's attribute **absent**,
and absent stays absent: no estimate, no zero. A count written by anything
other than MLflow's `openai` autolog cannot be traced to the provider, so
it is refused — naming the writer. A retriever is not a model call.

## 6. Free first: `submit=False`
"""
    ),
    code(
        """
dry = NeoMundiMlflowProcessor(submit=False).install()  # no key, no socket
print("previous processor retired:", processor.retired)
answer("What is the capital of Italy?")
dry.flush()
print([(o.span_type, o.status, o.token_count) for o in dry.outcomes])
dry.uninstall()
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
