# Changelog

All notable changes to this project are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## Unreleased

### Fixed

- The quickstart notebook stopped at section 8 with an `AttributeError`: it
  still called `_warn_on_version`, which 0.2.0 removed. CI does not execute
  notebooks, so this was found by running it. Section 8 now shows the drift
  through the real path: a mocked response serving an unverified version
  produces exactly one `RuntimeWarning`, emitted by the SDK, and the
  measurement is still submitted.
- `__version__` said `"0.1.0"` in 0.2.0. It now matches `pyproject.toml`.
- `INTEGRATION_CONTRACT["neomundi_min"]`, `verified_against["neomundi"]` and
  `INTEGRATION.yaml`'s `neomundi_sdk` said `2.1.0` under a `>=2.2.0` floor.
  They now say `2.2.0`.
- The README said production ran a measurement version the SDK had not
  qualified, and that the handler warned. Neither has been true since 0.2.0.
- Dead references to `KNOWN_LIMITATIONS.md` and `docs/VALUE.md` (the files
  are `LIMITATIONS.md` and `VALUE.md`).

### Added

- `tests/test_package_metadata.py`: `__version__`, the contract's SDK floor
  and the manifest are confronted with `pyproject.toml`, and the notebook
  builder may not call a handler attribute that does not exist.

## 0.2.0 — released 2026-11-26

### Changed

- 🔴 **Requires `neomundi>=2.2.0`.** The floor rises because the SDK now
  performs the measurement-version comparison itself; below 2.2.0 nothing
  would warn once this handler stopped.
- The handler no longer emits its own measurement-version `RuntimeWarning`.
  On `neomundi` 2.2.0 one version drift produced **two identical warnings**,
  proven by execution. The SDK is the single source of truth, and it warns
  for direct SDK users too, which this handler never could.
  `Outcome.measurement_version` is unchanged: recording the version is still
  this integration's job.

### Fixed

- `LIMITATIONS.md` §1 said production ran a measurement version the SDK had
  not qualified. It has, since 2026-11-26, on live REST and SSE evidence.

## 0.1.1 — never uploaded; its fix shipped in 0.2.0

### Fixed

- Every `[project.urls]` entry pointed at `neomundi-io/langchain-neomundi`,
  the standalone repository this integration lived in before it moved into
  the library. That repository is archived and read-only, so `Issues` was
  functionally dead: an archived repository accepts none. All five links now
  point at the library, where the code and the issue tracker actually are.

  Found by verifying the published package from PyPI rather than from the
  working tree. 0.1.0's metadata is immutable, so it keeps the old links;
  see `LIMITATIONS.md`.

## 0.1.0 — released 2026-11-26

First release. Measures LangChain LLM interactions through the NeoMundi
measurement API.

### Added

- `NeoMundiCallbackHandler`, a `BaseCallbackHandler` that drives the
  `neomundi` SDK's `client.trace()`. Latency, correlation and submission
  discipline are the SDK's; nothing is reimplemented.
- RAG context captured automatically from `on_retriever_end` and attached
  to the measurement as `rag_context`, matched to its LLM run by ancestry.
- `Outcome`, an explicit record of what happened to every run, with seven
  statuses. No decision is silent.
- Deterministic sampling via `sample_rate`, keyed on the run id so a
  sampled trace is reproducible. `sample_rate=0.0` opens no socket.
- `submit=False` to wire the integration up at zero cost.
- A warning, emitted once per handler, when the served
  `measurement_version` is not among those the installed SDK verified.

### Guarantees, each covered by tests

- **No exception reaches the host chain** — verified against a real LCEL
  chain under six distinct API failures, plus an injected defect inside the
  handler itself and a caller's broken `on_outcome` sink.
- **No token count is ever invented.** A provider that reports no usage
  produces no observation.
- **No content in an `Outcome`.** Prompts, responses, keys and exception
  messages are all absent; only the exception class name is kept.

### Verified against

- `neomundi` 2.1.0, `langchain-core` 1.8.5, Python 3.11–3.13.
- 54 offline tests, no network and no credentials required.
- One real-execution proof: 6 observations submitted to
  `https://api.neomundi.io` from a real chain driving a real local LLM, all
  6 read back as persisted. See `evidence/`.

### Known at release

Production serves `measurement_version 3.5.0`, which the SDK has
deliberately not qualified. See `KNOWN_LIMITATIONS.md` §1.
