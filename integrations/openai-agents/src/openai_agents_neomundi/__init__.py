"""Measure the model calls an OpenAI Agents SDK run makes, with NeoMundi.

One ``TracingProcessor``, added next to the processors already registered.
Each model call on the Responses API is measured with the provider's own
token count and the latency the SDK recorded. Aggregates, tool executions
and any span whose count cannot be traced to the provider are reported and
refused, with a named status.

See ``LIMITATIONS.md`` before drawing conclusions from a score.
"""

from .processor import (
    ANY_LLM_MODULE,
    NOT_SUBMITTED_AGGREGATE_SPAN,
    NOT_SUBMITTED_APPLICATION_ERROR,
    NOT_SUBMITTED_INTERNAL_ERROR,
    NOT_SUBMITTED_LOCAL_ONLY,
    NOT_SUBMITTED_NO_LATENCY,
    NOT_SUBMITTED_NO_PROVIDER_RESPONSE,
    NOT_SUBMITTED_NOT_A_MODEL_CALL,
    NOT_SUBMITTED_PROCESSOR_CLOSED,
    NOT_SUBMITTED_QUEUE_FULL,
    NOT_SUBMITTED_TRANSPORT_ERROR,
    NOT_SUBMITTED_UNSUPPORTED_SPAN,
    NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT,
    OUTCOMES,
    SKIPPED_SAMPLING,
    SUBMITTED,
    NeoMundiTraceProcessor,
    Outcome,
)
from .version import __version__

__all__ = [
    "__version__",
    "NeoMundiTraceProcessor",
    "Outcome",
    "OUTCOMES",
    "SUBMITTED",
    "SKIPPED_SAMPLING",
    "NOT_SUBMITTED_LOCAL_ONLY",
    "NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT",
    "NOT_SUBMITTED_NO_PROVIDER_RESPONSE",
    "NOT_SUBMITTED_AGGREGATE_SPAN",
    "NOT_SUBMITTED_NOT_A_MODEL_CALL",
    "NOT_SUBMITTED_UNSUPPORTED_SPAN",
    "NOT_SUBMITTED_NO_LATENCY",
    "NOT_SUBMITTED_APPLICATION_ERROR",
    "NOT_SUBMITTED_QUEUE_FULL",
    "NOT_SUBMITTED_PROCESSOR_CLOSED",
    "NOT_SUBMITTED_TRANSPORT_ERROR",
    "NOT_SUBMITTED_INTERNAL_ERROR",
    "ANY_LLM_MODULE",
]
