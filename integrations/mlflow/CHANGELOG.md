# Changelog

All notable changes to this project are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## 0.1.0 — unreleased

First release. Measures the model calls MLflow traces with its `openai`
autolog. Evidence level E2 at release (proof: `evidence/e2_qualification_20261126T165539Z.json`). This line said
"E1: offline only" until 2026-11-27: it was written before the qualification.

### Added

- `NeoMundiMlflowProcessor`, which drives `NeoMundiClient.govern` from
  MLflow's public span-processor hook,
  `mlflow.tracing.configure(span_processors=[...])`. `install()` appends a
  plain one-argument function and evicts nobody. A bound method would make
  the application's next `configure()` call raise.
- Each `LLM` / `CHAT_MODEL` span written by MLflow's `openai` autolog is
  measured with `mlflow.chat.tokenUsage.total_tokens`, taken literally,
  and the span's `end_time_ns - start_time_ns`.
- Refused with a named status: counts from any other writer (the Agents
  SDK tracer, LangChain, LiteLLM's logger, a hand-written span), no usage,
  a partial or zero usage, aggregates (`AGENT`, `CHAIN`, `WORKFLOW`,
  `TASK`), non-model work (`TOOL`, `RETRIEVER`, `RERANKER`, `EMBEDDING`,
  `GUARDRAIL`, `PARSER`, `MEMORY`, `EVALUATOR`), `UNKNOWN` and unknown
  types, and a failed call.
- After a submission, `neomundi.request_id.<span_id>` and
  `neomundi.measurement_version` are written on the MLflow trace
  (`tag_traces=True`). A refused tag is reported as `trace_tagged=False`.
- `installed`, which reports whether MLflow still calls the hook. It is
  dropped by `mlflow.tracing.reset()`, by a new tracking URI, and by the
  application's own `configure()`.
- Submission on a worker thread with a bounded queue, so the hook never
  waits for the network. `flush()`, `shutdown()`, `uninstall()`.
- One active processor per process: a second `install()` retires the
  first, so a re-run notebook cell does not bill twice.
- Deterministic sampling in the span id. `submit=False` runs every check
  with no key and no socket.
- `qualify.py` for the library's E2 harness: four billed calls against
  Ollama's OpenAI-compatible `/v1`, plus two free refusals on real spans.
