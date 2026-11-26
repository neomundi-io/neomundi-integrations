# Business value and ROI

## 1. Who this is for

A team that builds agents with the **OpenAI Agents SDK** (`openai-agents`,
about 20.6 M monthly downloads at the time of the mission 003 ranking). Its
agents call tools and hand off between agents. It wants the model calls
inside that loop measured by NeoMundi, without instrumenting each call
site, and without losing the traces it already sends to the OpenAI
dashboard.

## 2. What the technology keeps, and what NeoMundi adds

**The Agents SDK keeps** the agent loop, tools, handoffs, guardrails,
sessions and its tracing, including its own exporter to the OpenAI
dashboard and any processor the application registered. The processor
appends itself, and the suite proves that every other processor still
receives every span.

**NeoMundi adds** a signed, independently verifiable behavioural
measurement of **each model call the loop makes**. The token count comes
from the provider's own `Response` object, and the latency is the one the
SDK recorded on the span. Each outcome names the agent that made the call
and the tools that call requested.

**These are complements, not competitors.** The SDK's trace says what the
agent did and in what order. A NeoMundi observation says how the model
behaved at each step, signed and verifiable by a third party.

## 3. Where the value really is — and where it is not

### The steps between model calls are no longer invisible

`langchain-neomundi` and `langfuse-neomundi` both declare tool calls and
agent steps as a blind spot. Here, a run that asks for a tool and then
reads its result produces **two** observations, one per model call. The
first one's outcome says `tool_calls=('weather',)`. The tool execution
itself is reported by name. A behavioural regression that appears only
when the model decides on a tool, or only when it reads a tool's output,
becomes visible on its own.

### Provenance is established, not assumed

The count is the provider's `response.usage.total_tokens`, read from the
live object, which only a processor sees: `export()` drops it. Two things
the brief did not have were measured and are refused: the SDK's span usage
dicts (zero-filled, and on the LiteLLM route an estimate), and a
`Response` rebuilt by the `AnyLLMModel` extension. The guarantee is
narrower than "all agents", and it is real.

### Where it is not

- **Chat Completions and LiteLLM agents get nothing** but a named refusal.
  That is §1 of the limitations, and it is a choice: accepting those spans
  would mean accepting an estimate as a measurement.
- **It is not cheaper per call** than measuring the client directly. It is
  cheaper **to adopt**: one line, for every agent in the process.

## 4. ROI arithmetic

Per observation: 0.030 CHF (TIER_03). An agent run costs one observation
per model call. A three-step tool run costs 0.09 CHF at
`sample_rate=1.0`.

| Volume | sample_rate | Observations / month | Cost / month |
|---|---|---|---|
| 1 000 runs × 3 calls | 1.0 | 3 000 | 90 CHF |
| 1 000 runs × 3 calls | 0.1 | ≈ 300 | ≈ 9 CHF |
| 100 000 runs × 3 calls | 0.01 | ≈ 3 000 | ≈ 90 CHF |

Sampling is per **model call** (deterministic in the span id), not per run.
At `sample_rate=0.1` a run is therefore partially measured, which is the
right unit when the question is "how does the model behave at each step".

Start with `submit=False`. It is free, needs no key, and tells you on
your real traffic how many calls would be measured and how many would be
refused, and why.
