"""NeoMundi integration for LiteLLM.

Measure every chat completion that goes through LiteLLM -- in an
application or in the LiteLLM Proxy -- with the NeoMundi measurement API,
by attaching one custom logger:

    import litellm
    from litellm_neomundi import NeoMundiLogger

    litellm.callbacks = [NeoMundiLogger(api_key="ct_...", base_url="https://api.neomundi.io")]

This package **transports** measurements. It does not interpret them: no
threshold, no verdict, no "safe" label. That boundary belongs to the
``neomundi`` SDK and is kept here on purpose.

It also refuses to forward a token count it cannot trace back to the
provider's own payload: LiteLLM fills a silent provider's usage with ``0``
or a local estimate. Read ``LIMITATIONS.md`` before drawing a conclusion
from any score.
"""

from __future__ import annotations

__version__ = "0.1.0"

#: What this package was built and verified against.
#:
#: ``measurement_version`` is not restated here on purpose: the SDK owns it.
INTEGRATION_CONTRACT = {
    "neomundi_min": "2.1.0",
    "litellm_min": "1.111.0",
    "verified_against": {
        "neomundi": "2.1.0",
        "litellm": "1.111.0",
        "python": ("3.11", "3.12", "3.13"),
    },
    "route": "POST /v1/govern",
    "billed": True,
}

from .logger import (  # noqa: E402 - contract first, by design
    NOT_SUBMITTED_APPLICATION_ERROR,
    NOT_SUBMITTED_CACHE_HIT,
    NOT_SUBMITTED_INTERNAL_ERROR,
    NOT_SUBMITTED_LOCAL_ONLY,
    NOT_SUBMITTED_NO_LATENCY,
    NOT_SUBMITTED_TRANSPORT_ERROR,
    NOT_SUBMITTED_UNSUPPORTED_CALL,
    NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT,
    OUTCOMES,
    SKIPPED_SAMPLING,
    SUBMITTED,
    TOKENS_PROVIDER_SUM,
    TOKENS_PROVIDER_TOTAL,
    NeoMundiLogger,
    Outcome,
    provider_usage_numbers,
    verified_token_count,
)

__all__ = [
    "__version__",
    "INTEGRATION_CONTRACT",
    "NeoMundiLogger",
    "Outcome",
    "OUTCOMES",
    "SUBMITTED",
    "SKIPPED_SAMPLING",
    "NOT_SUBMITTED_LOCAL_ONLY",
    "NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT",
    "NOT_SUBMITTED_NO_LATENCY",
    "NOT_SUBMITTED_CACHE_HIT",
    "NOT_SUBMITTED_UNSUPPORTED_CALL",
    "NOT_SUBMITTED_APPLICATION_ERROR",
    "NOT_SUBMITTED_TRANSPORT_ERROR",
    "NOT_SUBMITTED_INTERNAL_ERROR",
    "TOKENS_PROVIDER_TOTAL",
    "TOKENS_PROVIDER_SUM",
    "provider_usage_numbers",
    "verified_token_count",
]
