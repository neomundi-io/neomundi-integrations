"""The examples must run, and must demonstrate what they claim.

Mission 001 shipped an example that showed the wrong refusal while looking
fine. These tests read what the examples print. Criterion A12.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _run(name: str) -> str:
    env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    result = subprocess.run(
        [sys.executable, str(EXAMPLES / name)],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_offline_agent_example_measures_each_model_call_then_dry_runs():
    out = _run("01_offline_agent.py")
    measured, dry = out.split("## ")[1:]
    assert "final_output  : Sunny in Paris." in measured
    assert measured.count("ResponseSpanData   submitted  tokens=14") == 2
    assert "tools=['weather']" in measured
    assert "FunctionSpanData   not_submitted_not_a_model_call  tool=weather" in measured
    assert measured.count("not_submitted_aggregate_span") == 3
    assert "requests sent : 2" in measured
    assert "sent          : token_count=[14, 14]" in measured
    assert "processors    : ['BatchTraceProcessor', 'NeoMundiTraceProcessor']" in measured
    assert dry.count("not_submitted_local_only  tokens=14") == 2
    assert "requests sent : 0" in dry
    assert "'NeoMundiTraceProcessor (retired)'" in dry


def test_refusals_example_shows_each_refusal_and_sends_nothing():
    out = _run("02_refusals.py")
    sections = out.split("## ")[1:]
    expected = [
        ("chat completions route", "GenerationSpanData", "not_submitted_unverified_token_count"),
        ("provider published no usage", "ResponseSpanData", "not_submitted_unverified_token_count"),
        ("run without sensitive data", "ResponseSpanData", "not_submitted_no_provider_response"),
        ("aggregate span", "TaskSpanData", "not_submitted_aggregate_span"),
        ("sample_rate = 0", "ResponseSpanData", "skipped_sampling"),
    ]
    assert len(sections) == len(expected)
    for section, (title, span, status) in zip(sections, expected, strict=True):
        assert section.startswith(title), section
        assert f"span          : {span}" in section, section
        assert f"status        : {status}" in section, section
        assert "requests sent : 0" in section, section
