<div align="center">

[![English](https://img.shields.io/badge/EN-English-2563eb?style=flat-square)](README.md) · [![Français](https://img.shields.io/badge/FR-Fran%C3%A7ais-6b7280?style=flat-square)](README_FR.md)

# Langfuse × NeoMundi

**NeoMundi's signed behavioural measurement, as scores on the Langfuse trace you already have open.**

**[Quickstart](#quickstart)** · **[What it refuses to do](#refusals)** · **[Notebook](notebooks/quickstart.ipynb)** · **[Limitations](LIMITATIONS.md)** · **[Cost](#what-it-costs)**

</div>

---

> **Evidence level E2.** Two real measurements on 2026-11-26, re-read from
> the NeoMundi API as persisted, **and all six scores re-read from the
> Langfuse server** on both traces. See
> [LIMITATIONS.md §6](LIMITATIONS.md#6-evidence-level-e2--and-what-it-does-not-cover).

## What this is

A **bridge**, not a second observer. Langfuse keeps the trace; NeoMundi
measures the interaction; `NeoMundiLangfuseBridge` writes the measurement's
signals onto that trace as Langfuse scores, with the NeoMundi `request_id`
in each score's metadata so you can go from the trace to the signed record.

It drives the `neomundi` SDK's result types and Langfuse's own
`create_score`. It reimplements neither, measures nothing, and never
contacts the NeoMundi API.

## What it is not

- **Not an evaluator.** It transports measurements; it does not interpret them.
- **Not a tracer.** Langfuse traces. This only adds scores to its traces.
- **Not a truth checker.** NeoMundi verifies integrity and authenticity, not whether an answer was true.

## Install

Not on PyPI yet — `pip install langfuse-neomundi` does not work. Install
from the repository:

```bash
pip install 'git+https://github.com/neomundi-io/neomundi-integrations.git#subdirectory=integrations/langfuse'
```

Python ≥ 3.11. Depends on `langfuse>=5.2.0,<6.0` and `neomundi>=2.1.0,<3.0`.

## Quickstart

```python
bridge = NeoMundiLangfuseBridge(langfuse)  # your existing Langfuse client
outcome = bridge.write(trace.close())  # a neomundi TraceRecord or GovernResult
print(outcome.status, outcome.signals_written)
```

In context, inside a Langfuse observation:

```python
from langfuse import get_client
from neomundi import NeoMundiClient
from langfuse_neomundi import NeoMundiLangfuseBridge

langfuse = get_client()
neomundi = NeoMundiClient(api_key=..., base_url="https://api.neomundi.io")
bridge = NeoMundiLangfuseBridge(langfuse)

with langfuse.start_as_current_observation(name="answer"):
    with neomundi.trace(model_name="my-model") as trace:
        answer = call_my_llm(question)
        trace.record(prompt=question, response=answer, token_count=usage.total_tokens)
    bridge.write(trace.close())  # attaches to the active trace
```

What lands on the trace (exact names, measured offline):

| Score | Type | Written when |
|---|---|---|
| `neomundi.stability_score` | NUMERIC | the server declared it measured |
| `neomundi.coherence_score` | NUMERIC | idem |
| `neomundi.factual_hallucination_score` | NUMERIC | idem |
| `neomundi.semantic_instability_score` | NUMERIC | idem |
| `neomundi.semantic_risk` | NUMERIC | idem |
| `neomundi.measurement` | CATEGORICAL | always: `complete`, `partial`, `not_attempted`, `unknown`, `not_submitted`, `submitted_no_result` |

Each score's metadata carries `request_id`, `measurement_version` and the
evaluator provenance. The summary lists what was withheld and why.

**With `langchain-neomundi`:** pass `bridge.as_outcome_callback()` as the
handler's `on_outcome`. Only the summary is written that way — see
[LIMITATIONS.md §3](LIMITATIONS.md#3-through-langchain-neomundi-only-the-summary-is-written).

## Refusals

| Situation | Status | What reaches Langfuse |
|---|---|---|
| A signal not declared measured (even with a conventional value) | `queued` | no score for it; listed in the summary |
| No `trace_id` and no active Langfuse trace | `not_written_no_trace` | nothing |
| The measurement was not submitted | `queued` | the summary only, `not_submitted` |
| The input is not a NeoMundi measurement | `not_written_unsupported_input` | nothing |
| `dry_run=True` | `dry_run` | nothing; scores returned on the outcome |
| The bridge or the Langfuse client raises | `not_written_internal_error` | nothing; the exception type, never its message |

🔴 **`queued` means handed to Langfuse, not delivered.** Langfuse drops
scores on an API error without raising. See
[LIMITATIONS.md §1](LIMITATIONS.md#1-queued-is-not-a-delivery-receipt).

## What it costs

The bridge **bills nothing**: it never contacts NeoMundi. The measurement
it carries was billed when it was submitted — 0.030 CHF per observation —
so the cost control is wherever you measure (`sample_rate` in
`langchain-neomundi`, or your own decision to call `client.trace()`).

`dry_run=True` computes every score and hands nothing to Langfuse, so you
can wire the bridge up and read what it would write before enabling it.

## Honest status

| | |
|---|---|
| Offline test suite | 33 tests, plus 2 composition tests run when `langchain-neomundi` is installed |
| Real execution | **E2** — 2 observations, 2 re-read as persisted, 6/6 scores read back from Langfuse, 0.06 CHF (2026-11-26) |
| Maturity | beta |

Read [LIMITATIONS.md](LIMITATIONS.md) before drawing a conclusion from any score.

## Running the tests

```bash
cd integrations/langfuse
python -m pip install -e ".[dev]"
pytest -q
```

Offline, no key, no cost. The four Langfuse-failure tests take a few
seconds each, because Langfuse really does retry.

## License

Apache-2.0. See [LICENSE](../../LICENSE).
