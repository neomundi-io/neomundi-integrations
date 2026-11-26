"""Measure the model calls MLflow already traces, with NeoMundi.

Design rules, inherited from the ``neomundi`` SDK and from ``otel-neomundi``,
and not negotiable here:

1. **Nothing is invented.** A token count is submitted only when it was
   written by MLflow's ``openai`` autolog, which copies the provider's
   ``response.usage`` verbatim and leaves it absent when the provider is
   silent. Other writers put counts in the same attribute that are a zero
   fill (the Agents SDK tracer), a local estimate (LiteLLM's logger) or
   whatever a chat model computed (LangChain). A processor cannot tell those
   from a published count, so they are refused, naming the writer.
2. **Nothing escapes.** No exception raised by this processor, the SDK or the
   network propagates into the host application.
3. **Nothing is silent.** Every MLflow span that ends produces an
   :class:`Outcome` with an explicit status -- aggregates, tools and
   retrievers included.

Facts this file relies on were established by execution on mlflow-tracing
3.18.0. The probes are in ``neomundi-devrel/missions/006-mlflow``. See
``LIMITATIONS.md`` before drawing conclusions from a score.
"""

from __future__ import annotations

import hashlib
import queue
import threading
import time
import weakref
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import neomundi
from neomundi import NeoMundiClient, NeoMundiError

__all__ = [
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

# ------------------------------------------------------------------ statuses
#
# The vocabulary is the one otel-neomundi and openai-agents-neomundi already
# use, so an outcome means the same thing in every integration.

#: The model call was measured and the API acknowledged an observation.
SUBMITTED = "submitted"
#: ``sample_rate`` excluded this call. No socket was opened, nothing was billed.
SKIPPED_SAMPLING = "skipped_sampling"
#: ``submit=False``. Everything was checked locally; nothing left the process.
NOT_SUBMITTED_LOCAL_ONLY = "not_submitted_local_only"
#: The provider published no usage: ``mlflow.chat.tokenUsage`` is absent.
NOT_SUBMITTED_NO_TOKEN_COUNT = "not_submitted_no_token_count"
#: Usage is present but its ``total_tokens`` is missing, zero or not an
#: integer. Never rebuilt from input + output.
NOT_SUBMITTED_INVALID_TOKEN_COUNT = "not_submitted_invalid_token_count"
#: The span was written by an instrumentation whose counts cannot be traced
#: to the provider (Agents SDK tracer, LiteLLM, LangChain, a manual span...).
NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT = "not_submitted_unverified_token_count"
#: ``AGENT``, ``CHAIN``, ``WORKFLOW``, ``TASK``: work that contains model
#: calls already measured one by one. Measuring it too would count twice.
NOT_SUBMITTED_AGGREGATE_SPAN = "not_submitted_aggregate_span"
#: ``TOOL``, ``RETRIEVER``, ``EMBEDDING``...: work that is not a prompt and
#: a completion.
NOT_SUBMITTED_NOT_A_MODEL_CALL = "not_submitted_not_a_model_call"
#: ``UNKNOWN``, or a span type this release does not know.
NOT_SUBMITTED_UNSUPPORTED_SPAN = "not_submitted_unsupported_span"
#: The span has no usable start and end time.
NOT_SUBMITTED_NO_LATENCY = "not_submitted_no_latency"
#: The model call failed (span status ``ERROR``).
NOT_SUBMITTED_APPLICATION_ERROR = "not_submitted_application_error"
#: The submission queue was full. Dropped rather than blocking the host.
NOT_SUBMITTED_QUEUE_FULL = "not_submitted_queue_full"
#: The processor was shut down before this call ended.
NOT_SUBMITTED_PROCESSOR_CLOSED = "not_submitted_processor_closed"
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
    NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT,
    NOT_SUBMITTED_AGGREGATE_SPAN,
    NOT_SUBMITTED_NOT_A_MODEL_CALL,
    NOT_SUBMITTED_UNSUPPORTED_SPAN,
    NOT_SUBMITTED_NO_LATENCY,
    NOT_SUBMITTED_APPLICATION_ERROR,
    NOT_SUBMITTED_QUEUE_FULL,
    NOT_SUBMITTED_PROCESSOR_CLOSED,
    NOT_SUBMITTED_TRANSPORT_ERROR,
    NOT_SUBMITTED_INTERNAL_ERROR,
)

