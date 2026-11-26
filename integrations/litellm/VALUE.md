# Business value and ROI

## 1. Who this is for

A team that routes its LLM calls through LiteLLM — as a library in its
application, or as the LiteLLM Proxy in front of several applications —
and wants each completion measured by NeoMundi without touching each call
site, or each application.

## 2. What the technology keeps, and what NeoMundi adds

**LiteLLM keeps** routing, retries, fallbacks, budgets, virtual keys, its
cost table and whatever logging callbacks you already run.

**NeoMundi adds** signed, independently verifiable behavioural measurement
of each completion that passes through the gateway — five signals with
explicit provenance and an explicit measured / not-measured state.

**These are complements, not competitors.** This does not replace
LiteLLM's logging integrations (Langfuse, OpenTelemetry, ...); it adds a
measured, signed record next to them.

## 3. Where the value really is — and where it is not

### The gateway sees every provider, once

A framework integration (LangChain) sees only calls made through that
framework. A LiteLLM logger sees every call LiteLLM routes, to every
provider it supports, from every application behind the proxy. **One
attachment point instead of one per framework and per service.**

### Latency is observed, not declared

LiteLLM hands the logger the start and end of the call. The latency sent
is the one LiteLLM observed around the provider call (with its caveats,
LIMITATIONS §3), not a number anyone typed.

### The silent-provider gap is *not* closed — and that is the finding

The hope behind this mission was that LiteLLM's usage normalisation would
turn LangChain's biggest refusal — a provider that publishes no token
count (`langchain-neomundi` LIMITATIONS §2) — into a non-issue.

Measured on litellm 1.111.0, it does not. **LiteLLM normalises the shape
of the usage, not its existence.** When the provider publishes nothing,
LiteLLM reports `0` or its own tokenizer's estimate, indistinguishable from
a real count. A naive gateway integration would therefore *appear* to
close the gap while submitting invented measurements — the exact failure
the SDK's first refusal exists to prevent.

What this integration does instead is verify each count against the
provider's own payload. Where the provider publishes, coverage is the same
as LangChain's, across more providers, from one place. Where it does not,
the refusal stands, and is now **explicit** rather than masked. The value
claim is therefore: wider coverage and one attachment point, with honesty
preserved — not "no more refusals".

### A declared cost is not a measurement

LiteLLM also reports a cost per call. It comes from a price table — `0.0`
for a local model, list price otherwise. It is useful context, shown on
each outcome, and sent to NeoMundi only on request (`send_declared_cost`).
It is never presented as measured.

## 4. Cost, honestly

| Item | Amount |
|---|---|
| Measurement, TIER_03 | 0.030 CHF per submitted observation |
| This logger | free, Apache-2.0 |
| Dry run (`submit=False`) | free, no key, no socket |

**At a gateway, sampling is the whole cost story.** A proxy carrying 100 000
completions a day at `sample_rate=1.0` submits up to 3 000 CHF a day of
measurement; at `0.01`, about 30 CHF. Refused completions cost nothing:
they never leave the process.

## 5. ROI arithmetic

The decision is not "measure everything"; it is "measure enough of every
route to see a behavioural regression latency and cost do not show". For a
gateway with 20 routes, 50 sampled completions per route per day is 1 000
observations, 30 CHF a day, one attachment point. Without the gateway, the
same coverage needs one integration per framework and per service — each
with its own maintenance.

## 6. When not to use it

- Your traffic is mostly **async streaming** through the Proxy: those calls
  are refused (LIMITATIONS §2). Measure at the application instead, or
  stream synchronously.
- Your providers publish no usage at all: everything is refused, by design.
  Check `logger.outcomes` coverage during the free dry run first.
