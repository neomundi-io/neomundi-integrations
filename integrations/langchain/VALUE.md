# Business value and ROI

Written for the person who has to justify the line item, and kept honest
enough to be read by the person who would challenge it.

---

## 1. Who this is for

Teams running an LLM feature in production who can answer *"what did it
cost"* and *"was it up"*, but cannot answer **"did its behaviour change"**.

Conventional observability (LangSmith, Langfuse, OpenTelemetry) answers the
first two very well. They record what happened. NeoMundi measures *how the
model behaved* — five signals with explicit provenance and an explicit
`measured` / `not_measured` state — and signs the record so a third party
can verify it without trusting the producer.

**These are complements, not competitors.** A team should keep its tracing
stack and add measurement where it needs evidence rather than telemetry.

## 2. The three situations where this pays for itself

### A. A silent behavioural regression

A prompt edit, a model version bump, or a provider-side change alters
behaviour without changing latency, cost or error rate. Nothing in a
conventional dashboard moves. The regression is found by a user.

With repeated controlled observations, a shift in the measured signals is
visible as a series, not as an anecdote. The value is the time between the
change and its detection.

### B. An audit, a procurement review, or an incident

Someone external asks *"show me how this system behaved on 14 March"*. A log
line is an assertion by the party being audited. A NeoMundi record is signed
and independently verifiable through key-free routes, and the integrity
check is a separate program (`neomundi-evidence-verify`) that does not
belong to NeoMundi's API.

The value here is not a feature, it is **admissibility**. Reconstructing
behavioural evidence after the fact, from logs that were never designed to
carry it, is the expensive alternative.

### C. EU AI Act preparation

🟡 Scoped claim, stated precisely: NeoMundi produces **inputs** to a
conformity dossier — runtime records with declared provenance and declared
limits. It does not produce a conformity assessment, and nobody should sell
it as one. Those inputs are the part that is hard to retrofit, because they
have to exist *at the time of the interaction*.

## 3. What this integration specifically changes

Before: a developer wanting runtime measurement had to instrument each LLM
call by hand — capture the prompt, the response, the provider's token count
and the latency, then submit them. NeoMundi's own public product
architecture listed the gap plainly: *"no framework hook, no
auto-instrumentation."*

After: three lines at the chain's edge, and RAG context is picked up without
any wiring at all.

| | Manual instrumentation | This integration |
|---|---|---|
| Code per call site | ~10 lines, repeated | 0 |
| Latency measurement | hand-rolled, easy to get wrong | measured by the SDK |
| RAG context | manual plumbing | automatic from the retriever |
| Missing token count | silently estimated, or crashes | refused and reported |
| API outage | breaks the chain unless guarded | cannot break the chain |

The last two rows are the ones that matter in production, and they are the
ones a team is least likely to get right by hand under time pressure.

## 4. Cost, honestly

| Item | Amount |
|---|---|
| Measurement, TIER_03 | **0.030 CHF** per submitted observation |
| Rate limit | 60 req/min per account, burst 20 |
| This package | free, Apache-2.0 |
| Integration effort | three lines, plus a decision about `sample_rate` |

**Sampling is the whole cost story.** At `sample_rate=1.0` an application
serving 10 000 LLM calls a day meters 10 000 observations — 300 CHF/day,
which is almost certainly not what anyone wants. The same application at
`sample_rate=0.02` meters 200 — 6 CHF/day — and still yields a usable
series, because detecting a behavioural shift is a statistical question, not
a census.

This is why `sample_rate` is a constructor argument rather than a
configuration file setting, and why `0.0` opens no socket at all.

### A worked figure

A team running 10 000 LLM calls/day, sampling 2%:

- **6 CHF/day**, roughly **180 CHF/month**, for a continuous behavioural
  series plus signed records.
- Compare against the cost of one undetected behavioural regression reaching
  customers, or one audit answered by reconstructing evidence from logs.

Whether that trade is worth it depends entirely on how much the team's
output is trusted by someone else. **For an internal prototype, it is not.
For a regulated or customer-facing system, the comparison is not close.**

## 5. What would make this more valuable

Named plainly, because the gaps are part of the assessment:

1. **Qualify measurement version 3.5.0** in the SDK. Until then every
   production series carries a comparability caveat (limitation 1).
2. **Measure agent steps and tool calls.** Today only LLM interactions are
   measured; agentic failures largely happen in the tool loop.
3. **Providers that report no usage cannot be measured at all** (limitation
   2). The API recently gained an `absent_runtime_metrics` field, which
   suggests a server-side path for declaring absent metrics — if the request
   side follows, this limitation could narrow considerably.
4. **A worked dashboard.** The signals are transported; turning a series into
   an alert is left to the consuming system by design, but a reference
   example would shorten the path from measurement to decision.
