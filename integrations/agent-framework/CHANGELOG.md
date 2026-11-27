# Changelog

All notable changes to `agent-framework-neomundi`.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.0] — unreleased

First version. Built in mission 007.

### Added

- `NeoMundiChatMiddleware`, an Agent Framework `ChatMiddleware` that
  measures every chat invocation of the client it is attached to.
- A closed set of outcome statuses. Every invocation produces one, and a
  refusal is recorded rather than skipped.
- `usage_token_count()`, the only place a count is read. It reads
  `total_token_count`, or sums `input_token_count` and
  `output_token_count`, and does nothing else — no estimation path, no
  default, no fallback.
- Latency measured around `call_next()`, so it is the duration of the
  call the framework actually made.
- Bilingual documentation, measured limitations, a value note, two
  runnable examples and a reproducible notebook.
- `qualify.py`, the E2 probe, for `tools/qualify_e2.py`.

### Measured, not assumed

- Agent Framework 1.26.0 leaves `usage_details` at `None` when the
  provider publishes no usage block — streaming and not. It does not
  fabricate a count.
- The provider's raw payload is **not** reachable at this hook, so the
  per-call verification `litellm-neomundi` performs is impossible here.
  LIMITATIONS §1.
- A middleware exception **reaches the host**: the framework does not
  isolate middleware. Every path here is therefore wrapped, and four
  distinct failure modes are tested.

### Fixed before release

- 🔴 `source_type` and `mode` were forwarded as explicit `None`,
  overriding the SDK's own defaults; the API answered **HTTP 422**. Found
  by the **first real run**, not by the offline suite, whose double had
  accepted anything. Nothing was billed by that failure — the refusal
  path held and the host was unaffected. The double now refuses what the
  API refuses, and two regression tests cover both directions.
- `qualify.py` did not propagate `error_type` into its records, so a
  transport failure could not be diagnosed from the evidence afterwards.

### Evidence

**E2**, established 2026-11-28 for 0.06 CHF: two real interactions
through a real Agent Framework client against a provider's
OpenAI-compatible route, **both re-read from the API as persisted** at
`measurement_version 3.5.0`, plus two refusal phases on real calls that
billed nothing. Twenty-one offline tests.
