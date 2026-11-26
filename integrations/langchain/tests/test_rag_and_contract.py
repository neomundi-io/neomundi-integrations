"""R6, R9 and R10: RAG context, version drift and secret hygiene."""

from __future__ import annotations

import warnings
from uuid import uuid4

import httpx
import neomundi
import pytest
from conftest import (
    TEST_API_KEY,
    UsageFakeChatModel,
    load_fixture,
    make_client,
    run_chat,
)
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables import RunnablePassthrough

import langchain_neomundi
from langchain_neomundi import NeoMundiCallbackHandler

CONTEXT = [
    "France is a country in Western Europe.",
    "The capital city of France is Paris.",
]


class StubRetriever(BaseRetriever):
    """A real retriever, so LangChain emits genuine retriever callbacks."""

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        return [Document(page_content=text) for text in CONTEXT]


def rag_chain(model=None):
    """A real LCEL RAG chain, shaped the way applications actually build one."""
    prompt = ChatPromptTemplate.from_messages(
        [("human", "Context:\n{context}\n\nQuestion: {question}")]
    )
    return (
        {
            "context": StubRetriever() | (lambda docs: "\n".join(d.page_content for d in docs)),
            "question": RunnablePassthrough(),
        }
        | prompt
        | (model or UsageFakeChatModel())
    )


# --------------------------------------------------------------------- R6 RAG


def test_retrieved_documents_reach_the_measured_payload(client, recorder):
    """R6: a REAL LCEL RAG chain must put its context in the payload.

    This test previously asserted only that ``handler._documents`` had
    captured something, which stayed green while ``rag_context`` was still
    absent from the submitted body. The notebook caught that. Assert the
    payload, which is the thing that actually matters.
    """
    handler = NeoMundiCallbackHandler(client, raise_errors=True)

    rag_chain().invoke("What is the capital of France?", config={"callbacks": [handler]})

    assert recorder.count == 1
    assert recorder.bodies()[0]["rag_context"] == CONTEXT


def test_retriever_is_not_an_ancestor_of_the_model_run(client, recorder):
    """Pins *why* the lookup is keyed on the root rather than on ancestry.

    Measured on langchain-core 1.8.5: in an LCEL RAG chain the retriever
    sits in a sibling subtree of the model run, so walking up from the
    model never reaches it. What they share is their root.
    """
    seen: list[tuple[str, str | None]] = []

    class Spy(NeoMundiCallbackHandler):
        def on_retriever_end(self, documents, *, run_id, parent_run_id=None, **kwargs):
            seen.append(("retriever", str(parent_run_id) if parent_run_id else None))
            super().on_retriever_end(
                documents, run_id=run_id, parent_run_id=parent_run_id, **kwargs
            )

        def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, **kw):
            seen.append(("model", str(parent_run_id) if parent_run_id else None))
            super().on_chat_model_start(
                serialized, messages, run_id=run_id, parent_run_id=parent_run_id, **kw
            )

    handler = Spy(client, raise_errors=True)
    rag_chain().invoke("q", config={"callbacks": [handler]})

    parents = dict(seen)
    assert parents["retriever"] != parents["model"], (
        "they are siblings in different subtrees, which is the whole reason "
        "the context lookup is keyed on the shared root"
    )
    # And despite that, the context arrived.
    assert recorder.bodies()[0]["rag_context"] == CONTEXT


def test_rag_context_is_attached_to_a_sibling_llm_run(client, recorder):
    """The hand-driven ancestry case, kept as the narrow unit check."""
    handler = NeoMundiCallbackHandler(client, raise_errors=True)
    chain_run = uuid4()
    retriever_run = uuid4()

    handler.on_chain_start({}, {}, run_id=chain_run, parent_run_id=None)
    handler.on_retriever_start({}, "q", run_id=retriever_run, parent_run_id=chain_run)
    handler.on_retriever_end(
        [Document(page_content=text) for text in CONTEXT],
        run_id=retriever_run,
        parent_run_id=chain_run,
    )
    run_chat(handler, parent_run_id=chain_run)

    assert recorder.bodies()[0]["rag_context"] == CONTEXT


def test_no_retrieval_means_no_rag_context_key(client, recorder):
    """Absence must stay absence -- never an empty list standing in."""
    handler = NeoMundiCallbackHandler(client, raise_errors=True)

    run_chat(handler)

    assert recorder.bodies()[0].get("rag_context") is None


