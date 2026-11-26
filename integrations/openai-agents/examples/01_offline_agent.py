"""One agent run with a tool, measured -- then the same run with submit=False.

Runs offline, with no key: a real ``Agent`` and the real ``Runner`` on the
real ``OpenAIResponsesModel``, whose provider is a mock transport; the
NeoMundi API is a mock replaying a captured response. What it prints is
what you would see against the real services.

The run makes two model calls (one that asks for the tool, one that reads
its result), so it produces two observations -- and a tool span, two turn
spans, an agent span and a task span, each reported and refused by name.

    pip install -e ".[examples]"
    python examples/01_offline_agent.py
"""

from __future__ import annotations

import asyncio

from _offline import neomundi_client, provider_client
from agents import Agent, OpenAIResponsesModel, Runner, function_tool
from agents.tracing import get_trace_provider

from openai_agents_neomundi import NeoMundiTraceProcessor


@function_tool
def weather(city: str) -> str:
    """Return the weather for a city."""
    return f"sunny in {city}"


def run(submit: bool) -> None:
    sent: list[dict] = []
    processor = NeoMundiTraceProcessor(
        neomundi_client(sent) if submit else None,
        submit=submit,
    ).install()  # add_trace_processor: the SDK's own exporter stays

    agent = Agent(
        name="weather-agent",
        instructions="Answer in one short sentence.",
        tools=[weather],
        model=OpenAIResponsesModel(model="example-model", openai_client=provider_client()),
    )
    result = asyncio.run(Runner.run(agent, "What is the weather in Paris?"))
    processor.flush()  # submissions run on a worker thread, off the agent's path

    print(f"final_output  : {result.final_output}")
    for o in processor.outcomes:
        line = f"  {o.span_type:<18} {o.status}"
        if o.span_type == "ResponseSpanData":
            line += f"  tokens={o.token_count} latency_ms={o.latency_ms:.3f}"
            line += f" agent={o.agent_name} tools={list(o.tool_calls)}"
        elif o.tool_name:
            line += f"  tool={o.tool_name}"
        print(line)
    print(f"requests sent : {len(sent)}")
    if sent:
        print(f"sent          : token_count={[b['raw_metrics']['token_count'] for b in sent]}")
    # Read for display only. Installing a second NeoMundi processor retired
    # the first: it stays listed (the SDK cannot remove one) but bills nothing.
    names = [
        type(p).__name__ + (" (retired)" if getattr(p, "retired", False) else "")
        for p in get_trace_provider()._multi_processor._processors
    ]
    print(f"processors    : {names}")


if __name__ == "__main__":
    print("## measured (mock NeoMundi API)")
    run(submit=True)
    print("\n## submit=False (no key, no socket)")
    run(submit=False)
