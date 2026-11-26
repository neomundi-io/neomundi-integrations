"""LangChain callback handler that measures LLM interactions with NeoMundi.

Design rules, inherited from the ``neomundi`` SDK and not negotiable here:

1. **Nothing is invented.** If the provider does not report a token count,
   no observation is submitted. An estimated token count would be a
   fabricated measurement.
2. **Nothing escapes.** No exception raised by this handler, the SDK or the
   network ever propagates into the host LangChain chain. Instrumentation
   that breaks the application it observes is worse than no
   instrumentation.
3. **Nothing is silent.** Every decision produces an :class:`Outcome` with
   an explicit status, available on the handler and through ``on_outcome``.

See ``KNOWN_LIMITATIONS.md`` before drawing conclusions from a score.
"""

from __future__ import annotations

import hashlib
import threading
import warnings
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import neomundi
from langchain_core.callbacks import BaseCallbackHandler
from neomundi import NeoMundiClient

__all__ = [
    "NeoMundiCallbackHandler",
    "Outcome",
    "OUTCOMES",
    "SUBMITTED",
    "SKIPPED_SAMPLING",
    "NOT_SUBMITTED_NO_TOKEN_COUNT",
    "NOT_SUBMITTED_APPLICATION_ERROR",
    "NOT_SUBMITTED_LOCAL_ONLY",
    "NOT_SUBMITTED_TRANSPORT_ERROR",
    "NOT_SUBMITTED_INTERNAL_ERROR",
]

#: The interaction was measured and the API acknowledged an observation.
SUBMITTED = "submitted"
#: ``sample_rate`` excluded this run. No socket was opened, nothing was billed.
SKIPPED_SAMPLING = "skipped_sampling"
#: The provider reported no token count. Refused rather than estimated.
NOT_SUBMITTED_NO_TOKEN_COUNT = "not_submitted_no_token_count"
#: The application's own LLM call failed. There is no complete interaction.
NOT_SUBMITTED_APPLICATION_ERROR = "not_submitted_application_error"
#: ``submit=False``. Latency was measured locally, nothing left the process.
NOT_SUBMITTED_LOCAL_ONLY = "not_submitted_local_only"
#: The measurement API could not be reached, or refused the submission.
NOT_SUBMITTED_TRANSPORT_ERROR = "not_submitted_transport_error"
#: A defect in this handler. Reported, never raised into the host chain.
NOT_SUBMITTED_INTERNAL_ERROR = "not_submitted_internal_error"

OUTCOMES = (
    SUBMITTED,
    SKIPPED_SAMPLING,
    NOT_SUBMITTED_NO_TOKEN_COUNT,
    NOT_SUBMITTED_APPLICATION_ERROR,
    NOT_SUBMITTED_LOCAL_ONLY,
    NOT_SUBMITTED_TRANSPORT_ERROR,
    NOT_SUBMITTED_INTERNAL_ERROR,
)

_ANCESTOR_WALK_LIMIT = 32


@dataclass(frozen=True)
class Outcome:
    """What the handler did about one LLM run, and why.

    ``submitted`` is the only status under which ``request_id`` is set, and
    ``request_id`` is the only reliable correlation key against the API --
    ``run_id`` is LangChain's, and the server never sees it as an identity.
    """

    run_id: str
    status: str
    submitted: bool = False
    request_id: str | None = None
    latency_ms: float | None = None
    token_count: int | None = None
    model_name: str | None = None
    measurement_version: str | None = None
    #: Human-readable reason, present on every non-``submitted`` status.
    reason: str | None = None
    #: Exception class name when one was caught. Never the message, which
    #: could carry a prompt or a key fragment.
    error_type: str | None = None

    def __bool__(self) -> bool:  # pragma: no cover - convenience only
        return self.submitted


@dataclass
class _Pending:
    trace: Any
    prompt: str | None = None
    model_name: str | None = None
    rag_context: list[str] | None = None
    extra_metadata: dict[str, Any] = field(default_factory=dict)


