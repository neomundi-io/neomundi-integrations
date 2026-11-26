"""E2 probe for the Langfuse integration: scores written, then read back.

Driven by ``tools/qualify_e2.py``, which owns the budget, the account
guard, the re-read of the NeoMundi observation and the evidence. This file
owns one thing: making a real measurement land as real scores on a real
Langfuse trace, and **reading them back from Langfuse**.

🔴 **Why the read-back is the whole point here.** Every other integration
in this library proves E2 by re-reading its observation from the NeoMundi
API. This one has a second destination, and ``WriteOutcome.status ==
"queued"`` means only *handed to the Langfuse client* -- the bridge says so
itself. Until a score comes back from the Langfuse server, nothing is
established. So each phase that writes is followed by
``api.scores.get_many(trace_id=...)``, and the record says how many of the
expected score names actually came back.

The host is what a Langfuse user runs: the real ``langfuse`` client, a real
observation, ``NeoMundiLangfuseBridge`` on top.

⚠ **Test data only, deliberately bland.** A capital-city question and a
one-word answer. The prompt and the response are written to a third-party
SaaS by the host application's own tracing; nothing about this run should
be worth reading.

🔴 **Credentials come from the environment, never from an argument.**
``LANGFUSE_PUBLIC_KEY``, ``LANGFUSE_SECRET_KEY`` and ``LANGFUSE_BASE_URL``
live in the Windows USER scope beside ``NEOMUNDI_VERIFICATION_KEY``. They
are never printed, never written into the evidence, and never passed on a
command line.
"""

from __future__ import annotations

import os
import time
from typing import Any

PROBE_NAME = "langfuse-neomundi bridge -> Langfuse (scores read back from the server)"

#: The six scores the bridge writes. Five numeric signals and one
#: categorical summary; the summary is always written, the signals only
#: when the server declared them measured.
EXPECTED_SCORES = (
    "neomundi.measurement",
    "neomundi.stability_score",
    "neomundi.coherence_score",
    "neomundi.factual_hallucination_score",
    "neomundi.semantic_instability_score",
    "neomundi.semantic_risk",
)

#: Langfuse ingests asynchronously. Poll rather than guess a sleep: a fixed
#: delay is either too short on a slow day or wasted on a fast one.
_READBACK_TIMEOUT_S = 120.0
_READBACK_INTERVAL_S = 3.0

QUESTIONS = (
    ("What is the capital of France?", "Paris."),
    ("What is the capital of Japan?", "Tokyo."),
)


