"""OpenTelemetry span processor that measures traced model calls with NeoMundi.

Design rules, inherited from the ``neomundi`` SDK and not negotiable here:

1. **Nothing is invented.** A token count read from a span is **never**
   submitted. ``gen_ai.usage.*`` is written by an instrumentation, not by
   the provider, and a span cannot say where the number came from (see
   ``LIMITATIONS.md`` §1: one instrumentation option replaces a provider's
   published count with a tokenizer estimate, and the span looks the
   same). The count is the one **the caller declares** from its provider's
   response, exactly as ``NeoMundiClient.trace()`` takes it.
2. **Nothing escapes.** No exception raised by this processor, the SDK or
   the network ever propagates into the host application. An exception the
   *application* raises inside an interaction is its own, and is re-raised
   unchanged.
3. **Nothing is silent.** Every interaction ends with an :class:`Outcome`
   carrying an explicit status.

What the span provides, and the only things read from it: start and end
time (the latency the instrumentation observed), the operation, the
requested and served model, the provider name, the status, and the trace
identity. **Content attributes are never read**, whether or not the
instrumentation captured them.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import neomundi
from neomundi import NeoMundiClient, NeoMundiError
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor
from opentelemetry.trace import StatusCode

from .version import __version__

__all__ = [
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

#: The interaction was measured and the API acknowledged an observation.
SUBMITTED = "submitted"
#: ``sample_rate`` excluded this interaction. No socket, nothing billed.
SKIPPED_SAMPLING = "skipped_sampling"
#: ``submit=False``. Everything was checked locally; nothing left the process.
NOT_SUBMITTED_LOCAL_ONLY = "not_submitted_local_only"
#: The caller declared no token count. A span's count is never a substitute.
NOT_SUBMITTED_NO_TOKEN_COUNT = "not_submitted_no_token_count"
#: The declared count is not a positive integer.
NOT_SUBMITTED_INVALID_TOKEN_COUNT = "not_submitted_invalid_token_count"
#: No GenAI span ended inside the interaction: nothing was observed.
NOT_SUBMITTED_NO_GENAI_SPAN = "not_submitted_no_genai_span"
#: Several GenAI spans ended inside one interaction; which one is meant is not guessed.
NOT_SUBMITTED_AMBIGUOUS_SPANS = "not_submitted_ambiguous_spans"
#: The GenAI span records an operation that is not one prompt and one completion.
NOT_SUBMITTED_UNSUPPORTED_OPERATION = "not_submitted_unsupported_operation"
#: The span carries no usable start and end time.
NOT_SUBMITTED_NO_LATENCY = "not_submitted_no_latency"
#: The application raised, or the model call itself failed (span status ERROR).
NOT_SUBMITTED_APPLICATION_ERROR = "not_submitted_application_error"
#: The measurement API could not be reached, or refused the submission.
NOT_SUBMITTED_TRANSPORT_ERROR = "not_submitted_transport_error"
#: A defect in this processor. Reported, never raised into the host.
NOT_SUBMITTED_INTERNAL_ERROR = "not_submitted_internal_error"

OUTCOMES = (
    SUBMITTED,
    SKIPPED_SAMPLING,
    NOT_SUBMITTED_LOCAL_ONLY,
    NOT_SUBMITTED_NO_TOKEN_COUNT,
    NOT_SUBMITTED_INVALID_TOKEN_COUNT,
    NOT_SUBMITTED_NO_GENAI_SPAN,
    NOT_SUBMITTED_AMBIGUOUS_SPANS,
    NOT_SUBMITTED_UNSUPPORTED_OPERATION,
    NOT_SUBMITTED_NO_LATENCY,
    NOT_SUBMITTED_APPLICATION_ERROR,
    NOT_SUBMITTED_TRANSPORT_ERROR,
    NOT_SUBMITTED_INTERNAL_ERROR,
)

#: The span this processor adds to the trace around each interaction. It
#: carries ``neomundi.status`` and, when submitted, ``neomundi.request_id``.
INTERACTION_SPAN_NAME = "neomundi.interaction"
_ATTR_INTERACTION = "neomundi.interaction_id"

#: GenAI operations whose result is one prompt and one completion.
MEASURED_OPERATIONS = frozenset({"chat", "text_completion", "generate_content"})

#: Read from the span, and nothing else. ``gen_ai.system`` is the deprecated
#: name of ``gen_ai.provider.name`` and is still written by instrumentations
#: in use (H5, SCOPE).
_OPERATION = "gen_ai.operation.name"
_REQUEST_MODEL = "gen_ai.request.model"
_RESPONSE_MODEL = "gen_ai.response.model"
_PROVIDER = ("gen_ai.provider.name", "gen_ai.system")
#: A span is a GenAI span when it carries one of these.
_GENAI_MARKERS = (_OPERATION, *_PROVIDER)
#: Usage keys seen in the wild, old and new. Only their *presence* is
#: reported on the outcome; their values are never read.
_USAGE_PREFIXES = ("gen_ai.usage.", "llm.usage.")


@dataclass(frozen=True)
class Outcome:
    """What the processor did about one interaction, and why.

    ``submitted`` is the only status under which ``request_id`` is set, and
    ``request_id`` is the only reliable correlation key against the API.
    It is also written into the trace, on the ``neomundi.interaction`` span.
    """

    interaction_id: str
    status: str
    submitted: bool = False
    request_id: str | None = None
    #: ``end - start`` of the GenAI span, as the instrumentation observed it.
    latency_ms: float | None = None
    #: The count the caller declared. Never a span attribute.
    token_count: int | None = None
    #: Always ``"caller_declared"`` when a count was used.
    token_source: str | None = None
    #: The served model when the span says, else the requested one.
    model_name: str | None = None
    provider: str | None = None
    operation: str | None = None
    #: 32 and 16 hex digits: the trace and the GenAI span, for the backend.
    trace_id: str | None = None
    span_id: str | None = None
    #: The span carried ``gen_ai.usage.*``. Reported, never submitted.
    #: ``None`` when no single GenAI span was examined (no span, several).
    span_reported_usage: bool | None = None
    measurement_version: str | None = None
    #: Whether the SDK verified ``measurement_version``. Results produced
    #: under different measurement versions are not comparable.
    measurement_version_verified: bool | None = None
    #: Human-readable reason, present on every non-``submitted`` status.
    reason: str | None = None
    #: Exception class name when one was caught. Never the message, which
    #: could carry a prompt or a key fragment.
    error_type: str | None = None

    def __bool__(self) -> bool:  # pragma: no cover - convenience only
        return self.submitted


@dataclass(frozen=True)
class _Observed:
    """The part of a finished GenAI span this processor keeps. No content."""

    trace_id: int
    span_id: int
    start_ns: int | None
    end_ns: int | None
    error: bool
    operation: str | None
    request_model: str | None
    response_model: str | None
    provider: str | None
    reported_usage: bool


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _is_genai(attributes: Mapping[str, Any]) -> bool:
    return any(key in attributes for key in _GENAI_MARKERS)


def _observe(span: ReadableSpan) -> _Observed:
    attributes = span.attributes or {}
    provider = None
    for key in _PROVIDER:
        provider = _as_str(attributes.get(key))
        if provider:
            break
    status = getattr(span, "status", None)
    return _Observed(
        trace_id=span.context.trace_id,
        span_id=span.context.span_id,
        start_ns=span.start_time,
        end_ns=span.end_time,
        error=getattr(status, "status_code", None) is StatusCode.ERROR,
        operation=_as_str(attributes.get(_OPERATION)),
        request_model=_as_str(attributes.get(_REQUEST_MODEL)),
        response_model=_as_str(attributes.get(_RESPONSE_MODEL)),
        provider=provider,
        reported_usage=any(str(key).startswith(_USAGE_PREFIXES) for key in attributes),
    )


def _latency_ms(start_ns: Any, end_ns: Any) -> float | None:
    if not isinstance(start_ns, int) or not isinstance(end_ns, int):
        return None
    if isinstance(start_ns, bool) or isinstance(end_ns, bool):
        return None
    delta = end_ns - start_ns
    if delta < 0:
        # A wall-clock step between the two readings. Refused, not clamped.
        return None
    return delta / 1e6


def _valid_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


class Interaction:
    """One model call, as the application delimits it.

    Created by :meth:`NeoMundiSpanProcessor.interaction`. Use it as a
    context manager around the call, and declare the token count the
    provider published::

        with processor.interaction() as interaction:
            reply = client.chat.completions.create(model=..., messages=...)
            interaction.declare(token_count=reply.usage.total_tokens if reply.usage else None)
        print(interaction.outcome.status)

    The instrumentation's GenAI span becomes a child of the
    ``neomundi.interaction`` span opened here, which is how the two are
    joined -- by the trace itself, not by a guess.
    """

    def __init__(self, processor: NeoMundiSpanProcessor, metadata: Mapping[str, Any] | None):
        self._processor = processor
        self._metadata = dict(metadata or {})
        self.interaction_id = uuid.uuid4().hex
        self._observed: list[_Observed] = []
        self._span: Span | None = None
        self._token: object | None = None
        self._attached = False
        self._declared_count: Any = None
        self._declared = False
        self._prompt: Any = None
        self._response: Any = None
        self._enter_error: str | None = None
        self.outcome: Outcome | None = None

    # ------------------------------------------------------------ declare

    def declare(
        self,
        *,
        token_count: Any = None,
        prompt: Any = None,
        response: Any = None,
    ) -> None:
        """Declare what the provider published. Never raises.

        ``token_count`` must be the count **the provider** returned (for
        OpenAI-shaped APIs, ``reply.usage.total_tokens``). Pass ``None``
        when it published none: the interaction is then refused, which is
        the honest result. ``prompt`` and ``response`` are optional and, if
        given, are sent to the measurement API -- never logged, never put
        on a span.
        """
        self._declared = True
        self._declared_count = token_count
        self._prompt = prompt if isinstance(prompt, str) else None
        self._response = response if isinstance(response, str) else None

    # ------------------------------------------------------ span plumbing

    def _open(self) -> None:
        try:
            self._processor._expect(self)
            tracer = self._processor._tracer()
            self._span = tracer.start_span(
                INTERACTION_SPAN_NAME,
                attributes={_ATTR_INTERACTION: self.interaction_id},
            )
            self._token = otel_context.attach(trace.set_span_in_context(self._span))
            self._attached = self._processor._is_attached(self)
        except Exception as exc:  # noqa: BLE001 - rule 2
            self._enter_error = type(exc).__name__

    def _close_span(self, outcome: Outcome) -> None:
        try:
            if self._token is not None:
                otel_context.detach(self._token)
                self._token = None
        except Exception:  # noqa: BLE001 - rule 2
            pass
        try:
            self._processor._forget(self)
        except Exception:  # noqa: BLE001
            pass
        span = self._span
        if span is None:
            return
        try:
            span.set_attribute("neomundi.status", outcome.status)
            if outcome.request_id:
                span.set_attribute("neomundi.request_id", outcome.request_id)
            if outcome.measurement_version:
                span.set_attribute("neomundi.measurement_version", outcome.measurement_version)
            span.end()
        except Exception:  # noqa: BLE001 - rule 2
            pass

    def _finish(self, exc_type: Any) -> Outcome:
        try:
            outcome = self._processor._decide(self, exc_type)
        except Exception as exc:  # noqa: BLE001 - rule 2
            outcome = Outcome(
                interaction_id=self.interaction_id,
                status=NOT_SUBMITTED_INTERNAL_ERROR,
                reason="the processor failed; reported, never raised into the host",
                error_type=type(exc).__name__,
            )
        self.outcome = outcome
        self._close_span(outcome)
        self._processor._record(outcome)
        return outcome

    # ----------------------------------------------------- context protocol

    def __enter__(self) -> Interaction:
        self._open()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._finish(exc_type)
        return False  # the application's own exception is never swallowed

    async def __aenter__(self) -> Interaction:
        self._open()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        # The SDK is synchronous. Submitting on the event loop would stall it
        # for the whole round trip, so the decision runs in a worker thread.
        # The context token was attached on this task, so it is detached here.
        try:
            if self._token is not None:
                otel_context.detach(self._token)
                self._token = None
        except Exception:  # noqa: BLE001
            pass
        try:
            await asyncio.to_thread(self._finish, exc_type)
        except Exception:  # noqa: BLE001 - rule 2
            pass
        return False


class NeoMundiSpanProcessor(SpanProcessor):
    """Measure traced model calls with NeoMundi, from inside an OTel pipeline.

    Add it to the tracer provider the application already uses, next to its
    exporters -- it replaces nothing::

        provider.add_span_processor(NeoMundiSpanProcessor(client))

    It does nothing on its own: a span's token count cannot be traced to the
    provider, so no span is ever turned into a measurement by itself. An
    interaction is measured when the application delimits it with
    :meth:`interaction` and declares the provider's count.

    .. warning::
       **Every submitted observation is a billed measurement.** There is no
       trace-only route on the NeoMundi API. Start with ``submit=False``,
       which needs no key and opens no socket.

    Parameters
    ----------
    client:
        An existing :class:`neomundi.NeoMundiClient`. When omitted, one is
        built from ``api_key`` and ``base_url``. Neither is needed with
        ``submit=False``.
    tracer_provider:
        The provider this processor is added to, used to open the
        ``neomundi.interaction`` span. Defaults to the global one.
    sample_rate:
        Fraction of interactions to submit, **deterministic in the trace id**.
    submit:
        ``False`` runs every check locally and opens no socket.
    on_outcome:
        Called with each :class:`Outcome`. Its exceptions are swallowed.
    """

    def __init__(
        self,
        client: NeoMundiClient | None = None,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        tracer_provider: Any = None,
        sample_rate: float = 1.0,
        submit: bool = True,
        mode: str = "OBS",
        source_type: str = "llm",
        metadata: Mapping[str, Any] | None = None,
        on_outcome: Callable[[Outcome], None] | None = None,
        max_outcomes: int = 256,
        max_tracked_spans: int = 4096,
    ) -> None:
        if not isinstance(sample_rate, (int, float)) or isinstance(sample_rate, bool):
            raise TypeError("sample_rate must be a number between 0.0 and 1.0")
        if not 0.0 <= float(sample_rate) <= 1.0:
            raise ValueError("sample_rate must be between 0.0 and 1.0")
        if client is not None and (api_key or base_url):
            raise ValueError("Pass either a client or api_key/base_url, not both.")
        if client is None and (api_key or base_url):
            if not api_key or not base_url:
                raise ValueError("Pass both api_key and base_url. The SDK never assumes a host.")
            client = NeoMundiClient(api_key=api_key, base_url=base_url)
        if client is None and submit:
            raise ValueError(
                "Pass either an existing client, or both api_key and base_url -- "
                "or submit=False to check everything locally without a key."
            )
        self._client = client
        self._tracer_provider = tracer_provider
        self._sample_rate = float(sample_rate)
        self._submit = bool(submit)
        self._mode = mode
        self._source_type = source_type
        self._metadata = dict(metadata or {})
        self._on_outcome = on_outcome
        self._max_outcomes = max(1, int(max_outcomes))
        self._max_tracked = max(1, int(max_tracked_spans))

        self._lock = threading.Lock()
        #: interaction id -> Interaction, between __enter__ and on_start.
        self._expected: dict[str, Interaction] = {}
        #: span id -> the Interaction it belongs to (the interaction span and
        #: every descendant that has started and not yet ended).
        self._owners: dict[int, Interaction] = {}
        self._outcomes: list[Outcome] = []

    # ---------------------------------------------------------------- state

    @property
    def outcomes(self) -> list[Outcome]:
        """The recorded outcomes, oldest first, capped at ``max_outcomes``."""
        with self._lock:
            return list(self._outcomes)

    @property
    def last_outcome(self) -> Outcome | None:
        with self._lock:
            return self._outcomes[-1] if self._outcomes else None

    @property
    def submitted_count(self) -> int:
        """How many observations were actually billed."""
        with self._lock:
            return sum(1 for outcome in self._outcomes if outcome.submitted)

    @property
    def sample_rate(self) -> float:
        return self._sample_rate

    @sample_rate.setter
    def sample_rate(self, value: float) -> None:
        """The kill switch: ``processor.sample_rate = 0.0`` stops all billing."""
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise TypeError("sample_rate must be a number between 0.0 and 1.0")
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError("sample_rate must be between 0.0 and 1.0")
        self._sample_rate = float(value)

    def interaction(self, *, metadata: Mapping[str, Any] | None = None) -> Interaction:
        """Delimit one model call. See :class:`Interaction`."""
        return Interaction(self, metadata)

    # ------------------------------------------------------- SpanProcessor
    #
    # Called by the SDK on the application's thread, inside span start and
    # end. Constant time, no I/O, and nothing escapes.

    def on_start(self, span: Span, parent_context: Any = None) -> None:
        try:
            attributes = span.attributes or {}
            interaction_id = attributes.get(_ATTR_INTERACTION)
            with self._lock:
                if isinstance(interaction_id, str) and interaction_id in self._expected:
                    owner = self._expected.pop(interaction_id)
                    self._owners[span.context.span_id] = owner
                    return
                parent = span.parent
                if parent is None:
                    return
                owner = self._owners.get(parent.span_id)
                if owner is not None and len(self._owners) < self._max_tracked:
                    self._owners[span.context.span_id] = owner
        except Exception:  # noqa: BLE001 - rule 2
            pass

    def on_end(self, span: ReadableSpan) -> None:
        try:
            with self._lock:
                owner = self._owners.get(span.context.span_id)
                if (
                    owner is None
                    or owner._span is not None
                    and (owner._span.get_span_context().span_id == span.context.span_id)
                ):
                    return
                del self._owners[span.context.span_id]
            if _is_genai(span.attributes or {}):
                owner._observed.append(_observe(span))
        except Exception:  # noqa: BLE001 - rule 2
            pass

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

    # ------------------------------------------------------------ internals

    def _tracer(self) -> Any:
        provider = self._tracer_provider or trace.get_tracer_provider()
        return provider.get_tracer("otel_neomundi", __version__)

    def _expect(self, interaction: Interaction) -> None:
        with self._lock:
            self._expected[interaction.interaction_id] = interaction

    def _is_attached(self, interaction: Interaction) -> bool:
        with self._lock:
            pending = self._expected.pop(interaction.interaction_id, None)
            return pending is None  # on_start consumed it: we are on that provider

    def _forget(self, interaction: Interaction) -> None:
        with self._lock:
            self._expected.pop(interaction.interaction_id, None)
            for span_id in [k for k, v in self._owners.items() if v is interaction]:
                del self._owners[span_id]

    def _should_sample(self, trace_id: int) -> bool:
        rate = self._sample_rate
        if rate >= 1.0:
            return True
        if rate <= 0.0:
            return False
        digest = hashlib.sha256(f"{trace_id:032x}".encode("ascii")).digest()[:8]
        return int.from_bytes(digest, "big") / float(1 << 64) < rate

    def _record(self, outcome: Outcome) -> None:
        with self._lock:
            self._outcomes.append(outcome)
            if len(self._outcomes) > self._max_outcomes:
                del self._outcomes[: len(self._outcomes) - self._max_outcomes]
        if self._on_outcome is not None:
            try:
                self._on_outcome(outcome)
            except Exception:  # noqa: BLE001 - rule 2 applies to callers too
                pass

    def _decide(self, interaction: Interaction, exc_type: Any) -> Outcome:
        base: dict[str, Any] = {"interaction_id": interaction.interaction_id}

        if interaction._enter_error is not None:
            return Outcome(
                **base,
                status=NOT_SUBMITTED_INTERNAL_ERROR,
                reason="the interaction span could not be opened; nothing was observed",
                error_type=interaction._enter_error,
            )
        if exc_type is not None:
            return Outcome(
                **base,
                status=NOT_SUBMITTED_APPLICATION_ERROR,
                reason="the application raised inside the interaction; a partial "
                "measurement would be a fabricated observation",
                error_type=getattr(exc_type, "__name__", None),
            )
        if not interaction._attached:
            return Outcome(
                **base,
                status=NOT_SUBMITTED_NO_GENAI_SPAN,
                reason="this processor never saw the interaction span start: either it "
                "is not attached to the tracer provider the interaction used (pass "
                "tracer_provider= or add it there), or that provider's sampler dropped "
                "the trace",
            )

        observed = list(interaction._observed)
        if not observed:
            return Outcome(
                **base,
                status=NOT_SUBMITTED_NO_GENAI_SPAN,
                reason="no GenAI span ended inside the interaction: is the model "
                "client instrumented, on this tracer provider?",
            )
        if len(observed) > 1:
            return Outcome(
                **base,
                status=NOT_SUBMITTED_AMBIGUOUS_SPANS,
                reason=f"{len(observed)} GenAI spans ended inside one interaction; "
                "which call the declared count belongs to is not guessed",
            )

        span = observed[0]
        base.update(
            trace_id=f"{span.trace_id:032x}",
            span_id=f"{span.span_id:016x}",
            model_name=span.response_model or span.request_model,
            provider=span.provider,
            operation=span.operation,
            span_reported_usage=span.reported_usage,
        )

        if span.operation not in MEASURED_OPERATIONS:
            return Outcome(
                **base,
                status=NOT_SUBMITTED_UNSUPPORTED_OPERATION,
                reason=f"GenAI operation {span.operation!r} is not one prompt and one completion",
            )
        if span.error:
            return Outcome(
                **base,
                status=NOT_SUBMITTED_APPLICATION_ERROR,
                reason="the model call failed (span status ERROR); there is no "
                "complete interaction to measure",
            )
        if not self._should_sample(span.trace_id):
            return Outcome(
                **base,
                status=SKIPPED_SAMPLING,
                reason=f"excluded by sample_rate={self._sample_rate}",
            )

        latency = _latency_ms(span.start_ns, span.end_ns)
        if latency is None:
            return Outcome(
                **base,
                status=NOT_SUBMITTED_NO_LATENCY,
                reason="the GenAI span has no usable start and end time; latency is "
                "observed or absent, never assumed",
            )
        base["latency_ms"] = latency

        count = interaction._declared_count
        if count is None:
            reason = "no token count was declared by the caller"
            if span.reported_usage:
                reason += (
                    "; the span carries gen_ai.usage.* but a span attribute cannot be "
                    "traced to the provider, so it is never submitted -- declare the "
                    "count the provider returned"
                )
            return Outcome(**base, status=NOT_SUBMITTED_NO_TOKEN_COUNT, reason=reason)
        if not _valid_count(count):
            return Outcome(
                **base,
                status=NOT_SUBMITTED_INVALID_TOKEN_COUNT,
                reason=f"the declared token count must be a positive integer, got a "
                f"{type(count).__name__}",
            )
        base["token_count"] = count
        base["token_source"] = "caller_declared"

        if not self._submit or self._client is None:
            return Outcome(
                **base,
                status=NOT_SUBMITTED_LOCAL_ONLY,
                reason="submit=False: nothing left the process",
            )

        prompt = interaction._prompt
        response = interaction._response
        metadata = dict(self._metadata)
        metadata.update(interaction._metadata)
        metadata.setdefault("client_trace_id", f"{span.trace_id:032x}-{span.span_id:016x}")
        try:
            result = self._client.govern(
                token_count=count,
                latency_ms=latency,
                prompt_length=len(prompt) if prompt is not None else None,
                response_length=len(response) if response is not None else None,
                model_name=base["model_name"],
                llm_response=response,
                llm_prompt=prompt,
                source_type=self._source_type,
                mode=self._mode,
                metadata=metadata,
            )
        except NeoMundiError as exc:
            return Outcome(
                **base,
                status=NOT_SUBMITTED_TRANSPORT_ERROR,
                reason="the measurement API could not be reached or refused the "
                "submission; the host application was not affected",
                error_type=type(exc).__name__,
            )

        audit = getattr(result, "audit", None)
        version = getattr(audit, "measurement_version", None)
        verified = neomundi.API_CONTRACT.get("verified_measurement_versions", ())
        return Outcome(
            **base,
            status=SUBMITTED,
            submitted=True,
            request_id=getattr(result, "request_id", None),
            measurement_version=version,
            measurement_version_verified=(version in tuple(verified)) if version else None,
        )

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(sample_rate={self._sample_rate}, "
            f"submit={self._submit}, submitted_count={self.submitted_count})"
        )
