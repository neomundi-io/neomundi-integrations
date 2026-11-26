<div align="center">

[![English](https://img.shields.io/badge/EN-English-2563eb?style=flat-square)](README.md) · [![Français](https://img.shields.io/badge/FR-Fran%C3%A7ais-6b7280?style=flat-square)](README_FR.md)

# MLflow × NeoMundi

**Measure the model calls MLflow already traces, and find each measurement on the MLflow trace. One function next to your own span processors, the latency MLflow recorded, and only token counts the provider published.**

**[Quickstart](#quickstart)** · **[What it refuses to do](#refusals)** · **[Notebook](notebooks/quickstart.ipynb)** · **[Limitations](LIMITATIONS.md)** · **[Cost](#what-it-costs)**

</div>

---

> **Evidence level: E2.** Four real model calls traced by the real
> `mlflow.openai.autolog()` against a local Ollama's OpenAI-compatible
> `/v1` were measured on the service verification account on 2026-11-26
> and re-read from the API as persisted: 4/4, served
> `measurement_version` 3.5.0, 0.12 CHF. Two more phases were refused on
> real spans and billed nothing: a stream without usage, and a span
> carrying a count of unknown origin. Proof: [`evidence/e2_qualification_20261126T165539Z.json`](evidence/e2_qualification_20261126T165539Z.json). See
> [LIMITATIONS.md §8](LIMITATIONS.md#8-evidence-level-e2-one-writer-one-provider).

## What this is

`NeoMundiMlflowProcessor` registers **one function** with MLflow's public
hook, `mlflow.tracing.configure(span_processors=[...])`. It is **appended**
to the processors you already have, and MLflow calls it for every span it
ends. For each model call written by MLflow's `openai` autolog it takes
three things:

- the token count, as the provider's own `total_tokens`, copied by MLflow
  into `mlflow.chat.tokenUsage`;
- the latency, as the span's `end_time_ns - start_time_ns` that MLflow
  recorded;
- the served model.

It then submits to NeoMundi's `POST /v1/govern` through the `neomundi` SDK,
on a worker thread, so your call never waits for it. Afterwards it writes
the NeoMundi `request_id` and `measurement_version` **back on the MLflow
trace** as tags. You can open the trace in the MLflow UI and find the
observation that measured it.

Every other span gets an outcome too, with a named status. Agent, chain,
workflow and task spans are aggregates. Tools, retrievers and embeddings
are not model calls. A count written by any other instrumentation is
unverified.

## What it is not

- **Not a measurement of every MLflow autolog.** MLflow stores token counts from many writers under one attribute, and some of them are not the provider's. We measured three cases where the provider published **nothing**: the Agents SDK tracer writes `0/0/0`, LiteLLM's MLflow logger writes its own estimate (`14/7/21`), and LangChain copies whatever the chat model computed. Only the `openai` autolog was shown to copy the provider's usage verbatim and to leave it absent when there is none ([LIMITATIONS §1](LIMITATIONS.md#1-only-the-openai-autolog-is-measured--by-design)).
- **Not an OpenTelemetry processor.** A processor added to MLflow's tracer provider is silently dropped on the next `mlflow.set_experiment()`. This uses MLflow's own public hook instead ([§2](LIMITATIONS.md#2-staying-attached-what-removes-the-hook)).
- **Not a reader of the trace-level aggregate.** MLflow's trace total sums calls that are measured one by one.
- **Not a tracer, evaluator or truth checker.** Every submission is a billed measurement. NeoMundi verifies integrity and authenticity, not whether an answer was true.

## Install

```bash
pip install mlflow-neomundi
```

Python ≥ 3.11. Depends on `mlflow-tracing>=3.18.0,<3.19`, the light
package a full `mlflow` already includes, and on `neomundi>=2.1.0,<3.0`.
MLflow needs a store to trace at all: with `mlflow-tracing` alone and no
tracking URI, it records no span ([§5](LIMITATIONS.md#5-mlflow-must-be-able-to-trace)).

## Quickstart

```python
import os

import mlflow
from mlflow_neomundi import NeoMundiMlflowProcessor

mlflow.set_tracking_uri("http://localhost:5000")  # first: a new URI resets MLflow's hooks
mlflow.openai.autolog()

processor = NeoMundiMlflowProcessor(
    api_key=os.environ["NEOMUNDI_API_KEY"],
    base_url="https://api.neomundi.io",
).install()

reply = client.chat.completions.create(model="gpt-4o-mini", messages=[...])
processor.flush()  # submissions run off your call's path

for o in processor.outcomes:
    print(o.span_type, o.status, o.token_count, o.latency_ms, o.request_id)
print(processor.installed)  # is MLflow still calling it?
```

Free first: `NeoMundiMlflowProcessor(submit=False).install()` needs no
key, opens no socket, and runs every check. It shows, on your own traffic,
which calls would be measured and which would be refused, and why.

## Refusals

Each status is a constant exported by the package. Every refusal submits
nothing.

| Status | When |
|---|---|
| `not_submitted_unverified_token_count` | the span was written by something other than MLflow's `openai` autolog: the Agents SDK tracer, LangChain, LiteLLM's logger, a hand-written span |
| `not_submitted_no_token_count` | the provider published no usage, so the attribute is absent and stays absent |
| `not_submitted_invalid_token_count` | usage without a positive integer `total_tokens` (partial, null, zero); never rebuilt |
| `not_submitted_aggregate_span` | `AGENT`, `CHAIN`, `WORKFLOW`, `TASK`: they contain calls already measured |
| `not_submitted_not_a_model_call` | `TOOL`, `RETRIEVER`, `RERANKER`, `EMBEDDING`, `GUARDRAIL`, `PARSER`, `MEMORY`, `EVALUATOR` |
| `not_submitted_unsupported_span` | `UNKNOWN`, or a span type this release does not know |
| `not_submitted_application_error` | the model call failed (span status `ERROR`) |
| `not_submitted_no_latency` | no usable start and end time |
| `skipped_sampling` | excluded by `sample_rate` |
| `not_submitted_local_only` | `submit=False` |
| `not_submitted_transport_error` | the NeoMundi API was unreachable or refused; your call is untouched |
| `not_submitted_queue_full`, `not_submitted_processor_closed`, `not_submitted_internal_error` | the processor protected your application |

Outcomes never carry the prompt, the response or a key. They carry only
counts, names and identifiers. [`examples/02_refusals.py`](examples/02_refusals.py)
shows each refusal on a real MLflow span.

## What it costs

🔴 **Every submitted observation is a billed measurement** (0.030 CHF on
TIER_03). One observation per measured model call. Aggregate spans are
never billed, so an agent run under `mlflow.openai.autolog()` is not
counted twice. `sample_rate` (default `1.0`) is deterministic in the span
id. **Kill switch:** `processor.sample_rate = 0.0`, immediate. Installing a
second processor retires the first, so re-running a notebook cell does not
bill twice.

## Honest status

| | |
|---|---|
| Offline test suite | 75 tests, green on Python 3.12 / Linux locally; CI runs 3.11–3.13 on Linux, macOS, Windows |
| Real execution | **E2**: 4 observations submitted to `https://api.neomundi.io`, 4/4 re-read as persisted (2026-11-26, Python 3.12, `neomundi` 2.1.0). See [`evidence/`](evidence/) |
| Maturity | beta |

Read [LIMITATIONS.md](LIMITATIONS.md) before drawing a conclusion from any score.

## License

Apache-2.0. See [LICENSE](../../LICENSE).
