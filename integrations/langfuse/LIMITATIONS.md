# Known limitations

Each item was measured on 2026-11-26 against `langfuse` 5.2.0 and
`neomundi` 2.1.0, offline, rather than assumed. Read this before relying on
a NeoMundi score in Langfuse.

---

## 1. `queued` is not a delivery receipt

`bridge.write()` returns `queued` once the scores are handed to the
Langfuse client. Langfuse sends them in the background and, **when its API
fails, logs an error and drops them** rather than raising. Measured with a
mock transport: on 401, 429, 500, a connection error and a timeout, the
bridge reports `queued` and no score reaches a server.

That is the right behaviour for "never break the host", and it means the
bridge cannot tell you whether the scores landed. Only reading the trace
back from Langfuse can. This mirrors NeoMundi's own distinction between an
acknowledged and a persisted observation.

## 2. Langfuse's `flush()` blocks while it retries

`write()` itself takes well under a millisecond (measured: 0.1–0.2 ms). But
when the Langfuse API is failing, **`langfuse.flush()` blocked for 5.6 s on
HTTP 500 and 8.8 s on a connection error** while Langfuse retried each
score. That is Langfuse's behaviour, not the bridge's, and it is reached
whenever your application calls `flush()` or `shutdown()` — for example at
the end of a serverless handler.

Score ids are deterministic in (trace, `request_id`, score name), so
writing the same measurement twice sends the same ids. **Whether Langfuse
upserts on those ids was not verified**: no Langfuse server was available.

## 3. Through `langchain-neomundi`, only the summary is written

`langchain_neomundi.Outcome` 0.1.0 carries the `request_id` but **not the
measurement result**. Measured on the real class: composed through
`as_outcome_callback()`, the bridge writes `neomundi.measurement =
submitted_no_result` with the `request_id`, and no signal score.

To get the signals today, measure with the SDK (`client.trace()` or
`client.govern()`) and pass the `TraceRecord` or `GovernResult`. Lifting
this needs a change to `langchain-neomundi`, not here;
`tests/test_composition.py` fails the day the handler exposes the result.

## 4. Values are transported as published, not explained

Each numeric score is the server's value, unrescaled. Two consequences a
Langfuse chart will not show you:

- `semantic_risk` is **binary** (0.0 or 0.85), not a gradient.
- `factual_hallucination_score = 0.0` means the judge found nothing, never
  that there is no hallucination.

Aggregates (`g_score`, `g_final`) are deliberately **not** written: their
effective range is not `[0, 1]` and accounts with different weights are
not comparable. See the `neomundi` SDK's limitations.

## 5. A check that never ran is invisible as a number

Only signals the server lists in `measured_signals` become scores. A check
that was not attempted can carry a conventional value (`coherence_score:
1.0`); the bridge withholds it and lists it in the summary's
`signals_withheld`. An older server response with neither provenance list
yields **no signal score at all**, by design.

## 6. Evidence level: E1, not E2

No real measurement was taken and no score was read back from a Langfuse
server. The window that built this had neither the NeoMundi service
verification key nor a Langfuse instance. The suite asserts the exact body
Langfuse sends, using the real clients with mock transports; that proves
the payload, not its persistence.

Reaching E2 needs: one guarded real measurement on the service account
(about 0.18 CHF for six), a Langfuse target (self-hosted or free tier), and
a read-back of the scores from that Langfuse.

## 7. What is not measured at all

- **Truth.** NeoMundi verifies the integrity and authenticity of a record.
  It does not verify that the answer was correct.
- **Anything Langfuse already measures.** No latency, cost or token score
  is written; Langfuse owns those.
