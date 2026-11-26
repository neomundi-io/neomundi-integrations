# Known limitations

Each item was **measured, not assumed**, on 2026-11-26 with
mlflow-tracing 3.18.0 (plus mlflow-skinny 3.18.0 for a local store),
opentelemetry-sdk 1.45.2, openai 3.60.0, openai-agents 0.26.0,
langchain-core 1.8.5, litellm 1.111.0 and neomundi 2.1.0, on Python 3.12 /
Linux. The probes that established them are in
[neomundi-devrel/missions/006-mlflow](https://github.com/neomundi-io/neomundi-devrel/tree/main/missions/006-mlflow)
and run under a scrubbed environment against local mock providers only.

A limitation listed here is a limit of what can be **honestly** measured,
not a bug waiting to be fixed. Several are deliberate refusals.

## 1. Only the `openai` autolog is measured — by design

MLflow stores every writer's token count under the same attribute,
`mlflow.chat.tokenUsage`, and **no attribute says where the number came
from**. MLflow's own tracing code never estimates: `tiktoken` does not
appear in `mlflow/tracing/`. But several autologs **copy** counts that are
not the provider's. Each writer was driven with a provider that publishes
**no usage at all**:

| Writer (`mlflow.message.format`) | Silent provider produces | Outcome here |
|---|---|---|
| `openai` — `mlflow.openai.autolog()` on the `openai` client (Chat Completions, Responses, streamed) | attribute **absent** | measured when present |
| `openai-agent` — the Agents SDK tracer, which `mlflow.openai.autolog()` also installs | **`0 / 0 / 0`** | refused |
| none — LiteLLM's own `MlflowLogger` (`mlflow.litellm.autolog()`) | **`14 / 7 / 21`**, LiteLLM's local estimate | refused |
| `langchain` — `mlflow.langchain.autolog()` | whatever the chat model put in `usage_metadata`; a word count in the probe | refused |
| anything else, or a hand-written span | not verified | refused |

**What this means for you:** if your application calls models through
the `openai` client, including any OpenAI-compatible server it points at,
it is measured. Through LangChain, LiteLLM, the Agents SDK or another
framework, every call is refused with
`not_submitted_unverified_token_count`, and the reason names the writer.
Nothing is skipped silently. When you also trace the underlying `openai`
client, that inner span is measured instead: an Agents SDK run under
`mlflow.openai.autolog()` has both spans and is billed once (tested).

**The trust boundary:** "the provider" is whatever endpoint the `openai`
client calls. A proxy that estimates, such as a LiteLLM proxy server,
cannot be told apart from a provider that counts. The same boundary holds
in `openai-agents-neomundi`.

## 2. Staying attached: what removes the hook

The integration registers one function through MLflow's public, documented
hook, `mlflow.tracing.configure(span_processors=[...])`, **appended** to
yours. Measured:

| Call | Hook |
|---|---|
| `mlflow.set_experiment()`, `mlflow.tracing.set_destination()`, `mlflow.tracing.disable()` / `enable()`, `mlflow.openai.autolog()` | **kept** |
| `mlflow.tracing.reset()` | **removed** |
| `mlflow.set_tracking_uri()` to a **different** URI (it calls `reset()`) | **removed** |
| your own `mlflow.tracing.configure(span_processors=[...])` (it replaces the list) | **removed** |

So: **install after `set_tracking_uri`**. After any of the three removals,
check `processor.installed` and call `install()` again; MLflow gives no
signal of its own. When you configure your own processors later, include
the existing ones: `configure(span_processors=[*get_config().span_processors, yours])`.

Why not an OpenTelemetry `SpanProcessor` on MLflow's provider? A processor
added through the public `get_bridged_tracer_provider()` is **silently
lost** on the next `mlflow.set_experiment()`, and on all five calls above,
because MLflow builds a new provider each time. Why not
`set_destination`? It accepts only MLflow's own closed set of locations.

## 3. Only `total_tokens`, literally

The submitted count is `total_tokens` from `mlflow.chat.tokenUsage`, as a
positive integer. It is never rebuilt from `input_tokens + output_tokens`.
A provider that published a partial usage block (no total, or a `null`
total) or a zero total is refused with
`not_submitted_invalid_token_count`. MLflow itself keeps a missing total as
`null` on the live span, which is right. Its own trace-level aggregation
then fails with `int += None` and continues without the aggregate (a log
warning, observed while running the examples).

## 4. Latency is MLflow's span, and a stream includes your loop

`latency_ms` is the MLflow span's `end_time_ns - start_time_ns`, as MLflow
recorded it. It is never re-measured here. MLflow calls the hook just
**before** it ends the span, so the worker thread waits for the end time,
up to `latency_wait_s` (5 s by default), and refuses with
`not_submitted_no_latency` rather than inventing one. For a streamed call
MLflow ends the span when **your code** finishes consuming the stream, so
your loop's time is included.

## 5. MLflow must be able to trace

With `mlflow-tracing` installed **alone** and no tracking URI, MLflow
records **no span at all**: `@mlflow.trace` returns normally, no span
reaches any processor, and `get_bridged_tracer_provider()` raises
`ModuleNotFoundError: sqlparse` (the package has no local store). This
integration then sees nothing, and therefore says nothing. Configure a
tracking server, or install `mlflow` (or `mlflow-skinny` + `sqlalchemy` +
`alembic`) for a local store, and check that your traces appear in MLflow
before you rely on this package.

## 6. The trace tag can fail, and that is reported

After a submission the processor calls `mlflow.set_trace_tag` to write
`neomundi.request_id.<span_id>` and `neomundi.measurement_version`.
MLflow writes the tag to the live trace when the trace is still open, and
to the store otherwise. If the store refuses, the observation is still
submitted, and the outcome says `trace_tagged=False`. Turn tagging off
with `tag_traces=False`. The measurement API does not persist `metadata`,
so the request id on the trace or on the outcome is the only join key.

## 7. The prompt sent is what MLflow recorded

`llm_prompt` is the text of the `messages` (or `input`) MLflow recorded as
the span's inputs. `llm_response` is the text of the response MLflow
recorded. A span processor of yours that redacts those **runs before
this one** and is respected. Turn either off with `capture_prompt=False` /
`capture_response=False`: only their lengths are lost. The count and the
latency stay.

## 8. Evidence level E2, one writer, one provider

**Measured 2026-11-26.** Four real model calls traced by the real
`mlflow.openai.autolog()` into a throwaway local store, against
`llama3.2:3b` on a local Ollama's OpenAI-compatible `/v1`, from the service
verification account: two Chat Completions inside an application span, one
Responses API call, and one stream with `stream_options.include_usage`.
Re-read by `request_id`: 4/4 persisted, served `measurement_version`
3.5.0, 0.12 CHF ([`evidence/e2_qualification_20261126T165539Z.json`](evidence/e2_qualification_20261126T165539Z.json)).

The assumption this section used to list as unverified is now measured:
Ollama honours `stream_options.include_usage`. The stream with it produced
a provider count; the stream without it produced none, and was refused
(`not_submitted_no_token_count`) rather than estimated.

What that does **not** cover, and what it means for you:

- **One writer.** Only the `openai` autolog was measured for real. The
  application span was refused as an aggregate, and a hand-written span
  carrying a count the way LiteLLM's MLflow logger writes its estimate was
  refused as unverified provenance. Both billed nothing. Other autologs
  stay refused (§1).
- **One provider, one platform.** Paid providers were exercised only
  through mock transports. The real run was Python 3.12 with `neomundi`
  2.1.0; the CI matrix covers 3.11–3.13 on Linux, macOS and Windows
  offline.
- **Not E3.** Nobody outside the team has reproduced it.

## 9. Pinned to mlflow-tracing 3.18.x

Everything in §1 to §5 is MLflow code that can change in a minor release:
the `openai` autolog's handling of a silent provider, the writer names, the
hook's behaviour. The package therefore requires `mlflow-tracing>=3.18.0,<3.19`.
The library's weekly drift job tests the newest release before the pin
moves.

## 10. MLflow's own usage telemetry

MLflow sends anonymous usage telemetry by default, except in CI. The suite,
the examples, the notebook and `qualify.py` switch it off
(`MLFLOW_DISABLE_TELEMETRY=true`), because they promise to open no socket.
In your application it is MLflow's setting, not this package's.
