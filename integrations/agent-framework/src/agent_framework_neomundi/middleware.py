"""A Microsoft Agent Framework chat middleware that measures with NeoMundi.

Design rules, inherited from the ``neomundi`` SDK and not negotiable:

1. **Nothing is invented.** The token count comes from the framework's
   ``ChatResponse.usage_details`` and from nowhere else. We measured, on
   ``agent-framework`` 1.26.0, that a provider publishing no ``usage``
   leaves ``usage_details`` at ``None`` rather than filled with an
   estimate -- see ``LIMITATIONS.md`` §1 for what that does and does not
   establish.
2. **Nothing escapes.** We measured that Agent Framework does **not**
   isolate a failing middleware: an exception raised in ``process()``
   reaches the caller. So every path here is wrapped, and the host never
   sees a failure of ours.
3. **Nothing is silent.** Every chat invocation produces an
   :class:`Outcome` with an explicit status, refusals included.

Latency is measured **around** ``call_next()``, so it is the time the
provider call actually took as seen from the framework -- not a number
read off a span.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import neomundi
from agent_framework import ChatMiddleware
from neomundi import NeoMundiClient, NeoMundiError

__all__ = [
    "NeoMundiChatMiddleware",
    "Outcome",
    "OUTCOMES",
    "SUBMITTED",
    "NOT_SUBMITTED_LOCAL_ONLY",
    "NOT_SUBMITTED_NO_TOKEN_COUNT",
    "NOT_SUBMITTED_INVALID_TOKEN_COUNT",
    "NOT_SUBMITTED_STREAMING",
    "NOT_SUBMITTED_NO_RESULT",
    "NOT_SUBMITTED_APPLICATION_ERROR",
    "NOT_SUBMITTED_TRANSPORT_ERROR",
    "NOT_SUBMITTED_INTERNAL_ERROR",
    "TOKENS_FRAMEWORK_TOTAL",
    "TOKENS_FRAMEWORK_SUM",
    "usage_token_count",
]

#: The call was measured and the API acknowledged an observation.
SUBMITTED = "submitted"
#: ``submit=False``: everything was computed locally, nothing left the process.
NOT_SUBMITTED_LOCAL_ONLY = "not_submitted_local_only"
#: The provider published no count, so ``usage_details`` is ``None``.
#: Inventing one here is the thing this integration exists not to do.
NOT_SUBMITTED_NO_TOKEN_COUNT = "not_submitted_no_token_count"
#: ``usage_details`` was present but carried nothing usable as a positive int.
NOT_SUBMITTED_INVALID_TOKEN_COUNT = "not_submitted_invalid_token_count"
#: A streaming invocation. Usage only exists once the stream is consumed,
#: and this middleware resumes before that -- see ``LIMITATIONS.md`` §3.
NOT_SUBMITTED_STREAMING = "not_submitted_streaming"
#: ``call_next()`` returned but the context carried no usable result.
NOT_SUBMITTED_NO_RESULT = "not_submitted_no_result"
#: The host's own call failed. Nothing of ours caused it and nothing is sent.
NOT_SUBMITTED_APPLICATION_ERROR = "not_submitted_application_error"
#: The measurement API could not be reached or refused the submission.
NOT_SUBMITTED_TRANSPORT_ERROR = "not_submitted_transport_error"
#: Anything else. Reported, never raised into the host.
NOT_SUBMITTED_INTERNAL_ERROR = "not_submitted_internal_error"

#: Every status this middleware can produce. The list is closed on purpose:
#: a reader can enumerate what may happen without reading the code.
OUTCOMES = (
    SUBMITTED,
    NOT_SUBMITTED_LOCAL_ONLY,
    NOT_SUBMITTED_NO_TOKEN_COUNT,
    NOT_SUBMITTED_INVALID_TOKEN_COUNT,
    NOT_SUBMITTED_STREAMING,
    NOT_SUBMITTED_NO_RESULT,
    NOT_SUBMITTED_APPLICATION_ERROR,
    NOT_SUBMITTED_TRANSPORT_ERROR,
    NOT_SUBMITTED_INTERNAL_ERROR,
)

#: ``total_token_count`` was present and used literally.
TOKENS_FRAMEWORK_TOTAL = "framework_total"
#: ``input_token_count`` and ``output_token_count`` were summed.
TOKENS_FRAMEWORK_SUM = "framework_sum"


@dataclass(frozen=True)
class Outcome:
    """What happened to one chat invocation. Always produced."""

    call_id: str
    status: str
    submitted: bool = False
    request_id: str | None = None
    token_count: int | None = None
    token_source: str | None = None
    latency_ms: float | None = None
    model_name: str | None = None
    stream: bool = False
    measurement_version: str | None = None
    measurement_version_verified: bool | None = None
    reason: str | None = None
    error_type: str | None = None
    usage_details: Mapping[str, Any] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.status not in OUTCOMES:
            message = f"unknown status {self.status!r}; OUTCOMES is closed"
            raise ValueError(message)


def _positive_int(value: Any) -> int | None:
    """An int that is really an int and really positive. ``bool`` is not one."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def usage_token_count(usage: Mapping[str, Any] | None) -> tuple[int | None, str | None, str | None]:
    """Return ``(token_count, source, refusal_reason)`` from ``usage_details``.

    🔴 **No arithmetic beyond adding the two counts the framework itself
    published.** There is no estimation path, no default, and no fallback
    to a locally computed number. ``agent-framework`` ships a
    ``CharacterEstimatorTokenizer``; it is used for compaction and it was
    measured never to reach ``usage_details``. If that ever changes, this
    function is where the damage would enter, which is why it reads only
    the two documented keys.
    """
    if usage is None:
        return (
            None,
            None,
            "the provider published no token count, so the framework left "
            "usage_details absent; an invented count would be a fabricated "
            "measurement",
        )

    total = _positive_int(usage.get("total_token_count"))
    if total is not None:
        return total, TOKENS_FRAMEWORK_TOTAL, None

    prompt = _positive_int(usage.get("input_token_count"))
    completion = _positive_int(usage.get("output_token_count"))
    if prompt is not None and completion is not None:
        return prompt + completion, TOKENS_FRAMEWORK_SUM, None

    return (
        None,
        None,
        "usage_details was present but carried no positive integer under "
        "total_token_count, nor under both input_token_count and "
        "output_token_count",
    )


