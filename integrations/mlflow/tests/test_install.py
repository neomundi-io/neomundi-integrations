"""How the processor attaches to MLflow, and stays attached -- or says it did not.

Criteria M1, M2, M3. The hook is MLflow's public
``mlflow.tracing.configure(span_processors=[...])``.
"""

from __future__ import annotations

import mlflow
from conftest import chat, model_calls, use_experiment
from mlflow.tracing.config import get_config

from mlflow_neomundi import SUBMITTED, NeoMundiMlflowProcessor


def test_appends_and_keeps_app_processors(attach, recorder):
    """M1: the application's own span processor keeps running, first."""
    seen: list[str] = []

    def redact(span) -> None:
        seen.append(span.name)

    mlflow.tracing.configure(span_processors=[redact])
    processor = attach()
    assert get_config().span_processors[0] is redact
    chat()
    processor.flush()

    assert "Completions" in seen
    assert [o.status for o in model_calls(processor.outcomes)] == [SUBMITTED]


def test_app_can_still_configure(attach):
    """M2: a bound method would make this raise. The hook is a plain function."""
    attach()

    def other(span) -> None:
        pass

    mlflow.tracing.configure(span_processors=[*get_config().span_processors, other])
    with mlflow.tracing.configure(span_processors=[other]):
        pass


def test_install_twice_is_one_hook(attach, recorder):
    processor = attach()
    processor.install()
    chat()
    processor.flush()
    assert recorder.count == 1
    assert len(model_calls(processor.outcomes)) == 1


def test_second_processor_retires_the_first(attach, recorder):
    """Re-running a notebook cell must not bill every call twice."""
    first = attach()
    second = attach()
    chat()
    first.flush()
    second.flush()

    assert first.retired and not first.installed
    assert second.installed
    assert recorder.count == 1
    assert model_calls(first.outcomes) == []
    assert len(model_calls(second.outcomes)) == 1


def test_installed_survives_set_experiment(attach):
    processor = attach()
    use_experiment("mlflow-neomundi-tests-other")
    try:
        assert processor.installed
    finally:
        use_experiment("mlflow-neomundi-tests")


def test_installed_is_false_after_reset_and_install_puts_it_back(attach, recorder):
    processor = attach()
    mlflow.tracing.reset()
    assert not processor.installed
    chat()
    processor.flush()
    assert recorder.count == 0  # MLflow no longer calls it: said, not hidden

    processor.install()
    assert processor.installed
    chat()
    processor.flush()
    assert recorder.count == 1


def test_installed_is_false_when_the_app_replaces_the_list(attach):
    processor = attach()

    def other(span) -> None:
        pass

    mlflow.tracing.configure(span_processors=[other])
    assert not processor.installed


def test_uninstall_keeps_the_others(attach, recorder):
    def other(span) -> None:
        pass

    mlflow.tracing.configure(span_processors=[other])
    processor = attach()
    processor.uninstall()
    assert get_config().span_processors == [other]
    chat()
    assert recorder.count == 0


def test_processor_without_a_reference_keeps_measuring(recorder):
    """The caller may not keep the object; MLflow's hook keeps it alive."""
    import gc

    from conftest import make_client

    NeoMundiMlflowProcessor(make_client(recorder=recorder)).install()
    gc.collect()
    chat()
    hook = get_config().span_processors[-1]
    assert hook.__name__ == "neomundi_mlflow_hook"
    # Wait for the worker through a fresh handle on the same queue.
    import time

    deadline = time.monotonic() + 10
    while recorder.count == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert recorder.count == 1


def test_repr_says_whether_it_is_installed():
    processor = NeoMundiMlflowProcessor(submit=False)
    assert "installed=False" in repr(processor)
    processor.install()
    assert "installed=True" in repr(processor)
