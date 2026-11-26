# Mission report — first DevRel integration

**Mission:** build and prove a real NeoMundi integration with LangChain.
**Date:** 2026-11-25 / 26
**Spend:** 0.18 CHF of measurement (6 billed observations). LLM free (local Ollama).
**Status:** delivered, PR #1 open, CI green on the whole matrix.

This mission doubles as the end-to-end acceptance test of the DevRel system
itself, so the report records how the work actually went — including what
went wrong — rather than only what shipped.

---

## 1. Acceptance criteria, defined before implementation

The ten criteria were fixed in the architecture dossier *before* any code
was written. Each is verified by a command, not by an opinion.

| | Criterion | Status | Where |
|---|---|---|---|
| R1 | A measured interaction produces a persisted observation, re-read from the API | ✅ | `evidence/`: 6 submitted, **6/6 read back HTTP 200** |
| R2 | A provider with no token count submits nothing, invents nothing | ✅ | `test_refusals.py`, and end to end on a real chain |
| R3 | No exception ever reaches the host chain | ✅ | `test_never_raises.py`: 6 API failures + injected defect + broken sink |
| R4 | Reported latency is the provider call's, not the measurement's | ✅ | `test_measured.py::test_latency_is_measured_not_declared` |
| R5 | `sample_rate=0` opens no socket and debits nothing | ✅ | `test_refusals.py`, request count asserted at 0 |
| R6 | A RAG chain's documents reach the measured observation | ✅ | payload-level assertion on a **real** LCEL chain |
| R7 | The whole suite runs offline, no key, no cost | ✅ | CI matrix green with no secret configured |
| R8 | The notebook runs end to end on a fresh machine from the README alone | ✅ | `notebooks/quickstart.ipynb`, executed, 0 error cells |
| R9 | An unverified `measurement_version` warns without blocking | ✅ | `test_rag_and_contract.py`, plus the real 3.5.0 case pinned |
| R10 | No key, prompt or response in any log, outcome or error | ✅ | hygiene tests + tree scan for a live key prefix |

**54 offline tests**, green on Python 3.11 / 3.12 / 3.13 and on Linux,
Windows and macOS. `ruff check` and `ruff format --check` clean.

## 2. What was built, and what was deliberately not

One class — `NeoMundiCallbackHandler` — driving the SDK's existing
`client.trace()`. Latency measurement, the request contract, retries and
submission all stayed in `neomundi`. The adapter adds exactly four things
the SDK cannot know about: LangChain's hook lifecycle, token extraction
from `usage_metadata`, RAG context from the retriever, and sampling.

Not built, on purpose: no evaluator, no threshold, no verdict, no
reimplementation of anything the SDK already owns, and no new orchestration.

## 3. Three defects found, and how

Worth recording, because two of them were found by the *deliverables*
rather than by the tests — which is an argument for keeping notebooks and
examples in scope rather than treating them as decoration.

### 3a. RAG context never arrived — found by the notebook

The first implementation matched retrieved documents to an LLM run by
walking up `parent_run_id`. In a real LCEL chain that can never work: the
retriever is not an ancestor of the model, it sits in a sibling subtree.

```
root
├── parallel
│   └── "context" branch
│       └── retriever      <- documents produced here
└── model                  <- measurement opened here
```

**The unit test passed anyway**, because it asserted only that
`handler._documents` had captured something — not that `rag_context`
reached the submitted payload. The notebook printed `rag_context : null`
and exposed it.

Fixed by keying the lookup on the shared **root**. The weak test was
replaced with a payload-level assertion, and
`test_retriever_is_not_an_ancestor_of_the_model_run` now pins the ancestry
fact that justifies the design.

### 3b. The refusals example demonstrated the wrong refusal — found by reading its output

`examples/02_refusals.py` claimed to show API failures (401/402/429,
unreachable) but used a provider reporting no usage. The handler therefore
refused *before* any network contact, and every case printed
`not_submitted_no_token_count`. The example taught the wrong lesson while
appearing to work.

Fixed with a usage-reporting model for those cases, and locked by
`test_examples.py`, which asserts that each claimed section shows its own
distinct status.

### 3c. The minimal example died on an optional provider — found by CI

`examples/01_minimal.py` imported `langchain_ollama` at module level. The
local environment had it installed for the execution proof, so this was
invisible; a clean environment — which is what a developer following the
README has — got `ModuleNotFoundError` before the example could report a
missing key.

Fixed by checking the key first, importing the provider lazily with an
actionable message, and declaring an `examples` extra that CI installs.
Both failure paths are now tested.

A fourth, smaller one: the CI wheel check used a bash heredoc, which is a
parse error in PowerShell on `windows-latest`. Every test passed there;
only the verification step was red. Moved to `scripts/check_wheel.py`.

## 4. Ecosystem findings

Two things learned about the surrounding product, reported rather than
silently worked around.

**Production serves `measurement_version 3.5.0`.** The SDK verifies only
`3.3.0` and `3.3.1`, deliberately — its own `docs/COMPATIBILITY_350.md`
says *"DO NOT add 3.5.0 to `verified_measurement_versions` yet."* So the
handler's drift warning fires on every real production use today. That is
correct behaviour, documented as limitation 1, and
`test_production_version_350_is_currently_unverified_and_warns` turns red
when the SDK qualifies 3.5.0.

**This integration contradicts a public document.** NeoMundi's product
architecture lists *"no framework hook, no auto-instrumentation"* as a
current limit, and marks TRACE 🟡. Its §8 forbids describing in the
present tense a capability not marked 🟢. **That document must be updated
alongside the first release**, in another repository — flagged in the PR,
not changed unilaterally.

Separately, and outside this mission's scope: the SDK's deployed wire
contract parity check currently fails, and its workflow is path-filtered
so it cannot detect the drift. Recorded for a follow-up mission.

## 5. Cost

| Item | Amount |
|---|---|
| Measurement API | 6 observations × 0.030 CHF = **0.18 CHF** |
| LLM | 0 — local Ollama `llama3.2:3b` |
| CI | GitHub Actions minutes only |
| Total | **0.18 CHF** |

Every observation was produced on the **service verification account**,
with `verification_account_guard.py` authorising it beforehand, and marked
`role=devrel-verify` / `excluded_from=Study2_Stage1_Stage2_Stage3`. No
pilot account was touched. The guard exists precisely because that went
wrong once before, on 2026-11-21.

The dry-run path (`--dry-run`, `submit=False`) was used first and cost
nothing, which is the pattern the README recommends to developers.

## 6. What this says about the DevRel system

What worked: fixing acceptance criteria before implementation made the
RAG defect undeniable rather than debatable; reusing the SDK's own test
tooling (`httpx.MockTransport`, the fake-key convention, a real production
fixture) removed most setup work; and producing the notebook and examples
as first-class deliverables found two defects the test suite missed.

What to carry forward: a local environment that has extra packages
installed will hide dependency defects, so a clean-environment check
belongs in every mission; and a test that asserts internal state instead
of observable output can stay green over a real bug.

## 7. Next

Not done here, each a separate decision:

- **PyPI publication.** The name is free; publishing is deliberate.
- **Making the repository public.** The only irreversible step in this
  mission, so it stays a decision rather than a default.
- **Updating `PRODUCT_ARCHITECTURE.md`**, which belongs to another
  repository.
- **The contract parity defect** in the SDK, as its own mission.
