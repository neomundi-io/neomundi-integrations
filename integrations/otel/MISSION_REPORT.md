# Mission report — OpenTelemetry (mission 004)

Built in one autonomous window on 2026-11-26, following the DevRel mission
method. Brief: `neomundi-devrel#23`. Evidence level reached: **E1**,
declared as such. Spend: **0.00 CHF**.

## Outcome in one line

The brief's central question — *can the provenance of a token count be
established from an OTel span?* — is answered **no**, from the
specification and from real code. So the integration is outcome 2 + 3 of
the brief: **a span's count is never submitted**; the caller declares the
provider's count, the span supplies the latency, the model and the trace,
and the NeoMundi `request_id` is written back into that trace.

## The hypotheses, settled before any code

Probes, outputs and versions: `neomundi-devrel/missions/004-otel/`
(`SCOPE.md`, `probes/`).

| # | Hypothesis in the brief | Verdict |
|---|---|---|
| H1 | spans carry `gen_ai.usage.input_tokens` / `output_tokens` | true when the provider publishes, **absent** when it is silent (contrib 2.4b0, OpenLLMetry 0.63.1); **other names** in OpenLLMetry 0.30.0 |
| H2 | content is off by default, opt-in | true for contrib; **false for OpenLLMetry 0.63.1**, which captures the prompt by default |
| H3 | latency from start/end | true; streams include the consumer's loop |
| H4 | `on_end` is the hook and composes | true; existing exporter unchanged, 8/8 spans to both |
| H5 | names stable enough to pin | **false**; all GenAI attributes *Development*, all constants deprecated in semconv 0.66b2 (moved repository), two renames in current use |
| — | **provenance from a span** | **no**: spec says "best effort"; an option (OpenLLMetry 0.30.0 `enrich_token_usage`) **replaced** a published 11/7 by an estimated 7/5 with an identical-looking span; contrib spans carry a shared public helper's scope, reproducible by any code with an estimate |

## Acceptance criteria against their evidence

Written before the implementation (`missions/004-otel/CRITERIA.md`).

| # | Criterion | Evidence |
|---|---|---|
| C1 | Measured interaction persisted, re-read | ⚪ **not verifiable at E1** — no service key. Offline: `request_id` reaches the outcome and the trace |
| C2 | Unmeasurable → nothing sent, reason given | `test_refusals.py`: recorder count 0 + explicit status, 12 refusal cases |
| C3 | No exception reaches the host, ≥ 4 API failures | `test_never_raises.py`: 401, 402, 429, 500, 503, network, timeout, invalid JSON, exploding client, garbage spans, raising callback, broken tracer provider |
| C4 | Latency is the host's call | equals the GenAI span's duration; a 400 ms API does not change it |
| C5 | Sampling at zero opens no socket | recorder count 0 |
| C6 | Suite offline, no key, no cost | 48 tests, `httpx.MockTransport` on both sides, `example.invalid` |
| C7 | Notebook runs end to end | executed with nbclient in a fresh venv, outputs committed and read. CI does not execute notebooks |
| C8 | No key, prompt or response leaks | markers absent from outcomes, our span, captured logs and the wire — **with content capture on** |
| O1 | A span's count is never submitted | real openai-v2 span with 11/7, nothing declared → 0 requests |
| O2 | Declared count wins | span 7/5, declared 18 → 18 on the wire |
| O3 | Content attributes never read | content capture on, marker absent from the wire |
| O4 | `request_id` in the same trace | read from the application's own `InMemorySpanExporter` |
| O5 | Existing pipeline unchanged | GenAI span identical with and without the processor |
| O6–O9 | ambiguity, failed call, invalid count, deterministic sampling | `test_refusals.py` |
| O10 | examples print what they claim; real instrumentation drives the suite | `test_examples.py`; every GenAI span in the suite comes from openai-v2 |
| O11 | `qualify.py` honours the harness contract | loaded through the harness's own loader; dry run in-suite and through `tools/qualify_e2.py --dry-run` |

## Defects and findings, and how each was found

1. **`opentelemetry-instrumentation-openai-v2` 2.4b0 does not import as
   pip installs it** (`opentelemetry-util-genai` 1.3b0 removed a module it
   uses). Found by the first probe, before any code. Pinned in our extras;
   LIMITATIONS §6.
2. **H2 is false for OpenLLMetry**: prompt captured by default. Found by
   probing a second instrumentation, which the brief did not ask for.
3. **The span's scope does not name the instrumentation** (contrib emits
   under `opentelemetry.util.genai.handler`). Found by reading the scope
   in probe 01; turned into the decisive probe 03.
4. **An estimate can replace a published count.** Found by running
   OpenLLMetry 0.30.0 — after two false negatives in the probe itself
   (tiktoken could not map the scripted model names; the endpoint echoed
   the scenario as the model). Both probe defects, fixed in the probe.
5. **The wire body nests metrics under `raw_metrics`.** Same test defect
   as mission 003, repeated: four tests read `body["token_count"]`. Fixed
   by reading the SDK's real request. *Lesson for the method: read one
   real request body before writing wire assertions.*
6. **Example 02 printed `span usage : absent` for two spans that both
   carried usage.** Found by reading the example's output, not by the
   suite. `span_reported_usage` is now `None` ("not examined") when no
   single span was examined, and a test pins it.
7. **The E2 harness refused a dry run without a key**, though a dry run
   reads none. Found by running it as the brief asked. Fixed in
   `tools/qualify_e2.py` (see below), with two more defects found on the
   way: a dry run whose host was never reached **exited 0**, and a dry
   run's output was **named like a proof** (`e2_qualification_*.json`).
8. **The scripted endpoint ignored the request's `stream` flag**, found
   when the harness dry run drove it as a real host. Probe defect, fixed.
9. **Suite green → mutation-tested.** Falling back to the span's count
   turned 2 tests red, including the invariant test O1; taking latency at
   submission time turned 1 red.

## Shared component changed: `tools/qualify_e2.py`

Three changes, each closing a defect found in this window; the real run's
three gates (`NEOMUNDI_VERIFICATION_KEY`, `--authorised-by`,
`--budget-chf`) are untouched and re-tested:

- `--dry-run` neither needs nor reads the key; the probe gets `client=None`.
  `litellm-neomundi`'s probe accepts that (`NeoMundiLogger(None, submit=False)`).
- A dry run writes `dry_run_<stamp>.json`, marked `"dry_run": true` and
  *"DRY RUN, not a proof"*.
- A run where **no phase reached the host** exits 1 as *INCONCLUSIVE*; a
  dry run that submitted anything exits 1 as a probe defect.

## Spend

**0.00 CHF.** No observation was submitted to the production API.

## What was not done, and why

- **E2.** No `NEOMUNDI_VERIFICATION_KEY` in the window, and no billed
  observation authorised. Next: the account guard, then `python
  tools/qualify_e2.py otel --authorised-by ... --budget-chf 0.15` with
  Ollama → 4 billed observations, re-read (≈ 0.12 CHF).
- **A dry run against a real model.** Installing Ollama on the runner was
  refused by the session's permission policy. The dry run drove the real
  client, instrumentation and processor over real HTTP against a scripted
  endpoint instead; against Ollama it remains to be run.
- **Other instrumentations** (Anthropic, Google GenAI, LangChain contrib):
  not probed.
- **Platforms.** Only Python 3.12 on Linux ran in the window; CI covers
  the rest.
- **An upstream proposal** for a provenance marker in the GenAI
  conventions: external community, needs explicit authorisation.
