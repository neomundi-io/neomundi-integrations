# Known limitations

Each item was measured on 2026-11-26, not assumed. Unless stated otherwise:
`opentelemetry-sdk` 1.45.2, `opentelemetry-semantic-conventions` 0.66b2,
`opentelemetry-instrumentation-openai-v2` 2.4b0 with
`opentelemetry-util-genai` 1.1b0, `openai` 3.60.0, `neomundi` 2.1.0, Python
3.12 on Linux, against a scripted OpenAI-compatible endpoint on
`127.0.0.1`. The probes that established each point are in
`neomundi-devrel/missions/004-otel/probes/`.

Read this before relying on any NeoMundi score produced through this
package.

---

## 1. A span's token count is never measured — and that is the design

🔴 **This integration does not turn existing spans into measurements on its
own.** It cannot, honestly. `gen_ai.usage.input_tokens` /
`output_tokens` are written by an **instrumentation**, not by the provider,
and nothing on a span says where the number came from:

| What we measured | Result |
|---|---|
| The specification, `semantic-conventions-genai` @ `81b5d43` | *"Instrumentations SHOULD make a best effort to populate this value, using a total provided by the provider when available or … by summing"*. A SHOULD; no provenance attribute exists. |
| OpenLLMetry 0.30.0, `enrich_token_usage=True`, stream **with** the provider's usage `11 / 7` | span says **`7 / 5`** — a tiktoken estimate **replaced** the provider's count |
| Same, stream **without** provider usage | span says `7 / 5` — an estimate where the provider published nothing |
| Same instrumentation, option off | same scope name and version, same keys: the option is invisible on the span |
| Contrib openai-v2 2.4b0 | spans are emitted under `opentelemetry.util.genai.handler 1.1b0`, a **public helper**; ten lines of other code on that helper, setting `len(text)//4`, produce a span with the **same scope name and version** and the same keys |
| Provider's raw payload | not on the span, so the check `litellm-neomundi` relies on (count verified against the provider's JSON) has nothing to verify against |

Good news, also measured: the two current instrumentations we ran —
contrib openai-v2 2.4b0 and OpenLLMetry 0.63.1 — **copy** the provider's
count and leave the attribute **absent** when the provider is silent. But
"the instrumentation I happen to run today copies faithfully" is not a
property a span can prove, so it is not a basis for a measurement.

**What to do.** Declare the count your provider returned, from its
response object: `interaction.declare(token_count=reply.usage.total_tokens
if reply.usage else None)`. The server republishes it as
`token_count_source: "caller_declared"`. A span alone, however complete,
produces `not_submitted_no_token_count` and costs nothing.

## 2. One `with` block per measured call — this is not zero-touch

The brief was to measure an instrumented application "without a second
instrumentation". It achieves that for the **timer** (latency is the
span's) and for the **trace** (no second exporter, no second pipeline). It
does **not** achieve it for the call site: you delimit each measured call
and declare its count. That follows from §1, not from a shortcut.

Inside one interaction there must be **exactly one** GenAI span. Zero
(`not_submitted_no_genai_span`) or several
(`not_submitted_ambiguous_spans`) are refused: which call a declared count
belongs to is never guessed. Spans in between are fine — an application
span wrapping the model call is followed down to it.

## 3. Latency is the instrumentation's definition, and streams include your loop

`latency_ms` is `end_time - start_time` of the GenAI span, nanoseconds from
the SDK's wall clock (`time_ns`), as the instrumentation set them.

- **Non-streamed call:** around the client call, including the client's
  own parsing. Measured: 2–11 ms against a local endpoint.
- **Streamed call:** the span ends when the stream is **exhausted or
  closed**. If your code does work between chunks, that time is in the
  latency. It is the instrumentation's choice, not ours, and we report it
  unchanged.
- A negative delta (wall-clock step) is refused, never clamped.

## 4. Content: never read by us, but your instrumentation may capture it

This processor reads only start/end time, status, operation, model, and
provider name. **It never reads `gen_ai.input.messages`,
`gen_ai.output.messages`, `gen_ai.system_instructions` or any
`gen_ai.prompt*` / `gen_ai.completion*` attribute** — tested with content
capture switched on.

What your **instrumentation** does is another matter, and it differs:

| Instrumentation | Prompt on the span by default? |
|---|---|
| contrib openai-v2 2.4b0 | **no** — opt-in via `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=SPAN_ONLY` (or `EVENT_ONLY`, `SPAN_AND_EVENT`); `true` is rejected with a warning and means *no content* |
| OpenLLMetry 0.63.1 | **yes** — the prompt is in `gen_ai.input.messages` with no configuration |

