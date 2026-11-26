"""LiteLLM custom logger that measures completions with NeoMundi.

Design rules, inherited from the ``neomundi`` SDK and not negotiable here:

1. **Nothing is invented.** Only a token count the *provider* published is
   submitted. LiteLLM fills a silent provider's ``usage`` with ``0`` or with
   a local tokenizer estimate, and the two are indistinguishable from a
   reported count on ``response_obj.usage``. This logger therefore checks
   every submitted count against the provider's own payload, and refuses
   when it cannot.
2. **Nothing escapes.** No exception raised by this logger, the SDK or the
   network ever propagates into the host application or the proxy.
3. **Nothing is silent.** Every completion LiteLLM reports produces an
   :class:`Outcome` with an explicit status.

See ``LIMITATIONS.md`` before drawing conclusions from a score.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import combinations
from typing import Any

import neomundi
from litellm.integrations.custom_logger import CustomLogger
from neomundi import NeoMundiClient, NeoMundiError

__all__ = [
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
]

#: The completion was measured and the API acknowledged an observation.
SUBMITTED = "submitted"
#: ``sample_rate`` excluded this call. No socket was opened, nothing was billed.
SKIPPED_SAMPLING = "skipped_sampling"
#: ``submit=False``. Everything was checked locally; nothing left the process.
NOT_SUBMITTED_LOCAL_ONLY = "not_submitted_local_only"
#: The token count could not be traced back to the provider's own payload.
NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT = "not_submitted_unverified_token_count"
#: LiteLLM handed no usable start/end time, so there is no observed latency.
NOT_SUBMITTED_NO_LATENCY = "not_submitted_no_latency"
#: LiteLLM answered from its cache: no model interaction took place.
NOT_SUBMITTED_CACHE_HIT = "not_submitted_cache_hit"
#: Not a chat completion (embedding, image, transcription...).
NOT_SUBMITTED_UNSUPPORTED_CALL = "not_submitted_unsupported_call"
#: The application's LiteLLM call failed. There is no complete interaction.
NOT_SUBMITTED_APPLICATION_ERROR = "not_submitted_application_error"
#: The measurement API could not be reached, or refused the submission.
NOT_SUBMITTED_TRANSPORT_ERROR = "not_submitted_transport_error"
#: A defect in this logger. Reported, never raised into the host.
NOT_SUBMITTED_INTERNAL_ERROR = "not_submitted_internal_error"

OUTCOMES = (
    SUBMITTED,
    SKIPPED_SAMPLING,
    NOT_SUBMITTED_LOCAL_ONLY,
    NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT,
    NOT_SUBMITTED_NO_LATENCY,
    NOT_SUBMITTED_CACHE_HIT,
    NOT_SUBMITTED_UNSUPPORTED_CALL,
    NOT_SUBMITTED_APPLICATION_ERROR,
    NOT_SUBMITTED_TRANSPORT_ERROR,
    NOT_SUBMITTED_INTERNAL_ERROR,
)

#: The call types whose result is one prompt and one completion.
MEASURED_CALL_TYPES = frozenset({"completion", "acompletion"})

#: Provenance of a submitted token count.
TOKENS_PROVIDER_TOTAL = "provider_total"
TOKENS_PROVIDER_SUM = "provider_prompt_plus_completion"

#: A key whose lower-cased name contains one of these marks a container of
#: provider usage numbers (``usage``, ``usageMetadata``, ``prompt_eval_count``,
#: ``billed_units``...). Durations, timestamps and indices do not match.
_USAGE_KEY_HINTS = ("usage", "token", "count", "billed")
_MAX_DEPTH = 8
_MAX_SUBSET_VALUES = 12


@dataclass(frozen=True)
class Outcome:
    """What the logger did about one LiteLLM call, and why.

    ``submitted`` is the only status under which ``request_id`` is set, and
    ``request_id`` is the only reliable correlation key against the API.
    ``call_id`` is LiteLLM's ``litellm_call_id``; the server never sees it
    as an identity.
    """

    call_id: str
    status: str
    submitted: bool = False
    request_id: str | None = None
    #: ``end_time - start_time`` as LiteLLM observed it around the call.
    latency_ms: float | None = None
    token_count: int | None = None
    #: How ``token_count`` was traced to the provider's payload.
    token_source: str | None = None
    #: The model the caller asked LiteLLM for. Declared, not observed.
    model_name: str | None = None
    provider: str | None = None
    #: LiteLLM's ``response_cost``: a price-table value, never a measurement.
    declared_cost: float | None = None
    declared_cost_sent: bool = False
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


# --------------------------------------------------------------------- helpers


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _collect_usage_numbers(obj: Any, inside: bool, out: list[int], depth: int = 0) -> None:
    """Collect the integers a provider published under usage-like keys."""
    if depth > _MAX_DEPTH:
        return
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            hit = inside or any(h in str(key).lower() for h in _USAGE_KEY_HINTS)
            if hit and _is_int(value) and value >= 0:
                out.append(value)
            elif isinstance(value, (Mapping, list, tuple)):
                _collect_usage_numbers(value, hit, out, depth + 1)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _collect_usage_numbers(item, inside, out, depth + 1)


def _usage_as_mapping(usage: Any) -> Mapping[str, Any] | None:
    if usage is None:
        return None
    if isinstance(usage, Mapping):
        return usage
    out: dict[str, Any] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = getattr(usage, key, None)
        if _is_int(value):
            out[key] = value
    return out or None


def provider_usage_numbers(original_response: Any) -> tuple[list[int] | None, str]:
    """Return the integers the provider itself published about usage.

    ``(None, reason)`` when the provider's payload is not reachable at all.
    An empty list means it was reachable and published nothing.

    Three shapes were observed on litellm 1.111.0:

    * non-streamed call: the provider's JSON body, as a string;
    * sync stream: LiteLLM's stream wrapper, whose ``chunks`` keep the
      provider's usage chunk when one was sent, and none otherwise;
    * async stream: a coroutine -- nothing to verify against.
    """
    raw = original_response
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", "replace")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None, "the provider's raw payload is not JSON"
    if isinstance(raw, (Mapping, list)):
        numbers: list[int] = []
        _collect_usage_numbers(raw, False, numbers)
        return numbers, "raw payload"
    chunks = getattr(raw, "chunks", None)
    if isinstance(chunks, list):
        numbers = []
        for chunk in chunks:
            usage = _usage_as_mapping(getattr(chunk, "usage", None))
            if usage:
                _collect_usage_numbers({"usage": usage}, False, numbers)
        return numbers, "stream chunks"
    if asyncio.iscoroutine(raw):
        # LiteLLM's own object; it is neither awaited nor closed here.
        return None, "async stream: the provider's payload is not reachable from the logger"
    return None, "the provider's raw payload is not available"


def _subset_sums(values: Iterable[int]) -> set[int]:
    pool = [v for v in values if v > 0][:_MAX_SUBSET_VALUES]
    sums: set[int] = set()
    for size in range(1, len(pool) + 1):
        for combo in combinations(pool, size):
            sums.add(sum(combo))
    return sums


def verified_token_count(
    usage: Any, original_response: Any
) -> tuple[int | None, str | None, str | None]:
    """Return ``(token_count, source, refusal_reason)``.

    The count is LiteLLM's normalised one, **accepted only if it can be
    derived from integers present in the provider's own payload**:

    * ``total_tokens`` appears there literally, or
    * ``completion_tokens`` appears there literally and ``prompt_tokens``
      either appears there or is a sum of numbers that do (Anthropic's
      ``input_tokens + cache_read_input_tokens``, for instance).

    Anything else is refused: a count LiteLLM computed with its own
    tokenizer, or defaulted to zero, is not a measurement.
    """
    numbers, where = provider_usage_numbers(original_response)
    if numbers is None:
        return None, None, where
    if not numbers:
        return (
            None,
            None,
            (
                "the provider published no token count; LiteLLM's usage is then a "
                "default or a local estimate, and an estimate would be a fabricated "
                "measurement"
            ),
        )
    mapping = _usage_as_mapping(usage) or {}
    total = mapping.get("total_tokens")
    prompt = mapping.get("prompt_tokens")
    completion = mapping.get("completion_tokens")
    if _is_int(total) and total > 0 and total in numbers:
        return total, TOKENS_PROVIDER_TOTAL, None
    if _is_int(prompt) and _is_int(completion) and prompt > 0 and completion > 0:
        if completion in numbers:
            rest = list(numbers)
            rest.remove(completion)
            if prompt in rest or prompt in _subset_sums(rest):
                return prompt + completion, TOKENS_PROVIDER_SUM, None
    return (
        None,
        None,
        (
            "the token count LiteLLM reports cannot be traced to the provider's "
            "payload (partial or estimated usage); refused rather than estimated"
        ),
    )


def _latency_ms(start_time: Any, end_time: Any) -> float | None:
    try:
        if isinstance(start_time, datetime) and isinstance(end_time, datetime):
            delta = end_time - start_time
        elif isinstance(start_time, (int, float)) and isinstance(end_time, (int, float)):
            delta = timedelta(seconds=float(end_time) - float(start_time))
        else:
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    value = delta.total_seconds() * 1000.0
    if value < 0:
        # A wall-clock step between the two readings. Refused, not clamped.
        return None
    return value


def _text_of(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content] if content else []
    parts: list[str] = []
    if isinstance(content, (list, tuple)):
        for block in content:
            if isinstance(block, str) and block:
                parts.append(block)
            elif isinstance(block, Mapping) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
    return parts


def _prompt_text(messages: Any) -> str | None:
    parts: list[str] = []
    if isinstance(messages, (list, tuple)):
        for message in messages:
            if isinstance(message, Mapping):
                parts.extend(_text_of(message.get("content")))
            else:
                parts.extend(_text_of(getattr(message, "content", None)))
    elif isinstance(messages, str):
        parts.append(messages)
    return "\n\n".join(parts) if parts else None


def _response_text(response_obj: Any) -> str | None:
    parts: list[str] = []
    for choice in getattr(response_obj, "choices", None) or ():
        message = getattr(choice, "message", None)
        if message is None and isinstance(choice, Mapping):
            message = choice.get("message")
        content = (
            message.get("content")
            if isinstance(message, Mapping)
            else getattr(message, "content", None)
        )
        parts.extend(_text_of(content))
    return "\n\n".join(parts) if parts else None


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


# ---------------------------------------------------------------------- logger


class NeoMundiLogger(CustomLogger):
    """Measure every chat completion that goes through LiteLLM.

    Attach it once, in an application or in the LiteLLM Proxy::

        litellm.callbacks = [NeoMundiLogger(api_key=..., base_url=...)]

    The logger is a thin driver for ``NeoMundiClient.govern``: the SDK
    enforces the request contract and submits the observation. Latency is
    the one LiteLLM observed around the provider call (``end_time -
    start_time``); this logger measures nothing itself.

    .. warning::
       **Every submitted observation is a billed measurement.** There is no
       trace-only route on the NeoMundi API. With the default
       ``sample_rate=1.0`` every completion is metered. Lower it, or pass
       ``submit=False``, before pointing this at a busy gateway.

    Parameters
    ----------
    client:
        An existing :class:`neomundi.NeoMundiClient`. When omitted, one is
        built from ``api_key`` and ``base_url``. Neither is needed with
        ``submit=False``.
    sample_rate:
        Fraction of calls to submit, between 0.0 and 1.0, **deterministic in
        ``litellm_call_id``**.
    submit:
        ``False`` runs every check locally and opens no socket.
    send_declared_cost:
        ``False`` (default): LiteLLM's ``response_cost`` is reported on the
        outcome but not sent. It comes from a price table, not from the
        provider's bill.
    on_outcome:
        Called with each :class:`Outcome`. Its exceptions are swallowed.
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
        send_declared_cost: bool = False,
        on_outcome: Callable[[Outcome], None] | None = None,
        max_outcomes: int = 256,
        max_tracked_calls: int = 4096,
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
        super().__init__()

        self._client = client
        self._sample_rate = float(sample_rate)
        self._submit = bool(submit)
        self._mode = mode
        self._source_type = source_type
        self._metadata = dict(metadata or {})
        self._capture_prompt = bool(capture_prompt)
        self._capture_response = bool(capture_response)
        self._send_declared_cost = bool(send_declared_cost)
        self._on_outcome = on_outcome
        self._max_outcomes = max(1, int(max_outcomes))
        self._max_tracked = max(1, int(max_tracked_calls))

        self._lock = threading.Lock()
        self._seen: OrderedDict[str, None] = OrderedDict()
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
        """The kill switch: ``logger.sample_rate = 0.0`` stops all billing at once.

        Settable at runtime because detaching a logger from LiteLLM is not
        what it looks like: reassigning ``litellm.callbacks`` leaves the old
        instance registered and firing (measured on litellm 1.111.0, see
        LIMITATIONS.md).
        """
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise TypeError("sample_rate must be a number between 0.0 and 1.0")
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError("sample_rate must be between 0.0 and 1.0")
        self._sample_rate = float(value)

    def reset(self) -> None:
        with self._lock:
            self._seen.clear()
            self._outcomes.clear()

    # ------------------------------------------------------------ internals

    def _should_sample(self, call_id: str) -> bool:
        rate = self._sample_rate
        if rate >= 1.0:
            return True
        if rate <= 0.0:
            return False
        digest = hashlib.sha256(call_id.encode("utf-8")).digest()[:8]
        position = int.from_bytes(digest, "big") / float(1 << 64)
        return position < rate

    def _claim(self, call_id: str) -> bool:
        """``True`` the first time a call id is seen. Guards double billing."""
        with self._lock:
            if call_id in self._seen:
                return False
            self._seen[call_id] = None
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

    @staticmethod
    def _call_id(kwargs: Any) -> str:
        if isinstance(kwargs, Mapping):
            value = kwargs.get("litellm_call_id")
            if isinstance(value, str) and value:
                return value
        return f"unidentified-{uuid.uuid4().hex}"

    def _measure(
        self, kwargs: Mapping[str, Any], response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        call_id = self._call_id(kwargs)
        if not self._claim(call_id):
            return
        model_name = _as_str(kwargs.get("model"))
        provider = _as_str(kwargs.get("custom_llm_provider"))
        base = {"call_id": call_id, "model_name": model_name, "provider": provider}

        call_type = kwargs.get("call_type")
        if call_type not in MEASURED_CALL_TYPES:
            self._record(
                Outcome(
                    **base,
                    status=NOT_SUBMITTED_UNSUPPORTED_CALL,
                    reason=f"call type {call_type!r} is not a chat completion",
                )
            )
            return

        if not self._should_sample(call_id):
            self._record(
                Outcome(
                    **base,
                    status=SKIPPED_SAMPLING,
                    reason=f"excluded by sample_rate={self._sample_rate}",
                )
            )
            return

        if kwargs.get("cache_hit") is True:
            self._record(
                Outcome(
                    **base,
                    status=NOT_SUBMITTED_CACHE_HIT,
                    reason="LiteLLM answered from its cache; no model interaction took place",
                )
            )
            return

        latency = _latency_ms(start_time, end_time)
        declared_cost = kwargs.get("response_cost")
        if not isinstance(declared_cost, (int, float)) or isinstance(declared_cost, bool):
            declared_cost = None
        base["declared_cost"] = declared_cost
        if latency is None:
            self._record(
                Outcome(
                    **base,
                    status=NOT_SUBMITTED_NO_LATENCY,
                    reason="LiteLLM handed no usable start and end time; latency is "
                    "observed or absent, never assumed",
                )
            )
            return
        base["latency_ms"] = latency

        token_count, token_source, refusal = verified_token_count(
            getattr(response_obj, "usage", None), kwargs.get("original_response")
        )
        if token_count is None:
            self._record(
                Outcome(**base, status=NOT_SUBMITTED_UNVERIFIED_TOKEN_COUNT, reason=refusal)
            )
            return
        base["token_count"] = token_count
        base["token_source"] = token_source

        send_cost = (
            self._send_declared_cost and declared_cost is not None and float(declared_cost) > 0
        )
        if not self._submit or self._client is None:
            self._record(
                Outcome(
                    **base,
                    status=NOT_SUBMITTED_LOCAL_ONLY,
                    reason="submit=False: nothing left the process",
                )
            )
            return

        prompt = _prompt_text(kwargs.get("messages")) if self._capture_prompt else None
        response = _response_text(response_obj) if self._capture_response else None
        metadata = dict(self._metadata)
        metadata.setdefault("client_trace_id", call_id)
        try:
            result = self._client.govern(
                token_count=token_count,
                latency_ms=latency,
                cost=float(declared_cost) if send_cost else None,
                prompt_length=len(prompt) if prompt is not None else None,
                response_length=len(response) if response is not None else None,
                model_name=model_name,
                llm_response=response,
                llm_prompt=prompt,
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

        audit = getattr(result, "audit", None)
        version = getattr(audit, "measurement_version", None)
        verified = neomundi.API_CONTRACT.get("verified_measurement_versions", ())
        request_id = getattr(result, "request_id", None)
        self._record(
            Outcome(
                **base,
                status=SUBMITTED,
                submitted=True,
                request_id=request_id,
                declared_cost_sent=send_cost,
                measurement_version=version,
                measurement_version_verified=(version in tuple(verified)) if version else None,
            )
        )

    def _safe_measure(self, kwargs: Any, response_obj: Any, start_time: Any, end_time: Any) -> None:
        try:
            if not isinstance(kwargs, Mapping):
                raise TypeError("LiteLLM passed no kwargs mapping")
            self._measure(kwargs, response_obj, start_time, end_time)
        except Exception as exc:  # noqa: BLE001 - rule 2
            try:
                self._record(
                    Outcome(
                        call_id=self._call_id(kwargs),
                        status=NOT_SUBMITTED_INTERNAL_ERROR,
                        reason="the logger failed; reported, never raised into the host",
                        error_type=type(exc).__name__,
                    )
                )
            except Exception:  # noqa: BLE001
                pass

    def _failure(self, kwargs: Any, start_time: Any, end_time: Any) -> None:
        try:
            call_id = self._call_id(kwargs)
            if not self._claim(call_id):
                return
            exception = kwargs.get("exception") if isinstance(kwargs, Mapping) else None
            self._record(
                Outcome(
                    call_id=call_id,
                    status=NOT_SUBMITTED_APPLICATION_ERROR,
                    latency_ms=_latency_ms(start_time, end_time),
                    model_name=_as_str(kwargs.get("model"))
                    if isinstance(kwargs, Mapping)
                    else None,
                    reason="the application's LiteLLM call failed; a partial measurement "
                    "would be a fabricated observation",
                    error_type=type(exception).__name__ if exception is not None else None,
                )
            )
        except Exception:  # noqa: BLE001 - rule 2
            pass

    # ------------------------------------------------------- LiteLLM hooks
    #
    # LiteLLM calls these with KEYWORD arguments (observed on 1.111.0:
    # ``response_obj=...``). The parameter names are part of the contract.

    def log_success_event(self, kwargs, response_obj, start_time, end_time):  # noqa: D102
        self._safe_measure(kwargs, response_obj, start_time, end_time)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):  # noqa: D102
        # The SDK is synchronous. Submitting on the event loop would stall a
        # proxy for the whole round trip, so it runs in a worker thread.
        try:
            await asyncio.to_thread(self._safe_measure, kwargs, response_obj, start_time, end_time)
        except Exception:  # noqa: BLE001 - rule 2
            pass

    def log_failure_event(self, kwargs, response_obj, start_time, end_time):  # noqa: D102
        self._failure(kwargs, start_time, end_time)

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):  # noqa: D102
        self._failure(kwargs, start_time, end_time)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(sample_rate={self._sample_rate}, "
            f"submit={self._submit}, submitted_count={self.submitted_count})"
        )
