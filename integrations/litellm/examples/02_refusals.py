"""What the logger refuses to submit, and what it never lets escape.

Offline: real ``litellm.completion``, mock provider, mock NeoMundi API, no
key, no cost. Each section prints the outcome and **how many requests
reached the NeoMundi mock**, so a refusal is visible as an absence on the
wire rather than asserted.

    python examples/02_refusals.py
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
import litellm
from litellm.llms.custom_httpx.http_handler import HTTPHandler
from neomundi import NeoMundiClient
from openai import AsyncOpenAI, OpenAI

from litellm_neomundi import NeoMundiLogger

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "rest_complete_allow.json"
MESSAGES = [{"role": "user", "content": "What is the capital of France?"}]
PROVIDER = "https://provider.example.invalid"


def openai_handler(report_usage: bool, status: int = 200):
    def handle(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "provider down"}})
        stream = json.loads(request.content).get("stream")
        usage = {"prompt_tokens": 14, "completion_tokens": 3, "total_tokens": 17}
        if stream:
            chunk = {"id": "c", "object": "chat.completion.chunk", "created": 1, "model": "m"}
            lines = [
                {**chunk, "choices": [{"index": 0, "delta": {"content": "Paris."}}]},
                {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ]
            if report_usage:
                lines.append({**chunk, "choices": [], "usage": usage})
            body = "".join(f"data: {json.dumps(x)}\n\n" for x in lines) + "data: [DONE]\n\n"
            return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})
        body = {
            "id": "c",
            "object": "chat.completion",
            "created": 1,
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Paris."},
                    "finish_reason": "stop",
                }
            ],
        }
        if report_usage:
            body["usage"] = usage
        return httpx.Response(200, json=body)

    return handle


def neomundi(sent: list, status: int | None = 200) -> NeoMundiClient:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload = {k: v for k, v in raw.items() if not k.startswith("_fixture_")}

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if status is None:
            raise httpx.ConnectError("unreachable", request=request)
        return httpx.Response(status, json=payload if status == 200 else {"detail": "down"})

    return NeoMundiClient(
        api_key="ct_test_example_not_a_key",
        base_url="https://example.invalid",
        transport=httpx.MockTransport(handle),
    )


def wait(logger: NeoMundiLogger) -> None:
    for _ in range(500):
        if logger.last_outcome is not None:
            return
        time.sleep(0.01)


def section(title: str, call, *, api_status: int | None = 200) -> None:
    sent: list = []
    logger = NeoMundiLogger(neomundi(sent, api_status))
    litellm.logging_callback_manager._reset_all_callbacks()  # see LIMITATIONS.md §4
    litellm.callbacks = [logger]
    try:
        answer = call()
        host = f"got {answer.choices[0].message.content!r}"
    except Exception as exc:  # the host's own exception, untouched
        host = f"raised its own {type(exc).__name__}"
    wait(logger)
    outcome = logger.last_outcome
    print(f"## {title}")
    print(f"   host          : {host}")
    print(f"   status        : {outcome.status}")
    print(f"   reason        : {outcome.reason}")
    print(f"   requests sent : {len(sent)}")


def sync_call(report_usage: bool = True, status: int = 200, **kwargs):
    client = OpenAI(
        api_key="sk-example",
        base_url=f"{PROVIDER}/v1",
        max_retries=0,
        http_client=httpx.Client(
            transport=httpx.MockTransport(openai_handler(report_usage, status))
        ),
    )
    return lambda: litellm.completion(
        model="openai/m", messages=MESSAGES, client=client, num_retries=0, **kwargs
    )


def ollama_silent():
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "llama3.2",
                "created_at": "2026-11-26T00:00:00Z",
                "message": {"role": "assistant", "content": "Paris."},
                "done": True,
            },
        )

    client = HTTPHandler(client=httpx.Client(transport=httpx.MockTransport(handle)))
    return lambda: litellm.completion(
        model="ollama_chat/llama3.2", api_base=PROVIDER, messages=MESSAGES, client=client
    )


def async_stream():
    async def handle(request: httpx.Request) -> httpx.Response:
        await request.aread()
        return openai_handler(True)(request)

    client = AsyncOpenAI(
        api_key="sk-example",
        base_url=f"{PROVIDER}/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )

    async def run():
        response = await litellm.acompletion(
            model="openai/m",
            messages=MESSAGES,
            client=client,
            stream=True,
            stream_options={"include_usage": True},
        )
        text = ""
        async for chunk in response:
            text += chunk.choices[0].delta.content or "" if chunk.choices else ""
        await asyncio.sleep(0.3)  # let LiteLLM's logging task run
        return type(
            "Answer",
            (),
            {"choices": [type("C", (), {"message": type("M", (), {"content": text})})]},
        )

    return lambda: asyncio.run(run())


def main() -> None:
    section("provider reports no usage: LiteLLM says 0 tokens", sync_call(report_usage=False))
    section("Ollama reports no counts: LiteLLM estimates them", ollama_silent())
    section("async stream: the provider's numbers are out of reach", async_stream())
    section("your LLM call fails", sync_call(status=500))
    section("NeoMundi answers 503", sync_call(), api_status=503)
    section("NeoMundi unreachable", sync_call(), api_status=None)


if __name__ == "__main__":
    main()
