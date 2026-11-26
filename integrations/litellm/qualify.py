"""E2 probe for the LiteLLM integration: real completions through Ollama.

Driven by ``tools/qualify_e2.py``, which owns the budget, the account
guard, the re-read and the evidence. This file owns one thing: how to make
LiteLLM do real work whose token counts the **provider itself** published.

Two routes to the same local model, on purpose, because the difference is
the whole point of this integration:

* ``openai/<model>`` against Ollama's **OpenAI-compatible** endpoint —
  LiteLLM passes the provider's JSON body through as ``original_response``,
  so the provenance check can verify the count against what the provider
  actually sent. **These are the observations the E2 claim rests on.**
* ``ollama/<model>`` — LiteLLM's native Ollama route gives
  ``original_response = None``. The counts it reports may well be real, but
  **nothing in the logger can prove they are not its own tokenizer's**, so
  the integration refuses. Measured on litellm 1.111.0.

The second route is kept deliberately: a refusal on a *real* call is
evidence that the invariant holds in production, not only in the offline
suite. It costs nothing, because nothing is submitted.

🔴 **The environment must be scrubbed by the caller.** LiteLLM reads
provider credentials from the environment by design, and this workstation
carries ``OPENAI_API_KEY`` and ``MISTRAL_API_KEY``. Run through
``tools/probe.sh`` or ``env -i``, so the only reachable provider is the
local Ollama — otherwise a probe could leave for a paid provider, which
would be both a cost and a measurement nobody asked for.

🔴 **LiteLLM's success hook is asynchronous.** ``last_outcome`` is still
empty when ``completion()`` returns and lands roughly 0.2 s later, so each
phase waits for its own outcome instead of reading immediately. Measured;
a probe that read straight away would record ``None`` and look like a
silent failure.
"""

from __future__ import annotations

import os
import time
from typing import Any

# ASCII only: an `env -i` shell loses its UTF-8 console encoding on Windows
# and a non-ASCII probe name then crashes the report.
PROBE_NAME = "litellm -> ollama (OpenAI-compatible route, provider publishes its counts)"

MODEL = "llama3.2:3b"
OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

#: Ollama ignores the key, but LiteLLM's OpenAI handler requires one to be
#: present. Obviously not a credential.
DUMMY_KEY = "ollama-local-no-credential"

#: The hook is asynchronous; wait for it rather than guessing a sleep.
_OUTCOME_TIMEOUT_S = 10.0

QUESTIONS = (
    "In one sentence, what is the capital of France?",
    "In one sentence, what does the HTTP status code 404 mean?",
    "In one sentence, what is a SQL primary key?",
    "In one sentence, who wrote the play Hamlet?",
)


def _await_outcome(logger: Any, already: int) -> Any:
    """Wait until the asynchronous hook has recorded one more outcome."""
    deadline = time.monotonic() + _OUTCOME_TIMEOUT_S
    while time.monotonic() < deadline:
        if len(logger.outcomes) > already:
            return logger.last_outcome
        time.sleep(0.05)
    return None


def _record(phase: str, outcome: Any, *, expectation: str) -> dict[str, Any]:
    if outcome is None:
        return {
            "phase": phase,
            "expectation": expectation,
            "status": "no_outcome_recorded",
            "submitted": False,
            "reason": f"the asynchronous hook recorded nothing within {_OUTCOME_TIMEOUT_S}s",
        }
    return {
        "phase": phase,
        "expectation": expectation,
        "status": getattr(outcome, "status", None),
        "submitted": bool(getattr(outcome, "submitted", False)),
        "request_id": getattr(outcome, "request_id", None),
        "latency_ms": getattr(outcome, "latency_ms", None),
        "token_count": getattr(outcome, "token_count", None),
        "model_name": getattr(outcome, "model_name", None),
        "measurement_version": getattr(outcome, "measurement_version", None),
        "reason": getattr(outcome, "reason", None),
    }


def probe(client, *, submit: bool, metadata: dict) -> list[dict[str, Any]]:
    import litellm

    from litellm_neomundi import NeoMundiLogger

    logger = NeoMundiLogger(client, submit=submit, metadata=metadata)
    # Assigned, not appended: a previous logger of the same class would be
    # silently kept otherwise -- finding 4 of the mission report.
    litellm.callbacks = [logger]

    records: list[dict[str, Any]] = []
    try:
        # 1. The verifiable route. The provider's own usage block reaches
        #    the logger, so the provenance check can accept the count.
        for index, question in enumerate(QUESTIONS, start=1):
            before = len(logger.outcomes)
            litellm.completion(
                model=f"openai/{MODEL}",
                messages=[{"role": "user", "content": question}],
                api_base=f"{OLLAMA}/v1",
                api_key=DUMMY_KEY,
            )
            records.append(
                _record(
                    f"openai-compatible {index}",
                    _await_outcome(logger, before),
                    expectation="submitted, count verified against the provider's payload",
                )
            )

        # 2. The native route, kept as a real refusal. original_response is
        #    None there, so the count cannot be proven to be the
        #    provider's. Nothing is submitted; nothing is billed.
        before = len(logger.outcomes)
        litellm.completion(
            model=f"ollama/{MODEL}",
            messages=[{"role": "user", "content": "Name one European capital, one word."}],
            api_base=OLLAMA,
        )
        records.append(
            _record(
                "ollama native route",
                _await_outcome(logger, before),
                expectation="refused: the provider's payload is not reachable",
            )
        )

        # 3. A non-chat call, which must not be measured at all.
        before = len(logger.outcomes)
        try:
            litellm.embedding(
                model="ollama/nomic-embed-text", input=["qualification"], api_base=OLLAMA
            )
            records.append(
                _record(
                    "embedding",
                    _await_outcome(logger, before),
                    expectation="not measured: outside the declared scope",
                )
            )
        except Exception as exc:  # noqa: BLE001
            records.append(
                {
                    "phase": "embedding",
                    "expectation": "not measured: outside the declared scope",
                    "status": "host_call_failed",
                    "submitted": False,
                    "reason": f"the embedding call itself failed: {type(exc).__name__}",
                }
            )
    finally:
        litellm.callbacks = []

    return records