def _langfuse_client():
    """Build the Langfuse client, or say precisely what is missing."""
    public = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    secret = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
    host = os.environ.get("LANGFUSE_BASE_URL", "").strip()
    missing = [
        name
        for name, value in (
            ("LANGFUSE_PUBLIC_KEY", public),
            ("LANGFUSE_SECRET_KEY", secret),
            ("LANGFUSE_BASE_URL", host),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"missing environment variables: {', '.join(missing)}")

    from langfuse import Langfuse

    return Langfuse(public_key=public, secret_key=secret, host=host)


def _read_scores_back(langfuse: Any, trace_id: str) -> dict[str, Any]:
    """Poll Langfuse until its scores for this trace appear, or time out."""
    deadline = time.monotonic() + _READBACK_TIMEOUT_S
    names: list[str] = []
    last_error: str | None = None
    while time.monotonic() < deadline:
        try:
            page = langfuse.api.scores.get_many(trace_id=trace_id, limit=100)
            names = sorted({getattr(s, "name", "") for s in (page.data or [])})
            # 🔴 Wait for ALL of them, not for the first.
            #
            # The first run of this probe stopped as soon as one score
            # appeared and recorded "2 of 6" and "1 of 6" as if that were
            # the result. Langfuse ingests asynchronously and writes the
            # six scores over several seconds; a free re-read minutes later
            # found 6/6 on both traces. The integration was never at fault.
            # A poll that stops at the first arrival turns a complete write
            # into a partial-looking one, which is a fabricated finding.
            if all(name in names for name in EXPECTED_SCORES):
                break
        except Exception as exc:  # noqa: BLE001 - type only, never the message
            last_error = type(exc).__name__
        time.sleep(_READBACK_INTERVAL_S)

    expected = [n for n in EXPECTED_SCORES if n in names]
    return {
        "langfuse_trace_id": trace_id,
        "scores_read_back": len(names),
        "expected_scores_read_back": len(expected),
        "score_names_read_back": names,
        "readback_error_type": last_error,
        # The claim this probe exists to support. `queued` is not
        # persistence, and a PARTIAL read-back is not persistence either:
        # all six must come back, or the record says how many did.
        "persisted_in_langfuse": len(expected) == len(EXPECTED_SCORES),
        "expected_scores_total": len(EXPECTED_SCORES),
    }


def probe(client, *, submit: bool, metadata: dict) -> list[dict[str, Any]]:
    from langfuse_neomundi import (
        NOT_WRITTEN_NO_TRACE,
        NOT_WRITTEN_UNSUPPORTED_INPUT,
        NeoMundiLangfuseBridge,
    )

    records: list[dict[str, Any]] = []
    last_record: Any = None
    langfuse = _langfuse_client() if submit else None
    bridge = NeoMundiLangfuseBridge(langfuse, dry_run=not submit)

    try:
        # ---- the billed phases: a real measurement, written and read back --
        for index, (question, answer) in enumerate(QUESTIONS, start=1):
            phase = f"measure + write {index}"
            if not submit:
                outcome = bridge.write(None)
                records.append(
                    {
                        "phase": phase,
                        "expectation": "dry run: scores computed, nothing handed to Langfuse",
                        "status": "not_submitted_local_only",
                        "submitted": False,
                        "write_status": getattr(outcome, "status", None),
                        "reason": "no NeoMundi client in a dry run, and no socket opened",
                    }
                )
                continue

            with langfuse.start_as_current_observation(name=f"qualify-{index}") as span:  # noqa: F841
                trace_id = langfuse.get_current_trace_id()
                with client.trace(model_name="qualification-probe", metadata=metadata) as trace:
                    trace.record(prompt=question, response=answer, token_count=12)
                record = trace.close()
                outcome = bridge.write(record)

            last_record = record
            langfuse.flush()
            readback = _read_scores_back(langfuse, trace_id)
            records.append(
                {
                    "phase": phase,
                    "expectation": "submitted to NeoMundi, scores written to Langfuse and read back",
                    "status": "submitted"
                    if getattr(record, "submitted", False)
                    else "not_submitted",
                    "submitted": bool(getattr(record, "submitted", False)),
                    "request_id": getattr(record, "request_id", None),
                    "latency_ms": getattr(record, "latency_ms", None),
                    "token_count": getattr(record, "token_count", None),
                    "measurement_version": getattr(record, "measurement_version", None),
                    "write_status": getattr(outcome, "status", None),
                    "signals_written": getattr(outcome, "signals_written", None),
                    **readback,
                }
            )

        # ---- the refusals: real calls, nothing written, nothing billed -----
        if last_record is not None:
            orphan = bridge.write(last_record)
            records.append(
                {
                    "phase": "no active trace",
                    "expectation": "refused: a valid measurement, written outside any "
                    "observation, would be an orphaned score",
                    "status": getattr(orphan, "status", None),
                    "submitted": False,
                    "reason": "the measurement is valid; there is simply nothing to "
                    "attach it to, and nothing is re-billed to find that out",
                }
            )

        unsupported = bridge.write({"not": "a measurement"})
        records.append(
            {
                "phase": "unsupported input",
                "expectation": "refused: not a NeoMundi measurement this bridge understands",
                "status": getattr(unsupported, "status", None),
                "submitted": False,
                "reason": "the input is not a TraceRecord or a GovernResult",
            }
        )
        for record_ in records[-2:]:
            if record_["status"] not in (
                NOT_WRITTEN_NO_TRACE,
                NOT_WRITTEN_UNSUPPORTED_INPUT,
                "dry_run",
            ):
                record_["unexpected"] = True
    finally:
        if langfuse is not None:
            langfuse.flush()

    return records
