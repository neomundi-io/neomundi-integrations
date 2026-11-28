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

## 9. Evidence level: E2, on one route

**Measured 2026-11-26.** Four completions through LiteLLM's
OpenAI-compatible route to `llama3.2:3b` on a local Ollama were submitted
from the service verification account and re-read by `request_id`: 4/4
persisted, served `measurement_version` 3.5.0, 0.12 CHF
([`evidence/e2_qualification_20261126T094917Z.json`](evidence/e2_qualification_20261126T094917Z.json)).

What that does **not** cover, and what it means for you:

- **One route.** The same model through LiteLLM's native `ollama/` route
  was refused on a real call (`not_submitted_unverified_token_count`):
  LiteLLM hands the logger no provider response there, so the count cannot
  be proven to be the provider's. If you use that route, expect refusals,
  not measurements.
- **One provider.** Paid providers and Anthropic's usage block were
  exercised only through mock transports.
- **One platform.** The real run was Python 3.12 with `neomundi` 2.1.0.
  The CI matrix covers 3.11–3.13 on Linux, macOS and Windows offline.
- **Not E3.** Nobody outside the team has reproduced it. Real providers other than
the mocked shapes (OpenAI-compatible, Ollama, Anthropic's usage block)
were not exercised.

## 10. Provenance vocabulary: how ours lines up with LiteLLM's `known` / `estimated` / `unknown`

**Read on 2026-11-28 against `litellm` 1.111.0, by introspection.** LiteLLM
defines `UsageObservation` with three values — `known`, `unknown`,
`estimated` (`litellm/litellm_core_utils/litellm_logging.py:705`). The same
three-way distinction is being discussed for OpenTelemetry GenAI at
`open-telemetry/semantic-conventions#4075`.

🔴 **This integration does not map onto those three words at runtime, and
cannot.** A `CustomLogger` never sees LiteLLM's marker: on `completion`,
`completion(stream=True)` and `acompletion(stream=True)`, the
`_accounting_usage_observed` key was **absent** from the `kwargs` handed to
the success hook (offline, `mock_response`; the non-streamed `acompletion`
event was not captured by that probe and is not claimed). The marker is
internal to LiteLLM's cost accounting.

🔴 **And the words do not mean the same thing.** LiteLLM sets `known` when a
usage object **exists** (`litellm_logging.py:2961-2967`). This integration
submits only when the count is **traced to the provider's own payload**. A
usage object can exist and still be a default (`0 / 0 / 0`) or LiteLLM's
own tokenizer estimate — §1 measures three such cases. So:

| what `Outcome` says | closest LiteLLM word | why it is not an equivalence |
|---|---|---|
| `submitted`, `token_source = "provider_total"` | `known` | 🟢 ours is **stricter**: the total was found in the provider's payload |
| `submitted`, `token_source = "provider_prompt_plus_completion"` | `known` | 🟢 stricter: prompt and completion were both found in the payload, and summed by us |
| `not_submitted_unverified_token_count`, reason *"the provider published no token count…"* | `unknown` | the provider payload reached the logger and held no count |
| `not_submitted_unverified_token_count`, reason *"…cannot be traced to the provider's payload (partial or estimated usage)…"* | 🔴 **none** | LiteLLM may call this `known` (a usage object exists) or `estimated`; **we cannot tell which**, and we do not guess. It is a fourth state: **present but unprovable** |
| `not_submitted_unverified_token_count` with no provider payload at all (§0, native `ollama/`) | 🔴 **none** | nothing reached the logger to classify |

**What to do.** Read `outcome.status` and `outcome.token_source`, not a
three-word label. ⚠ **Never translate LiteLLM's `known` into "measured by
NeoMundi"**: the first means a usage block exists, the second means it was
proven to be the provider's.