# --------------------------------------------------------------- span types

#: Model calls: one prompt, one completion.
MODEL_CALLS = frozenset({"LLM", "CHAT_MODEL"})
#: Spans that contain model calls rather than being one.
AGGREGATES = frozenset({"AGENT", "CHAIN", "WORKFLOW", "TASK"})
#: Work that is not a prompt and a completion.
NOT_MODEL_CALLS = frozenset(
    {"TOOL", "RETRIEVER", "RERANKER", "EMBEDDING", "GUARDRAIL", "PARSER", "MEMORY", "EVALUATOR"}
)

# ------------------------------------------------------------------ writers

#: ``mlflow.message.format`` values whose ``mlflow.chat.tokenUsage`` was
#: verified, by execution, to be the provider's own count copied verbatim and
#: left absent when the provider is silent. One, on mlflow-tracing 3.18.0.
VERIFIED_WRITERS = frozenset({"openai"})

_WRITER_REASONS = {
    "openai-agent": (
        "written by MLflow's OpenAI Agents SDK tracer, which copies "
        "GenerationSpanData.usage: the SDK zero-fills it (0/0/0) when the provider is "
        "silent. The inner 'openai' span of the same call is the one measured"
    ),
    "langchain": (
        "written by MLflow's LangChain autolog, which copies usage_metadata: a LangChain "
        "chat model fills it however it likes, an estimate included"
    ),
    None: (
        "the span names no writer (mlflow.message.format absent): LiteLLM's MLflow logger "
        "writes this way and stores its local estimate as if published, and a manual span's "
        "count has no provenance at all"
    ),
}

_USAGE = "mlflow.chat.tokenUsage"
_FORMAT = "mlflow.message.format"
_MODEL = "mlflow.llm.model"

#: Written on the MLflow trace after a submission (``mlflow.set_trace_tag``).
TAG_REQUEST_ID_PREFIX = "neomundi.request_id."
TAG_MEASUREMENT_VERSION = "neomundi.measurement_version"

#: Provenance of a submitted token count.
TOKENS_PROVIDER_USAGE_TOTAL = "provider_usage_total_via_mlflow_openai_autolog"


@dataclass(frozen=True)
class Outcome:
    """What the processor did about one finished MLflow span, and why.

    ``submitted`` is the only status under which ``request_id`` is set, and
    ``request_id`` is the only reliable correlation key against the API. It
    is also written on the MLflow trace as ``neomundi.request_id.<span_id>``
    when ``tag_traces`` is on, so the trace leads to its observation.
    """

    span_id: str
    status: str
    span_type: str | None = None
    span_name: str | None = None
    #: MLflow's trace id (``tr-...``).
    trace_id: str | None = None
    #: ``mlflow.message.format`` -- the instrumentation that wrote the span.
    writer: str | None = None
    submitted: bool = False
    request_id: str | None = None
    #: ``end_time_ns - start_time_ns`` of the MLflow span. Never measured here.
    latency_ms: float | None = None
    token_count: int | None = None
    #: How ``token_count`` was traced to the provider.
    token_source: str | None = None
    #: The model the provider says served the call, else the requested one.
    model_name: str | None = None
    measurement_version: str | None = None
    #: Whether the SDK verified ``measurement_version``. Results produced
    #: under different measurement versions are not comparable.
    measurement_version_verified: bool | None = None
    #: ``True`` once the request id is on the MLflow trace, ``False`` if
    #: MLflow refused the tag, ``None`` when no tag was attempted.
    trace_tagged: bool | None = None
    #: Human-readable reason, present on every non-``submitted`` status.
    reason: str | None = None
    #: Exception class name when one was caught. Never the message, which
    #: could carry a prompt or a key fragment.
    error_type: str | None = None

    def __bool__(self) -> bool:  # pragma: no cover - convenience only
        return self.submitted


