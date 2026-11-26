"""Criterion L5: composition with langchain-neomundi, on the real classes.

Skipped when ``langchain_neomundi`` is not installed -- it is not
published, so CI cannot install it from an index. Run it locally with:

    pip install -e ../langchain
    pytest tests/test_composition.py

The handler's outcome carries the ``request_id`` but **not** the measurement
result, so only the summary score can be written. This test pins that
limit down, so the day the handler exposes the result it fails and the
limitation can be lifted deliberately rather than noticed by accident.
"""

from __future__ import annotations

import pytest
from conftest import load_fixture, make_langfuse, make_neomundi

from langfuse_neomundi import SUMMARY_SCORE, NeoMundiLangfuseBridge

langchain_neomundi = pytest.importorskip("langchain_neomundi")
fake_chat = pytest.importorskip("langchain_core.language_models.fake_chat_models")
messages = pytest.importorskip("langchain_core.messages")


def _model():
    reply = messages.AIMessage(
        content="Paris.",
        usage_metadata={"input_tokens": 7, "output_tokens": 2, "total_tokens": 9},
    )
    return fake_chat.GenericFakeChatModel(messages=iter([reply]))


def test_a_real_handler_outcome_lands_on_the_langfuse_trace(recorder, trace_id):
    langfuse = make_langfuse(recorder=recorder)
    bridge = NeoMundiLangfuseBridge(langfuse)
    handler = langchain_neomundi.NeoMundiCallbackHandler(
        make_neomundi(), on_outcome=bridge.as_outcome_callback(trace_id=trace_id)
    )

    answer = _model().invoke("capital of France?", config={"callbacks": [handler]})
    langfuse.flush()

    assert answer.content == "Paris."
    assert handler.last_outcome.status == "submitted"
    sent = recorder.score_bodies()
    assert [s["name"] for s in sent] == [SUMMARY_SCORE]
    assert sent[0]["metadata"]["request_id"] == load_fixture()["request_id"]
    assert sent[0]["value"] == "submitted_no_result"


def test_the_handler_outcome_still_does_not_carry_the_result():
    """If this fails, the handler now exposes the result: lift LIMITATIONS §3."""
    assert "result" not in langchain_neomundi.Outcome.__dataclass_fields__
