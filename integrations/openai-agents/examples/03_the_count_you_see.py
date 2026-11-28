"""openai-agents-neomundi -- an agent run that reports 0 tokens, and why 0 is not a count.

    pip install openai-agents-neomundi && python examples/03_the_count_you_see.py

No key, no account, no socket: the provider is a function in this file that
answers on the Responses API -- the path this integration measures -- and
publishes no usage block.
"""

import asyncio

import httpx
from agents import Agent, OpenAIResponsesModel, Runner
from openai import AsyncOpenAI

from openai_agents_neomundi import NeoMundiTraceProcessor

processor = NeoMundiTraceProcessor(submit=False).install()  # <- line 1: nothing leaves the process


def silent_provider(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "resp_1",
            "object": "response",
            "created_at": 1,
            "model": "gpt-4o-mini",
            "status": "completed",
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": [],
            "output": [
                {
                    "type": "message",
                    "id": "m",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "Hello.", "annotations": []}],
                }
            ],
        },
    )  # no "usage": the provider said nothing about tokens


client = AsyncOpenAI(
    api_key="not-a-key",
    http_client=httpx.AsyncClient(transport=httpx.MockTransport(silent_provider)),
)
agent = Agent(name="a", model=OpenAIResponsesModel(model="gpt-4o-mini", openai_client=client))
result = asyncio.run(Runner.run(agent, "hi"))
processor.flush()  # <- line 2

print("the agent run reports :", result.context_wrapper.usage.total_tokens, "tokens")
for o in processor.outcomes:
    if o.span_type == "ResponseSpanData":
        print("traced to a provider  :", o.status)
        print("why                   :", o.reason)
