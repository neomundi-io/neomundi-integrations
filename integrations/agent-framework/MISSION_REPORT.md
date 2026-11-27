# Mission 007 — Microsoft Agent Framework

**2026-11-28.** Builder. Seventh integration of the library.

Delivered at **E1**: `agent-framework-neomundi` 0.1.0, built, tested,
documented, with its E2 probe written and its dry run executed. **No
real execution against the production measurement API has been
performed**, so no evidence level beyond E1 is claimed.

---

## 1. The question was whether to build at all

The library already ships `otel-neomundi`, and Agent Framework emits
OpenTelemetry natively. So the mission opened on a refusable question:
*does this host offer anything a span does not?*

🔴 It mattered because of a limit already measured in this library.
`otel-neomundi` LIMITATIONS §1: a span's `gen_ai.usage.*` is written by
an instrumentation, and nothing on the span says where the number came
from. We once measured an instrumentation replacing a provider's real
`11/7` with a tiktoken estimate `7/5`, under the same scope name and
version.

Two of the three possible answers were "do not build".

## 2. What the audit measured

A real `OpenAIChatCompletionClient` over `httpx.MockTransport`: the
framework's real middleware pipeline and real response parsing, no
socket.

| Provider payload | `usage_details` |
|---|---|
| `{"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}` | copied faithfully |
| **no `usage` block** | **`None`** |

Both cases again, streaming: same result.

🟢 **Agent Framework does not invent a token count.** `CharacterEstimatorTokenizer`
exists in its public surface — which is precisely why this was probed
rather than assumed — and it did not reach `usage_details`.

🟢 And the hook **brackets** the call: latency is measured around
`await call_next()`, not read from a span.

That is enough to justify a package: `otel-neomundi` on the same spans
would refuse the count and hand the problem back to the user, who would
declare it by hand on every call.

## 3. 🔴 What the audit also measured, and what it costs the claim

The provider's raw payload is **not reachable at this hook**:

| Object | `raw_representation` |
|---|---|
| `ChatResponse` | `None` |
| `Message` | `None` |
| content item | `Choice` — carries no usage |

So the per-call verification `litellm-neomundi` performs is impossible
here. The provenance is **structural, not verified per response**:
the framework was measured not to fabricate, on version 1.26.0.

That is weaker, and it is LIMITATIONS §1, stated in the README, in the
value note and in the notebook's closing cell. It was not softened
anywhere.

## 4. Three more findings that changed the code or the docs

🔴 **A middleware exception reaches the host.** The framework does not
isolate middleware failures — measured. The "nothing escapes" invariant
is therefore entirely ours, and four distinct failure modes are tested.

⚠ One deliberate exception: when the **host's own** call fails, we record
and **re-raise**. Swallowing it would leave the application believing a
failed call succeeded.

⚠ **Two clients, two provider shapes.** `OpenAIChatClient` targets the
Responses API and raised `'NoneType' object is not iterable` on a
`chat.completion` body; `OpenAIChatCompletionClient` is the Chat
Completions one. Found by a probe failure, not by reading.

⚠ **`pip install agent-framework` pulls ~30 packages**, including
`agent-framework-copilotstudio` and `agent-framework-foundry`. The
package therefore depends on `agent-framework-core` alone.

## 5. What this does not cover, said plainly

- **Not Copilot Studio, not Microsoft Foundry.** Connectors to both live
  inside the framework; whether a call routed through them surfaces usage
  at the chat middleware layer is **untested**.
- **One connector probed**: OpenAI Chat Completions. Azure, Anthropic,
  Bedrock, Gemini, Mistral, Ollama and the Responses path were not.
- **Streaming is refused by name**, not measured.
- **Agent runs and tool calls are not measured as units.**

## 6. What was delivered

| | |
|---|---|
| Package | `agent-framework-neomundi` 0.1.0, two dependencies |
| Offline tests | **19**, driving the real pipeline and connector |
| Examples | 2, both executed and their output read |
| Notebook | built and executed end to end, offline |
| Docs | bilingual README, 10 measured limitations, value note, changelog |
| Manifest | E1, `experimental`, `published_on: none` |
| E2 probe | written; dry run executed, evidence committed |

## 7. What it cost

**0.00 CHF.** Nothing was submitted to the measurement API. The dry run
is free by construction.

## 8. What broke, and what it taught

- 🔴 **I introduced a `NameError` into `qualify.py`** by applying a Ruff
  fix to both loops when only one was unused. It would have fired during
  a **billed** run. Caught by re-reading the diff; the lesson is that an
  autofix applied to a file one is about to spend money with deserves a
  second read.
- The library's **install-honesty check caught me** offering a copyable
  `pip install` for an unpublished package. The check was right.
- **cp1252 again**: my own probe died printing an emoji — the trap
  mission 004 recorded. Probes print ASCII.
- **Windows long paths**: the first install failed on
  `hyperlight_sandbox_backend_wasm`. Recorded in LIMITATIONS §9 rather
  than worked around silently.

## 9. What is prepared and not done

**E2.** The probe is written and its dry run passes. A real run needs:

| | |
|---|---|
| Observations | 2 billed, plus 2 refusal phases that bill nothing |
| Estimated cost | **0.06 CHF** |
| Also required | an OpenAI-compatible endpoint publishing a `usage` block, reachable from the runner, plus `NEOMUNDI_AF_BASE_URL`, `NEOMUNDI_AF_MODEL`, `NEOMUNDI_AF_PROVIDER_KEY` |
| 🔴 Blocked on | `tools/qualify_e2.py` refuses without `--authorised-by` and `--budget-chf`. **That authorisation is a human act.** |

**Publication.** Per `AGENTS.md` §9: register the pending publisher,
dispatch `release.yml` with `agent-framework-v0.1.0`, a human approves
the `pypi` environment, then verify from the index.

🔴 **Not prepared as a next step but as a question**: publishing an E1
package is a choice. Every other package in this library reached PyPI at
E2. Publishing at E1 would be the first exception, and it is Sébastien's
call, not mine.
