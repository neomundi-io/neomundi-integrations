"""Measure the model calls MLflow already traces, with NeoMundi.

One function, appended to MLflow's public span-processor hook
(``mlflow.tracing.configure``). Each model call traced by MLflow's
``openai`` autolog is measured with the provider's own token count and the
latency MLflow recorded, and the NeoMundi ``request_id`` is written back on
the MLflow trace. Aggregates, tools, retrievers, and every span whose count
cannot be traced to the provider are reported and refused, with a named
status.

See ``LIMITATIONS.md`` before drawing conclusions from a score.
"""

from .processor import (
    NOT_SUBMITTED_AGGREGATE_SPAN,
    NOT_SUBMITTED_APPLICATION_ERROR,
    NOT_SUBMITTED_INTERNAL_ERROR,
    NOT_SUBMITTED_INVALID_TOKEN_COUNT,
    NOT_SUBMITTED_LOCAL_ONLY,
    NOT_SUBMITTED_NO_LATENCY,
    NOT_SUBMITTED_NO_TOKEN_COUNT,
    NOT_SUBMITTED_NOT_A_MODEL_CALL,
    NOT_SUBMITTED_PROCESSOR_CLOSED,
    NOT_SUBMITTED_QUEUE_FULL,
    NOT_SUBMITTED_TRANSPORT_ERROR,
    NOT_SUBMITTED_UNSUPPORTED_SPAN,
    NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT,
    OUTCOMES,
    SKIPPED_SAMPLING,
    SUBMITTED,
    TAG_MEASUREMENT_VERSION,
    TAG_REQUEST_ID_PREFIX,
    VERIFIED_WRITERS,
    NeoMundiMlflowProcessor,
    Outcome,
)
from .version import __version__

__all__ = [
    "__version__",
    "NeoMundiMlflowProcessor",
    "Outcome",
    "OUTCOMES",
    "SUBMITTED",
    "SKIPPED_SAMPLING",
    "NOT_SUBMITTED_LOCAL_ONLY",
    "NOT_SUBMITTED_NO_TOKEN_COUNT",
    "NOT_SUBMITTED_INVALID_TOKEN_COUNT",
    "NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT",
    "NOT_SUBMITTED_AGGREGATE_SPAN",
    "NOT_SUBMITTED_NOT_A_MODEL_CALL",
    "NOT_SUBMITTED_UNSUPPORTED_SPAN",
    "NOT_SUBMITTED_NO_LATENCY",
    "NOT_SUBMITTED_APPLICATION_ERROR",
    "NOT_SUBMITTED_QUEUE_FULL",
    "NOT_SUBMITTED_PROCESSOR_CLOSED",
    "NOT_SUBMITTED_TRANSPORT_ERROR",
    "NOT_SUBMITTED_INTERNAL_ERROR",
    "VERIFIED_WRITERS",
    "TAG_REQUEST_ID_PREFIX",
    "TAG_MEASUREMENT_VERSION",
]
