"""R1 and R4: a provider that reports its tokens produces an observation."""

from __future__ import annotations

from conftest import chat_result, run_chat

from langchain_neomundi import SUBMITTED, NeoMundiCallbackHandler


def test_submits_one_observation_and_returns_the_server_request_id(client, recorder):
    handler = NeoMundiCallbackHandler(client, raise_errors=True)

    run_chat(handler)

    assert recorder.count == 1, "exactly one billed call per measured interaction"
    outcome = handler.last_outcome
    assert outcome.status == SUBMITTED
    assert outcome.submitted is True
    # R1: the request_id comes from the server body, not from the run id.
    assert outcome.request_id == "00000000-0000-4000-8000-000000000331"
    assert outcome.run_id != outcome.request_id
    assert handler.submitted_count == 1


def test_token_count_is_the_providers_own_total(client, recorder):
    handler = NeoMundiCallbackHandler(client, raise_errors=True)

    run_chat(handler, result=chat_result(input_tokens=100, output_tokens=23))

    body = recorder.bodies()[0]
    assert body["raw_metrics"]["token_count"] == 123, "the provider's total, never recomputed"
    assert handler.last_outcome.token_count == 123


def test_prompt_and_response_reach_the_payload(client, recorder):
    handler = NeoMundiCallbackHandler(client, raise_errors=True)

    run_chat(handler, prompt="What is the capital of France?")

    body = recorder.bodies()[0]
    assert body["llm_prompt"] == "What is the capital of France?"
    assert body["llm_response"] == "Paris is the capital of France."
    assert body["raw_metrics"]["model_name"] == "gpt-4o"


def test_latency_is_measured_not_declared(client):
    """R4: latency is observed by the SDK's own clock, between the hooks."""
    import time

    handler = NeoMundiCallbackHandler(client, raise_errors=True)
    from uuid import uuid4

    from conftest import messages

    rid = uuid4()
    handler.on_chat_model_start({"kwargs": {"model": "m"}}, messages(), run_id=rid)
    time.sleep(0.05)
    handler.on_llm_end(chat_result(), run_id=rid)

    latency = handler.last_outcome.latency_ms
    assert latency is not None
    # Observed, so it must bracket the sleep and stay sane.
    assert 40.0 <= latency < 5_000.0


def test_capture_can_be_switched_off_without_losing_the_measurement(client, recorder):
    """Some applications may not send prompts off-process at all."""
    handler = NeoMundiCallbackHandler(
        client, capture_prompt=False, capture_response=False, raise_errors=True
    )

    run_chat(handler)

    body = recorder.bodies()[0]
    assert body.get("llm_prompt") is None
    assert body.get("llm_response") is None
    assert body["raw_metrics"]["token_count"] == 19
    assert handler.last_outcome.submitted is True


def test_metadata_is_forwarded(client, recorder):
    handler = NeoMundiCallbackHandler(
        client, metadata={"role": "devrel-verify", "excluded_from": "all"}, raise_errors=True
    )

    run_chat(handler)

    body = recorder.bodies()[0]
    assert body["metadata"]["role"] == "devrel-verify"


def test_several_runs_are_measured_independently(client, recorder):
    handler = NeoMundiCallbackHandler(client, raise_errors=True)

    for _ in range(3):
        run_chat(handler)

    assert recorder.count == 3
    assert handler.submitted_count == 3
    assert len({o.run_id for o in handler.outcomes}) == 3


def test_interleaved_runs_do_not_mix_up(client, recorder):
    """Concurrent LangChain runs overlap; each trace is keyed by run_id."""
    from uuid import uuid4

    from conftest import messages

    handler = NeoMundiCallbackHandler(client, raise_errors=True)
    first, second = uuid4(), uuid4()

    handler.on_chat_model_start({"kwargs": {"model": "a"}}, messages("Q1"), run_id=first)
    handler.on_chat_model_start({"kwargs": {"model": "b"}}, messages("Q2"), run_id=second)
    handler.on_llm_end(chat_result("A2", input_tokens=5, output_tokens=5), run_id=second)
    handler.on_llm_end(chat_result("A1", input_tokens=1, output_tokens=1), run_id=first)

    bodies = {b["raw_metrics"]["model_name"]: b for b in recorder.bodies()}
    assert bodies["a"]["llm_prompt"] == "Q1"
    assert bodies["a"]["raw_metrics"]["token_count"] == 2
    assert bodies["b"]["llm_prompt"] == "Q2"
    assert bodies["b"]["raw_metrics"]["token_count"] == 10
