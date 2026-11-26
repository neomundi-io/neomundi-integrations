# Mission report — OpenAI Agents SDK (mission 005)

Brief: `neomundi-devrel#29`. Scope, probes and criteria:
`neomundi-devrel/missions/005-openai-agents/` (`SCOPE.md`, `CRITERIA.md`,
`probes/`). Delivered 2026-11-26 in one window. Evidence level **E1**,
declared as such.

## What was settled before any code

The brief recorded four facts verified on openai-agents 0.26.0 and left
four questions open. The open ones, each settled by a committed probe:

| Question | Answer | Probe |
|---|---|---|
| Which spans are leaves, which aggregates? | `ResponseSpanData` / `GenerationSpanData` are one per request. `TurnSpanData` repeats its call, and `TaskSpanData` sums the run (22/14 over two calls). Both are on **by default** | 01 |
| Does `GenerationSpanData.usage` have verifiable provenance? | **No.** Zero-filled when the provider is silent (0/0/0, 0/7/0). On `LitellmModel` it carries **21/7/28, LiteLLM's estimate**, for a provider that published nothing, under the same keys as a real 31/9/40 | 02, 03 |
| Is `export()` enough? | No: it keeps `response_id`, `type`, `usage` only. A processor is the only place the provider object is visible | 01 |
| Do tool and agent spans carry anything measurable? | No model, no count. Named on the outcome, refused | 01 |

**And one fact from the brief needed qualifying.** "`ResponseSpanData.response`
is the provider's own typed object" holds on the default route, with two
exceptions. (a) The object is attached **only when the run records
sensitive data**, otherwise `None`. (b) The `AnyLLMModel` extension fills it
with a `Response` **rebuilt** by `model_validate` from any-llm's conversion,
and probe 04 shows a processor cannot tell the two apart through any
public attribute.

## Acceptance criteria against evidence

| # | Criterion | Evidence |
|---|---|---|
| C1 | persisted observation, `request_id` re-read | **not met: E2 not run** (no key in the window). Offline: `request_id` reaches the outcome |
| C2 | refusal submits nothing, says why | `test_refusals.py`: zero requests asserted on each |
| C3 | ≥ 4 API failures never reach the host | `test_never_raises.py`: 8 failures, the run completes each time |
| C4 | latency is the host's | `test_latency_is_the_span_not_the_measurement`: equals `ended_at - started_at`; a 300 ms API is outside it |
| C5 | sampling 0 opens no socket | `test_sample_rate_zero_opens_no_socket` |
| C6 | offline suite | 45 tests, no key, `example.invalid` |
| C7 | notebook runs on a fresh machine | executed in a fresh venv built from the package's declared extras, outputs read |
| C8 | no secret in outcomes / logs | `test_nothing_secret_in_outcomes_or_logs`, including tool output and instructions |
| A1 | no eviction | `test_existing_processors_kept` |
| A2 | generation span refused | `test_generation_span_is_refused` |
| A3 | one observation per model call | `test_tool_run_submits_one_per_model_call` (2, not 2 + turns + task) |
| A4–A8 | provider total only; absent / partial / zero / no-sensitive / any-llm / failed refused | `test_refusals.py` |
| A9 | agent and tools named | `test_outcome_names_agent_and_tools` |
| A10 | never blocks the agent | `test_slow_api_does_not_block_the_agent`: 1 s API, run < 1 s |
| A11 | deterministic sampling | `test_sampling_is_deterministic_in_span_id` |
| A12 | examples print what they claim | `test_examples.py` |
| A13 | harness contract, free dry run | `test_qualify.py`, through `tools/qualify_e2.py`'s own loader |
| A14 | streaming measured | `test_streamed_run_is_measured` |

## Defects found, and what found them

1. **`on_span_end` runs on the agent's thread.** Reading
   `SynchronousMultiTracingProcessor` showed that a synchronous `govern()`
   there would stall every agent step for a full API round trip. Found by
   **introspection before the design**. A10 now fails if submission moves
   back onto the agent's thread (mutation checked).
2. **Installing twice billed twice.** Found by **running example 01 and
   reading its output**. The second section listed two NeoMundi processors,
   both live. In a notebook that is a re-run cell, and every call billed
   twice. The SDK has no public way to remove one processor, so
   `install()` now retires the previous one. Covered by
   `test_installing_twice_does_not_bill_twice`. The test suite, as first
   written, did not catch it: each test restored the processor list.
3. **The brief's provenance claim was route-dependent.** Found by
   `grep -rl "response_span("` over the installed package, then probe 04.
   Without it, an `AnyLLMModel` agent on Gemini would have had derived and
   zero-filled counts submitted as published ones.

Mutation check of the suite: four deliberate breaks were introduced
(measure aggregates, drop the any-llm guard, accept a zero total, submit on
the agent thread). Each was caught by a named test.

## Spend

**0.00 CHF.** No observation submitted, no provider called. Every probe ran
under `env -i` against a mock transport or `127.0.0.1`.

## Not done, and why

- **E2.** No `NEOMUNDI_VERIFICATION_KEY` in the window, and no authorisation.
  `qualify.py` is ready: four billed observations, about 0.12 CHF, against
  Ollama's `/v1/responses`. Unverified until then: that Ollama's Responses
  endpoint publishes `usage`, and that the model performs the tool call.
- **The any-llm Gemini conversion was read, not executed.** Running it
  needs the Google client mocked at depth. The guard does not depend on
  it: it refuses on the module being loaded.
- **CI on macOS / Windows / 3.11 / 3.13** is the pull request's run, not
  verified locally.
