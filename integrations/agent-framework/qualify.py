"""E2 probe for the Microsoft Agent Framework integration.

Driven by ``tools/qualify_e2.py``, which owns the budget, the account
guard, the re-read of the NeoMundi observation and the evidence. This
file owns one thing: making real model calls through a real Agent
Framework client, with the middleware attached, so the measurements are
produced the way a user's would be.

🔴 **Why the refusal phases are here and billed nothing.** The claim this
integration makes is not "it can measure" — six others do that. It is
"it refuses to invent a count, and the framework it sits on does not
invent one either". A qualification that only exercises the happy path
would not test the claim.

⚠ **The provider must publish a usage block on the route used.** The
integration reads ``ChatResponse.usage_details``; if the provider is
silent, every phase refuses and the run proves the refusal rather than
the measurement. That is a valid outcome and the evidence will say so.
"""

from __future__ import annotations

import os
from typing import Any

PROBE_NAME = "agent-framework-neomundi middleware -> real Agent Framework chat client"

#: The questions. Deliberately bland: the prompts and responses of a
#: qualification run are not worth reading, and nothing about them should be.
QUESTIONS = (
    "What is the capital of France?",
    "What is the capital of Japan?",
)


def _chat_client(middleware: list[Any]) -> Any:
    """A real client pointed at a real OpenAI-compatible endpoint.

    Credentials come from the environment, never from an argument, and
    are never printed. ``NEOMUNDI_AF_BASE_URL`` and ``NEOMUNDI_AF_MODEL``
    say where to call; ``NEOMUNDI_AF_PROVIDER_KEY`` authenticates.
    """
    from agent_framework.openai import OpenAIChatCompletionClient
    from openai import AsyncOpenAI

    base_url = os.environ.get("NEOMUNDI_AF_BASE_URL", "").strip()
    model = os.environ.get("NEOMUNDI_AF_MODEL", "").strip()
    provider_key = os.environ.get("NEOMUNDI_AF_PROVIDER_KEY", "").strip()
    missing = [
        name
        for name, value in (
            ("NEOMUNDI_AF_BASE_URL", base_url),
            ("NEOMUNDI_AF_MODEL", model),
            ("NEOMUNDI_AF_PROVIDER_KEY", provider_key),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"missing environment variables: {', '.join(missing)}")

    return OpenAIChatCompletionClient(
        model=model,
        async_client=AsyncOpenAI(api_key=provider_key, base_url=base_url),
        middleware=middleware,
    )


def probe(client, *, submit: bool, metadata: dict) -> list[dict[str, Any]]:
    import asyncio

    return asyncio.run(_probe_async(client, submit=submit, metadata=metadata))


async def _probe_async(client, *, submit: bool, metadata: dict) -> list[dict[str, Any]]:
    from agent_framework_neomundi import NeoMundiChatMiddleware

    records: list[dict[str, Any]] = []
    measure = NeoMundiChatMiddleware(
        client=client if submit else None,
        submit=submit,
        metadata=metadata,
    )

    if not submit:
        # A dry run opens no socket to a provider either: there is nothing
        # honest to call without a key, and inventing a local server would
        # prove something about the server.
        for index, _question in enumerate(QUESTIONS, start=1):
            records.append(
                {
                    "phase": f"measure {index}",
                    "expectation": "dry run: nothing is called and nothing is sent",
                    "status": "not_submitted_local_only",
                    "submitted": False,
                    "reason": "no provider call in a dry run, and no socket opened",
                }
            )
        records.append(
            {
                "phase": "streaming",
                "expectation": "dry run: refused by name, as a real stream would be",
                "status": "not_submitted_streaming",
                "submitted": False,
                "reason": "no provider call in a dry run",
            }
        )
        return records

    chat = _chat_client([measure])

    # ---- the billed phases: real calls, measured ---------------------
    for index, question in enumerate(QUESTIONS, start=1):
        await chat.get_response(question)
        outcome = measure.last_outcome
        records.append(
            {
                "phase": f"measure {index}",
                "expectation": "a real model call, measured with the provider's own count",
                "status": outcome.status,
                "submitted": outcome.submitted,
                "request_id": outcome.request_id,
                "latency_ms": outcome.latency_ms,
                "token_count": outcome.token_count,
                "token_source": outcome.token_source,
                "model_name": outcome.model_name,
                "measurement_version": outcome.measurement_version,
                "reason": outcome.reason,
                # A transport failure without its type cannot be diagnosed
                # afterwards. The first real run proved that the hard way.
                "error_type": outcome.error_type,
            }
        )

    # ---- the refusals: real calls, nothing measured, nothing billed ---
    stream = chat.get_response(QUESTIONS[0], stream=True)
    async for _ in stream:
        pass
    outcome = measure.last_outcome
    records.append(
        {
            "phase": "streaming",
            "expectation": "refused by name: usage exists only after the stream is consumed",
            "status": outcome.status,
            "submitted": outcome.submitted,
            "reason": outcome.reason,
        }
    )

    # A second middleware with submit=False, on a real call: everything is
    # computed from a real response and nothing leaves the process.
    local = NeoMundiChatMiddleware(client=client, submit=False, metadata=metadata)
    await _chat_client([local]).get_response(QUESTIONS[0])
    outcome = local.last_outcome
    records.append(
        {
            "phase": "submit=False on a real call",
            "expectation": "the count is computed from a real response and never sent",
            "status": outcome.status,
            "submitted": outcome.submitted,
            "token_count": outcome.token_count,
            "reason": outcome.reason,
        }
    )

    return records
