"""One traced application call, measured -- then the same call with submit=False.

Runs offline, with no key: a real ``openai`` client traced by the real
``mlflow.openai.autolog()`` into a throwaway sqlite store; the provider is a
mock transport; the NeoMundi API is a mock replaying a captured response.
What it prints is what you would see against the real services.

The application wraps the model call in its own ``@mlflow.trace`` span, as
MLflow users do. That parent span is an aggregate and is refused by name;
the model call is measured, and its NeoMundi ``request_id`` is written back
on the MLflow trace, where you can read it next to the run.

    pip install -e ".[examples]"
    python examples/01_offline_autolog.py
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
client = provider_client()


@mlflow.trace(name="answer", span_type=SpanType.AGENT)
def answer(question: str) -> str:
    reply = client.chat.completions.create(
        model="example-model", messages=[{"role": "user", "content": question}]
    )
    return reply.choices[0].message.content


def run(submit: bool) -> None:
    sent: list[dict] = []
    # Install AFTER mlflow.set_tracking_uri: a new tracking URI resets MLflow's
    # span-processor hooks, and this one with them.
    processor = NeoMundiMlflowProcessor(
        neomundi_client(sent) if submit else None,
        submit=submit,
    ).install()

    print(f"answer        : {answer('What is the capital of France?')}")
    processor.flush()  # submissions run on a worker thread, off the call's path

    for o in processor.outcomes:
        line = f"  {o.span_type:<11} {o.span_name:<12} {o.status}"
        if o.token_count is not None:
            line += f"  writer={o.writer} tokens={o.token_count} latency_ms={o.latency_ms:.3f}"
        print(line)
    print(f"installed     : {processor.installed}")
    print(f"requests sent : {len(sent)}")
    if sent:
        print(f"sent          : token_count={[b['raw_metrics']['token_count'] for b in sent]}")
        trace = mlflow.get_trace(mlflow.get_last_active_trace_id(), flush=True)
        tags = sorted(k for k in trace.info.tags if k.startswith("neomundi."))
        print(f"trace tags    : {[k.split('.')[1] for k in tags]}")
        submitted = [o for o in processor.outcomes if o.submitted]
        key = f"neomundi.request_id.{submitted[0].span_id}"
        print(f"tag matches   : {trace.info.tags[key] == submitted[0].request_id}")


if __name__ == "__main__":
    print("## measured")
    run(submit=True)
    print("## submit=False (no key, no socket)")
    run(submit=False)
