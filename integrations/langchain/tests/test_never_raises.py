"""R3: nothing the handler does can break the host application.

These tests run a **real** LangChain chain with the handler attached, then
break the measurement API in every way it can break. The chain must still
return the user's answer, unchanged, every time.

This is the criterion that decides whether the integration is safe to
recommend to a developer. Instrumentation that can take down the
application it observes has negative value.
"""

from __future__ import annotations

import httpx
import pytest
from conftest import Recorder, UsageFakeChatModel, make_client
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.prompts import ChatPromptTemplate
from neomundi.errors import NeoMundiError

from langchain_neomundi import (
    NOT_SUBMITTED_INTERNAL_ERROR,
    NOT_SUBMITTED_NO_TOKEN_COUNT,
    NOT_SUBMITTED_TRANSPORT_ERROR,
    NeoMundiCallbackHandler,
)

ANSWER = "Paris is the capital of France."


def build_chain():
    """A real LCEL chain whose model reports usage, so submission is reached."""
    prompt = ChatPromptTemplate.from_messages([("human", "{question}")])
    return prompt | UsageFakeChatModel(answer=ANSWER)


def build_chain_without_usage():
    """A real LCEL chain whose model reports no usage at all."""
    prompt = ChatPromptTemplate.from_messages([("human", "{question}")])
    return prompt | GenericFakeChatModel(messages=iter([ANSWER] * 50))


def failing_api(status: int):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"detail": "refused"})

    return handler


def unreachable_api(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("name or service not known")


def timing_out_api(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("read timed out")


@pytest.mark.parametrize(
    "name, transport_handler",
    [
        ("401 unauthenticated", failing_api(401)),
        ("402 payment required", failing_api(402)),
        ("429 rate limited", failing_api(429)),
        ("500 server error", failing_api(500)),
        ("connection refused", unreachable_api),
        ("read timeout", timing_out_api),
    ],
)
def test_chain_still_answers_when_measurement_fails(name, transport_handler):
    recorder = Recorder()
    client = make_client(transport_handler, recorder=recorder)
    handler = NeoMundiCallbackHandler(client)  # raise_errors stays False

    chain = build_chain()
    result = chain.invoke(
        {"question": "What is the capital of France?"},
        config={"callbacks": [handler]},
    )

    # The whole point: the user's result is intact.
    assert result.content == ANSWER, f"chain broken by {name}"

    outcome = handler.last_outcome
    assert outcome is not None, f"{name} was not reported"
    assert outcome.submitted is False
    assert outcome.status == NOT_SUBMITTED_TRANSPORT_ERROR
    # Reported, not swallowed -- and no message body kept.
    assert outcome.error_type is not None
    assert outcome.reason is not None


def test_chain_answers_on_the_happy_path_too():
    """The control case: measurement working must not change the answer."""
    recorder = Recorder()
    client = make_client(recorder=recorder)
    handler = NeoMundiCallbackHandler(client)

    result = build_chain().invoke(
        {"question": "What is the capital of France?"},
        config={"callbacks": [handler]},
    )

    assert result.content == ANSWER
    assert recorder.count == 1
    assert handler.last_outcome.submitted is True


def test_a_defect_inside_the_handler_cannot_escape():
    """Even a bug in this package must not reach the host chain."""
    client = make_client()
    handler = NeoMundiCallbackHandler(client)

    # Simulate a defect on the opening path.
    def boom(*args, **kwargs):
        raise RuntimeError("defect inside the handler")

    handler._open = boom  # type: ignore[method-assign]

    result = build_chain().invoke(
        {"question": "What is the capital of France?"},
        config={"callbacks": [handler]},
    )

    assert result.content == ANSWER
    assert handler.last_outcome.status == NOT_SUBMITTED_INTERNAL_ERROR
    assert handler.last_outcome.error_type == "RuntimeError"


def test_on_outcome_callback_cannot_break_the_chain_either():
    """A caller's own reporting hook is wrapped too."""
    client = make_client()

    def bad_sink(outcome):
        raise RuntimeError("the user's sink is broken")

    handler = NeoMundiCallbackHandler(client, on_outcome=bad_sink)

    result = build_chain().invoke(
        {"question": "What is the capital of France?"},
        config={"callbacks": [handler]},
    )

    assert result.content == ANSWER
    assert handler.last_outcome is not None


def test_handler_is_usable_with_streaming_chain():
    """Streaming emits the same start/end hooks; nothing may break."""
    client = make_client()
    handler = NeoMundiCallbackHandler(client)

    chunks = list(
        build_chain().stream(
            {"question": "What is the capital of France?"},
            config={"callbacks": [handler]},
        )
    )

    assert "".join(c.content for c in chunks) == ANSWER


def test_raise_errors_is_opt_in_and_really_raises():
    """The debug switch must actually work, or tests would prove nothing."""
    from conftest import chat_result, run_chat

    client = make_client(unreachable_api)
    handler = NeoMundiCallbackHandler(client, raise_errors=True)

    # Specifically the SDK's own error hierarchy, not any exception: that
    # distinction is what proves the error came from the measurement layer.
    with pytest.raises(NeoMundiError):
        run_chat(handler, result=chat_result(input_tokens=1, output_tokens=1))


def test_outcome_history_is_bounded():
    """A long-running application must not grow a list forever."""
    client = make_client()
    handler = NeoMundiCallbackHandler(client, sample_rate=0.0, max_outcomes=10)

    from conftest import run_chat

    for _ in range(50):
        run_chat(handler)

    assert len(handler.outcomes) == 10


def test_pending_traces_are_bounded_when_runs_never_close():
    """An abandoned run must not leak a trace forever."""
    from uuid import uuid4

    from conftest import messages

    client = make_client()
    handler = NeoMundiCallbackHandler(client, submit=False, max_tracked_runs=5)

    for _ in range(30):
        handler.on_chat_model_start({"kwargs": {"model": "m"}}, messages(), run_id=uuid4())

    assert len(handler._pending) <= 5


def test_real_chain_whose_provider_reports_no_usage_submits_nothing():
    """R2, end to end: the refusal holds inside a real chain, not just in unit calls.

    Many LangChain providers report no usage when streaming. This is that
    case, and the chain must still answer while nothing is billed.
    """
    recorder = Recorder()
    client = make_client(recorder=recorder)
    handler = NeoMundiCallbackHandler(client)

    result = build_chain_without_usage().invoke(
        {"question": "What is the capital of France?"},
        config={"callbacks": [handler]},
    )

    assert result.content == ANSWER
    assert recorder.count == 0, "nothing billed when the provider counts nothing"
    assert handler.last_outcome.status == NOT_SUBMITTED_NO_TOKEN_COUNT
