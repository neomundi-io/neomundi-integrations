<div align="center">

[![English](https://img.shields.io/badge/EN-English-2563eb?style=flat-square)](README.md) · [![Français](https://img.shields.io/badge/FR-Fran%C3%A7ais-6b7280?style=flat-square)](README_FR.md)

# LiteLLM × NeoMundi

**Measure every completion that goes through LiteLLM — in your application or in the Proxy — with one custom logger, and never a token count the provider did not publish.**

**[Quickstart](#quickstart)** · **[What it refuses to do](#refusals)** · **[Notebook](notebooks/quickstart.ipynb)** · **[Limitations](LIMITATIONS.md)** · **[Cost](#what-it-costs)**

</div>

---

> **Evidence level: E2.** Four real completions, through LiteLLM's
> OpenAI-compatible route to a local Ollama model, were measured on the
> service verification account on 2026-11-26 and re-read from the API as
> persisted: 4/4, served `measurement_version` 3.5.0, 0.12 CHF. Two more
> phases were refused on real calls and billed nothing: LiteLLM's native
> `ollama/` route, whose count cannot be proven to be the provider's, and
> an embedding. Proof: [`evidence/e2_qualification_20261126T094917Z.json`](evidence/e2_qualification_20261126T094917Z.json). See
> [LIMITATIONS.md §9](LIMITATIONS.md#9-evidence-level-e2-on-one-route).

## What this is

`NeoMundiLogger` is a LiteLLM `CustomLogger`. Attached once, it submits each
chat completion LiteLLM reports to NeoMundi's `POST /v1/govern`, with the
latency LiteLLM observed around the call and the token count **the
provider** published. It drives the `neomundi` SDK and reimplements none of
it.

The one thing it adds on top of a plain logger: LiteLLM fills a silent
provider's usage with `0` or with its own tokenizer's estimate, and those
look exactly like a real count. This logger checks each count against the
provider's raw payload and **refuses** what it cannot trace there.

## What it is not

- **Not an evaluator.** It transports measurements; it does not interpret them.
- **Not a tracer.** There is no trace-only route: every submission is a billed observation.
- **Not a truth checker.** NeoMundi verifies integrity and authenticity, not whether an answer was true.
- **Not a cost meter.** LiteLLM's `response_cost` is a price-table value; it is reported, and not sent unless you ask.

## Install

```bash
pip install litellm-neomundi
```

Python ≥ 3.11. Depends on `litellm>=1.111.0,<2.0` and `neomundi>=2.1.0,<3.0`.

## Quickstart

```python
logger = NeoMundiLogger(api_key=..., base_url="https://api.neomundi.io")
litellm.callbacks = [logger]
answer = litellm.completion(model="ollama_chat/llama3.2", messages=messages)
```

Free first: `NeoMundiLogger(submit=False)` needs no key, opens no socket,
and runs every check — so you can see how many of your completions would be
measured before spending anything.

In the **LiteLLM Proxy**, put an instance in a module and reference it under
`litellm_settings.callbacks` in `config.yaml`. Read
[LIMITATIONS §2](LIMITATIONS.md#2-async-streams-are-never-measured) first:
async streams are refused.

## Refusals

| Situation | Status | Sent to NeoMundi |
|---|---|---|
| The provider published no token count (LiteLLM says `0` or estimates) | `not_submitted_unverified_token_count` | nothing |
| Async stream: the provider's payload is out of the logger's reach | `not_submitted_unverified_token_count` | nothing |
| LiteLLM gave no usable start/end time | `not_submitted_no_latency` | nothing |
| LiteLLM answered from its cache | `not_submitted_cache_hit` | nothing |
| Your LLM call failed | `not_submitted_application_error` | nothing; your exception is untouched |
| Embedding, image or other non-chat call | `not_submitted_unsupported_call` | nothing |
| `sample_rate` excluded the call | `skipped_sampling` | nothing |
| `submit=False` | `not_submitted_local_only` | nothing |
| The NeoMundi API failed or was unreachable | `not_submitted_transport_error` | attempted; your call is untouched |

No exception from this logger ever reaches your application — tested under
eight distinct API failures and malformed inputs.

## What it costs

🔴 **Every submitted observation is a billed measurement** (0.030 CHF on
TIER_03). `sample_rate` defaults to `1.0`, and a gateway sees every call of
every application behind it. Lower it before going live; the decision is
deterministic in `litellm_call_id`.

**Kill switch:** `logger.sample_rate = 0.0` — immediate. Do not rely on
`litellm.callbacks = []`: LiteLLM keeps a logger registered after that
([LIMITATIONS §4](LIMITATIONS.md#4-replacing-or-removing-the-logger-is-not-what-it-looks-like)).

## Honest status

| | |
|---|---|
| Offline test suite | 47 tests, green on Python 3.12 / Linux locally; CI runs 3.11–3.13 on Linux, macOS, Windows |
| Real execution | **E2**: 4 observations submitted to `https://api.neomundi.io`, 4/4 re-read as persisted (2026-11-26, Python 3.12, `neomundi` 2.1.0). See [`evidence/`](evidence/) |
| Maturity | beta |

Read [LIMITATIONS.md](LIMITATIONS.md) before drawing a conclusion from any score.

## License

Apache-2.0. See [LICENSE](../../LICENSE).
