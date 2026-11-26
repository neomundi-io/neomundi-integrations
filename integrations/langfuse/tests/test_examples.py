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
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_offline_bridge_example_prints_the_six_scores_sent():
    out = _run("01_offline_bridge.py")
    assert "bridge status    : queued" in out
    assert "(6 scores)" in out
    assert "neomundi.coherence_score                 NUMERIC      0.8798" in out
    assert "neomundi.measurement                     CATEGORICAL  complete" in out


def test_refusals_example_shows_each_refusal_on_the_wire():
    out = _run("02_refusals.py")
    sections = out.split("## ")[1:]
    assert len(sections) == 4

    never_ran, no_trace, server_error, unreachable = sections
    assert "withheld      : coherence_score" in never_ran
    assert "neomundi.coherence_score" not in never_ran.split("scores landed")[1]

    assert "not_written_no_trace" in no_trace
    assert "scores landed : (none)" in no_trace

    # Langfuse failures: handed over, nothing landed, the host carried on.
    for section in (server_error, unreachable):
        assert "bridge status : queued" in section
        assert "scores landed : (none)" in section
        assert "host continued: yes" in section
