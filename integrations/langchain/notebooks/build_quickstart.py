#!/usr/bin/env python3
"""Build (and optionally execute) the quickstart notebook.

The notebook is generated rather than hand-edited so its JSON stays valid
and its cells stay in sync with the package. Run this, then commit the
executed notebook.

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
# LangChain × NeoMundi — quickstart

Measure what a LangChain application actually does at runtime, and see
exactly what the integration **refuses** to do.

**This notebook costs nothing to run.** Every cell below works offline,
with no NeoMundi key: the measurement API is replaced by a mock transport
that opens no socket. One clearly marked optional cell at the end performs
a real, billed measurement if you have a key.

The LLM is a real local model via [Ollama](https://ollama.com) when one is
available, and a usage-reporting stand-in otherwise — so the notebook runs
end to end on any machine.
"""
    ),
    md("## 1. Setup\n\nTwo dependencies, plus `pytest` only if you want to run the suite."),
    code(
        """
# pip install langchain-neomundi
import json
import sys

import httpx

import langchain_neomundi
import neomundi

print(f"langchain-neomundi {langchain_neomundi.__version__}")
print(f"neomundi           {neomundi.__version__}")
print(f"python             {sys.version.split()[0]}")
print()
print("integration contract:")
print(json.dumps(langchain_neomundi.INTEGRATION_CONTRACT, indent=2, default=str))
"""
    ),
    md(
        """
## 2. A measurement API that costs nothing

`NeoMundiClient` accepts an `httpx` transport. We point it at a mock that
returns a real production response captured under
`measurement_version 3.3.1`, and we count every request so we can prove
what was and was not submitted.

In your own application you would pass `api_key` and `base_url` instead —
the base URL is mandatory, because the SDK never assumes a host.
"""
    ),
    code(
        """
from pathlib import Path

FIXTURE = Path("..") / "tests" / "fixtures" / "rest_complete_allow.json"
PAYLOAD = {
    k: v
    for k, v in json.loads(FIXTURE.read_text(encoding="utf-8")).items()
    if not k.startswith("_fixture_")
}

REQUESTS = []


def mock_api(request: httpx.Request) -> httpx.Response:
    REQUESTS.append(json.loads(request.content.decode("utf-8")))
    return httpx.Response(200, json=PAYLOAD)


def fresh_client(handler=None):
    REQUESTS.clear()
    return neomundi.NeoMundiClient(
        api_key="ct_test_0000000000000000000000000000",
        base_url="https://example.invalid",  # RFC 2606: cannot resolve
        transport=httpx.MockTransport(handler or mock_api),
    )


print("client ready — no network, no key, no cost")
"""
    ),
    md(
        """
## 3. A real LLM, if one is around

`ChatOllama` reports real token usage, which is what the measurement
requires. If Ollama is not running, we fall back to a small model that
reports usage the same way, so the rest of the notebook behaves
identically.
"""
    ),
    code(
        """
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.messages.ai import UsageMetadata
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.prompts import ChatPromptTemplate


class FallbackModel(BaseChatModel):
    \"\"\"Reports usage exactly as a real provider does.\"\"\"

    @property
    def _llm_type(self) -> str:
        return "fallback"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        text = "Paris is the capital of France."
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content=text,
                        usage_metadata=UsageMetadata(
                            input_tokens=14, output_tokens=8, total_tokens=22
                        ),
                    )
                )
            ]
        )


def pick_model():
    try:
        from langchain_ollama import ChatOllama

        model = ChatOllama(model="llama3.2:3b", temperature=0)
        model.invoke("hi")  # probe
        return model, "ollama llama3.2:3b (real, local, free)"
    except Exception:
        return FallbackModel(), "fallback stand-in (no Ollama running)"


model, which = pick_model()
prompt = ChatPromptTemplate.from_messages([("human", "{question}")])
chain = prompt | model
print(f"LLM: {which}")
"""
    ),
    md(
        """
## 4. Measure one interaction

Three lines: build the handler, pass it as a callback, read the outcome.
"""
    ),
    code(
        """
from langchain_neomundi import NeoMundiCallbackHandler

client = fresh_client()
handler = NeoMundiCallbackHandler(client)

answer = chain.invoke(
    {"question": "In one sentence, what is the capital of France?"},
    config={"callbacks": [handler]},
)

outcome = handler.last_outcome
print(f"answer    : {answer.content.strip()}")
print()
print(f"status    : {outcome.status}")
print(f"submitted : {outcome.submitted}")
print(f"tokens    : {outcome.token_count}   (the provider's own count)")
print(f"latency   : {outcome.latency_ms:.0f} ms   (measured, not declared)")
print(f"request_id: {outcome.request_id}   (the only reliable correlation key)")
print(f"billed    : {handler.submitted_count} observation(s)")
"""
    ),
    md(
        """
### What actually went over the wire

Note that the metrics sit under `raw_metrics`, and that your LangChain
`run_id` is carried as `client_trace_id` — the server does not retain it
as an identity, which is why `request_id` is the key you correlate on.
"""
    ),
    code("print(json.dumps(REQUESTS[0], indent=2))"),
    md(
        """
## 5. The refusals

This is the part worth understanding before you trust any number.

### 5a. The provider reports no token count

Common while streaming, and common with self-hosted models. The handler
**submits nothing**. It does not estimate: the API counts nothing on this
route, so an invented count would be a fabricated measurement attributed
to your application.
"""
    ),
    code(
        """
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

silent_chain = prompt | GenericFakeChatModel(messages=iter(["Paris."] * 5))

client = fresh_client()
handler = NeoMundiCallbackHandler(client)
answer = silent_chain.invoke({"question": "Capital of France?"}, config={"callbacks": [handler]})

print(f"chain still answered : {answer.content!r}")
print(f"status               : {handler.last_outcome.status}")
print(f"requests sent        : {len(REQUESTS)}")
print(f"reason               : {handler.last_outcome.reason}")
"""
    ),
    md(
        """
### 5b. The measurement API is broken

401, 402, 429, a timeout, a refused connection — your chain must not care.
Nothing is raised into it.
"""
    ),
    code(
        """
def refuse(status):
    def handler_fn(request: httpx.Request) -> httpx.Response:
        REQUESTS.append({"attempted": True})
        return httpx.Response(status, json={"detail": "refused"})

    return handler_fn


def unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("name or service not known")


for label, transport in [
    ("401 unauthenticated", refuse(401)),
    ("402 payment required", refuse(402)),
    ("429 rate limited", refuse(429)),
    ("connection refused", unreachable),
]:
    client = fresh_client(transport)
    handler = NeoMundiCallbackHandler(client)
    answer = chain.invoke({"question": "Capital of France?"}, config={"callbacks": [handler]})
    print(f"{label:22} -> chain ok: {answer.content.strip()[:12]!r:16} "
          f"status: {handler.last_outcome.status}")
"""
    ),
    md(
        """
### 5c. Your own LLM call failed

There is no complete interaction to measure, so nothing is submitted. A
partial measurement would be a fabricated observation.
"""
    ),
    code(
        """
from uuid import uuid4

from langchain_core.messages import HumanMessage

client = fresh_client()
handler = NeoMundiCallbackHandler(client)

run_id = uuid4()
handler.on_chat_model_start({"kwargs": {"model": "m"}}, [[HumanMessage(content="Q")]], run_id=run_id)
handler.on_llm_error(TimeoutError("the provider timed out"), run_id=run_id)

o = handler.last_outcome
print(f"status     : {o.status}")
print(f"error_type : {o.error_type}   (class name only -- never the message)")
print(f"latency    : {o.latency_ms:.0f} ms   (what was observed is still reported)")
print(f"requests   : {len(REQUESTS)}")
"""
    ),
    md(
        """
## 6. Cost control

**Every submitted observation is billed** (0.030 CHF on TIER_03). There is
no trace-only route. With the default `sample_rate=1.0`, every LLM call of
your application is metered.

`sample_rate` is deterministic in the run id, so a sampled trace is
reproducible rather than a coin flip you cannot replay. `0.0` opens no
socket at all, which makes it a usable kill switch.
"""
    ),
    code(
        """
for rate in (1.0, 0.5, 0.0):
    client = fresh_client()
    handler = NeoMundiCallbackHandler(client, sample_rate=rate)
    for _ in range(20):
        chain.invoke({"question": "Capital of France?"}, config={"callbacks": [handler]})
    print(f"sample_rate={rate:<4} -> billed {handler.submitted_count:>2}/20   "
          f"requests sent: {len(REQUESTS):>2}   cost: {handler.submitted_count * 0.030:.2f} CHF")
"""
    ),
    md(
        """
## 7. RAG: context is picked up for you

Retrieved documents are read from `on_retriever_end` and attached to the
measurement as `rag_context`, matched to their LLM run by walking up the
run ancestry. No extra wiring.
"""
    ),
    code(
        """
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables import RunnablePassthrough

CONTEXT = [
    "NeoMundi measures the runtime behaviour of AI systems.",
    "The NeoMundi measurement API exposes POST /v1/govern for post-call measurement.",
]


class DemoRetriever(BaseRetriever):
    def _get_relevant_documents(self, query, *, run_manager):
        return [Document(page_content=t) for t in CONTEXT]


rag_prompt = ChatPromptTemplate.from_messages(
    [("human", "Context:\\n{context}\\n\\nQuestion: {question}")]
)
rag_chain = (
    {
        "context": DemoRetriever() | (lambda docs: "\\n".join(d.page_content for d in docs)),
        "question": RunnablePassthrough(),
    }
    | rag_prompt
    | model
)

client = fresh_client()
handler = NeoMundiCallbackHandler(client)
rag_chain.invoke("Which route is used for post-call measurement?", config={"callbacks": [handler]})

print(f"status      : {handler.last_outcome.status}")
print(f"rag_context : {json.dumps(REQUESTS[0].get('rag_context'), indent=2)}")
"""
    ),
    md(
        """
## 8. Comparability: the version caveat

Two results produced under two different `measurement_version` values are
**not comparable for the same input**. The handler warns once when the
served version is not one the installed SDK verified.

As measured on 2026-11-25, production serves `3.5.0` while the SDK
verifies `3.3.0` and `3.3.1` — deliberately, per the SDK's own
`docs/COMPATIBILITY_350.md`. So this warning fires on real production use
today. See `KNOWN_LIMITATIONS.md` §1.
"""
    ),
    code(
        """
import warnings

print(f"SDK verifies      : {neomundi.API_CONTRACT['verified_measurement_versions']}")
print("production serves : 3.5.0  (measured 2026-11-25)")
print()

client = fresh_client()
handler = NeoMundiCallbackHandler(client)
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    handler._warn_on_version("3.5.0")
    handler._warn_on_version("3.5.0")  # silent the second time

print(f"warnings emitted  : {len(caught)}  (once per handler, not once per call)")
print(f"message           : {str(caught[0].message)[:120]}...")
"""
    ),
    md(
        """
## 9. Optional: a real, billed measurement

Everything above was free. This cell performs **one real measurement**
against production (0.030 CHF) and is skipped unless `NEOMUNDI_API_KEY` is
set in your environment.

The execution proof shipped with this repository — 6 observations
submitted and all 6 read back as persisted — lives in `evidence/`.
"""
    ),
    code(
        """
import os

api_key = os.environ.get("NEOMUNDI_API_KEY")
if not api_key:
    print("NEOMUNDI_API_KEY not set -- skipped. Nothing was billed.")
else:
    real_client = neomundi.NeoMundiClient(api_key=api_key, base_url="https://api.neomundi.io")
    real_handler = NeoMundiCallbackHandler(
        real_client, metadata={"role": "notebook-demo", "excluded_from": "all"}
    )
    try:
        chain.invoke(
            {"question": "In one sentence, what is the capital of France?"},
            config={"callbacks": [real_handler]},
        )
        o = real_handler.last_outcome
        print(f"status             : {o.status}")
        print(f"request_id         : {o.request_id}")
        print(f"measurement_version: {o.measurement_version}")
        print(f"billed             : {real_handler.submitted_count} x 0.030 CHF")
    finally:
        real_client.close()
"""
    ),
    md(
        """
## Where to go next

- **[KNOWN_LIMITATIONS.md](../KNOWN_LIMITATIONS.md)** — ten specific
  limitations, each measured rather than assumed. Read it before drawing a
  conclusion from a score.
- **[docs/VALUE.md](../docs/VALUE.md)** — when this is worth paying for,
  with the sampling arithmetic spelled out.
- **[examples/02_refusals.py](../examples/02_refusals.py)** — the refusals
  as a standalone script.
- **[NeoMundi SDK](https://github.com/neomundi-io/neomundi-python-sdk)** —
  the brick this package drives.
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
