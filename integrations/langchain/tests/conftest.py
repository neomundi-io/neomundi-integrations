"""Test harness: no network, no real key, no cost.

Every test goes through ``httpx.MockTransport``, which ships with httpx and
opens no socket. The approach is copied from the ``neomundi`` SDK's own
suite on purpose -- same tool, same guarantees, no extra dependency.

``https://example.invalid`` is reserved by RFC 2606: if a mock transport
were ever forgotten, DNS resolution would fail rather than reach a server.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.messages.ai import UsageMetadata
from langchain_core.outputs import ChatGeneration, ChatResult, LLMResult
from neomundi import NeoMundiClient

#: Obviously fake, with a server-valid prefix. No real key exists in this
#: repository, and `test_no_secret_leak.py` enforces that.
TEST_API_KEY = "ct_test_0000000000000000000000000000"
TEST_BASE_URL = "https://example.invalid"

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    """Load a JSON fixture, dropping its ``_fixture_*`` provenance keys.

    Those keys document where the payload came from; they are not part of
    the wire contract, so no test may depend on their presence.
    """
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return {k: v for k, v in payload.items() if not k.startswith("_fixture_")}


class Recorder:
    """Counts the requests the transport actually saw.

    ``len(recorder.requests) == 0`` is the only honest proof that nothing
    was billed.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    @property
    def count(self) -> int:
        return len(self.requests)

    def bodies(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for request in self.requests:
            try:
                out.append(json.loads(request.content.decode("utf-8")))
            except Exception:  # pragma: no cover
                out.append({})
        return out


def make_client(
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
    *,
    recorder: Recorder | None = None,
) -> NeoMundiClient:
    """Build a client whose transport cannot reach the network."""
    payload = load_fixture("rest_complete_allow.json")

    def default_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    inner = handler or default_handler

    def transport_handler(request: httpx.Request) -> httpx.Response:
        if recorder is not None:
            recorder.requests.append(request)
        return inner(request)

    return NeoMundiClient(
        api_key=TEST_API_KEY,
        base_url=TEST_BASE_URL,
        transport=httpx.MockTransport(transport_handler),
    )


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def client(recorder: Recorder) -> NeoMundiClient:
    return make_client(recorder=recorder)


# --------------------------------------------------------------------- LangChain


def chat_result(
    text: str = "Paris is the capital of France.",
    *,
    input_tokens: int | None = 12,
    output_tokens: int | None = 7,
) -> LLMResult:
    """An ``LLMResult`` as a chat model produces it.

    ``input_tokens=None`` models the provider that reports no usage at all
    -- the case that must never be papered over with an estimate.
    """
    if input_tokens is None or output_tokens is None:
        message = AIMessage(content=text)
    else:
        message = AIMessage(
            content=text,
            usage_metadata=UsageMetadata(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
            ),
        )
    return LLMResult(generations=[[ChatGeneration(message=message)]])


def messages(text: str = "What is the capital of France?") -> Sequence[Sequence[Any]]:
    return [[HumanMessage(content=text)]]


def run_chat(
    handler: Any,
    *,
    result: LLMResult | None = None,
    run_id: UUID | None = None,
    parent_run_id: UUID | None = None,
    model: str = "gpt-4o",
    prompt: str = "What is the capital of France?",
) -> UUID:
    """Drive one full chat-model run through the handler, as LangChain does."""
    rid = run_id or uuid4()
    handler.on_chat_model_start(
        {"kwargs": {"model": model}},
        messages(prompt),
        run_id=rid,
        parent_run_id=parent_run_id,
        metadata={"ls_model_name": model},
    )
    handler.on_llm_end(result if result is not None else chat_result(), run_id=rid)
    return rid


class UsageFakeChatModel(BaseChatModel):
    """A real chat model that reports token usage, as a provider does.

    ``GenericFakeChatModel`` reports none, which exercises the refusal path
    but never the submission path. Both are needed, so both exist.
    """

    answer: str = "Paris is the capital of France."
    input_tokens: int = 12
    output_tokens: int = 7

    @property
    def _llm_type(self) -> str:
        return "usage-fake-chat-model"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
        message = AIMessage(
            content=self.answer,
            usage_metadata=UsageMetadata(
                input_tokens=self.input_tokens,
                output_tokens=self.output_tokens,
                total_tokens=self.input_tokens + self.output_tokens,
            ),
        )
        return ChatResult(generations=[ChatGeneration(message=message)])
