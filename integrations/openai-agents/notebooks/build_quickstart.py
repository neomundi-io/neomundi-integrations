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
# OpenAI Agents SDK × NeoMundi — quickstart

Measure **every model call** a tool-using agent makes, with one trace
processor appended to the ones the SDK already runs. Then see what this
integration refuses to turn into a measurement.

**This notebook costs nothing and opens no socket.** It uses a real
`Agent`, the real `Runner` and the real `OpenAIResponsesModel`. The
provider is an `httpx.MockTransport` behind the real `AsyncOpenAI`
client. The NeoMundi API is a mock that replays a captured production
response.

What it does **not** show: a real measurement persisted by the API. That
is outside this package's evidence (E1). See `LIMITATIONS.md`.
"""
    ),
    md("## 1. Setup"),
    code(
        """
import json
import os
from importlib.metadata import version
from pathlib import Path

import httpx
import neomundi
from agents import (Agent, OpenAIChatCompletionsModel, OpenAIResponsesModel, RunConfig,
                    Runner, function_tool)
from agents.tracing import get_trace_provider
from openai import AsyncOpenAI

from openai_agents_neomundi import NeoMundiTraceProcessor

os.environ.pop("OPENAI_API_KEY", None)   # the SDK's own exporter then exports nothing
for name in ("openai-agents-neomundi", "openai-agents", "openai", "neomundi"):
    print(f"{name:24} {version(name)}")
"""
    ),
    md(
        """
## 2. An agent with one tool

The scripted provider behaves like a model that uses tools. It asks for
`weather` first, then answers from the tool's result: **two model calls**.
It publishes `usage` 12 / 2 / 14 on each `Response`, unless told not to.
"""
    ),
    code(
        """
def provider(usage="default"):
    async def handle(request):
        await request.aread()
        body = json.loads(request.content)
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json={
                "id": "c", "object": "chat.completion", "created": 1, "model": "demo-model",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": "Sunny."}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 2, "total_tokens": 14}})
        tool_done = any(isinstance(i, dict) and i.get("type") == "function_call_output"
                        for i in body.get("input", []))
        if body.get("tools") and not tool_done:
            output = [{"type": "function_call", "id": "fc", "call_id": "call", "status": "completed",
                       "name": body["tools"][0]["name"], "arguments": '{"city": "Paris"}'}]
        else:
            output = [{"type": "message", "id": "m", "role": "assistant", "status": "completed",
                       "content": [{"type": "output_text", "text": "Sunny in Paris.",
                                    "annotations": []}]}]
        out = {"id": "r", "object": "response", "created_at": 1, "model": "demo-model",
               "status": "completed", "output": output, "parallel_tool_calls": True,
               "tool_choice": "auto", "tools": []}
        if usage == "default":
            out["usage"] = {"input_tokens": 12, "input_tokens_details": {"cached_tokens": 0},
                            "output_tokens": 2, "output_tokens_details": {"reasoning_tokens": 0},
                            "total_tokens": 14}
        return httpx.Response(200, json=out)

    return AsyncOpenAI(api_key="sk-demo", base_url="https://provider.example.invalid/v1",
                       max_retries=0,
                       http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))


@function_tool
def weather(city: str) -> str:
    \"\"\"Return the weather for a city.\"\"\"
    return f"sunny in {city}"


def agent(model_cls=OpenAIResponsesModel, **provider_kwargs):
    return Agent(name="weather-agent", instructions="Answer briefly.", tools=[weather],
                 model=model_cls(model="demo-model", openai_client=provider(**provider_kwargs)))
"""
    ),
    md(
        """
## 3. Install the processor, appended rather than replacing

`submit=False` first: no key, no socket, every check run locally. Note the
processor list: the SDK's own `BatchTraceProcessor` is still there.
`set_trace_processors` would have removed it.
"""
    ),
    code(
        """
processor = NeoMundiTraceProcessor(submit=False).install()
print([type(p).__name__ for p in get_trace_provider()._multi_processor._processors])

result = await Runner.run(agent(), "What is the weather in Paris?")
processor.flush()
print("final_output:", result.final_output)
for o in processor.outcomes:
    print(f"{o.span_type:18} {o.status:32} tokens={o.token_count} tools={list(o.tool_calls)}"
          + (f" tool={o.tool_name}" if o.tool_name else ""))
"""
    ),
    md(
        """
Two model calls, two measurable outcomes. The first asked for the
`weather` tool. The tool execution, both turns, the agent step and the
task total are reported **by name** and refused. The task span alone says
24 / 4: measuring it as well would have billed the run twice.

## 4. The refusals that define this integration

The same agent on **Chat Completions**. The provider publishes a full
usage block, and the span is still refused, because the SDK rebuilds that
dict. On the LiteLLM route we measured it carrying a local estimate where
the provider published nothing. Then a provider that publishes no usage
on the Responses API, and a run that does not record sensitive data.
"""
    ),
    code(
        """
for title, the_agent, config in [
    ("chat completions", agent(OpenAIChatCompletionsModel), {}),
    ("no usage published", agent(usage=None), {}),
    ("no sensitive data", agent(), {"trace_include_sensitive_data": False}),
]:
    processor.reset()
    await Runner.run(the_agent, "What is the weather in Paris?", run_config=RunConfig(**config))
    processor.flush()
    first = [o for o in processor.outcomes
             if o.span_type in ("ResponseSpanData", "GenerationSpanData")][0]
    print(f"{title:20} {first.span_type:18} {first.status}")
"""
    ),
    md(
        """
## 5. Measured

With a client, each model call is submitted on a worker thread. The agent
never waits for the measurement API. Installing this second processor
**retires** the first one, so nothing is measured twice.
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
measuring = NeoMundiTraceProcessor(client).install()

await Runner.run(agent(), "What is the weather in Paris?")
measuring.flush()
for o in measuring.outcomes:
    if o.submitted:
        print(f"submitted  tokens={o.token_count}  latency_ms={o.latency_ms:.3f}  "
              f"agent={o.agent_name}  tools={list(o.tool_calls)}  request_id={o.request_id}")
print("requests sent       :", len(SENT))
print("first processor     : retired =", processor.retired)
"""
    ),
    md(
        """
## 6. What next

- Read `LIMITATIONS.md`, especially §1 to §3, on which spans are refused
  and why.
- Point a client at the real API with `sample_rate` well below 1.0.
  **Every submitted observation is billed, once per model call.**
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