def test_empty_retrieval_is_not_recorded_as_context(client, recorder):
    handler = NeoMundiCallbackHandler(client, raise_errors=True)
    chain_run = uuid4()

    handler.on_retriever_end([], run_id=uuid4(), parent_run_id=chain_run)
    run_chat(handler, parent_run_id=chain_run)

    assert recorder.bodies()[0].get("rag_context") is None


def test_context_does_not_leak_between_separate_invocations(client, recorder):
    """One chain's retrieval must not attach to another chain's model run."""
    handler = NeoMundiCallbackHandler(client, raise_errors=True)

    rag_chain().invoke("first", config={"callbacks": [handler]})
    # A plain run, in its own run tree, with no retrieval of its own.
    run_chat(handler)

    bodies = recorder.bodies()
    assert bodies[0]["rag_context"] == CONTEXT
    assert bodies[1].get("rag_context") is None


# ----------------------------------------------------------------- R9 drift


def test_unverified_measurement_version_warns_but_does_not_block(recorder):
    """R9: warn on a version this SDK never verified. Never block."""
    payload = load_fixture("rest_complete_allow.json")
    payload["audit"] = dict(payload["audit"], measurement_version="9.9.9")

    def serve(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = make_client(serve, recorder=recorder)
    handler = NeoMundiCallbackHandler(client)

    with pytest.warns(RuntimeWarning, match="not comparable"):
        run_chat(handler)

    outcome = handler.last_outcome
    assert outcome.submitted is True, "a drift warning must not lose the measurement"
    assert outcome.measurement_version == "9.9.9"


def test_verified_version_does_not_warn(client, recorder):
    """3.3.1 is in the SDK's verified list, so silence is correct."""
    handler = NeoMundiCallbackHandler(client, raise_errors=True)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        run_chat(handler)
    assert handler.last_outcome.measurement_version == "3.3.1"


def test_production_version_350_is_currently_unverified_and_warns(client):
    """Pins a fact about today's production, not a hypothetical.

    Measured on 2026-11-25: ``api.neomundi.io`` serves
    ``measurement_version = 3.5.0``, and the SDK's own
    ``docs/COMPATIBILITY_350.md`` says 3.5.0 is deliberately **not** in
    ``verified_measurement_versions`` yet. So every production measurement
    through this integration warns once, by design.

    When the SDK qualifies 3.5.0 this test turns red, which is the intended
    signal to revisit KNOWN_LIMITATIONS.md section 1.
    """
    assert "3.5.0" not in neomundi.API_CONTRACT["verified_measurement_versions"]

    handler = NeoMundiCallbackHandler(client)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        handler._warn_on_version("3.5.0")
        handler._warn_on_version("3.5.0")

    assert len(caught) == 1, "warn once per handler, not once per call"
    assert "3.5.0" in str(caught[0].message)


def test_integration_contract_does_not_restate_the_measurement_version():
    """The SDK owns it; restating it here would let the two drift apart."""
    contract = langchain_neomundi.INTEGRATION_CONTRACT
    assert "measurement_version" not in contract
    assert contract["billed"] is True
    assert contract["neomundi_min"] == "2.1.0"


# ------------------------------------------------------------ R10 hygiene


def test_api_key_never_appears_in_an_outcome_or_repr(client):
    handler = NeoMundiCallbackHandler(client, raise_errors=True)

    run_chat(handler)

    blob = repr(handler) + repr(handler.outcomes)
    assert TEST_API_KEY not in blob
    assert "ct_test_" not in blob


def test_prompt_never_appears_in_an_outcome(client):
    """Outcomes are logged by callers; they must carry no content."""
    secret = "my-confidential-prompt-content"
    handler = NeoMundiCallbackHandler(client, raise_errors=True)

    run_chat(handler, prompt=secret)

    assert secret not in repr(handler.last_outcome)


def test_no_real_key_pattern_is_committed_in_this_repo():
    """R10: a live key prefix must never exist in the source tree."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1]
    live = re.compile(r"ct_live_[A-Za-z0-9]{8,}")
    offenders = []
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts or ".venv" in path.parts:
            continue
        if path.suffix not in {".py", ".md", ".toml", ".yml", ".yaml", ".json", ".ipynb", ".txt"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover
            continue
        if live.search(text):
            offenders.append(str(path.relative_to(root)))
    assert offenders == [], offenders
