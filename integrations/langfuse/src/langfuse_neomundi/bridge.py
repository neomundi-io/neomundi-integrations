"""Write NeoMundi measurements back into Langfuse, as scores on the trace.

A **bridge**, not a second observer. Langfuse keeps the trace; NeoMundi
measures the interaction; this module carries the measurement's signals
onto the trace the developer already has open, with the NeoMundi
``request_id`` attached so the two records are navigable both ways.

Design rules, inherited from the ``neomundi`` SDK and not negotiable here:

1. **Nothing is invented.** A signal the server did not declare *measured*
   produces no numeric score. Not a zero, not a default: nothing. The
   summary score says which signals are missing and why.
2. **Nothing escapes.** No exception raised by this module, by Langfuse or
   by the network ever reaches the host application.
3. **Nothing is silent.** Every call returns a :class:`WriteOutcome` with an
   explicit status, also delivered through ``on_outcome``.

What this module never does: measure, estimate a token count, contact the
NeoMundi API, or forward prompt, response or suspect-phrase text.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from neomundi import COVERAGE_SIGNALS, GovernResult, MeasurementState, TraceRecord

__all__ = [
    "NeoMundiLangfuseBridge",
    "WriteOutcome",
    "PlannedScore",
    "WRITE_OUTCOMES",
    "QUEUED",
    "DRY_RUN",
    "NOT_WRITTEN_NO_TRACE",
    "NOT_WRITTEN_UNSUPPORTED_INPUT",
    "NOT_WRITTEN_INTERNAL_ERROR",
    "SUMMARY_SCORE",
    "SCORE_PREFIX",
]

#: Scores were handed to the Langfuse client. **Not** a delivery receipt:
#: Langfuse sends them in the background and, on an API error, logs and
#: drops them rather than raising. See LIMITATIONS.md §1.
QUEUED = "queued"
#: ``dry_run=True``. The scores were computed and returned; nothing was
#: handed to Langfuse and no socket was opened.
DRY_RUN = "dry_run"
#: No trace id was given and none is active. A score with no trace would
#: be orphaned, so nothing was written.
NOT_WRITTEN_NO_TRACE = "not_written_no_trace"
#: The input is not a NeoMundi measurement this bridge understands.
NOT_WRITTEN_UNSUPPORTED_INPUT = "not_written_unsupported_input"
#: A defect here, or in the Langfuse client. Reported, never raised.
NOT_WRITTEN_INTERNAL_ERROR = "not_written_internal_error"

WRITE_OUTCOMES = (
    QUEUED,
    DRY_RUN,
    NOT_WRITTEN_NO_TRACE,
    NOT_WRITTEN_UNSUPPORTED_INPUT,
    NOT_WRITTEN_INTERNAL_ERROR,
)

#: Every score name starts with this. Score names are a public contract:
#: changing one breaks every Langfuse dashboard built on it.
SCORE_PREFIX = "neomundi"
#: The one score written for every input, measured or not.
SUMMARY_SCORE = f"{SCORE_PREFIX}.measurement"

#: Values of the summary score. A closed vocabulary, so a Langfuse
#: categorical chart has a fixed set of bars.
_SUMMARY_NOT_SUBMITTED = "not_submitted"
_SUMMARY_NO_RESULT = "submitted_no_result"
_SUMMARY_UNKNOWN = "unknown"
_SUMMARY_KNOWN = ("complete", "partial", "not_attempted")

#: Why nothing numeric is written for an observation on which the server
#: attempted no check. The SDK documents both conventions this refuses to
#: transport: ``coverage`` is 1.0 when nothing was attempted, and a check
#: that never ran can carry a reassuring value such as ``coherence_score``
#: 1.0. An aggregate that was required and never attempted is not a value.
_NOT_ATTEMPTED = (
    "not_attempted: the server attempted no check on this observation, so any "
    "value here is a convention, not a measurement"
)


def _nothing_attempted(result: GovernResult | None) -> bool:
    """``True`` when the server says no check ran: status or count, either one."""
    measurement = result.measurement if result is not None else None
    if measurement is None:
        return False
    return measurement.status == "not_attempted" or measurement.checks_attempted == 0


#: Evaluator provenance keys that are safe to forward: identities and
#: fingerprints, never content.
#: Spelled as the server publishes them (read from a captured response,
#: not from a document).
_EVALUATOR_KEYS = (
    "evaluator",
    "model_used",
    "model_configured",
    "served_by",
    "status",
    "reason",
    "prompt_template_hash",
    "inference_config_hash",
    "config_fingerprint",
)


@dataclass(frozen=True)
class PlannedScore:
    """One score, exactly as it is handed to ``Langfuse.create_score``."""

    name: str
    value: float | str
    data_type: str
    score_id: str
    comment: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WriteOutcome:
    """What the bridge did with one measurement, and why.

    ``queued`` means handed to the Langfuse client, which is the strongest
    statement this side can make. Whether Langfuse persisted it can only be
    established by reading the trace back.
    """

    status: str
    trace_id: str | None = None
    observation_id: str | None = None
    request_id: str | None = None
    #: The signals written as numeric scores, in ``COVERAGE_SIGNALS`` order.
    signals_written: tuple[str, ...] = ()
    #: Signals present in the contract but not written, with the reason.
    signals_withheld: dict[str, str] = field(default_factory=dict)
    #: Every score computed, whether or not it was handed over.
    scores: tuple[PlannedScore, ...] = ()
    reason: str | None = None
    #: Exception class name when one was caught. Never the message, which
    #: could carry a key fragment or response text.
    error_type: str | None = None

    @property
    def written(self) -> bool:
        return self.status == QUEUED


@dataclass(frozen=True)
class _Normalised:
    request_id: str | None
    submitted: bool
    result: GovernResult | None
    reason_not_submitted: str | None
    source: str


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _normalise(measurement: Any) -> _Normalised | None:
    """Accept the three shapes a NeoMundi measurement arrives in.

    * ``neomundi.GovernResult`` -- from ``client.govern()``;
    * ``neomundi.TraceRecord`` -- from ``client.trace(...).close()``;
    * an outcome object from ``langchain-neomundi`` (duck-typed on
      ``status``, ``submitted`` and ``request_id``, so this package does
      not depend on it). That object carries no result, so only the
      summary can be written -- see LIMITATIONS.md §3.
    """
    if isinstance(measurement, GovernResult):
        return _Normalised(measurement.request_id, True, measurement, None, "govern_result")

    if isinstance(measurement, TraceRecord):
        submitted = bool(measurement.submitted)
        return _Normalised(
            measurement.request_id,
            submitted,
            measurement.result if submitted else None,
            None if submitted else f"trace outcome {measurement.outcome!r}",
            "trace_record",
        )

    status = getattr(measurement, "status", None)
    submitted = getattr(measurement, "submitted", None)
    if (
        isinstance(status, str)
        and isinstance(submitted, bool)
        and hasattr(measurement, "request_id")
    ):
        request_id = measurement.request_id
        return _Normalised(
            request_id if isinstance(request_id, str) else None,
            submitted,
            None,
            None if submitted else f"handler outcome {status!r}",
            "outcome",
        )
    return None


def _score_id(trace_id: str, request_id: str | None, name: str) -> str:
    """Deterministic in (trace, request, name).

    Writing the same measurement twice produces the same ids rather than
    two sets of scores. Whether Langfuse upserts on the id is its own
    behaviour; see LIMITATIONS.md §2.
    """
    seed = f"{trace_id}\x1f{request_id or '-'}\x1f{name}".encode()
    return hashlib.sha256(seed).hexdigest()[:32]


class NeoMundiLangfuseBridge:
    """Write NeoMundi measurements onto Langfuse traces as scores.

    Parameters
    ----------
    langfuse:
        The application's existing ``langfuse.Langfuse`` client. The bridge
        never builds one and never reads Langfuse credentials.
    dry_run:
        ``True`` computes the scores and returns them on the outcome
        without handing anything to Langfuse. No socket opens.
    attach_to_observation:
        When no ``observation_id`` is passed, attach to the currently active
        Langfuse observation instead of the trace as a whole.
    on_outcome:
        Called with each :class:`WriteOutcome`. Exceptions it raises are
        caught and never propagate.
    raise_errors:
        ``False`` (default) upholds rule 2. ``True`` re-raises, which is for
        this package's own tests and for debugging -- never for production.
    """

    def __init__(
        self,
        langfuse: Any,
        *,
        dry_run: bool = False,
        attach_to_observation: bool = False,
        on_outcome: Callable[[WriteOutcome], None] | None = None,
        max_outcomes: int = 256,
        raise_errors: bool = False,
    ) -> None:
        if langfuse is None and not dry_run:
            raise ValueError("Pass the application's Langfuse client, or dry_run=True.")
        self._langfuse = langfuse
        self._dry_run = bool(dry_run)
        self._attach_to_observation = bool(attach_to_observation)
        self._on_outcome = on_outcome
        self._max_outcomes = max(1, int(max_outcomes))
        self._raise_errors = bool(raise_errors)
        self._lock = threading.Lock()
        self._outcomes: list[WriteOutcome] = []

    # ---------------------------------------------------------------- state

    @property
    def outcomes(self) -> list[WriteOutcome]:
        with self._lock:
            return list(self._outcomes)

    @property
    def last_outcome(self) -> WriteOutcome | None:
        with self._lock:
            return self._outcomes[-1] if self._outcomes else None

    # ------------------------------------------------------------ public API

    def write(
        self,
        measurement: Any,
        *,
        trace_id: str | None = None,
        observation_id: str | None = None,
    ) -> WriteOutcome:
        """Write one measurement as scores. Never raises (unless asked to)."""
        try:
            outcome = self._write(measurement, trace_id, observation_id)
        except Exception as exc:  # noqa: BLE001 - rule 2
            if self._raise_errors:
                raise
            outcome = WriteOutcome(
                status=NOT_WRITTEN_INTERNAL_ERROR,
                trace_id=trace_id,
                observation_id=observation_id,
                reason="the bridge failed; the host application was not affected",
                error_type=type(exc).__name__,
            )
        self._record(outcome)
        return outcome

    def as_outcome_callback(self, *, trace_id: str | None = None) -> Callable[[Any], None]:
        """Return a callable for ``langchain-neomundi``'s ``on_outcome``.

        The handler's outcome carries the ``request_id`` but not the
        measurement result, so this writes the summary score only.
        """

        def _callback(outcome: Any) -> None:
            self.write(outcome, trace_id=trace_id)

        return _callback

    # ------------------------------------------------------------ internals

    def _record(self, outcome: WriteOutcome) -> None:
        with self._lock:
            self._outcomes.append(outcome)
            if len(self._outcomes) > self._max_outcomes:
                del self._outcomes[: len(self._outcomes) - self._max_outcomes]
        if self._on_outcome is not None:
            try:
                self._on_outcome(outcome)
            except Exception:  # noqa: BLE001 - rule 2 applies to callers too
                pass

    def _resolve_target(
        self, trace_id: str | None, observation_id: str | None
    ) -> tuple[str | None, str | None]:
        if trace_id is None and self._langfuse is not None:
            trace_id = self._langfuse.get_current_trace_id()
            if observation_id is None and self._attach_to_observation:
                observation_id = self._langfuse.get_current_observation_id()
        if not isinstance(trace_id, str) or not trace_id:
            return None, None
        return trace_id, observation_id if isinstance(observation_id, str) else None

    def _write(
        self, measurement: Any, trace_id: str | None, observation_id: str | None
    ) -> WriteOutcome:
        normalised = _normalise(measurement)
        if normalised is None:
            return WriteOutcome(
                status=NOT_WRITTEN_UNSUPPORTED_INPUT,
                trace_id=trace_id,
                observation_id=observation_id,
                reason=(
                    f"{type(measurement).__name__} is not a GovernResult, a TraceRecord "
                    "or a langchain-neomundi outcome"
                ),
            )

        trace_id, observation_id = self._resolve_target(trace_id, observation_id)
        if trace_id is None:
            return WriteOutcome(
                status=NOT_WRITTEN_NO_TRACE,
                request_id=normalised.request_id,
                reason="no trace_id was passed and no Langfuse trace is active",
            )

        scores, written, withheld = self._plan(normalised, trace_id)

        if self._dry_run:
            return WriteOutcome(
                status=DRY_RUN,
                trace_id=trace_id,
                observation_id=observation_id,
                request_id=normalised.request_id,
                signals_written=written,
                signals_withheld=withheld,
                scores=scores,
                reason="dry_run=True: nothing was handed to Langfuse",
            )

        for score in scores:
            self._langfuse.create_score(
                name=score.name,
                value=score.value,
                trace_id=trace_id,
                observation_id=observation_id,
                score_id=score.score_id,
                data_type=score.data_type,
                comment=score.comment,
                metadata=score.metadata,
            )
        return WriteOutcome(
            status=QUEUED,
            trace_id=trace_id,
            observation_id=observation_id,
            request_id=normalised.request_id,
            signals_written=written,
            signals_withheld=withheld,
            scores=scores,
        )

    def _plan(
        self, m: _Normalised, trace_id: str
    ) -> tuple[tuple[PlannedScore, ...], tuple[str, ...], dict[str, str]]:
        result = m.result
        base: dict[str, Any] = {"request_id": m.request_id, "source": m.source}
        if result is not None:
            base["measurement_version"] = result.audit.measurement_version
            base["normalizer_version"] = result.audit.normalizer_version

        scores: list[PlannedScore] = []
        written: list[str] = []
        withheld: dict[str, str] = {}

        for signal in COVERAGE_SIGNALS:
            reason = self._withhold_reason(m, signal)
            if reason is not None:
                withheld[signal] = reason
                continue
            value = getattr(result.quality, signal)  # type: ignore[union-attr]
            name = f"{SCORE_PREFIX}.{signal}"
            metadata = dict(base)
            metadata["measurement_state"] = MeasurementState.MEASURED.value
            provenance = self._provenance(result, signal)
            if provenance:
                metadata["evaluator"] = provenance
            scores.append(
                PlannedScore(
                    name=name,
                    value=float(value),
                    data_type="NUMERIC",
                    score_id=_score_id(trace_id, m.request_id, name),
                    comment=f"NeoMundi request_id {m.request_id}, server-declared measured",
                    metadata=metadata,
                )
            )
            written.append(signal)

        summary_value, summary_comment = self._summary(m)
        summary_meta = dict(base)
        summary_meta["signals_written"] = list(written)
        summary_meta["signals_withheld"] = dict(withheld)
        if result is not None and result.measurement is not None:
            if _nothing_attempted(result):
                summary_meta["coverage"] = None
                summary_meta["coverage_withheld"] = _NOT_ATTEMPTED
            else:
                summary_meta["coverage"] = result.measurement.coverage
            summary_meta["missing_required_signals"] = result.measurement.missing_required_signals
        if m.reason_not_submitted:
            summary_meta["not_submitted_because"] = m.reason_not_submitted
        scores.append(
            PlannedScore(
                name=SUMMARY_SCORE,
                value=summary_value,
                data_type="CATEGORICAL",
                score_id=_score_id(trace_id, m.request_id, SUMMARY_SCORE),
                comment=summary_comment,
                metadata=summary_meta,
            )
        )
        return tuple(scores), tuple(written), withheld

    @staticmethod
    def _withhold_reason(m: _Normalised, signal: str) -> str | None:
        """Why a signal gets no numeric score, or ``None`` to write it."""
        if not m.submitted:
            return "not submitted: nothing was measured"
        result = m.result
        if result is None:
            return "no measurement result on this input"
        if _nothing_attempted(result):
            # Checked BEFORE `measured_signals`: if the server attempted
            # nothing, a signal it still lists as measured is not one.
            return _NOT_ATTEMPTED
        state = result.measurement_state(signal)
        if state is not MeasurementState.MEASURED:
            # A conventional value such as coherence_score 1.0 for a check
            # that never ran is exactly what this refuses to transport.
            server_reason = result.unmeasured_reason(signal)
            return f"{state.value}: {server_reason}" if server_reason else state.value
        if result.quality is None:
            return "declared measured but the quality block is absent"
        value = getattr(result.quality, signal, None)
        if not _is_number(value):
            return "declared measured but the server published no number"
        return None

    @staticmethod
    def _provenance(result: GovernResult | None, signal: str) -> dict[str, Any]:
        if result is None:
            return {}
        entry = result.evaluator_of(signal) or {}
        return {k: entry[k] for k in _EVALUATOR_KEYS if k in entry and entry[k] is not None}

    @staticmethod
    def _summary(m: _Normalised) -> tuple[str, str]:
        if not m.submitted:
            return _SUMMARY_NOT_SUBMITTED, f"not measured: {m.reason_not_submitted}"
        if m.result is None:
            return (
                _SUMMARY_NO_RESULT,
                f"NeoMundi request_id {m.request_id}; the result was not available to "
                "the bridge, so no signal was written",
            )
        status = m.result.measurement.status if m.result.measurement else None
        value = status if status in _SUMMARY_KNOWN else _SUMMARY_UNKNOWN
        return value, f"NeoMundi request_id {m.request_id}"