class NeoMundiChatMiddleware(ChatMiddleware):
    """Measure every chat invocation of an Agent Framework client.

    ::

        from agent_framework.openai import OpenAIChatCompletionClient
        from agent_framework_neomundi import NeoMundiChatMiddleware

        measure = NeoMundiChatMiddleware(
            client=NeoMundiClient(api_key=..., base_url="https://api.neomundi.io"),
        )
        chat = OpenAIChatCompletionClient(model="gpt-4o-mini", middleware=[measure])

        reply = await chat.get_response("What is the capital of France?")
        print(measure.outcomes[-1].status, measure.outcomes[-1].request_id)

    Args:
        client: a configured ``NeoMundiClient``. ``None`` means dry run:
            everything is computed and nothing leaves the process.
        submit: set ``False`` to compute without submitting, with a real
            client attached.
        capture_prompt: send the prompt text. Off by default -- the host's
            text is the host's.
        capture_response: send the response text. Off by default.
        measure_streaming: reserved. Streaming is refused by name today;
            see ``LIMITATIONS.md`` §3.
        metadata: merged into every submission.
        source_type, mode: passed through to the SDK unchanged.
        max_outcomes: ring size for :attr:`outcomes`. The middleware keeps a
            bounded history so a long-running host does not grow a list
            forever.
    """

    def __init__(
        self,
        *,
        client: NeoMundiClient | None = None,
        submit: bool = True,
        capture_prompt: bool = False,
        capture_response: bool = False,
        measure_streaming: bool = False,
        metadata: Mapping[str, Any] | None = None,
        source_type: str | None = None,
        mode: str | None = None,
        max_outcomes: int = 1000,
    ) -> None:
        self._client = client
        self._submit = bool(submit)
        self._capture_prompt = bool(capture_prompt)
        self._capture_response = bool(capture_response)
        self._measure_streaming = bool(measure_streaming)
        self._metadata = dict(metadata or {})
        self._source_type = source_type
        self._mode = mode
        self._max_outcomes = max(1, int(max_outcomes))
        self._outcomes: list[Outcome] = []

    # ------------------------------------------------------------ reading

    @property
    def outcomes(self) -> tuple[Outcome, ...]:
        """Every outcome recorded, oldest first, bounded by ``max_outcomes``."""
        return tuple(self._outcomes)

    @property
    def last_outcome(self) -> Outcome | None:
        return self._outcomes[-1] if self._outcomes else None

    @property
    def submitted_count(self) -> int:
        return sum(1 for o in self._outcomes if o.submitted)

    def _record(self, outcome: Outcome) -> Outcome:
        self._outcomes.append(outcome)
        if len(self._outcomes) > self._max_outcomes:
            del self._outcomes[: len(self._outcomes) - self._max_outcomes]
        return outcome

    # ------------------------------------------------------- the hook

    async def process(self, context: Any, call_next: Any) -> None:
        """Run the call, then measure it. Never raise into the host.

        🔴 The host's call is made **first and unconditionally**. Whatever
        this middleware then does, the application has already got its
        answer. We measured that Agent Framework propagates a middleware
        exception to the caller, so rule 2 is enforced here and nowhere
        else.
        """
        call_id = uuid.uuid4().hex
        started = time.perf_counter()
        try:
            await call_next()
        except Exception as exc:  # noqa: BLE001 - the host's failure, not ours
            self._safe_record(
                Outcome(
                    call_id=call_id,
                    status=NOT_SUBMITTED_APPLICATION_ERROR,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    reason="the host's own chat call raised; nothing was measured "
                    "and nothing was sent",
                    error_type=type(exc).__name__,
                )
            )
            raise
        latency_ms = (time.perf_counter() - started) * 1000

        try:
            self._measure(call_id, context, latency_ms)
        except Exception as exc:  # noqa: BLE001 - rule 2
            self._safe_record(
                Outcome(
                    call_id=call_id,
                    status=NOT_SUBMITTED_INTERNAL_ERROR,
                    latency_ms=latency_ms,
                    reason="the middleware failed; reported, never raised into the host",
                    error_type=type(exc).__name__,
                )
            )

    def _safe_record(self, outcome: Outcome) -> None:
        try:
            self._record(outcome)
        except Exception:  # noqa: BLE001, S110 - recording must never raise either
            pass

    # -------------------------------------------------------- measuring

    def _measure(self, call_id: str, context: Any, latency_ms: float) -> None:
        stream = bool(getattr(context, "stream", False))
        result = getattr(context, "result", None)

        if stream and not self._measure_streaming:
            self._record(
                Outcome(
                    call_id=call_id,
                    status=NOT_SUBMITTED_STREAMING,
                    latency_ms=latency_ms,
                    stream=True,
                    reason="a streaming invocation: usage exists only once the stream "
                    "is consumed, and this middleware resumes before that. Measuring "
                    "it here would mean guessing. See LIMITATIONS.md section 3.",
                )
            )
            return

        if result is None:
            self._record(
                Outcome(
                    call_id=call_id,
                    status=NOT_SUBMITTED_NO_RESULT,
                    latency_ms=latency_ms,
                    stream=stream,
                    reason="call_next() returned but context.result was empty",
                )
            )
            return

        usage = getattr(result, "usage_details", None)
        usage_map = dict(usage) if usage else None
        model_name = getattr(result, "model", None)
        token_count, token_source, refusal = usage_token_count(usage_map)

        base: dict[str, Any] = {
            "call_id": call_id,
            "latency_ms": latency_ms,
            "model_name": model_name,
            "stream": stream,
            "usage_details": usage_map,
        }

        if token_count is None:
            status = (
                NOT_SUBMITTED_NO_TOKEN_COUNT
                if usage_map is None
                else NOT_SUBMITTED_INVALID_TOKEN_COUNT
            )
            self._record(Outcome(**base, status=status, reason=refusal))
            return

        base["token_count"] = token_count
        base["token_source"] = token_source

        if not self._submit or self._client is None:
            self._record(
                Outcome(
                    **base,
                    status=NOT_SUBMITTED_LOCAL_ONLY,
                    reason="submit=False or no client: nothing left the process",
                )
            )
            return

        prompt = self._text_of(getattr(context, "messages", None)) if self._capture_prompt else None
        response = getattr(result, "text", None) if self._capture_response else None
        metadata = dict(self._metadata)
        metadata.setdefault("client_trace_id", call_id)

        try:
            outcome_result = self._client.govern(
                token_count=token_count,
                latency_ms=latency_ms,
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

        audit = getattr(outcome_result, "audit", None)
        version = getattr(audit, "measurement_version", None)
        verified = tuple(neomundi.API_CONTRACT.get("verified_measurement_versions", ()))
        self._record(
            Outcome(
                **base,
                status=SUBMITTED,
                submitted=True,
                request_id=getattr(outcome_result, "request_id", None),
                measurement_version=version,
                measurement_version_verified=(version in verified) if version else None,
            )
        )

    @staticmethod
    def _text_of(messages: Any) -> str | None:
        """Flatten the outgoing messages to text, defensively."""
        if not messages:
            return None
        parts: list[str] = []
        for message in messages:
            text = getattr(message, "text", None)
            if isinstance(text, str) and text:
                parts.append(text)
        return "\n".join(parts) if parts else None

    def __repr__(self) -> str:
        return (
            f"NeoMundiChatMiddleware(submit={self._submit}, "
            f"submitted_count={self.submitted_count}, "
            f"outcomes={len(self._outcomes)})"
        )