@dataclass(frozen=True)
class _Job:
    span: Any
    base: dict[str, Any]
    token_count: int
    prompt: str | None
    response: str | None


# --------------------------------------------------------------------- helpers


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _attribute(span: Any, key: str) -> Any:
    try:
        return span.get_attribute(key)
    except Exception:  # noqa: BLE001 - an unreadable attribute is an absent one
        return None


def provider_total_tokens(usage: Any) -> tuple[int | None, str | None, str | None]:
    """Return ``(total, refusal_status, reason)`` from ``mlflow.chat.tokenUsage``.

    Only ``total_tokens`` is accepted, literally, as a positive ``int``. It is
    never rebuilt from ``input_tokens + output_tokens``: a provider that did
    not publish a total did not publish one.
    """
    if usage is None:
        return (
            None,
            NOT_SUBMITTED_NO_TOKEN_COUNT,
            "the provider published no usage (mlflow.chat.tokenUsage is absent); "
            "a count is never estimated in its place",
        )
    total = usage.get("total_tokens") if isinstance(usage, Mapping) else None
    if not _is_int(total) or total <= 0:
        return (
            None,
            NOT_SUBMITTED_INVALID_TOKEN_COUNT,
            "the provider's usage carries no positive integer total_tokens (partial, "
            "null or zero); refused rather than rebuilt from its parts",
        )
    return total, None, None


def span_latency_ms(span: Any) -> float | None:
    """``end_time_ns - start_time_ns`` as MLflow recorded them."""
    start = getattr(span, "start_time_ns", None)
    end = getattr(span, "end_time_ns", None)
    if not _is_int(start) or not _is_int(end):
        return None
    delta = end - start
    if delta < 0:
        # A wall-clock step between the two readings. Refused, not clamped.
        return None
    return delta / 1e6


def _text_of(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content] if content else []
    parts: list[str] = []
    if isinstance(content, (list, tuple)):
        for block in content:
            if isinstance(block, str) and block:
                parts.append(block)
            elif isinstance(block, Mapping):
                text = block.get("text")
                if block.get("type") in ("text", "input_text", "output_text") and isinstance(
                    text, str
                ):
                    parts.append(text)
    return parts


def prompt_text(inputs: Any) -> str | None:
    """The text the call sent: ``messages`` (Chat Completions) or ``input`` (Responses)."""
    if not isinstance(inputs, Mapping):
        return None
    parts: list[str] = []
    messages = inputs.get("messages")
    if isinstance(messages, (list, tuple)):
        for message in messages:
            if isinstance(message, Mapping):
                parts.extend(_text_of(message.get("content")))
    items = inputs.get("input")
    if isinstance(items, str):
        parts.extend(_text_of(items))
    elif isinstance(items, (list, tuple)):
        for item in items:
            if isinstance(item, Mapping):
                parts.extend(_text_of(item.get("content")))
    prompt = inputs.get("prompt")
    if isinstance(prompt, str):
        parts.extend(_text_of(prompt))
    return "\n\n".join(parts) if parts else None


def response_text(outputs: Any) -> str | None:
    """The text the provider returned, from the response MLflow recorded."""
    if not isinstance(outputs, Mapping):
        return None
    parts: list[str] = []
    for choice in outputs.get("choices") or ():
        if not isinstance(choice, Mapping):
            continue
        message = choice.get("message")
        if isinstance(message, Mapping):
            parts.extend(_text_of(message.get("content")))
        elif isinstance(choice.get("text"), str):
            parts.extend(_text_of(choice["text"]))
    for item in outputs.get("output") or ():
        if isinstance(item, Mapping) and item.get("type") == "message":
            parts.extend(_text_of(item.get("content")))
    return "\n\n".join(parts) if parts else None


