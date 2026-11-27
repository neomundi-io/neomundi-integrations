# Changelog

All notable changes to this project are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## 0.1.0 — released 2026-11-26

First release. Measures chat completions routed by LiteLLM with NeoMundi.
Evidence level E1: offline only.

### Added

- `NeoMundiLogger`, a LiteLLM `CustomLogger` with sync and async success
  and failure hooks, driving `NeoMundiClient.govern`.
- Token-count **provenance check**: a count is submitted only if it can be
  derived from integers the provider published in its raw payload (or its
  stream chunks). LiteLLM's zero and tokenizer estimates are refused.
- Latency from LiteLLM's `start_time` / `end_time`; a missing or negative
  delta is refused, never assumed.
- `submit=False`: every check, no key, no socket.
- `sample_rate`, deterministic in `litellm_call_id`, settable at runtime as
  the kill switch.
- At most one submission per `litellm_call_id`, whichever hooks fire.
- Async submission in a worker thread, so a proxy's event loop is never
  blocked on the network.
- `send_declared_cost` (off by default) for LiteLLM's price-table cost.
- `Outcome` with ten explicit statuses. No decision is silent, and no
  exception reaches the host.

### Known

- Async streams are refused: the provider's payload is not reachable from
  a logger on litellm 1.111.0. LIMITATIONS.md §2.
- Reassigning `litellm.callbacks` does not detach a logger. §4.
