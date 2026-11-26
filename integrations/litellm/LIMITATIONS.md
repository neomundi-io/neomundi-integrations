# Known limitations

Each item was measured on 2026-11-26 against `litellm` 1.111.0 and
`neomundi` 2.1.0, Python 3.12 on Linux, offline, rather than assumed.
Read this before relying on any NeoMundi score produced through LiteLLM.

---


## 0. LiteLLM's native Ollama route cannot be measured at all

🔴 Measured on 2026-11-26, litellm 1.111.0. On the **native** `ollama/`
route LiteLLM hands the logger `original_response = None`. It still reports
a usage block, and those numbers may well be the provider's own — **but
nothing reaching the logger can prove they are not LiteLLM's own tokenizer
estimate**, so the integration refuses with
`not_submitted_unverified_token_count`.

That is the invariant working, not a defect: an unprovable count is not a
measurement.

🟢 **The OpenAI-compatible route is measurable.** Pointing LiteLLM at
Ollama's `/v1` endpoint with the `openai/` prefix makes it pass the
provider's JSON body through, usage block included, and the provenance
check verifies against it. The E2 qualification used exactly that route:
four observations submitted, four re-read as persisted.

**What to do.** If your calls take a route where `original_response` is
absent, this integration measures nothing and says so on every call. Check
`outcome.status` before relying on coverage. Whether a route populates it
is LiteLLM's choice, not ours, and it varies **by route**, not by provider.

## 1. A silent provider is still refused — LiteLLM hides the silence, it does not fill it

LiteLLM normalises usage into one `Usage` object across providers. When the
provider publishes no count, **LiteLLM does not leave it empty**:

| Provider response | What `response_obj.usage` says |
|---|---|
| OpenAI-compatible, no `usage` block | `0 / 0 / 0` |
| Ollama, no `prompt_eval_count` / `eval_count` | `14 / 5 / 19` — its own tokenizer's estimate |
| Sync stream, no usage chunk | `14 / 5 / 19` — its own tokenizer's estimate |

Each of these looks exactly like a reported count. Forwarding it would
submit a fabricated measurement. So the logger reads the **provider's raw
payload** (`kwargs["original_response"]`, or the stream's chunks) and
submits a count only if it can be derived from integers the provider itself
published there: the total literally, or the completion count literally and
the prompt count literally or as a sum of published numbers (Anthropic
reports `input_tokens + cache_read_input_tokens`, which LiteLLM adds up).
Everything else is `not_submitted_unverified_token_count`.

**What this means for you:** the gap LangChain's integration documents
(its LIMITATIONS §2) is not closed by measuring at the gateway. A provider
that publishes nothing is refused here too — the difference is that
through LiteLLM, the refusal is the only thing standing between you and a
silently invented number. Check coverage:

```python
measured = sum(1 for o in logger.outcomes if o.submitted)
print(f"{measured}/{len(logger.outcomes)} completions measured")
```

The provenance check matches integers under usage-like keys (`usage`,
`*token*`, `*count*`, `*billed*`). A provider that publishes its counts
under some other name is refused, not guessed. A coincidental collision
with an unrelated integer under such a key was not observed, but is not
impossible; it could only admit a count LiteLLM also computed.

## 2. Async streams are never measured

For `acompletion(..., stream=True)` — the common path in the LiteLLM
**Proxy** — `original_response` is a coroutine and the provider's chunks
are not reachable from the logger. Even when the provider did publish its
usage, the logger cannot verify it, and refuses. Sync streams and every
non-streamed call (sync or async) are measured.

Measured with an OpenAI-compatible mock that sent a usage chunk:
`async_log_success_event` received the right `Usage(11, 7, 18)` and nothing
to check it against. Lifting this needs LiteLLM to expose the raw usage
chunk to loggers; it is not worked around here.

## 3. Latency is LiteLLM's wall-clock delta, and includes LiteLLM

`latency_ms` is `end_time − start_time` as LiteLLM hands them to the hook —
`datetime` objects from the wall clock, not a monotonic clock. It covers
LiteLLM's own pre- and post-processing around the provider call, not the
provider alone. For streams, `end_time` is when your code finished reading
the stream, so a slow consumer lengthens it. A negative delta (clock
stepped back) is refused as `not_submitted_no_latency`, not clamped.

## 4. Replacing or removing the logger is not what it looks like

Measured on litellm 1.111.0:

- after `litellm.callbacks = []`, a logger that has already fired **keeps
  firing — and keeps billing**: LiteLLM copied it into its internal
  success and failure lists;
- a second `NeoMundiLogger` instance is **silently not registered** while
  the first one is: LiteLLM de-duplicates loggers by class name and public
  attributes.

The kill switch is `logger.sample_rate = 0.0`: immediate, no socket. To
actually detach, call
`litellm.logging_callback_manager.remove_callback_from_all_lists(logger)`.
Both are pinned by a test.

## 5. Every measurement is billed

Every `submitted` outcome is a billed observation (0.030 CHF on TIER_03).
There is no trace-only route. `sample_rate` defaults to `1.0`. A gateway
sees every call of every application behind it: at 100 000 completions a
day, `sample_rate=1.0` is 3 000 CHF a day. Start with `submit=False` (free,
no key needed), then a small rate.

## 6. The declared cost is not sent by default

`kwargs["response_cost"]` is LiteLLM's **price-table** value: `0.0` for
Ollama, `None` for a model the table does not know, list price otherwise.
It is reported on the outcome as `declared_cost` and sent only with
`send_declared_cost=True`, and never when it is zero or absent. It is not
the provider's bill.

## 7. Cache hits, failed calls and non-chat calls are not measured

A LiteLLM cache hit is `not_submitted_cache_hit`: no model was called. A
failed call is `not_submitted_application_error` and your exception reaches
you unchanged. Embeddings, images and other call types are
`not_submitted_unsupported_call`. The cache case is tested at the hook
only: `litellm.Cache` needs the proxy extras (`fastapi`), which a clean
install of this package does not carry.

## 8. `model_name` is what you asked for

It is `kwargs["model"]`, the model the caller requested from LiteLLM, not a
model the provider proved served the call.

## 9. Evidence level: E1, not E2

**No real measurement was taken.** The window that built this had no
`NEOMUNDI_VERIFICATION_KEY`, so nothing was submitted to the production API
and nothing was read back. What exists is an offline suite that drives the
real `litellm.completion` and the real `NeoMundiClient` through mock
transports and asserts the exact body sent to `POST /v1/govern`. That
proves the payload and the refusals, not persistence.

Reaching E2 needs one guarded run on the service verification account
(about 0.15 CHF for five observations), with a free local model through
LiteLLM (Ollama reports its counts), and the observations re-read by
`request_id`.

Only Python 3.12 on Linux ran in the building window; the CI matrix
covers 3.11–3.13 on Linux, macOS and Windows. Real providers other than
the mocked shapes (OpenAI-compatible, Ollama, Anthropic's usage block)
were not exercised.
