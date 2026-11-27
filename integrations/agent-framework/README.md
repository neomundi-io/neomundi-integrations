<div align="center">

[![English](https://img.shields.io/badge/EN-English-2563eb?style=flat-square)](README.md) · [![Français](https://img.shields.io/badge/FR-Fran%C3%A7ais-6b7280?style=flat-square)](README_FR.md)

# Microsoft Agent Framework × NeoMundi

**Signed behavioural measurement of every model call your agent makes, from one middleware.**

**[Quickstart](#quickstart)** · **[What it refuses to do](#refusals)** · **[Notebook](notebooks/quickstart.ipynb)** · **[Limitations](LIMITATIONS.md)** · **[Cost](#what-it-costs)**

</div>

---

> **Evidence level E2.** Two real measurements on 2026-11-28, through a
> real Agent Framework client against a provider's OpenAI-compatible
> route, **both re-read from the NeoMundi API as persisted** at
> `measurement_version 3.5.0`. Plus 21 offline tests on the framework's
> real middleware pipeline. See
> [LIMITATIONS §10](LIMITATIONS.md#10-evidence-level-e2--and-what-it-does-not-cover).

## What this is

[Microsoft Agent Framework](https://learn.microsoft.com/en-us/agent-framework/overview/)
is the successor to Semantic Kernel and AutoGen. It exposes a
`ChatMiddleware` layer that brackets every model call.

This package puts a NeoMundi measurement on that layer. Attach it once to
a chat client and every call that client makes is measured — with the
provider's own token count and the latency the call actually took.

```
your agent  ──▶  ChatMiddleware  ──▶  the provider
                       │
                       └──▶  NeoMundi:  5 signals, with provenance
```

## Install

🟡 **Not published on PyPI yet.** `pip install agent-framework-neomundi`
does not work today. Install from this repository:

```bash
pip install "git+https://github.com/neomundi-io/neomundi-integrations.git#subdirectory=integrations/agent-framework"
```

Python 3.11 or newer. Two dependencies: `agent-framework-core` and
`neomundi`.

🟢 **Not the meta-package.** `pip install agent-framework` pulls some
thirty packages — Copilot Studio, Foundry, Bedrock, Redis, a WASM
sandbox. This middleware needs none of them, so it depends on
`agent-framework-core` alone.

## Quickstart

```python
from agent_framework.openai import OpenAIChatCompletionClient
from agent_framework_neomundi import NeoMundiChatMiddleware
from neomundi import NeoMundiClient

measure = NeoMundiChatMiddleware(
    client=NeoMundiClient(api_key=..., base_url="https://api.neomundi.io"),
)

chat = OpenAIChatCompletionClient(model="gpt-4o-mini", middleware=[measure])

reply = await chat.get_response("What is the capital of France?")

outcome = measure.last_outcome
print(outcome.status)  # submitted, or the named refusal
print(outcome.request_id)  # your correlation key
print(outcome.token_count)  # the provider's number, never ours
```

Nothing else changes in your application. The answer reaches your code
exactly as before, and before anything is measured.

## Refusals

🔴 **The refusals are the part worth reading.** A measurement product
that quietly fills a gap is worse than one that says it cannot measure.

| Status | When |
|---|---|
| `not_submitted_no_token_count` | the provider published none, so the framework left `usage_details` absent. **We do not invent one.** |
| `not_submitted_invalid_token_count` | `usage_details` was there but carried no usable positive integer |
| `not_submitted_streaming` | a streaming call: usage exists only after the stream is consumed, and this middleware resumes before that |
| `not_submitted_local_only` | `submit=False`, or no client — nothing left the process |
| `not_submitted_no_result` | `call_next()` returned nothing usable |
| `not_submitted_application_error` | **your** call failed. Recorded, then re-raised — swallowing it would hide a failure from you |
| `not_submitted_transport_error` | the measurement API was unreachable or refused |
| `not_submitted_internal_error` | we failed. Reported, never raised into your application |

Run them: `python examples/02_refusals.py` makes four real calls and
prints the billed count — zero.

## Three things we measured, not assumed

🟢 **Agent Framework does not invent a token count.** Provider publishes
`11/7/18` → `usage_details` carries it. Provider publishes nothing →
`usage_details` is `None`. Both cases, streaming and not, on 1.26.0.

🔴 **But we cannot verify it per call.** The provider's raw payload is
not reachable at this hook, so the check `litellm-neomundi` performs is
impossible here. The count is the framework's, from a framework measured
not to fabricate — that is weaker, and
[LIMITATIONS §1](LIMITATIONS.md#1--the-token-count-is-the-frameworks-and-it-is-not-verified-per-call)
says so in full.

🔴 **A middleware exception reaches the host.** The framework does not
isolate middleware failures, so this integration catches everything
itself. Four distinct failure modes are tested.

## What it costs

Each submitted observation is a **billed** measurement. There is no
trace-only route.

🟢 Refusals cost nothing — no socket is opened to the measurement API.
There is no sampling in 0.1.0, so decide what you attach this to.

## What it is not

- **Not Copilot Studio or Microsoft Foundry.** Connectors to both exist
  inside the framework; whether a call routed through them surfaces usage
  at this layer is **untested**. [LIMITATIONS §7](LIMITATIONS.md#7--this-does-not-cover-copilot-studio-or-microsoft-foundry).
- **Not a measurement of agent runs or tool calls.** Only chat
  invocations. An agent run making three model calls yields three
  outcomes and no aggregate.
- **Not verified on every connector.** One was probed: OpenAI Chat
  Completions. [LIMITATIONS §2](LIMITATIONS.md#2--only-one-connector-was-measured).
- **Not a truth checker.** NeoMundi verifies integrity and authenticity,
  not whether an answer was true.

## More

[Limitations](LIMITATIONS.md) · [Value and ROI](VALUE.md) ·
[Notebook](notebooks/quickstart.ipynb) · [Examples](examples/) ·
[Changelog](CHANGELOG.md) · [Integration library](../../README.md)
