"""NeoMundi integration for LangChain.

Measure the runtime behaviour of a LangChain application with the NeoMundi
measurement API, by attaching one callback handler:

    from langchain_neomundi import NeoMundiCallbackHandler

    handler = NeoMundiCallbackHandler(
        api_key="ct_...",
        base_url="https://api.neomundi.io",
    )
    answer = chain.invoke(question, config={"callbacks": [handler]})
    print(handler.last_outcome)

This package **transports** measurements. It does not interpret them: no
threshold, no verdict, no "safe" label. That boundary belongs to the
``neomundi`` SDK and is kept here on purpose.

Read ``KNOWN_LIMITATIONS.md`` before drawing a conclusion from any score.
"""

from __future__ import annotations

__version__ = "0.1.0"

#: What this package was built and verified against.
#:
#: ``neomundi_min`` is the first SDK release that exposes ``client.trace()``,
#: which this integration drives. ``langchain_core_min`` is the first 1.x
#: core whose callback surface was introspected for this handler.
#:
#: ``measurement_version`` is not restated here on purpose: the SDK owns it,
#: and this package reads ``neomundi.API_CONTRACT`` at runtime so the two can
#: never drift apart silently.
INTEGRATION_CONTRACT = {
    "neomundi_min": "2.1.0",
    "langchain_core_min": "1.0.0",
    "verified_against": {
        "neomundi": "2.1.0",
        "langchain_core": "1.8.5",
        "python": ("3.11", "3.12", "3.13"),
    },
    "route": "POST /v1/govern",
    "billed": True,
}

from .callbacks import (  # noqa: E402 - contract first, by design
    NOT_SUBMITTED_APPLICATION_ERROR,
    NOT_SUBMITTED_INTERNAL_ERROR,
    NOT_SUBMITTED_LOCAL_ONLY,
    NOT_SUBMITTED_NO_TOKEN_COUNT,
    NOT_SUBMITTED_TRANSPORT_ERROR,
    OUTCOMES,
    SKIPPED_SAMPLING,
    SUBMITTED,
    NeoMundiCallbackHandler,
    Outcome,
)

__all__ = [
    "__version__",
    "INTEGRATION_CONTRACT",
    "NeoMundiCallbackHandler",
    "Outcome",
    "OUTCOMES",
    "SUBMITTED",
    "SKIPPED_SAMPLING",
    "NOT_SUBMITTED_NO_TOKEN_COUNT",
    "NOT_SUBMITTED_APPLICATION_ERROR",
    "NOT_SUBMITTED_LOCAL_ONLY",
    "NOT_SUBMITTED_TRANSPORT_ERROR",
    "NOT_SUBMITTED_INTERNAL_ERROR",
]
