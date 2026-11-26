"""Each refusal, on a real agent run, and proof that none of them sends anything.

Offline, no key. One processor, installed once; each section is a real
``Runner.run`` on a real SDK model class, against a mock provider scripted
for the case. Every section prints ``requests sent : 0``.

    pip install -e ".[examples]"
    python examples/02_refusals.py
"""

from __future__ import annotations

import asyncio

from _offline import CHAT_USAGE, neomundi_client, provider_client
from agents import Agent, OpenAIChatCompletionsModel, OpenAIResponsesModel, RunConfig, Runner

from openai_agents_neomundi import NeoMundiTraceProcessor

SENT: list[dict] = []
PROCESSOR = NeoMundiTraceProcessor(neomundi_client(SENT)).install()


def section(title: str, model, *, span_type: str, **config) -> None:
    PROCESSOR.reset()
    before = len(SENT)
    agent = Agent(name="assistant", instructions="Answer briefly.", model=model)
    asyncio.run(Runner.run(agent, "What is the weather in Paris?", run_config=RunConfig(**config)))
    PROCESSOR.flush()
    (outcome,) = [o for o in PROCESSOR.outcomes if o.span_type == span_type]
    print(f"## {title}")
    print(f"span          : {span_type}")
    print(f"status        : {outcome.status}")
    print(f"reason        : {outcome.reason}")
    print(f"requests sent : {len(SENT) - before}")
    print()


if __name__ == "__main__":
    section(
        "chat completions route",
        OpenAIChatCompletionsModel(
            model="example-model", openai_client=provider_client(CHAT_USAGE)
        ),
        span_type="GenerationSpanData",
    )
    section(
        "provider published no usage",
        OpenAIResponsesModel(model="example-model", openai_client=provider_client(None)),
        span_type="ResponseSpanData",
    )
    section(
        "run without sensitive data",
        OpenAIResponsesModel(model="example-model", openai_client=provider_client()),
        span_type="ResponseSpanData",
        trace_include_sensitive_data=False,
    )
    section(
        "aggregate span",
        OpenAIResponsesModel(model="example-model", openai_client=provider_client()),
        span_type="TaskSpanData",
        trace_include_sensitive_data=False,
    )
    PROCESSOR.sample_rate = 0.0
    section(
        "sample_rate = 0",
        OpenAIResponsesModel(model="example-model", openai_client=provider_client()),
        span_type="ResponseSpanData",
    )
