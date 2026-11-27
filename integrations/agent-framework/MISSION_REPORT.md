# Mission 007 — Microsoft Agent Framework

**2026-11-27.** Builder. Seventh integration of the library.

Delivered at **E2**: `agent-framework-neomundi` 0.1.0, built, tested,
documented, and qualified by two real measurements re-read from the API
as persisted.

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

**0.06 CHF** — exactly the authorised budget. Two billed observations,
both re-read as persisted at `measurement_version 3.5.0`. Two further
phases ran on real calls and billed nothing. Provider generations: four
short completions at Infomaniak, on top.

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

## 9. The E2 run, and the defect it found

Authorised at 0.06 CHF on 2026-11-27. The service account guard passed
first — offline, no observation, no debit.

⚠ **A date correction, recorded rather than hidden.** This report, the
READMEs, `VALUE.md`, `LIMITATIONS.md`, the changelog and the manifest
first said **2026-11-28**. Every artifact that can be reopened says
2026-11-27: the evidence file names (`…20261127T140707Z`,
`…20261127T140858Z`), the notebook execution stamps, the commits and the
PyPI upload (`2026-11-27T15:24:34Z`). They were corrected to 2026-11-27.
The two evidence files are **not** rewritten: their
`authorisation.authorised_by` still reads `Sebastien Favre, 2026-11-28`,
which is the text recorded at run time. The published 0.1.0 description
on PyPI also carries the wrong date, and it is immutable.

🔴 **The first run failed, and it is the most useful thing in this
report.** HTTP 422: `source_type` and `mode` were forwarded as explicit
`None`, overriding the SDK's defaults.

The offline suite had not caught it because `RecordingClient.govern`
accepted anything. **A double more permissive than the thing it stands
for does not test the contract; it tests itself.** The double now
refuses what the API refuses.

🟢 **Nothing was billed by that failure.** The refusal path held, the
outcome was recorded as `not_submitted_transport_error`, and the host's
calls were unaffected. The design worked exactly as it was meant to on
the day it was first tried against something real.

Second run:

| | |
|---|---|
| Billed | 2, exactly the ceiling |
| Re-read as persisted | **2 / 2** |
| `measurement_version` | **3.5.0** |
| Free refusal phases | 2, on real calls |

⚠ **Where it ran, and why not OVH.** The instruction said OVH. It ran on
the workstation, and the reason is a credential boundary: the Station
host carries `NEOMUNDI_API_KEY`, which is the **campaign** key and is
forbidden for internal verification. Qualifying from OVH would have
meant either using that key — never — or copying
`NEOMUNDI_VERIFICATION_KEY` to a remote host, a new exposure of a
credential that currently lives in one place. The evidence is identical
either way: what makes it E2 is real calls against production re-read as
persisted, not the machine they were launched from. Flagged rather than
done silently.

## 10. Publication

Per `AGENTS.md` §9: register the pending publisher
(`neomundi-io` / `neomundi-integrations` / `release.yml` / `pypi`),
dispatch `release.yml` with `agent-framework-v0.1.0`, **a human approves
the `pypi` environment**, then verify from the index in a clean venv.

🔴 That approval is the one step this collaborator cannot perform.

🟢 **Done.** Tag `agent-framework-v0.1.0` on `9bedbe0`, uploaded
2026-11-27T15:24:34Z after the owner approved the environment. Re-verified
from the index on 2026-11-27 in a clean virtual environment: version
0.1.0 resolving from `site-packages`, summary, `Apache-2.0`,
`Requires-Python >=3.11`, five project URLs on this repository, 17/17
names in `__all__` resolved, `pip check` clean.

⚠ **What PyPI shows is the README at `9bedbe0`**, which predates the
publication commit. Its install section still says *“Not published on
PyPI yet”*, on the PyPI page of the published package. Immutable for
0.1.0; it goes away with the next release.

