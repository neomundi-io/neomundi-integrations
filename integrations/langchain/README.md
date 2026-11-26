<div align="center">

[![English](https://img.shields.io/badge/EN-English-2563eb?style=flat-square)](README.md) · [![Français](https://img.shields.io/badge/FR-Fran%C3%A7ais-2563eb?style=flat-square)](README_FR.md)

# LangChain × NeoMundi

**Measure what your LangChain application actually does at runtime.**

One callback handler. Five measured signals per interaction. Verifiable evidence.

**[Quickstart](#quickstart)** · **[What it refuses to do](#four-refusals)** · **[Notebook](notebooks/quickstart.ipynb)** · **[Limitations](KNOWN_LIMITATIONS.md)** · **[Cost](#what-it-costs)**

[NeoMundi SDK](https://github.com/neomundi-io/neomundi-python-sdk) · [Product architecture](https://github.com/neomundi-io/.github/blob/main/docs/PRODUCT_ARCHITECTURE.md) · [Create an account](https://controltower.neomundi.io/welcome)

</div>

---

## What this is

`langchain-neomundi` attaches the [NeoMundi measurement API](https://github.com/neomundi-io/neomundi-python-sdk) to a LangChain application through a standard callback handler. Every LLM interaction your chain performs becomes a measured observation: five signals, each with explicit provenance and an explicit `measured` / `not_measured` state.

It is a thin driver for `neomundi`'s own `client.trace()`. The SDK measures latency, enforces the request contract and submits the observation. **Nothing is reimplemented here**, which is the reason this package is small enough to audit in one sitting.

```
your chain  ──▶  LangChain callback  ──▶  client.trace()  ──▶  POST /v1/govern
                 (this package)           (neomundi SDK)        (measurement API)
```

## What it is not

- **Not an evaluator.** It transports measurements; it does not interpret them. No threshold, no verdict, no `is_safe()`. That boundary is inherited from the SDK on purpose.
- **Not a tracer.** There is no trace-only route on the NeoMundi API. Closing a measurement *is* a billed observation. See [what it costs](#what-it-costs).
- **Not a truth checker.** NeoMundi verifies the integrity and authenticity of a record. It does **not** verify that your model's answer was true.

## Install

```bash
pip install langchain-neomundi
```

Python 3.11 or newer — the floor comes from LangChain 1.x, not from NeoMundi. Two dependencies: `langchain-core` and `neomundi`.

## Quickstart

```python
from langchain_neomundi import NeoMundiCallbackHandler

handler = NeoMundiCallbackHandler(
    api_key="ct_live_...",  # your NeoMundi key
    base_url="https://api.neomundi.io",  # mandatory: no host is ever assumed
)

answer = chain.invoke(
    {"question": "What changed in our refund policy?"},
    config={"callbacks": [handler]},
)

outcome = handler.last_outcome
print(outcome.status)  # 'submitted'
print(outcome.request_id)  # the server's id -- your only correlation key
print(outcome.latency_ms)  # measured, not declared
```

Wire it up for free first. `submit=False` measures latency locally and opens no socket:

```python
handler = NeoMundiCallbackHandler(client_or_credentials, submit=False)
```

### RAG chains

Retrieved documents are picked up automatically from `on_retriever_end` and attached to the measurement as `rag_context`, so grounding signals have something to work with. No extra wiring:

```python
chain = {"context": retriever | format_docs, "question": RunnablePassthrough()} | prompt | model
chain.invoke(question, config={"callbacks": [handler]})
```

## Four refusals

This is the part worth reading. An instrumentation layer that guesses, or that fails quietly, produces fabricated measurements — which is worse than measuring nothing. So:

| Situation | What happens | Status |
|---|---|---|
| The provider reports no token count | **Nothing is submitted.** No estimate is ever made: the API counts nothing on this route, and an invented count would be a fabricated measurement. | `not_submitted_no_token_count` |
| Your own LLM call failed | **Nothing is submitted.** There is no complete interaction to measure. | `not_submitted_application_error` |
| The measurement API is down, 401, 402 or 429 | **Your chain is unaffected** and still returns its answer. The failure is reported, never raised. | `not_submitted_transport_error` |
| `sample_rate` excluded the run | No socket, nothing billed. | `skipped_sampling` |

Every decision produces an `Outcome`. Nothing is silent:

```python
handler = NeoMundiCallbackHandler(..., on_outcome=my_metrics_sink)

for outcome in handler.outcomes:
    print(outcome.status, outcome.submitted, outcome.request_id)
```

**No exception from this handler, the SDK or the network ever reaches your chain.** That is tested against a real LCEL chain under six distinct API failures — see `tests/test_never_raises.py`.

## What it costs

🔴 **Every submitted observation is a billed measurement.** There is no trace-only route on the NeoMundi API: closing a measurement submits `POST /v1/govern`.

With the default `sample_rate=1.0`, **every LLM call your application makes is metered.** On a TIER_03 account that is 0.030 CHF per call. An application serving 10 000 LLM calls a day would meter all 10 000.

Sampling is therefore a first-class control, not an afterthought:

```python
# Measure one call in twenty.
handler = NeoMundiCallbackHandler(..., sample_rate=0.05)
```

The decision is **deterministic in the run id**, so a sampled trace is reproducible rather than a coin flip you cannot replay. `sample_rate=0.0` opens no socket at all, which makes it a usable kill switch.

`handler.submitted_count` tells you how many observations were actually billed.

## Honest status

| | |
|---|---|
| Offline test suite | **54 tests**, green on Python 3.11–3.13. No network, no key, no cost. |
| Real execution | **6 observations** submitted to `https://api.neomundi.io` and 6/6 read back as persisted, from a real LangChain chain driving a real local LLM. See [`evidence/`](evidence/). |
| Measurement version | Production currently serves **3.5.0**, which the SDK deliberately has **not** yet qualified. The handler warns once per instance. See [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md). |
| Maturity | **Alpha.** The callback surface is stable; the package is new. |

Read [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) before drawing a conclusion from any score. It is short, and it is not marketing.

How this was built, what broke along the way and what it cost: [docs/MISSION_REPORT.md](docs/MISSION_REPORT.md). Why it is worth paying for: [docs/VALUE.md](docs/VALUE.md).

## Reference

### `NeoMundiCallbackHandler`

| Parameter | Default | Meaning |
|---|---|---|
| `client` | — | An existing `neomundi.NeoMundiClient`. Mutually exclusive with the two below. |
| `api_key`, `base_url` | — | Build a client here instead. Both required: the SDK never assumes a host. |
| `sample_rate` | `1.0` | Fraction of runs submitted. Deterministic in the run id. |
| `submit` | `True` | `False` measures locally and opens no socket. |
| `mode` | `"OBS"` | Measurement mode passed through to the API. |
| `metadata` | `None` | Merged into the observation's metadata. |
| `capture_prompt` / `capture_response` | `True` | Set `False` to keep content in your process. |
| `on_outcome` | `None` | Called with each `Outcome`. Its own exceptions are caught. |
| `max_outcomes` | `256` | Bound on retained history. |
| `raise_errors` | `False` | `True` re-raises instead of reporting. For debugging only. |

### `Outcome`

`run_id`, `status`, `submitted`, `request_id`, `latency_ms`, `token_count`, `model_name`, `measurement_version`, `reason`, `error_type`.

`request_id` is the **only** reliable correlation key against the API. `run_id` is LangChain's; the server never stores it as an identity. `error_type` carries the exception class name and never its message, which could contain a prompt.

## Running the tests

```bash
pip install -e ".[dev]"
pytest
```

The examples drive a local Ollama model, which is **optional** and not a
dependency of the package — the handler is provider-agnostic. To run them:

```bash
pip install -e ".[dev,examples]"
```

Offline by design: every test goes through `httpx.MockTransport`, and the base URL is `https://example.invalid` (RFC 2606) so a forgotten mock fails DNS rather than reaching a server.

To reproduce the real-execution proof — **this spends money**:

```bash
python scripts/real_execution_proof.py --dry-run     # free
python scripts/real_execution_proof.py --max-billed 10
```

## License

Apache-2.0. See [LICENSE](LICENSE).
