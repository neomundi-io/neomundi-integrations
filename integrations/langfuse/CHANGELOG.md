# Changelog

All notable changes to this project are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## 0.1.0 — unreleased

First release. Writes NeoMundi measurements back into Langfuse as scores
on the trace. Evidence level E2 at release (proof: `evidence/e2_qualification_20261126T220436Z.json`). This line said
"E1: offline only" until 2026-11-27: it was written before the qualification.

### Added

- `NeoMundiLangfuseBridge.write()`, accepting a `neomundi.TraceRecord`, a
  `neomundi.GovernResult`, or a `langchain-neomundi` outcome, and writing:
  - one `NUMERIC` score `neomundi.<signal>` per signal the server declared
    measured, with the server's value unrescaled;
  - one `CATEGORICAL` summary `neomundi.measurement`, always, listing what
    was written and what was withheld and why.
- `request_id`, `measurement_version` and evaluator provenance in every
  score's metadata. Never prompt, response or suspect-phrase text.
- Score ids deterministic in (trace, `request_id`, score name).
- `dry_run=True`: compute the scores, hand nothing to Langfuse.
- `as_outcome_callback()` for `langchain-neomundi`'s `on_outcome`.
- `WriteOutcome` with five explicit statuses. No decision is silent, and no
  exception reaches the host.

### Known

- `queued` is not a delivery receipt; Langfuse drops scores on an API
  error. See LIMITATIONS.md §1.
- Through `langchain-neomundi` 0.1.0 only the summary is written. See §3.
