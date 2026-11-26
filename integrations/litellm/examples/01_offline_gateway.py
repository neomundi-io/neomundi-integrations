"""Measure a LiteLLM completion -- offline, no key, no cost.

Real ``litellm.completion``, real ``NeoMundiClient``; the provider and the
NeoMundi API are mock transports. The NeoMundi mock replays a captured
production response. Prints what was sent to ``POST /v1/govern``, then the
same call in the free dry-run mode.

    python examples/01_offline_gateway.py

In your application, the whole integration is one line::

    litellm.callbacks = [NeoMundiLogger(api_key=..., base_url="https://api.neomundi.io")]
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import litellm
from neomundi import NeoMundiClient
from openai import OpenAI

from litellm_neomundi import NeoMundiLogger

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "rest_complete_allow.json"
MESSAGES = [{"role": "user", "content": "What is the capital of France?"}]


def provider() -> OpenAI:
    """An OpenAI-compatible provider that reports its token usage."""

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-example",
                "object": "chat.completion",
                "created": 1764115200,
                "model": "example-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Paris."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 14, "completion_tokens": 3, "total_tokens": 17},
            },
        )

    return OpenAI(
        api_key="sk-example",
        base_url="https://provider.example.invalid/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    )


def neomundi(sent: list[dict]) -> NeoMundiClient:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload = {k: v for k, v in raw.items() if not k.startswith("_fixture_")}

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=payload)

    return NeoMundiClient(
        api_key="ct_test_example_not_a_key",
        base_url="https://example.invalid",
        transport=httpx.MockTransport(handle),
    )


def run(logger: NeoMundiLogger) -> None:
    litellm.logging_callback_manager._reset_all_callbacks()  # see LIMITATIONS.md §4
    litellm.callbacks = [logger]
    answer = litellm.completion(model="openai/example-model", messages=MESSAGES, client=provider())
    print(f"   host got      : {answer.choices[0].message.content!r}")
    for _ in range(500):  # LiteLLM runs success hooks in a worker thread
        if logger.last_outcome is not None:
            break
        time.sleep(0.01)


def main() -> None:
    sent: list[dict] = []
    print("## measured")
    logger = NeoMundiLogger(neomundi(sent))
    run(logger)
    outcome = logger.last_outcome
    print(f"   status        : {outcome.status}")
    print(f"   request_id    : {'set' if outcome.request_id else 'none'}")
    print(f"   token_count   : {outcome.token_count} ({outcome.token_source})")
    print(f"   latency       : {'observed' if outcome.latency_ms is not None else 'none'}")
    metrics = sent[0]["raw_metrics"]
    print(f"   sent          : token_count={metrics['token_count']}, model={metrics['model_name']}")
    print(f"   requests sent : {len(sent)}")

    print("## dry run (submit=False, no key)")
    logger = NeoMundiLogger(submit=False)
    run(logger)
    outcome = logger.last_outcome
    print(f"   status        : {outcome.status}")
    print(f"   token_count   : {outcome.token_count} ({outcome.token_source})")
    print(f"   requests sent : {len(sent) - 1}")


if __name__ == "__main__":
    main()
