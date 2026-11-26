# Mission report — 002, Langfuse

Scoped in [neomundi-devrel#2](https://github.com/neomundi-io/neomundi-devrel/issues/2).
Built on 2026-11-26 in one autonomous window. **Delivered at E1**, by
decision before the work started: the window had neither the NeoMundi
service verification key nor a Langfuse instance, so E2 was not reachable
and is not claimed.

Prerequisites (coexistence, Python floor, `create_score` surface,
correlation, offline feasibility) had been verified by introspection when
the mission was scoped and were not redone.

## 1. Acceptance criteria, defined before implementation

Written in `neomundi-devrel/missions/002-langfuse/CRITERIA.md` before any
code. Each against its evidence:

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | Persisted observation re-read; scores re-read from Langfuse | ⚪ **not met — E1** | no key, no Langfuse target |
| 2 | Unmeasured signal → no numeric score; not-submitted → no signal | 🟢 | `test_scores.py` (4 tests), `test_refusals.py` |
| 3 | No exception reaches the host under ≥ 4 Langfuse failures | 🟢 | `test_never_raises.py`: 401, 429, 500, connect error, timeout, raising client |
| 4 | No latency measured or written | 🟢 | `test_no_latency_score_is_written` |
| 5 | `dry_run` opens no socket; bridge never contacts NeoMundi | 🟢 | recorder counts 0 |
| 6 | Suite offline, no key, no cost | 🟢 | 33 passed, 1 skipped, clean venv, `example.invalid` |
| 7 | Notebook runs end to end | 🟢 | executed with nbclient, outputs committed |
| 8 | No key, prompt, response or suspect phrase on the wire or in an outcome | 🟢 | `test_no_key_prompt_response_or_suspect_phrase_reaches_langfuse` |
| L1 | One NUMERIC score per measured signal, exact value | 🟢 | `test_each_measured_signal_…` |
| L2 | `request_id` + `measurement_version` on every score | 🟢 | `test_every_score_carries_…` |
| L3 | Summary score always written | 🟢 | `test_the_summary_score_…`, refusal tests |
| L4 | No trace → nothing written | 🟢 | `test_no_trace_means_nothing_is_written` |
| L5 | Composes with `langchain-neomundi` | 🟡 **summary only** | `test_composition.py` on the real handler, locally; skipped in CI |
| L6 | Deterministic score ids | 🟢 (client side) | `test_score_ids_are_deterministic_…`; server upsert unverified |

## 2. What was built, and what was deliberately not

One class, `NeoMundiLangfuseBridge`, and one result type, `WriteOutcome`.
It reads `neomundi` result types and calls Langfuse's own `create_score`.

Not built: any measurement, any NeoMundi call, any Langfuse tracing, any
aggregate score (`g_score`, `g_final`), any rescaling.

## 3. Findings, and how each was found

### 3a. Langfuse drops scores on an API error — found by the failure tests

Every failure mode left `write()` returning `queued` and no score on a
server. Not a bug; a delivery limit the bridge cannot see past. Documented
as LIMITATIONS §1 and shown on the wire by `examples/02_refusals.py`.

### 3b. Langfuse's `flush()` blocks 5–9 s while retrying — found by test durations

The suite took 38 s. `--durations` pointed at the failure tests; a direct
measurement gave `write()` 0.1 ms but `flush()` 5.6 s on HTTP 500 and 8.8 s
on a connection error. Langfuse behaviour, reached by the host's own
`flush()`. LIMITATIONS §2.

### 3c. langchain-neomundi's outcome does not carry the result — found by introspection

`langchain_neomundi.Outcome` has `request_id` but no `GovernResult`, so the
composed path can only write the summary. Confirmed on the real class by
`test_composition.py`, which also fails on purpose the day that changes.
The issue's proposed shape assumed full composition; this is the gap.

### 3d. Evaluator provenance keys — found by reading a captured response

The first draft forwarded `instrument` and `cause`, from memory of the
SDK's docstring. The captured production response spells them `evaluator`
and `reason`. Fixed before any test ran; the wire is the reference.

### 3e. The suite was green on its first run — so it was mutation-tested

Two deliberate defects (write unmeasured signals; put quality text in the
comment) each turned the relevant tests red: 3 and 1 failures. Reverted.

## 4. Cost

**0.00 CHF.** No real measurement. Budget for E2: about 0.18 CHF (six
observations at 0.030), plus a Langfuse target.

## 5. Next

1. **E2**: one guarded real measurement on the service account, written to a
   self-hosted Langfuse, scores read back by API. Needs the key in the
   window and a Langfuse target.
2. **`langchain-neomundi`**: expose the `GovernResult` on `Outcome`, then
   lift LIMITATIONS §3.
3. **Verify** whether Langfuse upserts scores on a repeated id.
