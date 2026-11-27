#!/usr/bin/env python3
"""Build (and optionally execute) the quickstart notebook.

Generated rather than hand-edited so its JSON stays valid and its cells
stay in sync with the package. Run this, then commit the executed
notebook.

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
# Microsoft Agent Framework × NeoMundi — quickstart

Everything here runs **offline, with no key and no cost**. The chat
client, its middleware pipeline and its response parsing are the
framework's real code; only the provider's HTTP transport and the
NeoMundi API are stood in for.

What it shows, in order:

1. a measured call, and the outcome the host can read;
2. 🔴 a silent provider — and what the integration does **not** do;
3. the other refusals, with what each one billed: nothing.
"""
    ),
    code(
        """
import httpx
from agent_framework.openai import OpenAIChatCompletionClient
from openai import AsyncOpenAI

from agent_framework_neomundi import NeoMundiChatMiddleware

def reply(usage):
    body = {
        "id": "chatcmpl-nb", "object": "chat.completion", "created": 1764115200,
        "model": "gpt-4o-mini",
        "choices": [{"index": 0,
                     "message": {"role": "assistant", "content": "Paris."},
                     "finish_reason": "stop"}],
    }
    if usage is not None:
        body["usage"] = usage
    return body

class StandInApi:
    "What NeoMundiClient would do, without the socket or the bill."
    def __init__(self):
        self.billed = 0
    def govern(self, **kwargs):
        self.billed += 1
        r = type("R", (), {})()
        r.request_id = "11111111-2222-3333-4444-555555555555"
        r.audit = type("A", (), {"measurement_version": "3.5.0"})()
        return r

def chat(body, middleware):
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda rq: httpx.Response(200, json=body))
    )
    return OpenAIChatCompletionClient(
        model="gpt-4o-mini",
        async_client=AsyncOpenAI(api_key="not-a-real-key", http_client=http),
        middleware=middleware,
    )

print("ready")
"""
    ),
    md(
        """
## 1. A measured call

The provider publishes `11 / 7 / 18`. One middleware, nothing else
changed in the application.
"""
    ),
    code(
        """
api = StandInApi()
measure = NeoMundiChatMiddleware(client=api)

answer = await chat(reply({"prompt_tokens": 11, "completion_tokens": 7,
                           "total_tokens": 18}), [measure]).get_response(
    "What is the capital of France?"
)

o = measure.last_outcome
print("the host got     :", repr(answer.text))
print("status           :", o.status)
print("token_count      :", o.token_count, f"({o.token_source})")
print("latency_ms       :", round(o.latency_ms, 1))
print("request_id       :", o.request_id)
print("billed so far    :", api.billed)
"""
    ),
    md(
        """
## 2. 🔴 A silent provider

The same call, with **no `usage` block** in the provider's reply.

Agent Framework leaves `usage_details` at `None` rather than filling it —
measured on 1.26.0. This integration then refuses, by name, and bills
nothing.

**It does not estimate.** That is the whole point: a count nobody
published is not a measurement.
"""
    ),
    code(
        """
api = StandInApi()
measure = NeoMundiChatMiddleware(client=api)

answer = await chat(reply(None), [measure]).get_response("What is the capital of France?")

o = measure.last_outcome
print("the host still got:", repr(answer.text))
print("status            :", o.status)
print("token_count       :", o.token_count)
print("reason            :", o.reason)
print("billed            :", api.billed)
"""
    ),
    md(
        """
## 3. The other refusals

Each is a real call through the framework. None produces a measurement
and none is billed.

⚠ Note the fourth: when the **measurement API** fails, the host still
gets its answer. When the **host's own call** fails, we record it and
re-raise — swallowing that would hide a failure from the application.
"""
    ),
    code(
        """
from neomundi import NeoMundiError


class FailingApi(StandInApi):
    def govern(self, **kwargs):
        raise NeoMundiError("unreachable")

cases = []

USAGE = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}

api = StandInApi()
mw = NeoMundiChatMiddleware(client=api)
await chat(reply({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}), [mw]).get_response("q")
cases.append(("a zero count", mw.last_outcome, api.billed))

api = FailingApi()
mw = NeoMundiChatMiddleware(client=api)
await chat(reply(USAGE), [mw]).get_response("q")
cases.append(("measurement API down", mw.last_outcome, api.billed))

api = StandInApi()
mw = NeoMundiChatMiddleware(client=api, submit=False)
await chat(reply(USAGE), [mw]).get_response("q")
cases.append(("submit=False", mw.last_outcome, api.billed))

for label, o, billed in cases:
    print(f"{label:24s} {o.status:34s} billed={billed}")
"""
    ),
    md(
        """
## What this notebook did not show

- **A real measurement.** Nothing here touched the production API, so no
  `measurement_version` is verified. This package is **E1**.
- **Streaming**, beyond its refusal — see `LIMITATIONS.md` §3.
- **Any connector but OpenAI Chat Completions** — `LIMITATIONS.md` §2.
- **Copilot Studio or Foundry.** Connectors to both exist inside the
  framework; this integration does not cover them — `LIMITATIONS.md` §7.

🔴 And the limit worth carrying away: the count is the **framework's**,
from a framework measured not to invent one. It is not verified against
the provider's own bytes on each call, the way `litellm-neomundi`
verifies it. `LIMITATIONS.md` §1.
"""
    ),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="run it before writing")
    args = parser.parse_args()

    notebook = nbf.v4.new_notebook(cells=CELLS)
    notebook.metadata["kernelspec"] = {
        "display_name": "Python 3",
        "language": "python",
        "name": "python3",
    }

    if args.execute:
        from nbclient import NotebookClient

        NotebookClient(notebook, timeout=180, kernel_name="python3").execute()

    nbf.write(notebook, OUT)
    print(f"wrote {OUT}" + (" (executed)" if args.execute else ""))


if __name__ == "__main__":
    main()
