"""NeoMundi integration for Langfuse.

Write NeoMundi measurements back into Langfuse as scores on the trace a
developer already has open:

    from langfuse_neomundi import NeoMundiLangfuseBridge

    bridge = NeoMundiLangfuseBridge(langfuse)
    outcome = bridge.write(record)  # a neomundi TraceRecord or GovernResult
    print(outcome.status, outcome.signals_written)

This package **transports** measurements. It does not measure, does not
estimate a token count, does not contact the NeoMundi API, and does not
interpret a score: no threshold, no verdict, no "safe" label.

Read ``LIMITATIONS.md`` before drawing a conclusion from any score.
"""

from __future__ import annotations

__version__ = "0.1.0"

#: What this package was built and verified against. Evidence level E1:
#: offline only. No real Langfuse instance and no real measurement were
#: used -- see LIMITATIONS.md.
INTEGRATION_CONTRACT = {
    "neomundi_min": "2.1.0",
    "langfuse_min": "5.2.0",
    "verified_against": {
        "neomundi": "2.1.0",
        "langfuse": "5.2.0",
        "python": ("3.11", "3.12", "3.13"),
    },
    "langfuse_route": "POST /api/public/scores",
    "contacts_neomundi": False,
    "billed": False,
}

from .bridge import (  # noqa: E402 - contract first, by design
    DRY_RUN,
    NOT_WRITTEN_INTERNAL_ERROR,
    NOT_WRITTEN_NO_TRACE,
    NOT_WRITTEN_UNSUPPORTED_INPUT,
    QUEUED,
    SCORE_PREFIX,
    SUMMARY_SCORE,
    WRITE_OUTCOMES,
    NeoMundiLangfuseBridge,
    PlannedScore,
    WriteOutcome,
)

__all__ = [
    "__version__",
    "INTEGRATION_CONTRACT",
    "NeoMundiLangfuseBridge",
    "WriteOutcome",
    "PlannedScore",
    "WRITE_OUTCOMES",
    "QUEUED",
    "DRY_RUN",
    "NOT_WRITTEN_NO_TRACE",
    "NOT_WRITTEN_UNSUPPORTED_INPUT",
    "NOT_WRITTEN_INTERNAL_ERROR",
    "SCORE_PREFIX",
    "SUMMARY_SCORE",
]