def served_model(span: Any) -> str | None:
    try:
        outputs = span.outputs
    except Exception:  # noqa: BLE001
        outputs = None
    if isinstance(outputs, Mapping) and _as_str(outputs.get("model")):
        return outputs["model"]
    return _as_str(_attribute(span, _MODEL))


def _check_rate(value: Any) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError("sample_rate must be a number between 0.0 and 1.0")
    if not 0.0 <= float(value) <= 1.0:
        raise ValueError("sample_rate must be between 0.0 and 1.0")


# ------------------------------------------------------------------- processor

#: The processor :meth:`NeoMundiMlflowProcessor.install` last installed.
_ACTIVE: weakref.ReferenceType[NeoMundiMlflowProcessor] | None = None
_ACTIVE_LOCK = threading.Lock()


def _current_hooks() -> list[Any]:
    from mlflow.tracing.config import get_config

    return list(get_config().span_processors)


def _set_hooks(hooks: list[Any]) -> None:
    import mlflow

    mlflow.tracing.configure(span_processors=hooks)


class NeoMundiMlflowProcessor:
    """Measure every model call MLflow traces with its ``openai`` autolog.

    Install it **after** ``mlflow.set_tracking_uri(...)``::

        mlflow.set_tracking_uri("http://localhost:5000")
        mlflow.openai.autolog()
        processor = NeoMundiMlflowProcessor(api_key=..., base_url=...).install()

    It registers one function with MLflow's public hook,
    ``mlflow.tracing.configure(span_processors=[...])``, **appended** to the
    application's own. MLflow calls it for every span it ends. That hook
    survives ``set_experiment``, ``set_destination`` and ``disable/enable``;
    it is removed by ``mlflow.tracing.reset()``, by ``set_tracking_uri`` to a
    different URI (which calls ``reset``), and by the application calling
    ``configure(span_processors=...)`` itself. :attr:`installed` says whether
    it is still registered; :meth:`install` again puts it back.

    Submission runs on a worker thread: MLflow calls the hook on the host's
    own thread, inside the traced call. Call :meth:`flush` before reading
    outcomes or exiting.

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
    sample_rate:
        Fraction of model calls to submit, **deterministic in the span id**.
    submit:
        ``False`` runs every check locally and opens no socket.
    tag_traces:
        After a submission, write ``neomundi.request_id.<span_id>`` and
        ``neomundi.measurement_version`` on the MLflow trace.
    capture_prompt, capture_response:
        Send the call's text to the measurement API (never logged, never put
        on an outcome). Off: only lengths are lost, the count and latency stay.
    on_outcome:
        Called with each :class:`Outcome`. Its exceptions are swallowed.
    max_queue:
        Pending submissions. Beyond it a call is refused with
        ``not_submitted_queue_full`` rather than blocking the host.
    latency_wait_s:
        MLflow calls the hook just *before* it ends the span; the worker
        waits up to this long for the end time to be recorded.
    """

    def __init__(
        self,
        client: NeoMundiClient | None = None,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        sample_rate: float = 1.0,
        submit: bool = True,
        mode: str = "OBS",
        source_type: str = "llm",
        metadata: Mapping[str, Any] | None = None,
        tag_traces: bool = True,
        capture_prompt: bool = True,
        capture_response: bool = True,
        on_outcome: Callable[[Outcome], None] | None = None,
        max_outcomes: int = 256,
        max_queue: int = 1024,
        max_tracked_spans: int = 4096,
        latency_wait_s: float = 5.0,
    ) -> None:
        _check_rate(sample_rate)
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
        self._sample_rate = float(sample_rate)
        self._submit = bool(submit)
        self._mode = mode
        self._source_type = source_type
        self._metadata = dict(metadata or {})
        self._tag_traces = bool(tag_traces)
        self._capture_prompt = bool(capture_prompt)
        self._capture_response = bool(capture_response)
        self._on_outcome = on_outcome
        self._max_outcomes = max(1, int(max_outcomes))
        self._max_tracked = max(1, int(max_tracked_spans))
        self._latency_wait_s = max(0.0, float(latency_wait_s))

        self._lock = threading.Lock()
        self._outcomes: list[Outcome] = []
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._queue: queue.Queue[_Job | None] = queue.Queue(maxsize=max(1, int(max_queue)))
        self._worker: threading.Thread | None = None
        self._closed = False
        self._retired = False

        # MLflow validates a hook by ``__code__.co_argcount == 1`` -- but only
        # on the NEXT configure() call. A bound method (two arguments) is
        # accepted today and makes the application's next configure() raise.
        # A plain one-argument closure is the only safe shape. Probe 04.
        # It holds the processor strongly: while MLflow calls the hook, the
        # processor must exist, even if the caller kept no reference to it.
        processor = self

        def neomundi_mlflow_hook(span):
            processor._on_span_end(span)

        self._hook = neomundi_mlflow_hook

    # ---------------------------------------------------------------- public

    def install(self) -> NeoMundiMlflowProcessor:
        """Append this processor's hook to MLflow's span processors. Evicts nobody.

        One NeoMundi processor is active per process: installing a second one
        **retires** the previous one and removes its hook, so re-running a
        notebook cell does not bill every call twice. Installing the same
        instance again is a no-op -- or puts the hook back if MLflow dropped
        it (``mlflow.tracing.reset()``, a new tracking URI, the application's
        own ``configure``).
        """
        global _ACTIVE
        with _ACTIVE_LOCK:
            previous = _ACTIVE() if _ACTIVE is not None else None
            hooks = _current_hooks()
            if previous is not None and previous is not self:
                previous._retire()
                hooks = [h for h in hooks if h is not previous._hook]
            if self._hook not in hooks:
                hooks.append(self._hook)
            _set_hooks(hooks)
            _ACTIVE = weakref.ref(self)
            self._retired = False
        return self

    def uninstall(self) -> None:
        """Remove this processor's hook, keeping every other one."""
        global _ACTIVE
        with _ACTIVE_LOCK:
            hooks = _current_hooks()
            if self._hook in hooks:
                _set_hooks([h for h in hooks if h is not self._hook])
            if _ACTIVE is not None and _ACTIVE() is self:
                _ACTIVE = None
        self.flush()

    @property
    def installed(self) -> bool:
        """``True`` while MLflow still calls this processor. Read it; MLflow
        drops it silently on ``reset()`` and on a new tracking URI."""
        try:
            return (not self._retired) and self._hook in _current_hooks()
        except Exception:  # noqa: BLE001
            return False

    @property
    def retired(self) -> bool:
        """``True`` once another processor was installed in its place."""
        return self._retired

    def _retire(self) -> None:
        try:
            self.flush()
        finally:
            self._retired = True

    @property
    def outcomes(self) -> list[Outcome]:
        """The recorded outcomes, oldest first, capped at ``max_outcomes``."""
        with self._lock:
            return list(self._outcomes)

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
        _check_rate(value)
        self._sample_rate = float(value)

    def reset(self) -> None:
        """Forget recorded outcomes. Pending submissions are not cancelled."""
        with self._lock:
            self._outcomes.clear()
            self._seen.clear()

    def flush(self, timeout: float | None = 30.0) -> bool:
        """Wait until every queued call has an outcome. ``True`` if done."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while self._queue.unfinished_tasks:
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(0.005)
        return True

    def shutdown(self) -> None:
        """Flush, stop the worker, and refuse anything that ends afterwards."""
        try:
            self.flush()
            self._closed = True
            worker = self._worker
            if worker is not None and worker.is_alive():
                self._queue.put_nowait(None)
                worker.join(timeout=5.0)
        except Exception:  # noqa: BLE001 - rule 2
            pass

    # ------------------------------------------------------------- the hook

    def _on_span_end(self, span: Any) -> None:
        """Called by MLflow on the host's thread, just before the span ends."""
        if self._retired:
            return
        try:
            self._handle(span)
        except Exception as exc:  # noqa: BLE001 - rule 2
            try:
                self._record(
                    Outcome(
                        span_id=str(getattr(span, "span_id", None) or "unidentified"),
                        status=NOT_SUBMITTED_INTERNAL_ERROR,
                        reason="the processor failed; reported, never raised into the host",
                        error_type=type(exc).__name__,
                    )
                )
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------- internals

    def _claim(self, span_id: str) -> bool:
        with self._lock:
            if span_id in self._seen:
                return False
            self._seen[span_id] = None
            while len(self._seen) > self._max_tracked:
                self._seen.popitem(last=False)
            return True

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

    def _should_sample(self, span_id: str) -> bool:
        rate = self._sample_rate
        if rate >= 1.0:
            return True
        if rate <= 0.0:
            return False
        digest = hashlib.sha256(span_id.encode("utf-8")).digest()[:8]
        return int.from_bytes(digest, "big") / float(1 << 64) < rate

    def _handle(self, span: Any) -> None:
        span_id = _as_str(getattr(span, "span_id", None)) or f"unidentified-{id(span)}"
        if not self._claim(span_id):
            return
        span_type = getattr(span, "span_type", None)
        span_type = span_type if isinstance(span_type, str) else None
        writer = _as_str(_attribute(span, _FORMAT))
        base: dict[str, Any] = {
            "span_id": span_id,
            "span_type": span_type,
            "span_name": _as_str(getattr(span, "name", None)),
            "trace_id": _as_str(getattr(span, "trace_id", None)),
            "writer": writer,
        }

        if span_type in AGGREGATES:
            self._record(
                Outcome(
                    **base,
                    status=NOT_SUBMITTED_AGGREGATE_SPAN,
                    reason=f"a {span_type} span contains model calls that are measured one "
                    "by one; measuring it too would count them twice",
                )
            )
            return
        if span_type in NOT_MODEL_CALLS:
            self._record(
                Outcome(
                    **base,
                    status=NOT_SUBMITTED_NOT_A_MODEL_CALL,
                    reason=f"a {span_type} span is not a prompt and a completion; "
                    "measuring it would invent an interaction",
                )
            )
            return
        if span_type not in MODEL_CALLS:
            self._record(
                Outcome(
                    **base,
                    status=NOT_SUBMITTED_UNSUPPORTED_SPAN,
                    reason=f"span type {span_type!r} is not a model call this processor "
                    "measures (LLM, CHAT_MODEL)",
                )
            )
            return

        base["model_name"] = served_model(span)
        status = getattr(getattr(span, "status", None), "status_code", None)
        if str(getattr(status, "value", status)) == "ERROR":
            self._record(
                Outcome(
                    **base,
                    status=NOT_SUBMITTED_APPLICATION_ERROR,
                    reason="the model call failed (span status ERROR); a partial "
                    "measurement would be a fabricated observation",
                )
            )
            return
        if writer not in VERIFIED_WRITERS:
            reason = _WRITER_REASONS.get(
                writer,
                f"written by {writer!r}, an instrumentation whose token counts were not "
                "verified to be the provider's own",
            )
            self._record(
                Outcome(**base, status=NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT, reason=reason)
            )
            return

        token_count, refusal, reason = provider_total_tokens(_attribute(span, _USAGE))
        if token_count is None:
            self._record(Outcome(**base, status=refusal, reason=reason))
            return

        if not self._should_sample(span_id):
            self._record(
                Outcome(
                    **base,
                    status=SKIPPED_SAMPLING,
                    reason=f"excluded by sample_rate={self._sample_rate}",
                )
            )
            return

        base["token_count"] = token_count
        base["token_source"] = TOKENS_PROVIDER_USAGE_TOTAL
        prompt = response = None
        if self._submit:
            if self._capture_prompt:
                prompt = prompt_text(getattr(span, "inputs", None))
            if self._capture_response:
                response = response_text(getattr(span, "outputs", None))
        if self._closed:
            self._record(
                Outcome(
                    **base,
                    status=NOT_SUBMITTED_PROCESSOR_CLOSED,
                    reason="the processor was shut down; nothing was submitted",
                )
            )
            return
        job = _Job(span=span, base=base, token_count=token_count, prompt=prompt, response=response)
        self._ensure_worker()
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            self._record(
                Outcome(
                    **base,
                    status=NOT_SUBMITTED_QUEUE_FULL,
                    reason="the submission queue is full; dropped rather than blocking the host",
                )
            )

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._run_worker, name="neomundi-mlflow", daemon=True
            )
            self._worker.start()

    def _run_worker(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is None:
                    return
                self._finish(job)
            except Exception as exc:  # noqa: BLE001 - rule 2
                try:
                    self._record(
                        Outcome(
                            **job.base,  # type: ignore[union-attr]
                            status=NOT_SUBMITTED_INTERNAL_ERROR,
                            reason="the processor failed; reported, never raised into the host",
                            error_type=type(exc).__name__,
                        )
                    )
                except Exception:  # noqa: BLE001
                    pass
            finally:
                self._queue.task_done()

    def _wait_for_end(self, span: Any) -> None:
        deadline = time.monotonic() + self._latency_wait_s
        while getattr(span, "end_time_ns", None) is None and time.monotonic() < deadline:
            time.sleep(0.001)

    def _finish(self, job: _Job) -> None:
        base = dict(job.base)
        self._wait_for_end(job.span)
        latency = span_latency_ms(job.span)
        if latency is None:
            self._record(
                Outcome(
                    **base,
                    status=NOT_SUBMITTED_NO_LATENCY,
                    reason="the span has no usable start and end time; latency is observed "
                    "or absent, never assumed",
                )
            )
            return
        base["latency_ms"] = latency

        if not self._submit or self._client is None:
            self._record(
                Outcome(
                    **base,
                    status=NOT_SUBMITTED_LOCAL_ONLY,
                    reason="submit=False: nothing left the process",
                )
            )
            return

        metadata = dict(self._metadata)
        metadata.setdefault("client_trace_id", f"{base.get('trace_id')}/{base['span_id']}")
        try:
            result = self._client.govern(
                token_count=job.token_count,
                latency_ms=latency,
                prompt_length=len(job.prompt) if job.prompt is not None else None,
                response_length=len(job.response) if job.response is not None else None,
                model_name=base.get("model_name"),
                llm_response=job.response,
                llm_prompt=job.prompt,
                source_type=self._source_type,
                mode=self._mode,
                metadata=metadata,
            )
        except NeoMundiError as exc:
            self._record(
                Outcome(
                    **base,
                    status=NOT_SUBMITTED_TRANSPORT_ERROR,
                    reason="the measurement API could not be reached or refused the "
                    "submission; the host application was not affected",
                    error_type=type(exc).__name__,
                )
            )
            return

        request_id = getattr(result, "request_id", None)
        audit = getattr(result, "audit", None)
        version = getattr(audit, "measurement_version", None)
        verified = neomundi.API_CONTRACT.get("verified_measurement_versions", ())
        tagged = self._tag(base.get("trace_id"), base["span_id"], request_id, version)
        self._record(
            Outcome(
                **base,
                status=SUBMITTED,
                submitted=True,
                request_id=request_id,
                measurement_version=version,
                measurement_version_verified=(version in tuple(verified)) if version else None,
                trace_tagged=tagged,
            )
        )

    def _tag(
        self, trace_id: str | None, span_id: str, request_id: Any, version: Any
    ) -> bool | None:
        if not self._tag_traces or not trace_id or not isinstance(request_id, str):
            return None
        try:
            import mlflow

            mlflow.set_trace_tag(trace_id, f"{TAG_REQUEST_ID_PREFIX}{span_id}", request_id)
            if isinstance(version, str) and version:
                mlflow.set_trace_tag(trace_id, TAG_MEASUREMENT_VERSION, version)
            return True
        except Exception:  # noqa: BLE001 - rule 2: the store may refuse; reported
            return False

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(sample_rate={self._sample_rate}, "
            f"submit={self._submit}, installed={self.installed}, "
            f"submitted_count={self.submitted_count})"
        )
