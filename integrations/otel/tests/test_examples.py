"""The examples must run, and must demonstrate what they claim.

Mission 001 shipped an example that showed the wrong refusal while looking
fine. These tests read what the examples print.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _run(name: str) -> str:
    result = subprocess.run(
        [sys.executable, str(EXAMPLES / name)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_offline_interaction_example_submits_then_dry_runs():
    out = _run("01_offline_interaction.py")
    measured, dry = out.split("## ")[1:]
    assert "status        : submitted" in measured
    assert "token_count   : 14 (caller_declared)" in measured
    assert "sent          : token_count=14" in measured
    assert "requests sent : 1" in measured
    assert "same trace    : True" in measured
    assert "in the trace  : neomundi.request_id=None" not in measured
    assert "status        : not_submitted_local_only" in dry
    assert "requests sent : 0" in dry


def test_refusals_example_shows_each_refusal_and_sends_nothing():
    out = _run("02_refusals.py")
    sections = out.split("## ")[1:]
    expected = [
        ("span usage, nothing declared", "not_submitted_no_token_count", "present"),
        ("silent provider", "not_submitted_no_token_count", "absent"),
        ("two calls, one interaction", "not_submitted_ambiguous_spans", "not examined"),
        ("failed model call", "not_submitted_application_error", "absent"),
        ("sample_rate = 0", "skipped_sampling", "present"),
    ]
    assert len(sections) == len(expected)
    for section, (title, status, usage) in zip(sections, expected, strict=True):
        assert section.startswith(title), section
        assert f"status        : {status}" in section, section
        assert f"span usage    : {usage}" in section, section
        assert "requests sent : 0" in section, section
