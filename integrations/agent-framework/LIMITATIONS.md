# Known limitations

Each item was **measured on 2026-11-28** against `agent-framework`
1.26.0, `agent-framework-core` 1.26.0, `agent-framework-openai` 1.18.2,
`openai` 3.60.0 and `neomundi` 2.2.0, Python 3.12, offline — rather than
assumed. Read this before relying on any NeoMundi score produced through
Agent Framework.

---

## 1. 🔴 The token count is the framework's, and it is **not verified per call**

This is the most important sentence in this file, and it is the
difference between this integration and `litellm-neomundi`.

The count submitted here comes from `ChatResponse.usage_details`. What we
measured about that field:

| Provider payload | `usage_details` |
|---|---|
| `{"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}` | `{input 11, output 7, total 18}` — copied faithfully |
| **no `usage` block at all** | **`None`** |

Both cases again, **streaming**: usage chunk present → carried; absent →
`None`.

🟢 So Agent Framework does **not** invent a count. That is a genuinely
good property, and the opposite of LiteLLM, which fills a silent
provider's usage with `0/0/0` or with its own tokenizer's estimate.

🔴 **But we cannot check it on each call.** We walked the whole response
tree looking for the provider's raw payload:

| Object | `raw_representation` |
|---|---|
| `ChatResponse` | `None` |
| `Message` | `None` |
| content item | a `Choice` — which carries no usage |

`litellm-neomundi` verifies every submitted count against the integers in
the provider's own JSON. **Here there is no such JSON at this hook**, so
no equivalent check exists.

**What that means for you.** The provenance of a count measured through
this integration rests on a property of the framework, observed on
version 1.26.0, not on a per-response proof. If a future version starts
filling `usage_details` from `CharacterEstimatorTokenizer` — which exists
in the public surface, serves compaction, and did not reach
`usage_details` in either of our cases — this integration would submit it
without noticing. `upstream.yml` watches the host for drift; that is a
mitigation, not a proof.

## 2. 🔴 Only one connector was measured

Agent Framework ships roughly thirty packages. We probed exactly one
path: `agent-framework-openai`'s **`OpenAIChatCompletionClient`**.

| Connector | Measured |
|---|---|
| OpenAI Chat Completions | **yes** |
| OpenAI Responses (`OpenAIChatClient`) | no |
| Azure, Anthropic, Bedrock, Gemini, Mistral, Ollama | no |
| Copilot Studio, Foundry | no — see §7 |

The middleware attaches **above** the connector, so it is not coupled to
any provider shape. But whether every connector leaves `usage_details`
absent on a silent provider is **not established**. Check
`outcome.status` on your own route before relying on coverage.

⚠ Found by a probe failure rather than by reading: `OpenAIChatClient`
targets the **Responses** API and raised `'NoneType' object is not
iterable` on a `chat.completion` body. `OpenAIChatCompletionClient` is
the Chat Completions one. Two clients, two shapes.

## 3. Streaming is refused by name, not measured

`context.result` is a `ResponseStream` when the middleware resumes, and
the usage only exists once that stream has been consumed. Measuring at
that moment would mean guessing.

So a streaming invocation produces `not_submitted_streaming`, costs
nothing, and is recorded. `ChatContext` does expose
`stream_result_hooks` and `stream_cleanup_hooks`, which is the honest
route to streaming support; it is **not implemented in 0.1.0** and the
`measure_streaming` argument is reserved for it.

## 4. 🔴 A middleware exception reaches the host — so we catch everything

We measured it: a middleware that raises after `call_next()` propagates
`RuntimeError` straight to the caller. **Agent Framework does not isolate
middleware failures.**

Consequently every path in this integration is wrapped, and four distinct
failure modes are tested: the measurement API refusing, an unexpected
error in our own code, a malformed usage block, and the host's own call
failing.

⚠ **One exception to "nothing escapes", on purpose.** When the *host's*
call fails, we record `not_submitted_application_error` and **re-raise**.
Swallowing it would leave the application believing a failed call
succeeded — worse than useless.

## 5. Latency is the framework's view of the call

It is measured around `await call_next()`, so it includes the connector,
the HTTP round trip and any middleware registered *inside* ours. It is
not the provider's server-side time, and it is not a span's duration.

🟢 This is better than reading a span, and it is still **our**
measurement of someone else's call. Order your middleware deliberately:
anything slow placed inside this one is counted as provider latency.

## 6. Every measurement is billed

Each submitted observation is a metered measurement at the published
rate. There is **no trace-only route**.

🟢 A refusal costs nothing: `not_submitted_*` never opens a socket to the
measurement API. The four refusal paths are demonstrated in
`examples/02_refusals.py`, which prints the billed count — zero.

Before attaching this to a busy application, decide what you want
measured. There is no sampling in 0.1.0.

## 7. 🔴 This does **not** cover Copilot Studio or Microsoft Foundry

`pip install agent-framework` pulls some thirty packages, including
`agent-framework-copilotstudio` and `agent-framework-foundry`. That means
connectors to those products exist *inside* the framework. It does
**not** mean this integration measures them.

Whether a call routed through either connector surfaces a usage block at
the chat-middleware layer is **untested**. Nothing here should be read as
Copilot Studio or Foundry support.

This package depends on `agent-framework-core`, not on the meta-package,
precisely so that installing it does not drag in thirty unrelated things.

## 8. Agent and function layers are not measured

`AgentMiddleware` and `FunctionMiddleware` exist beside `ChatMiddleware`.
This integration attaches only to the **chat** layer, which is where
model calls happen.

An agent run that makes three model calls produces three outcomes. The
run itself, the tool invocations and any orchestration are **not**
measured as units, and no aggregate across them is computed.

## 9. ⚠ Installing on Windows can fail on path length

`agent-framework` pulls `hyperlight_sandbox_backend_wasm`, whose internal
path exceeds the Windows limit when the target directory is already deep.
Our first install failed with:

```
OSError: [Errno 2] No such file or directory:
'...\\hyperlight_sandbox_backend_wasm-0.9.0.dist-info\\sboms\\...json'
```

Enable Windows long-path support, or install from a shorter root. This is
the host's packaging, not ours, and installing `agent-framework-core`
alone — which is all this integration needs — avoids it.

## 10. Evidence level: E1

**E1 at 0.1.0.** Offline tests, green across platforms, driving the
framework's real middleware pipeline and real connector over a mocked
transport.

🔴 **No real execution against the production measurement API has been
performed**, so no `measurement_version` is declared verified and
`INTEGRATION_CONTRACT["measurement_versions_verified"]` is empty. The E2
probe is written and `tools/qualify_e2.py` will run it when a spend is
authorised. Until then this package claims E1 and nothing more.
