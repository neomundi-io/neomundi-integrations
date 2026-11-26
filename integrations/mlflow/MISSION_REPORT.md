# Mission report — MLflow (mission 006)

Brief: [neomundi-devrel#33](https://github.com/neomundi-io/neomundi-devrel/issues/33).
Scope, probes and criteria:
[neomundi-devrel/missions/006-mlflow](https://github.com/neomundi-io/neomundi-devrel/tree/main/missions/006-mlflow).
Evidence level delivered: **E2**. The building window delivered E1 at
0.00 CHF; the authorised E2 run on the workstation followed the same day
(4 observations, 4/4 re-read as persisted, **0.12 CHF**).

## What was settled before any code

The brief asked three questions and offered a working hypothesis for
each. All three were answered by execution, and **two hypotheses did not
survive**.

1. **Extension point.** The brief framed the choice as public
   `set_destination` / `TraceDestination` against the private
   `_add_span_processor_first` helpers. Neither is a third-party extension
   point. `set_destination` accepts only MLflow's closed set of locations,
   and a TODO in `_get_span_processors` says the pluggable registry does not
   exist. The public OpenTelemetry route, `get_bridged_tracer_provider()`
   plus `add_span_processor`, is **silently lost on the next
   `mlflow.set_experiment()`**. The supported route is
   `mlflow.tracing.configure(span_processors=[fn])`: public, documented,
   and it survives `set_experiment`. A bound method passed there is
   accepted, and then makes the **next** `configure()` call raise. The
   hook must therefore be a plain function.
2. **Does tracing estimate?** MLflow's tracing code does not. But MLflow
   **copies** counts written by other libraries under the same attribute.
   On a provider that publishes nothing: the Agents SDK tracer writes
   `0/0/0`, LiteLLM's logger writes an estimate (`14/7/21`), and LangChain
   writes whatever the chat model computed. Only the `openai` autolog was
   shown to keep a silent provider's usage **absent**. The brief's "never
   zero" holds for that writer and is false for another.
3. **Aggregates.** The trace total lives in trace metadata, not on a span.
   A new double-count path, which the brief did not name: an Agents SDK
   run under `mlflow.openai.autolog()` produces **two** `CHAT_MODEL`
   spans per provider call. Closed by measuring by writer, and tested.

What `otel-neomundi` contributed is therefore its doctrine (a count is
refused unless its origin is established), its status vocabulary and its
test shapes. It did not contribute its `SpanProcessor` class, which cannot
stay attached to MLflow. `NOT_SUBMITTED_AGGREGATE_SPAN` and
`NOT_SUBMITTED_NOT_A_MODEL_CALL` are reused from `openai-agents-neomundi`.

## Acceptance criteria against evidence

| # | Criterion | Evidence |
|---|---|---|
| C1 | persisted observation, re-read | **E2**: 4/4 re-read as persisted, mv 3.5.0 ([`evidence/e2_qualification_20261126T165539Z.json`](evidence/e2_qualification_20261126T165539Z.json)); offline: `request_id` on the outcome and on the trace |
| C2 | refusals submit nothing, say why | `test_refusals.py`, zero requests asserted on each |
| C3 | ≥ 4 API failures never reach the host | `test_never_raises.py`: 8 failures |
| C4 | latency is the host's call | `test_latency_is_the_span_mlflow_recorded`: equal to the stored span's end − start |
| C5 | sample 0 opens no socket | `test_sample_rate_zero_opens_no_socket` |
| C6 | offline, no key, no cost | mock transports, `example.invalid`, sqlite in a temp dir, MLflow telemetry off |
| C7 | notebook runs on a fresh machine | executed in a fresh venv from `.[examples,notebook]`, outputs read |
| C8 | no secret in outcomes, tags, logs | `test_nothing_secret_in_outcomes_tags_or_logs` |
| M1–M3 | hook appended, app can still configure, idempotent, retire, `installed` | `test_install.py` (10 tests) |
| M4 | count is the provider's total | `test_measured.py`, 18 on the wire, Chat Completions and Responses |
| M5 | no / partial / zero usage refused, never rebuilt | `test_refusals.py` |
| M6 | other writers refused, by name | Agents SDK zero fill, LangChain estimate, hand-written span — each re-read from the stored trace first |
| M7 | Agents SDK run billed once per call | `test_agents_run_not_double_counted`, both model classes |
| M8 | every real `SpanType` has a named status | parametrised over MLflow's own enum; a guard test fails if MLflow adds a type |
| M9 | failed call not measured | `test_failed_model_call_is_not_measured` |
| M10 | request id on the trace, re-read | `test_request_id_written_on_the_trace` |
| M11 | never blocks on the network | `test_slow_api_does_not_block_the_host` |
| M12 | deterministic sampling | checked against the documented rule |
| M13 | examples print what they claim | `test_examples.py` |
| M14 | `qualify.py` contract, dry run sends nothing | `test_qualify.py`, and `tools/qualify_e2.py mlflow --dry-run` green against a scripted endpoint over real HTTP |
| M15 | stream with usage measured, without refused | `test_measured.py`, `test_refusals.py` |

75 offline tests: green locally, and green again in a clean venv
installed from `.[dev,examples]` only. Mutation check: six deliberate
defects (any writer accepted, total rebuilt from parts, hooks replaced
instead of appended, latency from our own clock, a bound-method hook, AGENT
no longer an aggregate) were each caught by at least one test. A seventh,
AGENT added to the model calls, changed no behaviour because the aggregate
check runs first. It is an equivalent mutation, not a gap in the tests.

## Defects found, and what found them

| # | Defect | Found by | Fixed |
|---|---|---|---|
| 1 | an OTel processor on MLflow's provider is lost on `set_experiment` | probe 03 | architecture: MLflow's `configure` hook instead |
| 2 | a bound-method hook makes the host's next `configure()` raise | probe 04 | plain closure, tested (M2) |
| 3 | the hook holding a weak reference would let an unassigned processor be collected and go silent | design review, before the first run | strong reference, tested |
| 4 | at hook time the span's end time is not set yet | probe 04 | the worker waits for it (bounded), never uses its own clock |
| 5 | the examples' placeholder key was refused by the SDK's prefix check | **running the examples** | `ct_test_` prefix |
| 6 | MLflow's coding-assistant hints wrote local paths into the notebook outputs | **reading the notebook's outputs** | `MLFLOW_DISABLE_AGENT_HINT=1` in the notebook and examples |
| 7 | the linter's import sorting would move the environment setup below `import mlflow`, which breaks it silently | `ruff check` proposing a fix | `# isort: split`, fix not applied |
| 8 | MLflow telemetry is on outside CI, and `env -i` strips the CI variables: **the mission probes ran with it enabled** | reading `mlflow/telemetry` while writing the suite | probes, suite, examples, notebook and `qualify.py` now disable it |
| 9 | MLflow writes trace artifacts to `./mlruns` under the **current directory** even when the store is elsewhere: the suite, examples and notebook littered the working tree (280 files) | **reviewing `git status` before the commit** | experiments created with an explicit `artifact_location` in the temp store; regression tests assert no `mlruns` appears |
| 10 | upstream: MLflow's trace aggregation fails on a `null` total (`int += None`) and continues without the aggregate | reading the examples' stderr | not ours; recorded in LIMITATIONS §3; not reported upstream (no authorisation) |

## Spend

**0.12 CHF** in total, all of it the E2 run: four billed observations on
the service verification account, authorised beforehand (budget 0.20 CHF,
ceiling 6 observations), account guard run first. The building window
spent 0.00 CHF: no NeoMundi key existed in it, and no paid provider was
contacted. Every model provider was a mock transport or a scripted server
on 127.0.0.1.

## Not done, and why

- **E2 is done**, not by the building window: it had no key. The
  authorised run on the workstation (c82d3be) settled the one assumption
  the probe declared unverified -- Ollama honours
  `stream_options.include_usage`.
- **Writers beyond `openai`.** Anthropic, Gemini, Bedrock and other
  autologs may copy provider counts faithfully. None was driven, so all
  are refused until one is.
- **Upstream proposal**: a provenance marker next to
  `mlflow.chat.tokenUsage`, and the `null`-total aggregation failure. That
  is an external community interaction, so authorisation is required.
