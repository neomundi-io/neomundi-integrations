"""Each refusal, on a real MLflow span, with what it costs: nothing.

Every case below would be *measurable* if the integration were willing to
guess. It is not. Each prints the status, the reason, and how many requests
reached the NeoMundi API -- zero, every time.

1. the provider published no usage: MLflow leaves the count absent, and
   absent stays absent;
2. the provider published a partial usage (no total): never rebuilt;
3. a span whose count was written by something other than MLflow's
   ``openai`` autolog -- here a hand-written LLM span, the way LiteLLM's
   MLflow logger writes its own estimate;
4. a tool span: not a model call;
5. the model call failed;
6. ``sample_rate = 0``.

    pip install -e ".[examples]"
    python examples/02_refusals.py
"""

from __future__ import annotations

from _offline import (  # sets MLflow's environment first
    neomundi_client,
    provider_client,
    use_experiment,
)

# isort: split
import mlflow  # noqa: E402
from mlflow.entities import SpanType  # noqa: E402

from mlflow_neomundi import NeoMundiMlflowProcessor  # noqa: E402

use_experiment("mlflow-neomundi-example")
mlflow.openai.autolog()


def ask(client) -> None:
    client.chat.completions.create(
        model="example-model", messages=[{"role": "user", "content": "Capital of France?"}]
    )


def case(title: str, action, **kwargs) -> None:
    sent: list[dict] = []
    processor = NeoMundiMlflowProcessor(neomundi_client(sent), **kwargs).install()
    try:
        action()
    except Exception as exc:  # noqa: BLE001 - the host's own error, shown not hidden
        print(f"## {title}\n  host raised   : {type(exc).__name__} (its own error, re-raised)")
    else:
        print(f"## {title}")
    processor.flush()
    [o] = [o for o in processor.outcomes if o.span_type not in ("AGENT", "CHAIN")]
    print(f"  span          : {o.span_type} writer={o.writer}")
    print(f"  status        : {o.status}")
    print(f"  reason        : {o.reason}")
    print(f"  requests sent : {len(sent)}")


def hand_written_llm_span() -> None:
    with mlflow.start_span(name="litellm-completion", span_type=SpanType.LLM) as span:
        span.set_attribute(
            "mlflow.chat.tokenUsage",
            {"input_tokens": 14, "output_tokens": 7, "total_tokens": 21},
        )


def tool_span() -> None:
    with mlflow.start_span(name="lookup", span_type=SpanType.TOOL):
        pass


case("provider published no usage", lambda: ask(provider_client(usage=None)))
case(
    "provider published a partial usage",
    lambda: ask(provider_client(usage={"prompt_tokens": 12, "completion_tokens": 2})),
)
case("count written by another instrumentation", hand_written_llm_span)
case("tool span", tool_span)
case("model call failed", lambda: ask(provider_client(http_status=500)))
case("sample_rate = 0", lambda: ask(provider_client()), sample_rate=0.0)