def _flatten_messages(messages: Sequence[Sequence[Any]]) -> str | None:
    """Render chat messages as the prompt text that was actually sent."""
    parts: list[str] = []
    for batch in messages or ():
        for message in batch or ():
            content = getattr(message, "content", None)
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, (list, tuple)):
                # Multimodal content: keep the text blocks, ignore the rest.
                for block in content:
                    if isinstance(block, str):
                        parts.append(block)
                    elif isinstance(block, Mapping) and block.get("type") == "text":
                        text = block.get("text")
                        if isinstance(text, str):
                            parts.append(text)
    return "\n\n".join(parts) if parts else None


def _extract_token_count(response: Any) -> int | None:
    """Return the provider's own token count, or ``None``.

    Two sources, in order: ``usage_metadata`` on the message (the path
    LangChain normalises across providers), then ``llm_output`` for older
    integrations. **Never estimated** -- a missing count stays missing.
    """
    total = 0
    found = False
    for batch in getattr(response, "generations", None) or ():
        for generation in batch or ():
            message = getattr(generation, "message", None)
            usage = getattr(message, "usage_metadata", None)
            if not isinstance(usage, Mapping):
                continue
            value = usage.get("total_tokens")
            if not isinstance(value, int):
                prompt_tokens = usage.get("input_tokens")
                completion_tokens = usage.get("output_tokens")
                if isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
                    value = prompt_tokens + completion_tokens
                else:
                    value = None
            if isinstance(value, int) and not isinstance(value, bool):
                total += value
                found = True
    if found:
        return total

    llm_output = getattr(response, "llm_output", None)
    if isinstance(llm_output, Mapping):
        usage = llm_output.get("token_usage") or llm_output.get("usage")
        if isinstance(usage, Mapping):
            value = usage.get("total_tokens")
            if isinstance(value, int) and not isinstance(value, bool):
                return value
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")
            if isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
                return prompt_tokens + completion_tokens
    return None


def _extract_response_text(response: Any) -> str | None:
    parts: list[str] = []
    for batch in getattr(response, "generations", None) or ():
        for generation in batch or ():
            text = getattr(generation, "text", None)
            if isinstance(text, str) and text:
                parts.append(text)
                continue
            message = getattr(generation, "message", None)
            content = getattr(message, "content", None)
            if isinstance(content, str) and content:
                parts.append(content)
    return "\n\n".join(parts) if parts else None


def _extract_model_name(
    serialized: Mapping[str, Any] | None,
    metadata: Mapping[str, Any] | None,
    kwargs: Mapping[str, Any],
) -> str | None:
    """Report the model the caller *asked for*.

    The API does not publish which model actually served the call, so this
    is a declared value, not an observed one.
    """
    params = kwargs.get("invocation_params")
    if isinstance(params, Mapping):
        for key in ("model", "model_name", "model_id", "deployment_name"):
            value = params.get(key)
            if isinstance(value, str) and value:
                return value
    if isinstance(metadata, Mapping):
        for key in ("ls_model_name", "model", "model_name"):
            value = metadata.get(key)
            if isinstance(value, str) and value:
                return value
    if isinstance(serialized, Mapping):
        kw = serialized.get("kwargs")
        if isinstance(kw, Mapping):
            for key in ("model", "model_name"):
                value = kw.get(key)
                if isinstance(value, str) and value:
                    return value
    return None


def _documents_to_context(documents: Sequence[Any]) -> list[str]:
    context: list[str] = []
    for document in documents or ():
        content = getattr(document, "page_content", None)
        if isinstance(content, str) and content:
            context.append(content)
        elif isinstance(document, str) and document:
            context.append(document)
    return context


