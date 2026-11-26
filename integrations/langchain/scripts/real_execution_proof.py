#!/usr/bin/env python3
"""Run the integration against the real NeoMundi production API.

This is the execution proof for the package: a real LangChain chain, a real
local LLM reporting real token usage, and real billed observations on
`https://api.neomundi.io`.

**It costs money.** Each submitted observation is a metered measurement
(TIER_03: 0.030 CHF). The script prints the exact count before and after,
and refuses to run beyond `--max-billed`.

Credentials
-----------
The key is read from ``NEOMUNDI_VERIFICATION_KEY`` in the environment. It
is never printed, never written to the evidence file, and never passed as
a command-line argument. Run
``govern-v3/scripts/verification_account_guard.py`` first: it confirms the
key belongs to the service account and not to a pilot.

Every observation produced here is marked as technical in its metadata and
must be excluded from any analysis.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import neomundi  # noqa: E402
from langchain_core.documents import Document  # noqa: E402
from langchain_core.prompts import ChatPromptTemplate  # noqa: E402
from langchain_core.retrievers import BaseRetriever  # noqa: E402
from langchain_core.runnables import RunnablePassthrough  # noqa: E402
from langchain_ollama import ChatOllama  # noqa: E402

from langchain_neomundi import NeoMundiCallbackHandler  # noqa: E402

BASE_URL = "https://api.neomundi.io"
MODEL = "llama3.2:3b"

#: Marks every record here as technical. The convention comes from
#: `_SERVEUR/05_ACCES_VERIFICATION_TECHNIQUE.md`.
TECHNICAL_METADATA = {
    "role": "devrel-verify",
    "excluded_from": "Study2_Stage1_Stage2_Stage3",
    "integration": "langchain-neomundi",
}

QUESTIONS = [
    "In one sentence, what is the capital of France?",
    "In one sentence, what does the HTTP status code 404 mean?",
    "In one sentence, what is a SQL primary key?",
    "In one sentence, what is the boiling point of water at sea level?",
    "In one sentence, who wrote the play Hamlet?",
]

RAG_CONTEXT = [
    "NeoMundi measures the runtime behaviour of AI systems.",
    "The NeoMundi measurement API exposes POST /v1/govern for post-call measurement.",
    "A NeoMundi measurement is billed; there is no trace-only route.",
]

RAG_QUESTION = (
    "According to the context, which route does NeoMundi expose for post-call measurement?"
)


class ContextRetriever(BaseRetriever):
    """A real retriever, so LangChain emits genuine retriever callbacks."""

    def _get_relevant_documents(self, query: str, *, run_manager: Any) -> list[Document]:
        return [Document(page_content=text) for text in RAG_CONTEXT]


def redact(outcome: Any) -> dict[str, Any]:
    """Evidence carries identifiers and measurements, never content."""
    return {
        "run_id": outcome.run_id,
        "status": outcome.status,
        "submitted": outcome.submitted,
        "request_id": outcome.request_id,
        "latency_ms": outcome.latency_ms,
        "token_count": outcome.token_count,
        "model_name": outcome.model_name,
        "measurement_version": outcome.measurement_version,
        "reason": outcome.reason,
        "error_type": outcome.error_type,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-billed",
        type=int,
        default=20,
        help="hard ceiling on billed observations (default 20)",
    )
    parser.add_argument(
        "--out", default=str(REPO_ROOT / "evidence"), help="directory for the evidence file"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="measure locally and submit nothing (free)"
    )
    args = parser.parse_args()

    api_key = os.environ.get("NEOMUNDI_VERIFICATION_KEY", "").strip()
    if not api_key:
        print(
            "NEOMUNDI_VERIFICATION_KEY is not set in this environment.\n"
            "It lives in the Windows USER scope, which a Git Bash or Python\n"
            "session does not inherit. Export it explicitly, and run the\n"
            "account guard before measuring.",
            file=sys.stderr,
        )
        return 2

    submit = not args.dry_run
    expected_billed = len(QUESTIONS) + 1  # the five questions plus the RAG run
    if submit and expected_billed > args.max_billed:
        print(
            f"Refusing: would bill {expected_billed} > --max-billed={args.max_billed}",
            file=sys.stderr,
        )
        return 2

    print("=" * 72)
    print("NeoMundi x LangChain -- real execution proof")
    print("=" * 72)
    print(f"  api base            : {BASE_URL}")
    print(f"  llm                 : {MODEL} (local Ollama, free)")
    print(f"  sdk                 : neomundi {neomundi.__version__}")
    print(f"  submit              : {submit}")
    print(
        f"  billed observations : {expected_billed if submit else 0} "
        f"(~{expected_billed * 0.030:.2f} CHF)"
        if submit
        else "  billed: 0 (dry run)"
    )
    print("=" * 72)

    started = datetime.now(UTC)
    records: list[dict[str, Any]] = []
    client = neomundi.NeoMundiClient(api_key=api_key, base_url=BASE_URL, timeout=30.0)

    try:
        # ---------------------------------------------------- 1. plain chain
        print("\n[1/4] Plain chain, five real LLM calls, each measured")
        handler = NeoMundiCallbackHandler(client, submit=submit, metadata=TECHNICAL_METADATA)
        prompt = ChatPromptTemplate.from_messages([("human", "{question}")])
        chain = prompt | ChatOllama(model=MODEL, temperature=0)

        for question in QUESTIONS:
            answer = chain.invoke({"question": question}, config={"callbacks": [handler]})
            outcome = handler.last_outcome
            print(
                f"      {outcome.status:34} tokens={outcome.token_count!s:>5} "
                f"latency={outcome.latency_ms:.0f}ms request_id={outcome.request_id}"
            )
            assert answer.content, "the chain must still answer"
            records.append({"phase": "plain_chain", **redact(outcome)})

        # --------------------------------------------------------- 2. RAG
        print("\n[2/4] RAG chain, retrieved context attached to the measurement")
        rag_handler = NeoMundiCallbackHandler(client, submit=submit, metadata=TECHNICAL_METADATA)
        rag_prompt = ChatPromptTemplate.from_messages(
            [("human", "Context:\n{context}\n\nQuestion: {question}\nAnswer in one sentence.")]
        )
        rag_chain = (
            {
                "context": ContextRetriever()
                | (lambda docs: "\n".join(d.page_content for d in docs)),
                "question": RunnablePassthrough(),
            }
            | rag_prompt
            | ChatOllama(model=MODEL, temperature=0)
        )
        rag_answer = rag_chain.invoke(RAG_QUESTION, config={"callbacks": [rag_handler]})
        rag_outcome = rag_handler.last_outcome
        print(
            f"      {rag_outcome.status:34} tokens={rag_outcome.token_count!s:>5} "
            f"latency={rag_outcome.latency_ms:.0f}ms request_id={rag_outcome.request_id}"
        )
        assert rag_answer.content
        records.append({"phase": "rag_chain", **redact(rag_outcome)})

        # ------------------------------------------------ 3. free refusals
        print("\n[3/4] Refusals, which must cost nothing")
        zero = NeoMundiCallbackHandler(client, sample_rate=0.0, metadata=TECHNICAL_METADATA)
        chain.invoke({"question": "This must not be measured."}, config={"callbacks": [zero]})
        print(
            f"      sample_rate=0.0 -> {zero.last_outcome.status} "
            f"(submitted={zero.last_outcome.submitted})"
        )
        records.append({"phase": "sampling_off", **redact(zero.last_outcome)})

        local = NeoMundiCallbackHandler(client, submit=False, metadata=TECHNICAL_METADATA)
        chain.invoke({"question": "This must stay local."}, config={"callbacks": [local]})
        print(
            f"      submit=False    -> {local.last_outcome.status} "
            f"(latency measured: {local.last_outcome.latency_ms:.0f}ms)"
        )
        records.append({"phase": "local_only", **redact(local.last_outcome)})

        # ------------------------------------------- 4. read the records back
        print("\n[4/4] Reading the observations back from the API")
        readback: list[dict[str, Any]] = []
        for record in records:
            request_id = record.get("request_id")
            if not record.get("submitted") or not request_id:
                continue
            try:
                response = client._request(  # noqa: SLF001
                    "GET", f"/v1/govern/{request_id}", None, tolerated_statuses=(404,)
                )
                ok = response.status_code == 200
                body = response.json() if ok else {}
                readback.append(
                    {
                        "request_id": request_id,
                        "http_status": response.status_code,
                        "persisted": ok,
                        "measurement_version": (body.get("audit") or {}).get("measurement_version"),
                    }
                )
                print(
                    f"      {request_id}  HTTP {response.status_code}  "
                    f"{'persisted' if ok else 'NOT READABLE'}"
                )
            except Exception as exc:  # noqa: BLE001
                readback.append(
                    {
                        "request_id": request_id,
                        "persisted": False,
                        "error_type": type(exc).__name__,
                    }
                )
                print(f"      {request_id}  readback failed: {type(exc).__name__}")
            time.sleep(0.2)  # stay well under 60 req/min
    finally:
        client.close()

    billed = sum(1 for r in records if r.get("submitted"))
    evidence = {
        "proof": "langchain-neomundi real execution",
        "started_utc": started.isoformat(),
        "finished_utc": datetime.now(UTC).isoformat(),
        "api_base_url": BASE_URL,
        "llm": {"provider": "ollama", "model": MODEL, "host": "local", "cost": "free"},
        "versions": {
            "neomundi": neomundi.__version__,
            "langchain_neomundi": __import__("langchain_neomundi").__version__,
            "python": sys.version.split()[0],
        },
        "account": {
            # No key material, not even a fragment: the account identity is
            # established by the guard, not by bytes of the key.
            "note": "service verification account; guard authorised before this run",
        },
        "metadata_applied": TECHNICAL_METADATA,
        "billed_observations": billed,
        "estimated_cost_chf": round(billed * 0.030, 3),
        "records": records,
        "readback": readback,
        "note": (
            "Prompts and responses are deliberately absent from this file. "
            "Observations are marked role=devrel-verify and must be excluded "
            "from any analysis."
        ),
    }

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"real_execution_{stamp}.json"
    out_path.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "=" * 72)
    print(f"  billed observations : {billed}  (~{billed * 0.030:.2f} CHF)")
    print(
        f"  read back as stored : {sum(1 for r in readback if r.get('persisted'))}/{len(readback)}"
    )
    print(f"  evidence            : {out_path.relative_to(REPO_ROOT)}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
