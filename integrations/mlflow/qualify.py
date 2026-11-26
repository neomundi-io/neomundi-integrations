"""E2 probe for the MLflow integration: real model calls traced by MLflow, on Ollama.

Driven by ``tools/qualify_e2.py``, which owns the budget, the account
guard, the re-read and the evidence. This file owns one thing: how to make
an MLflow-traced application do real work with ``NeoMundiMlflowProcessor``
installed.

The host is what an MLflow user runs: the real ``openai`` client, traced by
the real ``mlflow.openai.autolog()`` into a throwaway local sqlite store,
pointed at Ollama's **OpenAI-compatible endpoint** (``/v1``).

Phases, on purpose -- **four billed observations**, the rest free:

* two Chat Completions calls inside an application span (``@mlflow.trace``,
  type AGENT): one observation each; the AGENT span is refused as an
  aggregate;
* one Responses API call (``/v1/responses``): one observation;
* one streamed call with ``stream_options.include_usage``: one observation;
* one streamed call **without** it: refused, the provider published no
  usage -- absent stays absent, on a real span;
* one hand-written LLM span carrying a count, the way LiteLLM's MLflow
  logger writes its estimate: refused as unverified, free.

Each billed call's NeoMundi ``request_id`` is also written on its MLflow
trace; the record says whether MLflow accepted the tag.

Verified before: Ollama's ``/v1/chat/completions`` and ``/v1/responses``
publish a ``usage`` block (otel and openai-agents E2). ⚠ Not verified:
that Ollama honours ``stream_options.include_usage``. If it does not, that
phase records ``not_submitted_no_token_count`` and bills nothing --
nothing is invented to fill in.

🔴 **The environment must be scrubbed by the caller** (``tools/probe.sh``
or ``env -i``): the ``openai`` client reads ``OPENAI_API_KEY`` and
``OPENAI_BASE_URL``. A probe must never leave for a paid provider. MLflow's
usage telemetry is disabled here: ``env -i`` removes the CI variables that
would otherwise disable it.

ASCII only in what is printed: an ``env -i`` shell on Windows loses its
UTF-8 console encoding.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

PROBE_NAME = "mlflow.openai.autolog + openai client -> ollama /v1"

MODEL = os.environ.get("NEOMUNDI_QUALIFY_MODEL", "llama3.2:3b")
OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

#: Ollama ignores the key, but the OpenAI client requires one to be present.
#: Obviously not a credential.
DUMMY_KEY = "ollama-local-no-credential"

MODEL_SPANS = ("LLM", "CHAT_MODEL")


def _records(phase: str, expectation: str, outcomes: list[Any]) -> list[dict[str, Any]]:
    calls = [o for o in outcomes if o.span_type in MODEL_SPANS]
    if not calls:
        return [
            {
                "phase": phase,
                "expectation": expectation,
                "status": "no_outcome_recorded",
                "submitted": False,
                "request_id": None,
                "latency_ms": None,
                "token_count": None,
                "reason": "MLflow produced no model-call span -- is a tracking store configured?",
            }
        ]
    aggregates = [o.status for o in outcomes if o.span_type not in MODEL_SPANS]
    out = []
    for index, o in enumerate(calls, start=1):
        out.append(
            {
                "phase": phase if len(calls) == 1 else f"{phase} / call {index}",
                "expectation": expectation,
                "status": o.status,
                "submitted": bool(o.submitted),
                "request_id": o.request_id,
                "latency_ms": o.latency_ms,
                "token_count": o.token_count,
                "token_source": o.token_source,
                "span_type": o.span_type,
                "writer": o.writer,
                "model_name": o.model_name,
                "trace_tagged": o.trace_tagged,
                "other_spans": aggregates,
                "measurement_version": o.measurement_version,
                "reason": o.reason,
            }
        )
    return out


def _failed(phase: str, expectation: str, exc: BaseException) -> dict[str, Any]:
    return {
        "phase": phase,
        "expectation": expectation,
        "status": "host_call_failed",
        "submitted": False,
        "request_id": None,
        "latency_ms": None,
        "token_count": None,
        "reason": f"the application call itself failed: {type(exc).__name__}",
    }


def probe(client, *, submit: bool, metadata: dict, http_client: Any = None) -> list[dict[str, Any]]:
    """Drive the host.

    ``http_client`` is for the offline test only: an ``httpx.Client`` on a
    mock transport, so the probe's logic is exercised without Ollama. The
    harness never passes it.
    """
    os.environ["MLFLOW_DISABLE_TELEMETRY"] = "true"
    os.environ["DO_NOT_TRACK"] = "true"
    store = tempfile.mkdtemp(prefix="mlflow-neomundi-qualify-")
    if not os.environ.get("MLFLOW_TRACKING_URI"):
        os.environ["MLFLOW_TRACKING_URI"] = "sqlite:///" + os.path.join(store, "mlflow.db")

    import mlflow
    from mlflow.entities import SpanType
    from openai import OpenAI

    from mlflow_neomundi import NeoMundiMlflowProcessor

    # Trace artifacts in the temp dir, not in ./mlruns under the current directory.
    if mlflow.get_experiment_by_name("neomundi-qualify-e2") is None:
        location = Path(store, "artifacts").as_uri()
        mlflow.create_experiment("neomundi-qualify-e2", artifact_location=location)
    mlflow.set_experiment("neomundi-qualify-e2")
    mlflow.openai.autolog()
    # Installed after the tracking URI is settled: a new URI resets MLflow's hooks.
    processor = NeoMundiMlflowProcessor(
        client if submit else None, submit=submit, metadata=metadata
    ).install()

    openai_client = OpenAI(
        base_url=f"{OLLAMA}/v1" if http_client is None else "https://ollama.example.invalid/v1",
        api_key=DUMMY_KEY,
        max_retries=0,
        timeout=120.0,
        http_client=http_client,
    )

    def messages(question: str) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": "Answer in one short sentence."},
            {"role": "user", "content": question},
        ]

    @mlflow.trace(name="qualify-app", span_type=SpanType.AGENT)
    def app(question: str) -> Any:
        reply = openai_client.chat.completions.create(model=MODEL, messages=messages(question))
        return reply.choices[0].message.content

    def responses_call() -> None:
        openai_client.responses.create(
            model=MODEL, input="In one sentence, what is an HTTP 301 redirect?"
        )

    def stream(include_usage: bool) -> None:
        kwargs: dict[str, Any] = {"stream": True}
        if include_usage:
            kwargs["stream_options"] = {"include_usage": True}
        for _ in openai_client.chat.completions.create(
            model=MODEL, messages=messages("In one sentence, what is a CSV file?"), **kwargs
        ):
            pass

    def hand_written_span() -> None:
        with mlflow.start_span(name="estimate-like", span_type=SpanType.LLM) as span:
            span.set_attribute(
                "mlflow.chat.tokenUsage",
                {"input_tokens": 14, "output_tokens": 7, "total_tokens": 21},
            )

    phases = [
        (
            "plain 1",
            "submitted, provider usage total; AGENT span refused as aggregate",
            lambda: app("In one sentence, what does the HTTP status code 404 mean?"),
        ),
        (
            "plain 2",
            "submitted, provider usage total",
            lambda: app("In one sentence, what is a SQL primary key?"),
        ),
        ("responses api", "submitted, provider usage total", responses_call),
        ("stream with usage", "submitted if Ollama honours include_usage", lambda: stream(True)),
        ("stream without usage", "refused: no token count", lambda: stream(False)),
        ("hand-written span", "refused: unverified writer", hand_written_span),
    ]

    records: list[dict[str, Any]] = []
    try:
        for phase, expectation, action in phases:
            processor.reset()
            try:
                action()
                processor.flush(timeout=120)
                records.extend(_records(phase, expectation, processor.outcomes))
            except Exception as exc:  # noqa: BLE001
                processor.flush(timeout=120)
                records.append(_failed(phase, expectation, exc))
    finally:
        processor.sample_rate = 0.0
        processor.uninstall()
    return records
