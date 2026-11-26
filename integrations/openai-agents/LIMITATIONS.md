# Known limitations

Each item was measured on 2026-11-26, not assumed. Unless stated
otherwise, the setup was `openai-agents` 0.26.0, `openai` 3.60.0, `litellm`
1.111.0 (the `[litellm]` extra), `neomundi` 2.1.0 and Python 3.12 on Linux.
The provider was scripted on an `httpx.MockTransport` or on `127.0.0.1`.
The probes that established each point are in
`neomundi-devrel/missions/005-openai-agents/probes/`.

Read this before relying on any NeoMundi score produced through this
package.

---

## 1. Chat Completions and LiteLLM spans are refused — by design

🔴 **An agent on `OpenAIChatCompletionsModel` or `LitellmModel` is not
measured.** Their model calls produce `GenerationSpanData`, whose `usage` is
a dict the SDK **rebuilds**, with no provider object beside it:

| Provider publishes | `GenerationSpanData.usage` input / output / total |
|---|---|
| 11 / 7 / 18 | 11 / 7 / 18 |
| no usage | **0 / 0 / 0** |
| `prompt_tokens: null`, 7, `total_tokens: null` | **0 / 7 / 0** |
| *(LitellmModel, Ollama-native, publishes 31 / 9)* | 31 / 9 / 40 |
| *(LitellmModel, Ollama-native, publishes **nothing**)* | **21 / 7 / 28**, LiteLLM's local estimate |

The last two rows use the same keys, in the same dict, and nothing on the
span names the route. A processor cannot tell a published count from an
estimate there, so it refuses the span
(`not_submitted_unverified_token_count`) **even when the provider published
a full usage block**.

**What to do.** Use `OpenAIResponsesModel`, the SDK's default. Or measure
the chat client directly: `litellm-neomundi` checks a LiteLLM count
against the provider's own payload.

## 2. The provider's `Response` is on the span only when the run records sensitive data

`ResponseSpanData.response` is set by the SDK **only** when
`RunConfig.trace_include_sensitive_data` is true. Its default comes from
`OPENAI_AGENTS_TRACE_INCLUDE_SENSITIVE_DATA` and is `true`. Turn it off,
and every response span is refused (`not_submitted_no_provider_response`).
The span's own `usage` dict is still there, but it is zero-filled exactly
like §1's, so it proves nothing.

The consequence to weigh: with the default, the **SDK's own exporter**
also sends prompts and responses to the OpenAI traces dashboard (when an
OpenAI key is present). That is the SDK's behaviour, unchanged by this
package. This processor reads the response in-process and sends NeoMundi
the text only if `capture_prompt` / `capture_response` are left on (the
default). With both off, it sends neither the text nor its length.

## 3. `AnyLLMModel` disables measurement for the whole process

Exactly two classes in openai-agents 0.26.0 open a response span:
`OpenAIResponsesModel` and the `AnyLLMModel` extension. The latter fills it
with a `Response` **rebuilt** by `Response.model_validate` from any-llm's
conversion. For Gemini, any-llm 1.55.0 computes the input as
`prompt + tool_use`, fills missing counts with 0 and computes a missing
total. That conversion was read in the installed source and **not
executed**. Probe 04 shows that the class, fields and extras of a rebuilt
`Response` are identical to a parsed one. Only a private attribute
differs, and that is not a contract.

So: while `agents.extensions.models.any_llm_model` is loaded in the
process, **every** response span is refused. That includes those from
`OpenAIResponsesModel`, and those from any-llm's own OpenAI provider,
whose object *is* the provider's. This is deliberately conservative.
`import agents` does not load it.

Not detected: a `Model` **you** write that opens a `response_span` and puts
an object it built there. That is your own code, not a library estimating
silently.

## 4. Only `total_tokens`, literally

The submitted count is `response.usage.total_tokens`, a positive `int` the
provider published. A missing `usage`, a `null` total, `0` or a `bool` is
refused. The count is **never** rebuilt from input + output, even when
both are present.

## 5. Latency is the SDK's span, and a streamed span includes your loop

`latency_ms` is `ended_at - started_at` of the response span, ISO
timestamps the SDK sets itself. Nothing is timed by this package. A
non-streamed span covers the HTTP call and the SDK's handling around it.
For `Runner.run_streamed`, the span stays open until the stream is
consumed, so it includes the time your code spends consuming events. A
backward clock step makes the latency negative, and it is refused, never
clamped.

## 6. The prompt sent is the span's input items, not the agent's instructions

`llm_prompt` is the text of the span's `input` items, which is the
conversation the model saw. The agent's `instructions` are **not** on the
span. `response.instructions` is there only if the provider echoes it, so
using it would make the prompt length depend on the provider. Not
included. `llm_response` is the text of the `message` items of the
response. A call that only asks for a tool has no response text, and none
is sent.

## 7. Correlation is on your side

The API does not persist `metadata` (`neomundi` 2.1.0, `trace.py`). The
join between an observation and a step of the loop is the `Outcome`:
`(trace_id, span_id, agent_name, tool_calls) -> request_id`. Keep the
outcomes, through `on_outcome`, if you need that join later. Nothing is
written back into the OpenAI trace: the span has ended by the time the
measurement returns.

## 8. One processor per process, and `set_trace_processors` is a trap

`set_trace_processors()` **replaces** the processor list. It removes the
SDK's `BatchTraceProcessor`, and with it the export to the OpenAI
dashboard, without a warning. `install()` uses `add_trace_processor`.

The SDK has no public way to remove a single processor. Installing a
second `NeoMundiTraceProcessor` with `install()` therefore **retires** the
first: it stays in the list but measures nothing, so a re-run notebook cell
does not bill twice. Calling `add_trace_processor` yourself twice
bypasses that guard, and then each processor bills.

Submission runs on a daemon worker thread with a bounded queue (`max_queue`,
default 1024). A full queue drops the call with
`not_submitted_queue_full` rather than blocking the agent. Call
`processor.flush()` or `force_flush()` before exiting, or the last
submissions may be lost with the process.

## 9. Evidence level E1, not E2

No real observation has been submitted through this package. The window
that built it had no `NEOMUNDI_VERIFICATION_KEY`, and no billed
measurement was authorised. `qualify.py` is ready: four billed
observations, about 0.12 CHF, against Ollama's `/v1/responses`. Its dry
run is green against a mock provider. **Two facts remain unverified until
it runs against a real Ollama:** that Ollama's Responses endpoint publishes
a `usage` block, and that the chosen model performs the tool call. If
either fails, the evidence will say so.

## 10. Pinned to openai-agents 0.26.x

The SDK is pre-1.0 and changed its tracing surface in this minor. Task and
turn spans are on by default, and `GenerationSpanData` is zero-filled. The
dependency is `>=0.26.0,<0.27` so that the next minor is introspected
before it is allowed, not after.
