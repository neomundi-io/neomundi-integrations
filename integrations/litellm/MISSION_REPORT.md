# Mission report — LiteLLM (mission 003)

Built in one autonomous window on 2026-11-26, following the DevRel mission
method. Evidence level reached: **E1**. Spend: **0.00 CHF**.

## Outcome in one line

One class, `NeoMundiLogger`, that measures every chat completion LiteLLM
routes — and the finding that **LiteLLM's usage normalisation hides a
silent provider instead of filling it**, which turned the integration's
central job from "forward the count" into "prove where the count came
from".

## Acceptance criteria against their evidence

Written before the implementation.

| # | Criterion | Evidence |
|---|---|---|
| 1 | Measured interaction persisted, re-read by `request_id` | ⚪ **not verifiable at E1** — no service key in the window |
| 2 | Unmeasurable → nothing submitted, nothing invented, reason given | `test_refusals.py`: recorder count 0 + explicit status |
| 3 | No exception reaches the host under ≥ 4 API failures | `test_never_raises.py`: 401, 402, 429, 500, 503, network, timeout, invalid JSON, broken client, malformed hook inputs, raising `on_outcome` |
| 4 | Latency is the host's call | `test_latency_is_the_host_call_not_the_measurement`: wire value == LiteLLM's delta, ≤ wall time |
| 5 | Sampling at zero / dry run opens no socket | recorder count 0, with and without a client |
| 6 | Suite offline, no key, no cost | 47 tests, `httpx.MockTransport` on both sides, `example.invalid` |
| 7 | Notebook runs end to end | executed with nbclient, outputs committed and read |
| 8 | No key, prompt or response in any outcome | `assert_no_secret` on outcomes in success, refusal and error paths |
| T1 | Silent provider refused (zero and estimate) | OpenAI-compatible `0/0/0`, Ollama estimate, sync stream estimate |
| T2 | Reported usage submitted, equal to provider's | OpenAI total, Ollama sum, sync stream usage chunk, async non-stream |
| T3 | Partial report not completed | `test_partial_report_is_not_completed_by_an_estimate` |
| T4 | Async stream refused with explicit reason | `test_async_stream_is_refused_because_nothing_can_be_verified` |
| T5 | Cache hit not submitted | hook-level (see finding 6) |
| T6 | Failed call → application error, host keeps its exception | real `InternalServerError` through `litellm.completion` |
| T7 | Declared cost off by default, positive only | three cases on the wire |
| T8 | One `litellm_call_id`, one submission | two hook calls → one request |
| T9 | Sampling deterministic | two runs of 40 ids decide identically |
| T10 | Async hook does not block the loop | ticker keeps running during a 0.5 s submission |
| T11 | Non-chat call not measured | `embedding` → `not_submitted_unsupported_call` |

## Defects and findings, and how each was found

1. **LiteLLM reports `Usage(0, 0, 0)` for a provider that publishes no
   usage, and a tokenizer estimate for Ollama and for streams.** Found by
   introspection with a local fake provider before any code was written.
   The mission brief assumed the opposite ("LiteLLM normalises exactly
   that"). This decided the design: a provenance check against
   `original_response`. LIMITATIONS §1.
2. **Async streams expose a coroutine as `original_response`.** Found by
   the same probe. Nothing to verify against → refused. LIMITATIONS §2.
3. **LiteLLM calls hooks with keyword arguments.** A probe logger with
   differently named parameters was never called on the async path
   (LiteLLM logged a `TypeError` and carried on). Parameter names are now
   part of the documented contract.
4. **Reassigning `litellm.callbacks` does not detach a logger, and a second
   instance of the same class is silently not registered.** Found because
   the suite went from 1 green to 20 red: every test after the first was
   being served by the first test's logger. For a user, a "removed" logger
   keeps billing. Led to the runtime kill switch (`sample_rate` setter) and
   LIMITATIONS §4.
5. **The wire body nests metrics under `raw_metrics`.** The first test
   draft read `body["token_count"]` and failed; the SDK's real request was
   read with `build_request_body` and the tests corrected. (Test defect,
   not code.)
6. **`litellm.Cache` needs `fastapi`**, which a clean install of this
   package does not carry. The end-to-end cache test failed with
   `ModuleNotFoundError`; the cache case is tested at the hook, with the
   `cache_hit` flag observed on the real kwargs.
7. **Both examples crashed on first run**: `ct_example_...` is not an
   accepted key prefix for the SDK. Found by running the deliverables, not
   by the suite.
8. **The notebook printed `litellm (see pip)`**: LiteLLM exposes no
   `__version__`. Found by reading the executed outputs.
9. **Suite green → mutation-tested.** Trusting LiteLLM's usage blindly
   turned 8 tests red; submitting on the event loop turned 1 red.

## Spend

**0.00 CHF.** No measurement was submitted to the production API.

## What was not done, and why

- **E2.** No `NEOMUNDI_VERIFICATION_KEY` in the window. Next step: the
  account guard, then ~5 observations through LiteLLM → Ollama (a free
  local model that reports its counts), re-read by `request_id` —
  ≈ 0.15 CHF.
- **Real providers.** Only mocked shapes (OpenAI-compatible, Ollama,
  Anthropic usage block) were exercised. Gemini, Bedrock, Cohere and
  others publish usage under other keys; the provenance check matches by
  key name and was not run against them.
- **LiteLLM Proxy end to end.** The proxy extras were not installed; the
  async hooks it uses are tested directly and through `acompletion`.
- **Platforms.** Only Python 3.12 on Linux ran in the window; CI covers
  the rest.
