# Business value and ROI

## 1. Who this is for

A team whose application **already traces its model calls with
OpenTelemetry** — through the contrib GenAI instrumentations, OpenLLMetry,
or its own — exports them to a backend it trusts, and wants those calls
measured by NeoMundi without a second timer, a second pipeline, or a
second place to look.

## 2. What the technology keeps, and what NeoMundi adds

**OpenTelemetry keeps** the instrumentation, the pipeline, the exporters,
the sampling and the tracing backend (Jaeger, Tempo, Honeycomb,
Datadog...). The processor is added next to the exporters and changes
nothing they receive — tested.

**NeoMundi adds** a signed, independently verifiable behavioural
measurement of a model call that is already traced, using the latency the
trace already observed — and the `request_id` of that measurement, set on a
span in **the same trace**.

**These are complements, not competitors.** A trace says what happened and
how long it took. A NeoMundi observation says how the model behaved, signed
and verifiable by a third party. This package joins the two.

## 3. Where the value really is — and where it is not

### One trace, both records, joined both ways

From a slow or suspicious trace in your backend, `neomundi.request_id` on
the `neomundi.interaction` span leads to the signed observation. From an
observation's `request_id`, a search on `neomundi.request_id` in your
backend leads back to the trace (the outcome also carries `trace_id` and
`span_id`). The server does not persist the `client_trace_id` sent in
metadata, so the trace is where the join lives. No correlation table to
maintain.

### Latency is the trace's, not a second stopwatch

The latency submitted is the GenAI span's own duration, as the
instrumentation observed it around the provider call. The measurement
round trip is never in it (tested with a deliberately slow API).

### The span's token count is *not* reused — and that is the finding

The hope behind this mission was that OTel spans, which already carry
`gen_ai.usage.*`, could be turned into measurements with no change to the
application. Measured on 2026-11-26, **they cannot be, honestly**: the
count is written by an instrumentation, an instrumentation option can
replace the provider's published count with an estimate while the span
looks identical, and the span's scope can name a shared public helper
rather than the instrumentation (LIMITATIONS §1).

So the application declares the count its provider returned — one line, in
a `with` block it adds at each measured call. The value claim is
therefore: **no second instrumentation, one shared trace, and the
measurement's honesty preserved** — not "measure every span for free".

## 4. The situations where this pays for itself

### A. A silent behavioural regression after a model or prompt change

Latency and error rate stay flat; the trace looks healthy. The behavioural
measurement moves. With the `request_id` in the trace, the regression is
found where the team already looks, at the exact call.

### B. An incident or an audit

"Show us what the model did on this request." The trace has the timing and
the path; the NeoMundi observation is signed and independently verifiable.
Both are reachable from either one.

### C. Regulatory preparation 🟡

A verifiable record of behaviour, linked to operational traces, is the
kind of evidence an AI-system logging obligation asks for. Whether it is
sufficient for a given regime is a legal question this package does not
answer.

## 5. Cost, honestly

| | |
|---|---|
| Per submitted interaction | 0.030 CHF (TIER_03) |
| Per span with nothing declared | **0** — never submitted |
| 10 000 traced calls / day, `sample_rate=0.05` | 500 observations ≈ **15 CHF / day** |
| 10 000 traced calls / day, `sample_rate=1.0` | 10 000 observations ≈ **300 CHF / day** |
| Wiring it up with `submit=False` | **0** — no key, no socket |

Sampling is deterministic in the trace id, so a sampled trace is
reproducible. Start with `submit=False` to see which of your calls would be
measured, then a low `sample_rate`.

## 6. What would make this more valuable

- **A provenance marker in the GenAI conventions** — e.g. an attribute
  saying a usage count was copied from the provider's response, not
  computed. With it, a span could become a measurement source and the
  `with` block could disappear for compliant instrumentations. That is an
  upstream conversation, and an external community one: it needs explicit
  authorisation before anyone opens it.
- **An E2 qualification** on the service account (`qualify.py` is ready).
- A probe of more instrumentations (Anthropic, Google GenAI, LangChain
  contrib) for the facts in LIMITATIONS §4–5.
