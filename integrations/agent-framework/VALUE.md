# Value and ROI — Agent Framework

## What Agent Framework already keeps

It is a complete agent runtime, and it is not short of observability. It
emits OpenTelemetry natively — traces, metrics and logs — carries a typed
`usage_details` on every response, records the model served, the finish
reason and the provider's own usage keys alongside the normalised ones.
A team running it already has dashboards, latency and spend.

🟢 **And it has a property worth naming**: when a provider publishes no
token count, Agent Framework leaves `usage_details` at `None` rather than
filling it. Measured, 2026-11-27, both streaming and not. Several hosts
in this library do the opposite.

## What NeoMundi adds

Signed, independently verifiable behavioural measurement of each model
call the framework makes — five signals with explicit provenance and an
explicit measured / not-measured state — attached at the framework's own
middleware layer, without writing a measurement call per call site.

The framework tells you what the model **cost** and how long it **took**.
It does not tell you how the answer **behaved**, and it produces no
record a third party could check without trusting you.

## Why this rather than `otel-neomundi` on the same spans

Agent Framework emits OpenTelemetry, so `otel-neomundi` can consume its
spans. The difference is where the numbers come from:

| | `otel-neomundi` on MAF spans | this integration |
|---|---|---|
| Token count | 🔴 refused — a span cannot prove where `gen_ai.usage.*` came from, so **the caller must declare it by hand on every call** | from the framework's typed contract |
| Latency | the span's duration | **measured around `call_next()`** |
| Attachment | a span processor plus a `with` block per measured call | one middleware on the client |

🔴 **Why the span route refuses.** We measured an instrumentation
replacing a provider's real `11/7` with a tiktoken estimate `7/5`, under
the same scope name and version. That is in `otel-neomundi`
LIMITATIONS §1, and it is why this mission existed at all.

⚠ **And the honest counterweight**: the count here is not verified
against the provider's bytes either, the way `litellm-neomundi` verifies
it. It is the framework's number, from a framework measured not to invent
one. See LIMITATIONS §1.

## Who this is for

A team that has standardised on Microsoft Agent Framework — most likely
coming from Semantic Kernel or AutoGen, both now in maintenance — and
that has to account for model behaviour to someone who does not take
their word for it: an auditor, a customer, a regulator, a risk function.

## What it costs

| | |
|---|---|
| Integration effort | one middleware on the client constructor |
| Dependencies added | `agent-framework-core`, `neomundi` — not the thirty-package meta-install |
| Added latency | the measurement call, synchronous, after the host already has its answer |
| Money | each submitted observation is metered. **Refusals cost nothing.** |

## What it does not do

It does not judge whether an answer was true, rank models, enforce a
threshold, or cover Copilot Studio or Microsoft Foundry. It measures
model calls, and it refuses — by name, in the open — everything it cannot
measure honestly.
