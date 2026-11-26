"""The examples must run, and must demonstrate what they claim.

Mission 001 shipped an example that showed the wrong refusal while looking
fine. These tests read what the examples print. Criterion M13.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _run(name: str) -> str:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("OPENAI_API_KEY", "MLFLOW_TRACKING_URI")  # the example sets its own
    }
    result = subprocess.run(
        [sys.executable, str(EXAMPLES / name)],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
        cwd=str(EXAMPLES),
    )
    assert result.returncode == 0, result.stderr
    # MLflow's default artifact root is ./mlruns in the CURRENT directory.
    assert not (EXAMPLES / "mlruns").exists(), "the example wrote trace artifacts into the cwd"
    return result.stdout


def test_offline_example_measures_the_call_then_dry_runs():
    out = _run("01_offline_autolog.py")
    measured, dry = out.split("## ")[1:]
    assert "answer        : Paris." in measured
    assert "CHAT_MODEL  Completions  submitted  writer=openai tokens=14" in measured
    assert "AGENT       answer       not_submitted_aggregate_span" in measured
    assert "requests sent : 1" in measured
    assert "sent          : token_count=[14]" in measured
    assert "trace tags    : ['measurement_version', 'request_id']" in measured
    assert "tag matches   : True" in measured
    assert "not_submitted_local_only  writer=openai tokens=14" in dry
    assert "requests sent : 0" in dry
    assert "installed     : True" in dry


def test_refusals_example_shows_each_refusal_and_sends_nothing():
    out = _run("02_refusals.py")
    sections = out.split("## ")[1:]
    expected = [
        ("provider published no usage", "CHAT_MODEL writer=openai", "not_submitted_no_token_count"),
        (
            "provider published a partial usage",
            "CHAT_MODEL writer=openai",
            "not_submitted_invalid_token_count",
        ),
        (
            "count written by another instrumentation",
            "LLM writer=None",
            "not_submitted_unverified_token_count",
        ),
        ("tool span", "TOOL writer=None", "not_submitted_not_a_model_call"),
        ("model call failed", "CHAT_MODEL writer=openai", "not_submitted_application_error"),
        ("sample_rate = 0", "CHAT_MODEL writer=openai", "skipped_sampling"),
    ]
    assert len(sections) == len(expected)
    for section, (title, span, status) in zip(sections, expected, strict=True):
        assert section.startswith(title), section
        assert f"span          : {span}" in section, section
        assert f"status        : {status}" in section, section
        assert "requests sent : 0" in section, section
