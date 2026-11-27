# Changelog

All notable changes to this project are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## 0.1.0 — released 2026-11-26

First release. Measures model calls an application already traces with
OpenTelemetry. Evidence level E1: offline only.

### Added

- `NeoMundiSpanProcessor`, an OpenTelemetry `SpanProcessor` added next to
  the application's exporters, driving `NeoMundiClient.govern`.
- `processor.interaction()`, sync and async context manager: opens a
  `neomundi.interaction` span, finds the single GenAI span that ends inside
  it (direct child or deeper), and measures it with the count the caller
  declares through `interaction.declare(token_count=...)`.
- **A span's token count is never submitted.** `gen_ai.usage.*` is
  reported on the outcome as present or absent, never sent.
- Latency from the GenAI span's start and end; missing or negative is
  refused, never assumed.
- `neomundi.request_id`, `neomundi.status` and
  `neomundi.measurement_version` set on the interaction span, in the same
  trace as the model call.
- Content attributes are never read, whatever the instrumentation captured.
- Deterministic sampling in the trace id; `submit=False` runs every check
  with no key and no socket.
- Explicit outcomes for every case, and no exception from the processor
  ever reaches the host.
- `qualify.py` for the library's E2 harness.