class NeoMundiCallbackHandler(BaseCallbackHandler):
    """Measure every LLM interaction of a LangChain application.

    The handler is a thin driver for ``client.trace()``: the SDK measures
    latency, enforces the request contract and submits the observation.
    Nothing is reimplemented here.

    .. warning::
       **Every submitted observation is a billed measurement.** There is no
       trace-only route on the NeoMundi API. With the default
       ``sample_rate=1.0`` each LLM call of the host application is metered.
       Lower it, or pass ``submit=False``, before pointing this at a busy
       application.

    Parameters
    ----------
    client:
        An existing :class:`neomundi.NeoMundiClient`. When omitted, one is
        built from ``api_key`` and ``base_url`` -- both required by the SDK,
        which never assumes a host.
    sample_rate:
        Fraction of runs to submit, between 0.0 and 1.0. The decision is
        **deterministic in the run id**, so a given run always decides the
        same way and a sampled trace is reproducible.
    submit:
        ``False`` measures latency locally and opens no socket. Useful to
        wire the integration up at zero cost before enabling it.
    on_outcome:
        Called with each :class:`Outcome`. Exceptions raised by this
        callback are caught and never propagate.
    raise_errors:
        ``False`` (default) upholds rule 2: nothing escapes into the host
        chain. ``True`` re-raises instead, which is for this package's own
        tests and for debugging -- never for production.
    """

    #: LangChain checks this attribute before calling the handler.
    raise_error: bool = False
    run_inline: bool = True

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
        max_tracked_runs: int = 1024,
        raise_errors: bool = False,
    ) -> None:
        if not isinstance(sample_rate, (int, float)) or isinstance(sample_rate, bool):
            raise TypeError("sample_rate must be a number between 0.0 and 1.0")
        if not 0.0 <= float(sample_rate) <= 1.0:
            raise ValueError("sample_rate must be between 0.0 and 1.0")

        if client is None:
            if not api_key or not base_url:
                raise ValueError(
                    "Pass either an existing client, or both api_key and "
                    "base_url. The SDK never assumes a host."
                )
            client = NeoMundiClient(api_key=api_key, base_url=base_url)
        elif api_key or base_url:
            raise ValueError("Pass either a client or api_key/base_url, not both.")

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
        self._max_tracked = max(1, int(max_tracked_runs))
        self._raise_errors = bool(raise_errors)

        self._lock = threading.Lock()
        self._pending: OrderedDict[str, _Pending] = OrderedDict()
        self._parents: OrderedDict[str, str | None] = OrderedDict()
        self._documents: OrderedDict[str, list[str]] = OrderedDict()
        self._outcomes: list[Outcome] = []
        self._version_warned = False

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

    def reset(self) -> None:
        with self._lock:
            self._pending.clear()
            self._parents.clear()
            self._documents.clear()
            self._outcomes.clear()

    # ------------------------------------------------------------ internals

    def _should_sample(self, run_id: str) -> bool:
        if self._sample_rate >= 1.0:
            return True
        if self._sample_rate <= 0.0:
            return False
        digest = hashlib.sha256(run_id.encode("utf-8")).digest()[:8]
        position = int.from_bytes(digest, "big") / float(1 << 64)
        return position < self._sample_rate

    def _remember_parent(self, run_id: Any, parent_run_id: Any) -> None:
        key = str(run_id)
        with self._lock:
            self._parents[key] = str(parent_run_id) if parent_run_id else None
            self._parents.move_to_end(key)
            while len(self._parents) > self._max_tracked:
                self._parents.popitem(last=False)

    def _lineage(self, run_id: Any, parent_run_id: Any) -> list[str]:
        """Return ``[run_id, parent, grandparent, ..., root]``.

        Caller must hold ``self._lock``. Bounded, and cycle-safe.
        """
        lineage = [str(run_id)]
        seen = {lineage[0]}
        cursor = str(parent_run_id) if parent_run_id else None
        for _ in range(_ANCESTOR_WALK_LIMIT):
            if cursor is None or cursor in seen:
                break
            lineage.append(cursor)
            seen.add(cursor)
            cursor = self._parents.get(cursor)
        return lineage

    def _context_for(self, run_id: Any, parent_run_id: Any) -> list[str] | None:
        """Find the documents retrieved during this run's invocation.

        Keyed on the **root** of the run tree, not on the model run's
        ancestors. In a real LCEL RAG chain the retriever is not an
        ancestor of the model at all -- it sits in a sibling subtree:

            root
            ├── parallel
            │   └── "context" branch
            │       └── retriever      <- documents produced here
            └── model                  <- measurement opened here

        Walking up from the model therefore never reaches the retriever.
        What the two genuinely share is their root, so that is the key.
        Measured on langchain-core 1.8.5; see KNOWN_LIMITATIONS.md §5.
        """
        with self._lock:
            lineage = self._lineage(run_id, parent_run_id)
            # Nearest first, root last: a nested retrieval closest to this
            # model wins over one from an outer scope.
            for key in lineage:
                documents = self._documents.get(key)
                if documents:
                    return list(documents)
        return None

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

    def _pop(self, run_id: Any) -> _Pending | None:
        with self._lock:
            return self._pending.pop(str(run_id), None)

    def _warn_on_version(self, measurement_version: str | None) -> None:
        """Warn once when the served measurement version was not verified.

        Two results produced under two different measurement versions are
        not comparable for the same input. This warns; it never blocks.
        """
        if not measurement_version or self._version_warned:
            return
        verified = neomundi.API_CONTRACT.get("verified_measurement_versions", ())
        if measurement_version in tuple(verified):
            return
        self._version_warned = True
        warnings.warn(
            "NeoMundi served measurement_version "
            f"{measurement_version!r}, which is not among the versions this "
            f"SDK verified ({tuple(verified)!r}). Measurements taken under "
            "different measurement versions are not comparable for the same "
            "input.",
            RuntimeWarning,
            stacklevel=2,
        )

    def _fail(
        self, run_id: Any, status: str, reason: str, error: BaseException | None = None
    ) -> None:
        self._record(
            Outcome(
                run_id=str(run_id),
                status=status,
                submitted=False,
                reason=reason,
                error_type=type(error).__name__ if error is not None else None,
            )
        )

    # ------------------------------------------------------------- lifecycle

    def _open(
        self,
        run_id: Any,
        parent_run_id: Any,
        prompt: str | None,
        model_name: str | None,
    ) -> None:
        key = str(run_id)
        if not self._should_sample(key):
            self._record(
                Outcome(
                    run_id=key,
                    status=SKIPPED_SAMPLING,
                    reason=f"excluded by sample_rate={self._sample_rate}",
                )
            )
            return

        rag_context = self._context_for(run_id, parent_run_id)
        metadata = dict(self._metadata)
        trace = self._client.trace(
            client_trace_id=key,
            submit=self._submit,
            model_name=model_name,
            mode=self._mode,
            source_type=self._source_type,
            rag_context=rag_context or None,
            metadata=metadata or None,
        )
        trace.__enter__()
        with self._lock:
            self._pending[key] = _Pending(
                trace=trace,
                prompt=prompt if self._capture_prompt else None,
                model_name=model_name,
                rag_context=rag_context,
            )
            self._pending.move_to_end(key)
            while len(self._pending) > self._max_tracked:
                _, stale = self._pending.popitem(last=False)
                try:
                    stale.trace.__exit__(RuntimeError, RuntimeError("abandoned"), None)
                except Exception:  # noqa: BLE001
                    pass

    def _close(self, run_id: Any, response: Any) -> None:
        pending = self._pop(run_id)
        if pending is None:
            # Either sampling skipped it, or the run was never opened.
            return

        token_count = _extract_token_count(response)
        response_text = _extract_response_text(response) if self._capture_response else None

        if token_count is None:
            # Rule 1. The SDK would raise here; we refuse before it does so
            # that the refusal is a reported outcome, not an exception.
            latency = None
            try:
                pending.trace.record(
                    prompt=pending.prompt,
                    response=response_text,
                )
                latency = pending.trace.latency_ms
            except Exception:  # noqa: BLE001
                pass
            try:
                pending.trace.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            self._record(
                Outcome(
                    run_id=str(run_id),
                    status=NOT_SUBMITTED_NO_TOKEN_COUNT,
                    submitted=False,
                    latency_ms=latency,
                    model_name=pending.model_name,
                    reason=(
                        "the provider reported no token count; the API counts "
                        "nothing on this route and an estimate would be a "
                        "fabricated measurement"
                    ),
                )
            )
            return

        try:
            pending.trace.record(
                prompt=pending.prompt,
                response=response_text,
                token_count=token_count,
            )
            record = pending.trace.close()
        except Exception as exc:  # noqa: BLE001 - rule 2
            if self._raise_errors:
                raise
            self._fail(
                run_id,
                NOT_SUBMITTED_TRANSPORT_ERROR,
                "the measurement API could not be reached or refused the "
                "submission; the host application was not affected",
                exc,
            )
            return

        result = getattr(record, "result", None)
        audit = getattr(result, "audit", None)
        measurement_version = getattr(audit, "measurement_version", None)
        self._warn_on_version(measurement_version)

        submitted = bool(getattr(record, "submitted", False))
        status = SUBMITTED if submitted else NOT_SUBMITTED_LOCAL_ONLY
        self._record(
            Outcome(
                run_id=str(run_id),
                status=status,
                submitted=submitted,
                request_id=getattr(record, "request_id", None),
                latency_ms=getattr(record, "latency_ms", None),
                token_count=getattr(record, "token_count", None),
                model_name=getattr(record, "model_name", None) or pending.model_name,
                measurement_version=measurement_version,
                reason=None if submitted else "submit=False: nothing left the process",
            )
        )

    # ------------------------------------------------------- LangChain hooks

    def on_chat_model_start(
        self,
        serialized: dict[str, Any] | None,
        messages: Sequence[Sequence[Any]],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        try:
            self._remember_parent(run_id, parent_run_id)
            self._open(
                run_id,
                parent_run_id,
                _flatten_messages(messages),
                _extract_model_name(serialized, metadata, kwargs),
            )
        except Exception as exc:  # noqa: BLE001
            if self._raise_errors:
                raise
            self._fail(run_id, NOT_SUBMITTED_INTERNAL_ERROR, "failed to open the trace", exc)

    def on_llm_start(
        self,
        serialized: dict[str, Any] | None,
        prompts: Sequence[str],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        try:
            self._remember_parent(run_id, parent_run_id)
            prompt = "\n\n".join(p for p in (prompts or ()) if isinstance(p, str)) or None
            self._open(
                run_id,
                parent_run_id,
                prompt,
                _extract_model_name(serialized, metadata, kwargs),
            )
        except Exception as exc:  # noqa: BLE001
            if self._raise_errors:
                raise
            self._fail(run_id, NOT_SUBMITTED_INTERNAL_ERROR, "failed to open the trace", exc)

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        try:
            self._close(run_id, response)
        except Exception as exc:  # noqa: BLE001
            if self._raise_errors:
                raise
            self._fail(run_id, NOT_SUBMITTED_INTERNAL_ERROR, "failed to close the trace", exc)

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        pending = self._pop(run_id)
        if pending is None:
            return
        try:
            # The SDK's own refusal: a failed application block has no
            # complete interaction to measure, so it submits nothing.
            pending.trace.__exit__(type(error), error, None)
            latency = pending.trace.latency_ms
        except Exception:  # noqa: BLE001
            latency = None
        self._record(
            Outcome(
                run_id=str(run_id),
                status=NOT_SUBMITTED_APPLICATION_ERROR,
                submitted=False,
                latency_ms=latency,
                model_name=pending.model_name,
                reason="the application's LLM call failed; a partial measurement "
                "would be a fabricated observation",
                error_type=type(error).__name__,
            )
        )

    def on_retriever_end(
        self,
        documents: Sequence[Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        try:
            self._remember_parent(run_id, parent_run_id)
            context = _documents_to_context(documents)
            if not context:
                return
            with self._lock:
                # Register under the retriever's own run, its parent, and
                # the root of the invocation. The root is the key that
                # actually matches the model run, which lives in a sibling
                # subtree rather than below the retriever.
                lineage = self._lineage(run_id, parent_run_id)
                keys = {lineage[0], lineage[-1]}
                if parent_run_id:
                    keys.add(str(parent_run_id))
                for key in keys:
                    self._documents[key] = context
                    self._documents.move_to_end(key)
                while len(self._documents) > self._max_tracked:
                    self._documents.popitem(last=False)
        except Exception as exc:  # noqa: BLE001
            if self._raise_errors:
                raise
            self._fail(
                run_id, NOT_SUBMITTED_INTERNAL_ERROR, "failed to capture retrieved documents", exc
            )

    def on_chain_start(
        self,
        serialized: dict[str, Any] | None,
        inputs: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        # Recorded only to walk ancestry when attaching RAG context.
        try:
            self._remember_parent(run_id, parent_run_id)
        except Exception:  # noqa: BLE001
            pass

    def on_retriever_start(
        self,
        serialized: dict[str, Any] | None,
        query: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        try:
            self._remember_parent(run_id, parent_run_id)
        except Exception:  # noqa: BLE001
            pass

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(sample_rate={self._sample_rate}, "
            f"submit={self._submit}, submitted_count={self.submitted_count})"
        )
