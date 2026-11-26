#!/usr/bin/env python3
"""The refusals, shown on purpose. Costs nothing: no key, no network.

An instrumentation layer is only trustworthy if you can see what it does
when things go wrong. This prints exactly that.

    python examples/02_refusals.py
"""

from __future__ import annotations

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.messages.ai import UsageMetadata
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.prompts import ChatPromptTemplate
from neomundi import NeoMundiClient

from langchain_neomundi import NeoMundiCallbackHandler

QUESTION = {"question": "What is the capital of France?"}
ANSWER = "Paris."


def client(status: int | None = None) -> NeoMundiClient:
    """A client that cannot reach the network. `status` forces a failure."""

    def respond(request: httpx.Request) -> httpx.Response:
        if status is None:
            raise httpx.ConnectError("unreachable")
        return httpx.Response(status, json={"detail": "refused"})

    return NeoMundiClient(
        api_key="ct_test_0000000000000000000000000000",
        base_url="https://example.invalid",
        transport=httpx.MockTransport(respond),
    )


class ReportsUsage(BaseChatModel):
    """A provider that does report its token count, like most do when not streaming."""

    @property
    def _llm_type(self) -> str:
        return "reports-usage"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
        message = AIMessage(
            content=ANSWER,
            usage_metadata=UsageMetadata(input_tokens=9, output_tokens=2, total_tokens=11),
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


def prompt():
    return ChatPromptTemplate.from_messages([("human", "{question}")])


def chain_without_usage():
    """A provider that reports no token count -- very common when streaming."""
    return prompt() | GenericFakeChatModel(messages=iter([ANSWER] * 10))


def chain_with_usage():
    """Needed for the API-failure cases: without a token count the handler
    refuses *before* it ever contacts the API, so no transport error would
    ever be reached."""
    return prompt() | ReportsUsage()


def show(label: str, handler: NeoMundiCallbackHandler, answer: str) -> None:
    outcome = handler.last_outcome
    print(f"\n{label}")
    print(f"  chain still answered : {answer!r}")
    print(f"  status               : {outcome.status}")
    print(f"  submitted            : {outcome.submitted}")
    print(f"  reason               : {outcome.reason}")


def main() -> None:
    print("=" * 70)
    print("Four refusals -- none of them breaks the chain, none is silent")
    print("=" * 70)

    handler = NeoMundiCallbackHandler(client())
    answer = chain_without_usage().invoke(QUESTION, config={"callbacks": [handler]})
    show("1. The provider reported no token count", handler, answer.content)

    handler = NeoMundiCallbackHandler(client(), sample_rate=0.0)
    answer = chain_without_usage().invoke(QUESTION, config={"callbacks": [handler]})
    show("2. Sampling excluded the run (no socket, nothing billed)", handler, answer.content)

    for status in (401, 402, 429):
        handler = NeoMundiCallbackHandler(client(status))
        answer = chain_with_usage().invoke(QUESTION, config={"callbacks": [handler]})
        show(f"3. The measurement API answered {status}", handler, answer.content)

    handler = NeoMundiCallbackHandler(client())
    answer = chain_with_usage().invoke(QUESTION, config={"callbacks": [handler]})
    show("4. The measurement API was unreachable", handler, answer.content)

    print("\n" + "=" * 70)
    print("In every case the application kept working and said why.")
    print("=" * 70)


if __name__ == "__main__":
    main()
