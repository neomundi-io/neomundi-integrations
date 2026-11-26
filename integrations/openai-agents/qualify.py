"""E2 probe for the OpenAI Agents SDK integration: real agent runs on Ollama.

Driven by ``tools/qualify_e2.py``, which owns the budget, the account
guard, the re-read and the evidence. This file owns one thing: how to make
an Agents SDK application do real work with ``NeoMundiTraceProcessor``
installed.

The host is what a developer would run: a real ``Agent`` and the real
``Runner`` on ``OpenAIResponsesModel`` (the SDK's default model class),
pointed at Ollama's **OpenAI-compatible Responses endpoint** (``/v1/responses``).

Phases, on purpose -- **four billed observations**, the rest free:

* two runs without tools, one model call each (billed when ``submit=True``);
* one run with a function tool: the model asks for the tool, then reads its
  result -- two model calls, two observations, plus a tool span refused as
  not a model call. This is the blind spot the integration closes;
* one run on ``OpenAIChatCompletionsModel``: its generation span is
  refused, whatever the provider published -- the invariant, on a real span;
* one run with ``trace_include_sensitive_data=False``: refused, no provider
  object on the span.

⚠ Unverified before the first real run: that Ollama's ``/v1/responses``
returns a ``usage`` block, and that the chosen model performs the tool
call. If it does not, the records say so; nothing is invented to fill in.

🔴 **The environment must be scrubbed by the caller** (``tools/probe.sh``
or ``env -i``): the ``openai`` client reads ``OPENAI_API_KEY`` and
``OPENAI_BASE_URL``, and the SDK's default exporter sends traces to OpenAI
when a key is present. A probe must never leave for a paid provider.

ASCII only in what is printed: an ``env -i`` shell on Windows loses its
UTF-8 console encoding.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

PROBE_NAME = "openai-agents Runner + OpenAIResponsesModel -> ollama /v1/responses"

MODEL = os.environ.get("NEOMUNDI_QUALIFY_MODEL", "llama3.2:3b")
OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

#: Ollama ignores the key, but the OpenAI client requires one to be present.
#: Obviously not a credential.
DUMMY_KEY = "ollama-local-no-credential"

MODEL_SPANS = ("ResponseSpanData", "GenerationSpanData")


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
                "reason": "the run produced no model-call span",
            }
        ]
    tools = [o.tool_name for o in outcomes if o.tool_name]
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
                "model_name": o.model_name,
                "agent_name": o.agent_name,
                "tool_calls": list(o.tool_calls),
                "tools_executed": tools,
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
        "reason": f"the agent run itself failed: {type(exc).__name__}",
    }


def probe(client, *, submit: bool, metadata: dict, http_client: Any = None) -> list[dict[str, Any]]:
    """Drive the host.

    ``http_client`` is for the offline test only: an ``httpx.AsyncClient`` on
    a mock transport, so the probe's logic is exercised without Ollama. The
    harness never passes it.
    """
    from agents import (
        Agent,
        OpenAIChatCompletionsModel,
        OpenAIResponsesModel,
        RunConfig,
        Runner,
        function_tool,
    )
    from openai import AsyncOpenAI

    from openai_agents_neomundi import NeoMundiTraceProcessor

    processor = NeoMundiTraceProcessor(
        client if submit else None, submit=submit, metadata=metadata
    ).install()

    def openai_client() -> AsyncOpenAI:
        return AsyncOpenAI(
            base_url=f"{OLLAMA}/v1" if http_client is None else "https://ollama.example.invalid/v1",
            api_key=DUMMY_KEY,
            max_retries=0,
            timeout=120.0,
            http_client=http_client,
        )

    @function_tool
    def capital_of(country: str) -> str:
        """Return the capital city of a country."""
        return {"france": "Paris", "japan": "Tokyo"}.get(country.strip().lower(), "unknown")

    def agent(*, tools: bool = False, chat: bool = False) -> Any:
        model_cls = OpenAIChatCompletionsModel if chat else OpenAIResponsesModel
        return Agent(
            name="qualify-agent",
            instructions="Answer in one short sentence."
            + (" Use the capital_of tool for any capital city." if tools else ""),
            tools=[capital_of] if tools else [],
            model=model_cls(model=MODEL, openai_client=openai_client()),
        )

    phases = [
        (
            "plain 1",
            "submitted, provider usage total",
            agent(),
            {},
            "In one sentence, what does the HTTP status code 404 mean?",
        ),
        (
            "plain 2",
            "submitted, provider usage total",
            agent(),
            {},
            "In one sentence, what is a SQL primary key?",
        ),
        (
            "tool run",
            "two calls submitted around one tool execution",
            agent(tools=True),
            {},
            "What is the capital of Japan?",
        ),
        (
            "chat completions",
            "refused: a generation span's count is unverifiable",
            agent(chat=True),
            {},
            "Name one European capital, one word.",
        ),
        (
            "no sensitive data",
            "refused: no provider Response on the span",
            agent(),
            {"trace_include_sensitive_data": False},
            "Say hello in one word.",
        ),
    ]

    records: list[dict[str, Any]] = []
    try:
        for phase, expectation, the_agent, config, question in phases:
            processor.reset()
            try:
                asyncio.run(Runner.run(the_agent, question, run_config=RunConfig(**config)))
                processor.flush(timeout=120)
                records.extend(_records(phase, expectation, processor.outcomes))
            except Exception as exc:  # noqa: BLE001
                processor.flush(timeout=120)
                records.append(_failed(phase, expectation, exc))
    finally:
        processor.sample_rate = 0.0
    return records
