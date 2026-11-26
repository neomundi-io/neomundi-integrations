# Business value and ROI

## 1. Who this is for

A team that already runs Langfuse, debugs from its traces, and has started
— or is about to start — measuring its LLM interactions with NeoMundi. Today
those two records live in two places; the person looking at a bad trace
does not see the measurement, and the person reading the measurement cannot
find the trace.

## 2. What the technology keeps, and what NeoMundi adds

**Langfuse keeps** the trace: the prompt, the completion, latency, cost,
sessions, datasets, experiments, and the UI a developer already has open.

**NeoMundi adds** signed, independently verifiable behavioural measurement
of the same interaction — five signals with explicit provenance and an
explicit measured / not-measured state — **as scores on that trace**, with
the `request_id` that leads to the signed record.

The distinction: a Langfuse trace is an assertion by the party being
observed. A NeoMundi record is signed and verifiable by a third party.
Putting the second inside the first costs the developer nothing and changes
what their evidence is worth.

**These are complements, not competitors.** This does not replace Langfuse
tracing or Langfuse's own evaluators; it adds a measured, signed source of
scores next to them. With `langchain-neomundi`, the handler measures and
the bridge writes back.

## 3. The situations where this pays for itself

### A. A behavioural regression that latency and cost do not show

A prompt or model change keeps latency and cost flat while coherence
drops. With the scores on the trace, the drop is a Langfuse chart filtered
by `neomundi.coherence_score`, next to the deployment that caused it.

### B. An audit or an incident review

The reviewer starts from a Langfuse trace, which is the operator's own
record. The `request_id` on the score leads to a NeoMundi record they can
verify without trusting the operator.

### C. Regulatory preparation 🟡

Runtime records of behaviour are an input to an EU AI Act technical file
and cannot be produced after the fact. This bridge does not produce them —
NeoMundi does — it makes them findable from the tool engineers use.

## 4. Cost, honestly

| Item | Amount |
|---|---|
| Measurement, TIER_03 | 0.030 CHF per submitted observation |
| This bridge | free, Apache-2.0, bills nothing itself |
| Langfuse scores | whatever your Langfuse plan charges for ingestion; six scores per measurement |

**Sampling is the whole cost story, and it happens upstream.** An
application making 10 000 LLM calls a day and measuring 1 in 20 submits
500 observations, 15 CHF a day, about 450 CHF a month. The bridge adds six
Langfuse scores to each of those 500 traces, and nothing to the other 9 500.

Worth it for a team that already decided to measure and wants the result
where it works. Not worth it on its own: without a NeoMundi measurement
there is nothing to write.

## 5. What would make this more valuable

- **`langchain-neomundi` exposing the result** on its outcome, so the
  composed path writes signals instead of only the summary (LIMITATIONS §3).
- **A real execution proof (E2)**: one guarded measurement written to a
  Langfuse instance and read back. Not done; no target was available.
- **Delivery confirmation**: reading the scores back after `flush()` would
  turn `queued` into `persisted`. Langfuse's client does not offer it.
