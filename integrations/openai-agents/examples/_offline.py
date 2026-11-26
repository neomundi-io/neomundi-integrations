"""Offline stand-ins shared by the examples: a provider and a NeoMundi API.

Both are ``httpx.MockTransport``s, so nothing leaves the process. The
provider speaks the Responses API (the Agents SDK's default) and Chat
Completions; when the agent offers a tool and has not yet called it, the
provider asks for one call first, as a real model would.

The NeoMundi API replays a captured production ``POST /v1/govern``
response: the suite's fixture.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
from neomundi import NeoMundiClient
from openai import AsyncOpenAI

USAGE = {
    "input_tokens": 12,
    "input_tokens_details": {"cached_tokens": 0},
    "output_tokens": 2,
    "output_tokens_details": {"reasoning_tokens": 0},
    "total_tokens": 14,
}
CHAT_USAGE = {"prompt_tokens": 12, "completion_tokens": 2, "total_tokens": 14}
_DEFAULT = object()


def _tool_done(body: dict[str, Any]) -> bool:
    items = body.get("input")
    if isinstance(items, list):
        return any(isinstance(i, dict) and i.get("type") == "function_call_output" for i in items)
    return any(m.get("role") == "tool" for m in body.get("messages", []))


def provider_client(usage: Any = _DEFAULT) -> AsyncOpenAI:
    async def handler(request: httpx.Request) -> httpx.Response:
        await request.aread()
        body = json.loads(request.content)
        if request.url.path.endswith("/chat/completions"):
            out: dict[str, Any] = {
                "id": "chatcmpl-example",
                "object": "chat.completion",
                "created": 1764115200,
                "model": "example-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Sunny in Paris."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": CHAT_USAGE if usage is _DEFAULT else usage,
            }
            return httpx.Response(200, json=out)
        if body.get("tools") and not _tool_done(body):
            output = [
                {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_1",
                    "name": body["tools"][0]["name"],
                    "arguments": json.dumps({"city": "Paris"}),
                    "status": "completed",
                }
            ]
        else:
            output = [
                {
                    "type": "message",
                    "id": "msg_1",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {"type": "output_text", "text": "Sunny in Paris.", "annotations": []}
                    ],
                }
            ]
        out = {
            "id": "resp_example",
            "object": "response",
            "created_at": 1764115200,
            "model": "example-model",
            "status": "completed",
            "output": output,
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": [],
        }
        chosen = USAGE if usage is _DEFAULT else usage
        if chosen is not None:
            out["usage"] = chosen
        return httpx.Response(200, json=out)

    return AsyncOpenAI(
        api_key="not-a-key",
        base_url="https://provider.example.invalid/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def neomundi_client(sent: list[dict]) -> NeoMundiClient:
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=_captured_response())

    return NeoMundiClient(
        api_key="ct_test_example",
        base_url="https://example.invalid",
        transport=httpx.MockTransport(handler),
    )


def _captured_response() -> dict:
    fixture = (
        Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "rest_complete_allow.json"
    )
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    return {k: v for k, v in payload.items() if not k.startswith("_fixture_")}
