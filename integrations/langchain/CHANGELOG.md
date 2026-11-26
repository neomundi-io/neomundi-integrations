# Changelog

All notable changes to this project are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## 0.1.0 — unreleased

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
