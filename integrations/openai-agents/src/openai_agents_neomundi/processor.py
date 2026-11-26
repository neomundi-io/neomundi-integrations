"""A TracingProcessor that measures the model calls an agent run makes.

Design rules, inherited from the ``neomundi`` SDK and not negotiable here:

1. **Nothing is invented.** Only a token count the provider published on
   its own ``Response`` object is submitted. The SDK's span ``usage`` dicts
   are zero-filled when the provider is silent, and on the ``LitellmModel``
   route they can carry a local tokenizer estimate. A processor cannot tell
   either from a published count, so those dicts are never read for a count.
2. **Nothing escapes.** No exception raised by this processor, the SDK or the
   network propagates into the agent run.
3. **Nothing is silent.** Every span the SDK reports produces an
   :class:`Outcome` with an explicit status.

Facts this file relies on were established by execution on openai-agents
0.26.0. The probes are in ``neomundi-devrel/missions/005-openai-agents``.
See ``LIMITATIONS.md`` before drawing conclusions from a score.
"""

from __future__ import annotations

import hashlib
import queue
import sys
import threading
import time
import weakref
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import neomundi
from agents.tracing import TracingProcessor
from neomundi import NeoMundiClient, NeoMundiError

__all__ = [
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

#: The model call was measured and the API acknowledged an observation.
SUBMITTED = "submitted"
#: ``sample_rate`` excluded this call. No socket was opened, nothing was billed.
SKIPPED_SAMPLING = "skipped_sampling"
#: ``submit=False``. Everything was checked locally; nothing left the process.
NOT_SUBMITTED_LOCAL_ONLY = "not_submitted_local_only"
#: The count cannot be traced to the provider: a Chat Completions or
#: LiteLLM generation span, a missing or partial ``usage``, or a route that
#: rebuilds the ``Response`` object is loaded.
NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT = "not_submitted_unverified_token_count"
#: The response span carries no provider object: the run does not record
#: sensitive data (``trace_include_sensitive_data=False``).
NOT_SUBMITTED_NO_PROVIDER_RESPONSE = "not_submitted_no_provider_response"
#: A turn or task span: a sum of model calls already measured one by one.
NOT_SUBMITTED_AGGREGATE_SPAN = "not_submitted_aggregate_span"
#: A tool execution, an agent step, a handoff, a guardrail: no model call.
NOT_SUBMITTED_NOT_A_MODEL_CALL = "not_submitted_not_a_model_call"
#: Speech, transcription or an unknown span type: not a text model call.
NOT_SUBMITTED_UNSUPPORTED_SPAN = "not_submitted_unsupported_span"
#: The span has no usable start and end time.
NOT_SUBMITTED_NO_LATENCY = "not_submitted_no_latency"
#: The model call failed, or the response did not complete.
NOT_SUBMITTED_APPLICATION_ERROR = "not_submitted_application_error"
#: The submission queue was full. Dropped rather than blocking the agent.
NOT_SUBMITTED_QUEUE_FULL = "not_submitted_queue_full"
#: The processor was shut down before this call ended.
NOT_SUBMITTED_PROCESSOR_CLOSED = "not_submitted_processor_closed"
#: The measurement API could not be reached, or refused the submission.
NOT_SUBMITTED_TRANSPORT_ERROR = "not_submitted_transport_error"
#: A defect in this processor. Reported, never raised into the agent.
NOT_SUBMITTED_INTERNAL_ERROR = "not_submitted_internal_error"

OUTCOMES = (
    SUBMITTED,
    SKIPPED_SAMPLING,
    NOT_SUBMITTED_LOCAL_ONLY,
    NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT,
    NOT_SUBMITTED_NO_PROVIDER_RESPONSE,
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

#: The one class in openai-agents 0.26.0, besides ``OpenAIResponsesModel``,
#: that opens a response span -- and it fills it with a ``Response`` rebuilt
#: by ``model_validate`` from any-llm's conversion. Once it is loaded, a
#: response span's origin can no longer be established.
ANY_LLM_MODULE = "agents.extensions.models.any_llm_model"

#: Provenance of a submitted token count.
TOKENS_PROVIDER_RESPONSE_TOTAL = "provider_response_usage_total"

_RESPONSE = "ResponseSpanData"
_GENERATION = "GenerationSpanData"
_AGGREGATES = frozenset({"TaskSpanData", "TurnSpanData"})
_NOT_MODEL_CALLS = frozenset(
    {
        "AgentSpanData",
        "FunctionSpanData",
        "HandoffSpanData",
        "GuardrailSpanData",
        "CustomSpanData",
        "MCPListToolsSpanData",
        "SpeechGroupSpanData",
    }
)


@dataclass(frozen=True)
class Outcome:
    """What the processor did about one finished span, and why.

    ``submitted`` is the only status under which ``request_id`` is set, and
    ``request_id`` is the only reliable correlation key against the API. The
    API does not persist ``metadata``, so the join between an observation
    and a step of the agent loop is ``(trace_id, span_id) -> request_id``,
    kept here, on the caller's side.
    """

    span_id: str
    status: str
    span_type: str | None = None
    trace_id: str | None = None
    submitted: bool = False
    request_id: str | None = None
    #: ``ended_at - started_at`` as the Agents SDK recorded them on the span.
    latency_ms: float | None = None
    token_count: int | None = None
    #: How ``token_count`` was traced to the provider.
    token_source: str | None = None
    #: The model the provider says served the call (``response.model``).
    model_name: str | None = None
    #: The agent whose loop made this call, or the agent/tool this span is.
    agent_name: str | None = None
    #: For a tool span, the tool's name. Never its input or output.
    tool_name: str | None = None
    #: Tools the model asked for in this response. Names only.
    tool_calls: tuple[str, ...] = ()
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
class _Job:
    base: dict[str, Any]
    token_count: int
    latency_ms: float
    prompt: str | None
    response: str | None


# --------------------------------------------------------------------- helpers


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def span_latency_ms(span: Any) -> float | None:
    """``ended_at - started_at`` as the SDK recorded them. Never measured here."""
    start = _parse_time(getattr(span, "started_at", None))
    end = _parse_time(getattr(span, "ended_at", None))
    if start is None or end is None:
        return None
    try:
        value = (end - start).total_seconds() * 1000.0
    except (TypeError, ValueError, OverflowError):
        return None
    if value < 0:
        # A wall-clock step between the two readings. Refused, not clamped.
        return None
    return value


def provider_total_tokens(response: Any) -> tuple[int | None, str | None]:
    """Return ``(total_tokens, refusal_reason)`` from the provider's ``Response``.

    Only ``response.usage.total_tokens`` is accepted, literally, as a
    positive ``int``. It is never rebuilt from input + output, and the span's
    own ``usage`` dict is never consulted: the SDK fills it with ``0`` when
    the provider is silent.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return None, (
            "the provider published no usage on its Response; the span's usage "
            "dict is then zero-filled by the SDK, and a zero is not a measurement"
        )
    total = getattr(usage, "total_tokens", None)
    if not _is_int(total) or total <= 0:
        return None, (
            "the provider's usage carries no positive total_tokens (partial or "
            "empty usage); refused rather than rebuilt from its parts"
        )
    return total, None


def _text_parts(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content] if content else []
    parts: list[str] = []
    if isinstance(content, (list, tuple)):
        for block in content:
            if isinstance(block, str) and block:
                parts.append(block)
                continue
            kind = block.get("type") if isinstance(block, Mapping) else getattr(block, "type", None)
            text = block.get("text") if isinstance(block, Mapping) else getattr(block, "text", None)
            if kind in ("input_text", "output_text", "text") and isinstance(text, str) and text:
                parts.append(text)
    return parts


def prompt_text(items: Any) -> str | None:
    """The text of the input items the span recorded.

    The agent's instructions are not on the span; ``response.instructions``
    is there only when the provider echoes it, so using it would make the
    prompt length depend on the provider. Not included, and documented.
    """
    parts: list[str] = []
    if isinstance(items, str):
        parts.extend(_text_parts(items))
    elif isinstance(items, (list, tuple)):
        for item in items:
            content = (
                item.get("content") if isinstance(item, Mapping) else getattr(item, "content", None)
            )
            parts.extend(_text_parts(content))
    return "\n\n".join(parts) if parts else None


def response_text(response: Any) -> str | None:
    parts: list[str] = []
    for item in getattr(response, "output", None) or ():
        if getattr(item, "type", None) == "message":
            parts.extend(_text_parts(getattr(item, "content", None)))
    return "\n\n".join(parts) if parts else None


def requested_tools(response: Any) -> tuple[str, ...]:
    names: list[str] = []
    for item in getattr(response, "output", None) or ():
        if getattr(item, "type", None) == "function_call":
            name = _as_str(getattr(item, "name", None))
            if name:
                names.append(name)
    return tuple(names)


# ------------------------------------------------------------------- processor

#: The processor :meth:`NeoMundiTraceProcessor.install` last installed.
_ACTIVE: weakref.ReferenceType[NeoMundiTraceProcessor] | None = None
_ACTIVE_LOCK = threading.Lock()


class NeoMundiTraceProcessor(TracingProcessor):
    """Measure every model call an OpenAI Agents SDK run makes.

    Attach it **next to** the processors already registered::

        processor = NeoMundiTraceProcessor(api_key=..., base_url=...)
        agents.add_trace_processor(processor)   # NOT set_trace_processors

    ``set_trace_processors`` *replaces* the list and silently removes the
    SDK's own exporter to the OpenAI dashboard. :meth:`install` does the
    right thing.

    What is measured: each ``ResponseSpanData`` -- one provider request on
    the Responses API, the SDK's default -- with the provider's own
    ``response.usage.total_tokens`` and the span's ``ended_at -
    started_at``. Everything else is reported and refused, with a named
    status.

    Submission runs on a worker thread: ``on_span_end`` is called on the
    agent's own thread, and a network round trip there would stall the run.
    Call :meth:`force_flush` (or :meth:`flush`) before reading outcomes or
    exiting.

    .. warning::
       **Every submitted observation is a billed measurement.** There is no
       trace-only route on the NeoMundi API. An agent run makes one model
       call per step, so a tool-using agent bills several observations per
       run. Lower ``sample_rate``, or pass ``submit=False``, first.

    Parameters
    ----------
    client:
        An existing :class:`neomundi.NeoMundiClient`. When omitted, one is
        built from ``api_key`` and ``base_url``. Neither is needed with
        ``submit=False``.
    sample_rate:
        Fraction of model calls to submit, between 0.0 and 1.0,
        **deterministic in the span id**.
    submit:
        ``False`` runs every check locally and opens no socket.
    on_outcome:
        Called with each :class:`Outcome`. Its exceptions are swallowed.
    max_queue:
        Pending submissions. Beyond it a call is refused with
        ``not_submitted_queue_full`` rather than blocking the agent.
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
        capture_prompt: bool = True,
        capture_response: bool = True,
        on_outcome: Callable[[Outcome], None] | None = None,
        max_outcomes: int = 256,
        max_queue: int = 1024,
        max_tracked_spans: int = 4096,
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
        self._capture_prompt = bool(capture_prompt)
        self._capture_response = bool(capture_response)
        self._on_outcome = on_outcome
        self._max_outcomes = max(1, int(max_outcomes))
        self._max_tracked = max(1, int(max_tracked_spans))

        self._lock = threading.Lock()
        self._outcomes: list[Outcome] = []
        self._seen: OrderedDict[str, None] = OrderedDict()
        #: span_id -> (parent_id, span type, name), to find a call's agent.
        self._spans: OrderedDict[str, tuple[str | None, str, str | None]] = OrderedDict()

        self._queue: queue.Queue[_Job | None] = queue.Queue(maxsize=max(1, int(max_queue)))
        self._worker: threading.Thread | None = None
        self._closed = False
        self._installed = False
        self._retired = False

    # ---------------------------------------------------------------- public

    def install(self) -> NeoMundiTraceProcessor:
        """Append this processor with ``add_trace_processor``. Evicts nobody.

        One NeoMundi processor is active per process: installing a second one
        **retires** the previous one, so re-running a notebook cell does not
        bill every call twice. The retired processor stays in the SDK's list
        -- the SDK has no public way to remove a single processor -- but it
        no longer measures anything. Installing the same instance twice is a
        no-op.
        """
        global _ACTIVE
        from agents import add_trace_processor

        with _ACTIVE_LOCK:
            previous = _ACTIVE() if _ACTIVE is not None else None
            if previous is not None and previous is not self:
                previous._retire()
            _ACTIVE = weakref.ref(self)
            self._retired = False
            if not self._installed:
                add_trace_processor(self)
                self._installed = True
        return self

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
        """The kill switch: ``processor.sample_rate = 0.0`` stops all billing.

        The Agents SDK has no public way to remove one processor, short of
        ``set_trace_processors``, which would remove the others too.
        """
        _check_rate(value)
        self._sample_rate = float(value)

    def reset(self) -> None:
        with self._lock:
            self._outcomes.clear()
            self._seen.clear()
            self._spans.clear()

    def flush(self, timeout: float | None = 30.0) -> bool:
        """Wait until every queued submission has an outcome. ``True`` if done."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while self._queue.unfinished_tasks:
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(0.005)
        return True

    # ------------------------------------------------------ TracingProcessor

    def on_trace_start(self, trace: Any) -> None:  # noqa: D102
        pass

    def on_trace_end(self, trace: Any) -> None:  # noqa: D102
        pass

    def on_span_start(self, span: Any) -> None:  # noqa: D102
        if self._retired:
            return
        try:
            span_id = getattr(span, "span_id", None)
            if not isinstance(span_id, str):
                return
            data = getattr(span, "span_data", None)
            with self._lock:
                self._spans[span_id] = (
                    getattr(span, "parent_id", None),
                    type(data).__name__,
                    _as_str(getattr(data, "name", None)),
                )
                while len(self._spans) > self._max_tracked:
                    self._spans.popitem(last=False)
        except Exception:  # noqa: BLE001 - rule 2
            pass

    def on_span_end(self, span: Any) -> None:  # noqa: D102
        if self._retired:
            return
        try:
            self._handle(span)
        except Exception as exc:  # noqa: BLE001 - rule 2
            try:
                self._record(
                    Outcome(
                        span_id=str(getattr(span, "span_id", "unidentified")),
                        status=NOT_SUBMITTED_INTERNAL_ERROR,
                        reason="the processor failed; reported, never raised into the agent",
                        error_type=type(exc).__name__,
                    )
                )
            except Exception:  # noqa: BLE001
                pass

    def force_flush(self) -> None:  # noqa: D102
        try:
            self.flush()
        except Exception:  # noqa: BLE001 - rule 2
            pass

    def shutdown(self) -> None:  # noqa: D102
        try:
            self.flush()
            self._closed = True
            worker = self._worker
            if worker is not None and worker.is_alive():
                self._queue.put_nowait(None)
                worker.join(timeout=5.0)
        except Exception:  # noqa: BLE001 - rule 2
            pass

    # ------------------------------------------------------------- internals

    def _agent_of(self, parent_id: Any) -> str | None:
        with self._lock:
            current = parent_id
            for _ in range(32):
                entry = self._spans.get(current) if isinstance(current, str) else None
                if entry is None:
                    return None
                parent, kind, name = entry
                if kind == "AgentSpanData":
                    return name
                current = parent
        return None

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
        span_id = getattr(span, "span_id", None)
        if not isinstance(span_id, str) or not span_id:
            span_id = f"unidentified-{id(span)}"
        if not self._claim(span_id):
            return
        data = getattr(span, "span_data", None)
        kind = type(data).__name__
        base: dict[str, Any] = {
            "span_id": span_id,
            "span_type": kind,
            "trace_id": _as_str(getattr(span, "trace_id", None)),
        }

        if kind in _AGGREGATES:
            self._record(
                Outcome(
                    **base,
                    agent_name=_as_str(getattr(data, "agent_name", None))
                    or self._agent_of(getattr(span, "parent_id", None)),
                    status=NOT_SUBMITTED_AGGREGATE_SPAN,
                    reason="a turn or task span sums model calls that are measured one by "
                    "one; measuring it too would count them twice",
                )
            )
            return
        if kind in _NOT_MODEL_CALLS:
            name = _as_str(getattr(data, "name", None))
            self._record(
                Outcome(
                    **base,
                    agent_name=name
                    if kind == "AgentSpanData"
                    else self._agent_of(getattr(span, "parent_id", None)),
                    tool_name=name if kind == "FunctionSpanData" else None,
                    status=NOT_SUBMITTED_NOT_A_MODEL_CALL,
                    reason=f"{kind} is not a model call; it carries no token count, and "
                    "measuring it would invent an interaction",
                )
            )
            return
        base["agent_name"] = self._agent_of(getattr(span, "parent_id", None))
        if kind == _GENERATION:
            self._record(
                Outcome(
                    **base,
                    model_name=_as_str(getattr(data, "model", None)),
                    status=NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT,
                    reason="a generation span (Chat Completions, LiteLLM) carries a usage "
                    "dict the SDK rebuilt: zero-filled when the provider is silent, and a "
                    "local estimate on the LiteLLM route. Its origin cannot be established",
                )
            )
            return
        if kind != _RESPONSE:
            self._record(
                Outcome(
                    **base,
                    status=NOT_SUBMITTED_UNSUPPORTED_SPAN,
                    reason=f"{kind} is not a text model call this processor measures",
                )
            )
            return
        self._handle_response(span, data, base)

    def _handle_response(self, span: Any, data: Any, base: dict[str, Any]) -> None:
        response = getattr(data, "response", None)
        if response is not None:
            base["model_name"] = _as_str(getattr(response, "model", None))
            base["tool_calls"] = requested_tools(response)

        if getattr(span, "error", None) is not None:
            self._record(
                Outcome(
                    **base,
                    status=NOT_SUBMITTED_APPLICATION_ERROR,
                    reason="the model call failed; a partial measurement would be a "
                    "fabricated observation",
                )
            )
            return
        if response is None:
            self._record(
                Outcome(
                    **base,
                    status=NOT_SUBMITTED_NO_PROVIDER_RESPONSE,
                    reason="the span carries no provider Response: the run does not record "
                    "sensitive data (trace_include_sensitive_data=False), and the span's "
                    "own usage dict is zero-filled, so it proves nothing",
                )
            )
            return
        status = getattr(response, "status", None)
        if status is not None and status != "completed":
            self._record(
                Outcome(
                    **base,
                    status=NOT_SUBMITTED_APPLICATION_ERROR,
                    reason=f"the response did not complete (status {status!r})",
                )
            )
            return
        if ANY_LLM_MODULE in sys.modules:
            self._record(
                Outcome(
                    **base,
                    status=NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT,
                    reason="AnyLLMModel is loaded in this process: it fills response spans "
                    "with a Response rebuilt from any-llm's conversion, which a processor "
                    "cannot tell from the provider's own",
                )
            )
            return

        if not self._should_sample(base["span_id"]):
            self._record(
                Outcome(
                    **base,
                    status=SKIPPED_SAMPLING,
                    reason=f"excluded by sample_rate={self._sample_rate}",
                )
            )
            return

        latency = span_latency_ms(span)
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

        token_count, refusal = provider_total_tokens(response)
        if token_count is None:
            self._record(
                Outcome(**base, status=NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT, reason=refusal)
            )
            return
        base["token_count"] = token_count
        base["token_source"] = TOKENS_PROVIDER_RESPONSE_TOTAL

        if not self._submit or self._client is None:
            self._record(
                Outcome(
                    **base,
                    status=NOT_SUBMITTED_LOCAL_ONLY,
                    reason="submit=False: nothing left the process",
                )
            )
            return

        prompt = prompt_text(getattr(data, "input", None)) if self._capture_prompt else None
        answer = response_text(response) if self._capture_response else None
        job = _Job(
            base=base, token_count=token_count, latency_ms=latency, prompt=prompt, response=answer
        )
        if self._closed:
            self._record(
                Outcome(
                    **base,
                    status=NOT_SUBMITTED_PROCESSOR_CLOSED,
                    reason="the processor was shut down; nothing was submitted",
                )
            )
            return
        self._ensure_worker()
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            self._record(
                Outcome(
                    **base,
                    status=NOT_SUBMITTED_QUEUE_FULL,
                    reason="the submission queue is full; dropped rather than blocking the agent",
                )
            )

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._run_worker, name="neomundi-openai-agents", daemon=True
            )
            self._worker.start()

    def _run_worker(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is None:
                    return
                self._submit_job(job)
            except Exception as exc:  # noqa: BLE001 - rule 2
                try:
                    self._record(
                        Outcome(
                            **job.base,  # type: ignore[union-attr]
                            status=NOT_SUBMITTED_INTERNAL_ERROR,
                            reason="the processor failed; reported, never raised into the agent",
                            error_type=type(exc).__name__,
                        )
                    )
                except Exception:  # noqa: BLE001
                    pass
            finally:
                self._queue.task_done()

    def _submit_job(self, job: _Job) -> None:
        base = job.base
        metadata = dict(self._metadata)
        metadata.setdefault("client_trace_id", base["span_id"])
        try:
            result = self._client.govern(  # type: ignore[union-attr]
                token_count=job.token_count,
                latency_ms=job.latency_ms,
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
                    "submission; the agent run was not affected",
                    error_type=type(exc).__name__,
                )
            )
            return
        audit = getattr(result, "audit", None)
        version = getattr(audit, "measurement_version", None)
        verified = neomundi.API_CONTRACT.get("verified_measurement_versions", ())
        self._record(
            Outcome(
                **base,
                status=SUBMITTED,
                submitted=True,
                request_id=getattr(result, "request_id", None),
                measurement_version=version,
                measurement_version_verified=(version in tuple(verified)) if version else None,
            )
        )

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(sample_rate={self._sample_rate}, "
            f"submit={self._submit}, submitted_count={self.submitted_count})"
        )


def _check_rate(value: Any) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError("sample_rate must be a number between 0.0 and 1.0")
    if not 0.0 <= float(value) <= 1.0:
        raise ValueError("sample_rate must be between 0.0 and 1.0")
