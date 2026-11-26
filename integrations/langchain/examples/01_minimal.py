#!/usr/bin/env python3
"""The smallest useful thing: measure one LLM call from a LangChain chain.

Run it for free first -- `submit=False` opens no socket:

    python examples/01_minimal.py --dry-run

Then, with a real key, measure for real (0.030 CHF per observation):

    export NEOMUNDI_API_KEY=ct_live_...
    python examples/01_minimal.py

The LLM here is a local Ollama model, which is free but is **not** a
dependency of this package. Install it with the `examples` extra:

    pip install "langchain-neomundi[examples]"
"""

from __future__ import annotations

import argparse
import os
import sys

from langchain_core.prompts import ChatPromptTemplate

from langchain_neomundi import NeoMundiCallbackHandler


def load_model(name: str):
    """Import the provider lazily, and say what to do if it is absent.

    Imported here rather than at module level so that a missing optional
    provider produces an instruction instead of a traceback -- and so the
    key check below runs first either way.
    """
    try:
        from langchain_ollama import ChatOllama
    except ModuleNotFoundError:
        print(
            "This example drives a local Ollama model, which is optional.\n"
            "  pip install 'langchain-neomundi[examples]'\n"
            "and make sure Ollama is running, or adapt this file to the\n"
            "provider you already use -- the handler is provider-agnostic.",
            file=sys.stderr,
        )
        raise SystemExit(3) from None
    return ChatOllama(model=name, temperature=0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="measure locally, submit nothing")
    parser.add_argument("--base-url", default="https://api.neomundi.io")
    parser.add_argument("--model", default="llama3.2:3b")
    args = parser.parse_args()

    # Checked before anything else is imported or built: a missing key is
    # the most likely mistake, and it must not hide behind a provider error.
    api_key = os.environ.get("NEOMUNDI_API_KEY")
    if not api_key:
        print("Set NEOMUNDI_API_KEY. The base URL is never assumed.", file=sys.stderr)
        return 2

    handler = NeoMundiCallbackHandler(
        api_key=api_key,
        base_url=args.base_url,
        submit=not args.dry_run,
    )

    chain = ChatPromptTemplate.from_messages([("human", "{question}")]) | load_model(args.model)

    answer = chain.invoke(
        {"question": "In one sentence, what is the capital of France?"},
        config={"callbacks": [handler]},
    )

    print(f"answer   : {answer.content.strip()}")

    outcome = handler.last_outcome
    print(f"status   : {outcome.status}")
    print(f"submitted: {outcome.submitted}")
    print(f"latency  : {outcome.latency_ms:.0f} ms (measured, not declared)")
    print(f"tokens   : {outcome.token_count}")
    print(f"requestid: {outcome.request_id}")

    if not outcome.submitted:
        # Never a silent gap: the handler always says why.
        print(f"reason   : {outcome.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
