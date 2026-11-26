<div align="center">

[![English](https://img.shields.io/badge/EN-English-2563eb?style=flat-square)](README.md) · [![Français](https://img.shields.io/badge/FR-Fran%C3%A7ais-6b7280?style=flat-square)](README_FR.md)

# OpenTelemetry × NeoMundi

**Measure the model calls your application already traces — one span processor next to your exporters, the latency your trace already observed, and the NeoMundi `request_id` written back into the same trace.**

**[Quickstart](#quickstart)** · **[What it refuses to do](#refusals)** · **[Notebook](notebooks/quickstart.ipynb)** · **[Limitations](LIMITATIONS.md)** · **[Cost](#what-it-costs)**

</div>

---

> **Evidence level: E2 — measured against production.** Four real
> observations on 2026-11-26, each re-read from the API as persisted,
> served `measurement_version 3.5.0`. Two further phases were refused on
> real spans and billed nothing. Earlier text said E1; that was true until
> the qualification ran. The window that built it had no NeoMundi
> service verification key. The suite drives the real `openai` client, the
> real contrib openai-v2 instrumentation and the real OpenTelemetry SDK
> through mock transports. See [LIMITATIONS.md §9](LIMITATIONS.md#9-evidence-level-e1-not-e2).

## What this is

`NeoMundiSpanProcessor` is an OpenTelemetry `SpanProcessor`. You add it to
the tracer provider your application already uses. Around a model call you
want measured, you open an interaction and declare the token count your
provider returned; the processor finds the GenAI span your instrumentation
produced inside it, takes its latency, model and trace identity, and
submits to NeoMundi's `POST /v1/govern` through the `neomundi` SDK. The
`request_id` is then set on a `neomundi.interaction` span in the **same
trace**, which your exporter ships to your backend.

## What it is not

- **Not a way to measure spans on their own.** A span's `gen_ai.usage.*` is written by an instrumentation, and we measured one that replaces the provider's count with an estimate while the span looks the same. A span's count is **never** submitted ([LIMITATIONS §1](LIMITATIONS.md#1-a-spans-token-count-is-never-measured--and-that-is-the-design)).
- **Not zero-touch.** One `with` block per measured call, to declare the count.
- **Not an instrumentation.** It reads the GenAI span of whichever instrumentation you run; it installs none.
- **Not a tracer, evaluator or truth checker.** Every submission is a billed measurement; NeoMundi verifies integrity and authenticity, not whether an answer was true.

## Install

```bash
pip install otel-neomundi
```

Python ≥ 3.11. Depends on `opentelemetry-sdk>=1.45.2,<2.0` and
`neomundi>=2.1.0,<3.0`. If you use the contrib openai-v2 instrumentation,
pin `opentelemetry-util-genai>=1.1b0,<1.2` — the latest release breaks its
import ([LIMITATIONS §6](LIMITATIONS.md#6-the-latest-contrib-instrumentation-does-not-import-as-pip-installs-it)).

## Quickstart

```python
processor = NeoMundiSpanProcessor(
    api_key=..., base_url="https://api.neomundi.io", tracer_provider=tracer_provider
)
tracer_provider.add_span_processor(processor)  # next to your exporters

with processor.interaction() as interaction:
    reply = client.chat.completions.create(model=..., messages=...)
    # The count the PROVIDER returned -- or None if it returned none.
    interaction.declare(token_count=reply.usage.total_tokens if reply.usage else None)

print(interaction.outcome.status, interaction.outcome.request_id)
```

Free first: `NeoMundiSpanProcessor(submit=False, tracer_provider=...)`
needs no key, opens no socket, and tells you which of your calls would be
measured. Runnable, offline: [`examples/01_offline_interaction.py`](examples/01_offline_interaction.py)
and the [notebook](notebooks/quickstart.ipynb).

## Refusals

Every interaction ends with an `Outcome` and an explicit status. Nothing is
submitted, and nothing is billed, when:

| Status | Why |
|---|---|
| `not_submitted_no_token_count` | nothing declared — **even if the span carries `gen_ai.usage.*`** |
| `not_submitted_invalid_token_count` | the declared count is not a positive integer |
| `not_submitted_no_genai_span` | no GenAI span inside the interaction, or the processor is on another provider |
| `not_submitted_ambiguous_spans` | several GenAI spans: which call is meant is not guessed |
| `not_submitted_unsupported_operation` | not chat / text completion / generate content, or no operation name |
| `not_submitted_application_error` | your block raised (re-raised unchanged), or the span status is `ERROR` |
| `not_submitted_no_latency` | the span has no usable start and end |
| `skipped_sampling` / `not_submitted_local_only` | `sample_rate` excluded it / `submit=False` |
| `not_submitted_transport_error` | the API was unreachable — your call was not affected |

No exception from this package reaches your application. Outcomes carry no
prompt, response or key — only lengths and identifiers.
[`examples/02_refusals.py`](examples/02_refusals.py) shows each one on a real span.

## What it costs

🔴 **Every submitted observation is a billed measurement** (0.030 CHF on
TIER_03). A span alone is never billed; only an interaction with a declared
count. `sample_rate` (default `1.0`) is deterministic in the trace id.
**Kill switch:** `processor.sample_rate = 0.0`, immediate.

## Honest status

| | |
|---|---|
| Offline test suite | 48 tests, green on Python 3.12 / Linux locally; CI runs 3.11–3.13 on Linux, macOS, Windows |
| Real execution | **E2** — 4 observations submitted against production, 4 re-read as persisted, `measurement_version 3.5.0`, 0.12 CHF (2026-11-26) |
| Maturity | beta |

Read [LIMITATIONS.md](LIMITATIONS.md) before drawing a conclusion from any score.

## License

Apache-2.0. See [LICENSE](../../LICENSE).
