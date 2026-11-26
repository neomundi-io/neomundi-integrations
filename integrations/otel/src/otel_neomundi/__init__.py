"""Measure model calls an application already traces with OpenTelemetry.

One span processor, added next to the exporters the application already
runs. A span's token count is **never** submitted: it is written by an
instrumentation, not by the provider, and a span cannot prove where it came
from. The caller declares the provider's count; the span supplies the
latency it observed, the model and the trace identity, and the NeoMundi
``request_id`` is written back into the same trace.

See ``LIMITATIONS.md`` before drawing conclusions from a score.
"""

from .processor import (
    INTERACTION_SPAN_NAME,
    NOT_SUBMITTED_AMBIGUOUS_SPANS,
    NOT_SUBMITTED_APPLICATION_ERROR,
    NOT_SUBMITTED_INTERNAL_ERROR,
    NOT_SUBMITTED_INVALID_TOKEN_COUNT,
    NOT_SUBMITTED_LOCAL_ONLY,
    NOT_SUBMITTED_NO_GENAI_SPAN,
    NOT_SUBMITTED_NO_LATENCY,
    NOT_SUBMITTED_NO_TOKEN_COUNT,
    NOT_SUBMITTED_TRANSPORT_ERROR,
    NOT_SUBMITTED_UNSUPPORTED_OPERATION,
    OUTCOMES,
    SKIPPED_SAMPLING,
    SUBMITTED,
    Interaction,
    NeoMundiSpanProcessor,
    Outcome,
)
from .version import __version__

__all__ = [
    "__version__",
    "NeoMundiSpanProcessor",
    "Interaction",
    "Outcome",
    "OUTCOMES",
    "SUBMITTED",
    "SKIPPED_SAMPLING",
    "NOT_SUBMITTED_LOCAL_ONLY",
    "NOT_SUBMITTED_NO_TOKEN_COUNT",
    "NOT_SUBMITTED_INVALID_TOKEN_COUNT",
    "NOT_SUBMITTED_NO_GENAI_SPAN",
    "NOT_SUBMITTED_AMBIGUOUS_SPANS",
    "NOT_SUBMITTED_UNSUPPORTED_OPERATION",
    "NOT_SUBMITTED_NO_LATENCY",
    "NOT_SUBMITTED_APPLICATION_ERROR",
    "NOT_SUBMITTED_TRANSPORT_ERROR",
    "NOT_SUBMITTED_INTERNAL_ERROR",
    "INTERACTION_SPAN_NAME",
]
