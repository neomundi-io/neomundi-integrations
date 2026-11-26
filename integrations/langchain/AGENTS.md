# Agent instructions

This repository is an integration package. It is **not** the measurement
engine, and it must never grow into one.

## Hard rules

1. **Never reimplement the SDK.** Latency measurement, the request
   contract, retries and submission belong to `neomundi`. If something is
   missing, the change belongs in the SDK, not here. Announce it rather
   than working around it.
2. **Never invent a measurement.** No estimated token count, no default
   numeric value, no inferred latency. A missing input produces a reported
   refusal, never a fabricated observation.
3. **Never let an exception reach the host chain.** `raise_errors=True`
   exists for this package's own tests and for debugging. The default must
   stay `False`, and `tests/test_never_raises.py` must stay green.
4. **Never interpret a measurement.** No threshold, no verdict, no
   `is_safe()`. That boundary is the SDK's and it is deliberate.
5. **Never commit a real key.** `tests/test_rag_and_contract.py` scans the
   tree for a live key prefix. Keep it passing.

## Tests

`pytest` must run offline, with no key and no cost. Every test goes through
`httpx.MockTransport`, and the base URL is `https://example.invalid`
(RFC 2606). A test that needs the network does not belong in the suite.

The real-execution proof is a separate, explicit script:
`scripts/real_execution_proof.py`. It spends money. Run the account guard
first, and never point it at a pilot account.

## Before claiming a capability

NeoMundi's public product architecture marks each capability 🟢 / 🟡 / 🔴
and forbids describing anything not marked 🟢 in the present tense. Check
it before editing the README. Note that this package changes the
"no framework hook, no auto-instrumentation" line — that document needs
updating alongside the first release.
