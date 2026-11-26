# Known limitations

Short, specific, and not marketing. Each item was measured, not assumed.
Read this before drawing a conclusion from any score.

---

## 1. Production runs a measurement version this SDK has not qualified

🔴 Measured on **2026-11-25** against `https://api.neomundi.io`: production
serves `measurement_version = 3.5.0`. The installed `neomundi` 2.1.0 declares
`verified_measurement_versions = ("3.3.0", "3.3.1")`, and the SDK's own
`docs/COMPATIBILITY_350.md` is explicit: *"PARTIALLY QUALIFIED; DO NOT add
3.5.0 to `verified_measurement_versions` yet."*

**Consequence:** the handler emits one `RuntimeWarning` per instance on your
first production measurement. That is correct behaviour, not a defect.

**Why it matters:** two results produced under two different measurement
versions **are not comparable for the same input**. If you are building a
time series, record the `measurement_version` of every observation and do
not mix them.

This is not something this package can fix. It is resolved when the SDK
qualifies 3.5.0.

## 2. The token count comes from your provider, and many providers are silent

LangChain normalises usage into `AIMessage.usage_metadata`. When the provider
publishes it, the count is exact. When it does not — which is common while
streaming, and common with self-hosted models — `usage_metadata` is `None`
and **no observation is submitted**.

The refusal is deliberate: the API counts nothing on this route
(`token_count_source: "caller_declared"`), so an estimate would be a
fabricated measurement attributed to your application.

**What to do:** check `outcome.status`. A chain whose provider never reports
usage will produce `not_submitted_no_token_count` on every call, and measure
nothing. Verify coverage before you rely on it:

```python
measured = sum(1 for o in handler.outcomes if o.submitted)
print(f"{measured}/{len(handler.outcomes)} interactions measured")
```

## 3. Every measurement is billed

There is no trace-only route. Closing a measurement submits
`POST /v1/govern`, which is metered (0.030 CHF on TIER_03). With the default
`sample_rate=1.0`, every LLM call of the host application is metered.

Account rate limit: **60 requests per minute**, burst 20. A chain bursting
above that will see `not_submitted_transport_error` on the excess — your
chain keeps working, but those interactions are **not measured**.

## 4. `model_name` is what you asked for, not what served the call

The API does not publish which model actually served a request. This package
reports the model declared by your LangChain configuration. If a gateway
silently reroutes to another model, the measurement will not say so.

## 5. RAG context is attached by ancestry, and ancestry can be unusual

Retrieved documents are matched to an LLM run by walking up `parent_run_id`
(bounded at 32 levels). This is correct for the normal LCEL shape, where the
retriever and the model are siblings under one chain run and retrieval
completes before the model starts.

It will **not** attach context when:

- retrieval happens in a separate, unrelated run tree;
- documents are fetched outside LangChain entirely (pass them yourself via
  the SDK instead);
- more than one retriever feeds one model — the nearest ancestor's documents
  win, and the others are not merged.

When no context is found, `rag_context` is **absent**, never an empty list
standing in for "nothing retrieved".

## 6. Streaming is measured at the end, not during

The handler closes on `on_llm_end`, so a streamed response is measured once,
as a whole, after the last token. Latency therefore covers the full
generation, and no partial measurement exists mid-stream.

The SDK does expose a live SSE path (`client.stream()`), which is a different
product surface and is **not** wired into this handler.

## 7. `run_id` is not a server-side identity

`client_trace_id` carries your LangChain `run_id` into the observation's
metadata, but the server does not retain it as an identity and
`audit.trace_id` is derived rather than unique. **`request_id` from the
response is the only reliable correlation key.**

## 8. Async callbacks are inherited, not specialised

The handler implements the synchronous callback surface. LangChain runs it in
a thread for async chains, which works, and the handler guards its state with
a lock. A purpose-built `AsyncCallbackHandler` would avoid that hop and does
not exist yet.

## 9. What is not measured at all

- **Tool calls and agent steps.** Only LLM interactions are measured. An
  agent's tool invocations produce no observation.
- **Embeddings and retrievers.** Retrieval is read for context only; it is
  never itself measured.
- **Truth.** NeoMundi verifies the integrity and authenticity of a record.
  It does not verify that the answer was correct.

## 10. Alpha maturity

54 offline tests and one real-execution proof of 6 observations. That is
enough to trust the refusals and the safety guarantee; it is not enough to
call the package battle-tested. The LangChain callback surface it depends on
is stable; this package is new.
