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


def test_offline_gateway_example_submits_then_dry_runs():
    out = _run("01_offline_gateway.py")
    measured, dry = out.split("## ")[1:]
    assert "status        : submitted" in measured
    assert "token_count   : 17 (provider_total)" in measured
    assert "sent          : token_count=17" in measured
    assert "requests sent : 1" in measured
    assert "status        : not_submitted_local_only" in dry
    assert "requests sent : 0" in dry


def test_refusals_example_shows_each_refusal_on_the_wire():
    out = _run("02_refusals.py")
    sections = out.split("## ")[1:]
    assert len(sections) == 6
    silent, estimated, async_stream, app_error, api_503, unreachable = sections

    # Refused before any network contact: nothing reached NeoMundi.
    for section in (silent, estimated, async_stream):
        assert "status        : not_submitted_unverified_token_count" in section
        assert "requests sent : 0" in section
        assert "host          : got 'Paris.'" in section
    assert "async stream" in async_stream

    assert "raised its own InternalServerError" in app_error
    assert "not_submitted_application_error" in app_error
    assert "requests sent : 0" in app_error

    # API failures: it did try, failed, and the host carried on.
    for section in (api_503, unreachable):
        assert "status        : not_submitted_transport_error" in section
        assert "requests sent : 1" in section
        assert "host          : got 'Paris.'" in section
