# Business value and ROI

## 1. Who this is for

Teams that already trace their LLM application with MLflow: they keep
experiments, version models, and compare traces and runs side by side.
MLflow is the largest remaining Python-ecosystem candidate in the
library's ranking (about 24 M monthly PyPI downloads, mission 003). Its
users have one habit this integration speaks to directly: they **refuse to
compare results produced under different versions**.

## 2. What the technology keeps, and what NeoMundi adds

**MLflow keeps** its autolog instrumentation, the traces, the tracking
store, the UI, experiments, evaluation and run comparison. The
integration appends one function to MLflow's own span processors and
replaces nothing. Your own processors, redaction included, run first.

**NeoMundi adds** a signed, independently verifiable behavioural
measurement of each model call MLflow traced. It uses the provider's own
token count and the latency MLflow already recorded, so there is no second
timer and no second instrumentation. The NeoMundi `request_id` and
`measurement_version` are written **back on the MLflow trace**, so the
measurement sits next to the run you are comparing, not in a separate tool.

## 3. Where the value really is — and where it is not

### The measurement lives where the comparison happens

An MLflow user compares two runs by opening their traces. With this
integration each trace carries `neomundi.request_id.<span_id>`, which leads
to a verifiable observation, and `neomundi.measurement_version`. Two runs
whose measurement versions differ are not comparable. That is the
discipline the user already applies to model versions, now applied to the
measurement itself.

### Provenance is established, not assumed

MLflow puts token counts from every autolog under one attribute, with no
marker of origin. On a provider that published nothing, we measured a
zero fill (Agents SDK tracer), a local estimate (LiteLLM's logger) and
whatever a LangChain chat model computed, all stored exactly like a
published count. This integration measures only the writer shown to copy
the provider's usage verbatim and to leave it absent otherwise. It refuses
every other writer **by name**, so a user can see what is not measured and
why.

### Where it is not

- **Not every framework, today.** LangChain, LiteLLM and Agents SDK
  applications get refusals, not measurements, unless their underlying
  `openai` client is traced too. Accepting their counts would mean
  accepting an estimate as a measurement.
- **Not cheaper per call** than measuring the client directly. It is
  cheaper **to adopt**: one line, for every traced call in the process,
  and no change to the code that makes the calls.

## 4. ROI arithmetic

Per observation: 0.030 CHF (TIER_03). One observation per measured model
call. Aggregate spans (agent, chain, workflow, task) are never billed.

| Volume | sample_rate | Observations / month | Cost / month |
|---|---|---|---|
| 10 000 calls | 1.0 | 10 000 | 300 CHF |
| 10 000 calls | 0.1 | ≈ 1 000 | ≈ 30 CHF |
| 1 000 000 calls | 0.01 | ≈ 10 000 | ≈ 300 CHF |

Sampling is per model call, deterministic in the span id.

Start with `submit=False`. It is free, needs no key, and shows, on your
real traffic, how many traced calls would be measured, how many would be
refused, and why. For an MLflow user on LangChain or LiteLLM, that dry run
is the honest answer to "would this work for us".
