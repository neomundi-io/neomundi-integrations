<div align="center">

[![English](https://img.shields.io/badge/EN-English-2563eb?style=flat-square)](README.md) · [![Français](https://img.shields.io/badge/FR-Fran%C3%A7ais-6b7280?style=flat-square)](README_FR.md)

# OpenAI Agents SDK × NeoMundi

**Measure every model call your agent loop makes, including the call that decides on a tool and the call that reads its result. One trace processor next to the ones you already run, the latency the SDK recorded, and only token counts the provider published.**

**[Quickstart](#quickstart)** · **[What it refuses to do](#refusals)** · **[Notebook](notebooks/quickstart.ipynb)** · **[Limitations](LIMITATIONS.md)** · **[Cost](#what-it-costs)**

</div>

---

> **Evidence level: E2.** Four model calls of a real `Agent` run, through
> the real `Runner` on `OpenAIResponsesModel` against a local Ollama's
> `/v1/responses`, were measured on the service verification account on
> 2026-11-26 and re-read from the API as persisted: 4/4, served
> `measurement_version` 3.5.0, 0.12 CHF. A tool run produced two model
> calls and both were measured. Proof: [`evidence/e2_qualification_20261126T140137Z.json`](evidence/e2_qualification_20261126T140137Z.json). See
> [LIMITATIONS.md §9](LIMITATIONS.md#9-evidence-level-e2-on-the-responses-route).

## What this is

`NeoMundiTraceProcessor` is an Agents SDK `TracingProcessor`. You install
it once, and it **appends** itself to the processors already registered.
For each model call on the Responses API (the SDK's default model class)
it takes three things. The token count comes from the provider's own
`Response` object. The latency is the span's `ended_at - started_at`, as
the SDK recorded it. The agent and the tools come from the agent loop. It
then submits to NeoMundi's `POST /v1/govern` through the `neomundi` SDK, on
a worker thread, so the agent never waits for it.

A tool-using run makes one model call per step, and each one is measured
separately. Tool executions, agent steps, turns and the run total are
reported by name and **not** measured: they are not model calls, or they
are sums of calls already measured.

## What it is not

- **Not a way to measure Chat Completions or LiteLLM agents.** Their spans carry a usage dict the SDK rebuilt. It is zero-filled when the provider is silent, and on the LiteLLM route we measured it carrying a local estimate (21/7/28) where the provider published nothing. These spans are refused ([LIMITATIONS §1](LIMITATIONS.md#1-chat-completions-and-litellm-spans-are-refused--by-design)).
- **Not a reader of exported traces.** `export()` drops the provider's `Response`. The measurement is only possible in-process, from a processor.
- **Not a measurement of tools.** A tool execution has no model and no token count, so it is named on the outcome, never billed.
- **Not a tracer, evaluator or truth checker.** Every submission is a billed measurement. NeoMundi verifies integrity and authenticity, not whether an answer was true.

## Install

Not on PyPI yet, so `pip install openai-agents-neomundi` does not work.
Install from the repository:

```bash
pip install 'git+https://github.com/neomundi-io/neomundi-integrations.git#subdirectory=integrations/openai-agents'
```

Python ≥ 3.11. Depends on `openai-agents>=0.26.0,<0.27` (pinned to the
minor whose tracing surface was verified) and `neomundi>=2.1.0,<3.0`.

## Quickstart

```python
from openai_agents_neomundi import NeoMundiTraceProcessor

processor = NeoMundiTraceProcessor(
    api_key=..., base_url="https://api.neomundi.io"
).install()  # add_trace_processor -- NEVER set_trace_processors

result = await Runner.run(agent, "What is the weather in Paris?")

processor.flush()  # submissions run off the agent's thread
for outcome in processor.outcomes:
    print(outcome.span_type, outcome.status, outcome.token_count, outcome.request_id)
```

🔴 **Do not use `set_trace_processors([processor])`.** It *replaces* the
list. That silently removes the SDK's own exporter to the OpenAI dashboard,
and any processor your application registered.

Free first: `NeoMundiTraceProcessor(submit=False)` needs no key, opens no
socket, and tells you which of your calls would be measured. Runnable,
offline: [`examples/01_offline_agent.py`](examples/01_offline_agent.py)
and the [notebook](notebooks/quickstart.ipynb).

## Refusals

Every span ends with an `Outcome` and an explicit status. Nothing is
submitted, and nothing is billed, when:

| Status | Why |
|---|---|
| `not_submitted_unverified_token_count` | a Chat Completions / LiteLLM generation span; the provider published no usage or a partial one; or `AnyLLMModel` is loaded, which rebuilds `Response` objects |
| `not_submitted_no_provider_response` | the run does not record sensitive data (`trace_include_sensitive_data=False`), so the span carries no provider object |
| `not_submitted_aggregate_span` | a turn or task span: a sum of calls already measured |
| `not_submitted_not_a_model_call` | a tool execution, an agent step, a handoff or a guardrail, named on the outcome |
| `not_submitted_unsupported_span` | speech, transcription, or an unknown span type |
| `not_submitted_application_error` | the model call failed, or the response did not complete |
| `not_submitted_no_latency` | the span has no usable start and end |
| `skipped_sampling` / `not_submitted_local_only` | `sample_rate` excluded it / `submit=False` |
| `not_submitted_queue_full` / `not_submitted_processor_closed` | dropped rather than blocking the agent / after `shutdown()` |
| `not_submitted_transport_error` | the API was unreachable, and your run was not affected |

No exception from this package reaches your agent. Outcomes carry no
prompt, response, tool input or output, or key. They carry only lengths,
names and identifiers. [`examples/02_refusals.py`](examples/02_refusals.py)
shows each refusal on a real run.

## What it costs

🔴 **Every submitted observation is a billed measurement** (0.030 CHF on
TIER_03). **An agent run is billed once per model call**: a run that calls
one tool makes two calls and costs two observations. `sample_rate`
(default `1.0`) is deterministic in the span id. **Kill switch:**
`processor.sample_rate = 0.0`, immediate. Installing a second processor
retires the first, so re-running a notebook cell does not bill twice.

## Honest status

| | |
|---|---|
| Offline test suite | 45 tests, green on Python 3.12 / Linux locally; CI runs 3.11–3.13 on Linux, macOS, Windows |
| Real execution | **E2**: 4 observations submitted to `https://api.neomundi.io`, 4/4 re-read as persisted (2026-11-26, Python 3.12, `neomundi` 2.1.0). See [`evidence/`](evidence/) |
| Maturity | beta |

Read [LIMITATIONS.md](LIMITATIONS.md) before drawing a conclusion from any score.

## License

Apache-2.0. See [LICENSE](../../LICENSE).
