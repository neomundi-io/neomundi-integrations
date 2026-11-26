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
# LiteLLM × NeoMundi — quickstart

Measure every chat completion that goes through LiteLLM with **one custom
logger**, and see the one thing this integration refuses: a token count
the provider never published.

**This notebook costs nothing and opens no socket.** The provider is an
`httpx.MockTransport` behind a real OpenAI client, so every call goes
through the real `litellm.completion`. The NeoMundi API is a mock that
replays a captured production response.

What it does **not** show: a real measurement persisted by the API. That
is outside this package's evidence (E1) — see `LIMITATIONS.md`.
"""
    ),
    md("## 1. Setup"),
    code(
        """
import json
import time
from importlib.metadata import version
from pathlib import Path

import httpx
import litellm
import neomundi
from openai import OpenAI

import litellm_neomundi
from litellm_neomundi import NeoMundiLogger

print(f"litellm-neomundi {litellm_neomundi.__version__}")
print(f"litellm          {version('litellm')}")
print(f"neomundi         {neomundi.__version__}")
"""
    ),
    md(
        """
## 2. A provider and an API that cannot reach the network

`provider(report_usage)` is an OpenAI-compatible endpoint that either
publishes its `usage` block or stays silent, as many self-hosted and
compatible servers do.
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
SENT = []


def provider(report_usage: bool) -> OpenAI:
    def handle(request):
        body = {
            "id": "c",
            "object": "chat.completion",
            "created": 1,
            "model": "demo-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Paris."},
                    "finish_reason": "stop",
                }
            ],
        }
        if report_usage:
            body["usage"] = {"prompt_tokens": 14, "completion_tokens": 3, "total_tokens": 17}
        return httpx.Response(200, json=body)

    return OpenAI(
        api_key="sk-demo",
        base_url="https://provider.example.invalid/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    )


def neomundi_api(request):
    SENT.append(json.loads(request.content))
    return httpx.Response(200, json=PAYLOAD)


client = neomundi.NeoMundiClient(
    api_key="ct_test_notebook_not_a_key",
    base_url="https://example.invalid",
    transport=httpx.MockTransport(neomundi_api),
)
"""
    ),
    md(
        """
## 3. Attach the logger — one line

In your application this is the whole integration. In the LiteLLM Proxy,
the same instance goes under `litellm_settings.callbacks`.
"""
    ),
    code(
        """
logger = NeoMundiLogger(client)
litellm.callbacks = [logger]


def ask(report_usage: bool):
    before = len(logger.outcomes)
    answer = litellm.completion(
        model="openai/demo-model",
        messages=[{"role": "user", "content": "Capital of France?"}],
        client=provider(report_usage),
    )
    while len(logger.outcomes) == before:  # LiteLLM logs in a worker thread
        time.sleep(0.01)
    return answer, logger.last_outcome


answer, outcome = ask(report_usage=True)
print("host got     :", answer.choices[0].message.content)
print("status       :", outcome.status)
print("request_id   :", "set" if outcome.request_id else None)
print("token_count  :", outcome.token_count, f"({outcome.token_source})")
print("latency      :", "observed by LiteLLM" if outcome.latency_ms is not None else None)
print("on the wire  :", json.dumps(SENT[-1]["raw_metrics"], default=str)[:200])
"""
    ),
    md(
        """
## 4. The provider stays silent

LiteLLM hands over `Usage(0, 0, 0)` — or, for Ollama and for streams, an
estimate from its own tokenizer. Both look exactly like a reported count.
The logger checks the provider's raw payload, finds no count there, and
**submits nothing**.
"""
    ),
    code(
        """
SENT.clear()
answer, outcome = ask(report_usage=False)
print("LiteLLM says :", answer.usage.total_tokens, "tokens")
print("status       :", outcome.status)
print("reason       :", outcome.reason)
print("requests sent:", len(SENT))
"""
    ),
    md(
        """
## 5. The free dry run, and the kill switch

`submit=False` runs every check and opens no socket — wire it up for free
first. `logger.sample_rate = 0.0` stops all billing at once. Do **not**
rely on `litellm.callbacks = []` to stop it: LiteLLM keeps the logger
registered (LIMITATIONS.md §4).
"""
    ),
    code(
        """
SENT.clear()
logger.sample_rate = 0.0
answer, outcome = ask(report_usage=True)
print("status       :", outcome.status)
print("requests sent:", len(SENT))
litellm.logging_callback_manager.remove_callback_from_all_lists(logger)
client.close()
"""
    ),
    md(
        """
## Where to go next

- **[LIMITATIONS.md](../LIMITATIONS.md)** — read it before relying on a
  score, in particular §1 (what is refused) and §2 (async streams).
- **[examples/02_refusals.py](../examples/02_refusals.py)** — every refusal
  and API failure as a standalone script.
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