The specification says content *"SHOULD NOT"* be captured by default. One
widely used instrumentation does it anyway: check yours before you export
spans to a shared backend. The `neomundi.interaction` span this package
adds carries only `neomundi.status`, `neomundi.request_id` and
`neomundi.measurement_version`.

## 5. Attribute names are not stable

Every GenAI attribute is status *Development*. In
`opentelemetry-semantic-conventions` 0.66b2 there is no stable `gen_ai`
module, and **every** GenAI constant in `_incubating` is marked *Deprecated:
moved to the semantic-conventions-genai repository*. Within the versions in
use today, `gen_ai.system` became `gen_ai.provider.name` and
`gen_ai.usage.prompt_tokens` became `input_tokens`.

Consequences here: a span is recognised as GenAI by `gen_ai.operation.name`,
`gen_ai.provider.name` **or** `gen_ai.system`, and measured only when
`gen_ai.operation.name` is `chat`, `text_completion` or `generate_content`.
An older instrumentation that writes no operation name (OpenLLMetry 0.30.0)
is refused with `not_submitted_unsupported_operation` rather than guessed.

## 6. The latest contrib instrumentation does not import as pip installs it

`pip install opentelemetry-instrumentation-openai-v2` (2.4b0) resolves
`opentelemetry-util-genai` 1.3b0, and `import
opentelemetry.instrumentation.openai_v2` fails with `ModuleNotFoundError:
No module named 'opentelemetry.util.genai.instruments'`. 2.4b0 declares the
dependency unbounded; 1.2b0 and 1.3b0 fail, **1.1b0 is the newest that
works**. Pin `opentelemetry-util-genai>=1.1b0,<1.2` until upstream fixes
it. Our `dev` and `examples` extras do. This package itself does not depend
on any instrumentation.

Also observed with that pair: on a failed call, util-genai 1.1b0 writes
`error.type` as the string `"<class 'openai.InternalServerError'>"` and
logs *"Invalid type <class 'type'> for attribute value"*. Harmless here — a
failed call is refused on the span **status** — but visible in your logs.

## 7. Every measurement is billed, and submission blocks the block's exit

🔴 Each `submitted` outcome is a billed observation (TIER_03, 0.030 CHF).
There is no trace-only route. Sampling is deterministic in the trace id;
`processor.sample_rate = 0.0` stops billing immediately.

The SDK is synchronous: leaving a `with processor.interaction()` block
waits for `POST /v1/govern`. That time is **not** in the measured latency
(the GenAI span ended before), but it is on your thread. `async with`
submits in a worker thread so the event loop is not stalled.

## 8. The processor must be on the provider the instrumentation uses

The `neomundi.interaction` span is opened from `tracer_provider` (default:
the global one). If the processor is not attached to that provider, the
interaction is refused with a reason saying so — not measured from a
different trace.

**Your sampler decides first.** If the tracer provider's sampler drops a
trace (head sampling, `ALWAYS_OFF`, a ratio), the SDK calls no processor
for it: no GenAI span is observed and the interaction is refused with
`not_submitted_no_genai_span`. A trace you do not keep is never measured.
Measured with `ALWAYS_OFF`. If you sample traces at 10 %, at most 10 % of
interactions can be measured, before `sample_rate` applies.

## 9. Evidence level: E2 — and what E2 does not say

🟢 Measured on **2026-11-26**, after the service account's guard: four real
interactions through the `openai` client instrumented by
`opentelemetry-instrumentation-openai-v2`, against Ollama's
OpenAI-compatible endpoint, with `NeoMundiSpanProcessor` on the tracer
provider. All four served `measurement_version 3.5.0` and were **re-read
from the API as persisted** — an acknowledged submission is not a persisted
one. 0.12 CHF.

Two further phases were refused on **real** spans and billed nothing: a
span carrying the provider's usage where the application declared nothing,
and a streamed call without `include_usage`. Those refusals are the point
of this integration, and they now rest on real spans rather than on a
scripted endpoint.

⚠ **E2 is not a statement about your provider.** It says this package
submitted real observations and they persisted. It says nothing about the
accuracy of the counts your application declares — OpenTelemetry has no
attribute distinguishing a published count from an estimate, which is
exactly why a count the application does not declare is refused rather than
read off the span. See section 1.

Not verified either: Python 3.11 and 3.13, macOS and Windows locally (CI
runs them), instrumentations other than the three named above.
