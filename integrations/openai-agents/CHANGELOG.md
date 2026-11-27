# Changelog

All notable changes to this project are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## 0.1.0 — released 2026-11-26

First release. Measures the model calls an OpenAI Agents SDK run makes.
Evidence level E1: offline only.

### Added

- `NeoMundiTraceProcessor`, an Agents SDK `TracingProcessor` driving
  `NeoMundiClient.govern`. `install()` appends it with `add_trace_processor`
  and evicts nobody.
- Each `ResponseSpanData` is measured with the provider's
  `response.usage.total_tokens`, taken literally, and the span's
  `ended_at - started_at`.
- Refused with a named status: `GenerationSpanData` (Chat Completions,
  LiteLLM), turn and task aggregates, tool / agent / handoff / guardrail
  spans, a missing or partial provider usage, a run without sensitive data,
  any response span while `AnyLLMModel` is loaded, a failed or incomplete
  call.
- Outcomes name the agent and the tools each model call requested, and
  the tool each tool span ran. Names only, never input or output.
- Submission on a worker thread with a bounded queue: `on_span_end` never
  waits for the network. `flush()` / `force_flush()` / `shutdown()`.
- One active processor per process through `install()`: a second install
  retires the first, so a re-run notebook cell does not bill twice.
- Deterministic sampling in the span id. `submit=False` runs every check
  with no key and no socket. `sample_rate` is settable at runtime as a kill
  switch.
- `qualify.py` for the E2 harness: four billed observations against
  Ollama's `/v1/responses`, two free refusals.
