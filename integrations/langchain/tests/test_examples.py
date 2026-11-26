"""The examples must run, and must demonstrate what they claim.

An example that silently shows the wrong refusal teaches the wrong thing.
`examples/02_refusals.py` once did exactly that: its API-failure cases used
a provider reporting no usage, so the handler refused before ever reaching
the network and the output said `not_submitted_no_token_count`. This test
exists so that cannot come back.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_refusals_example_runs_offline_and_shows_each_distinct_refusal():
    result = subprocess.run(
        [sys.executable, str(EXAMPLES / "02_refusals.py")],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout

    # Each claimed situation must produce its own, correct status.
    assert "not_submitted_no_token_count" in out
    assert "skipped_sampling" in out
    assert "not_submitted_transport_error" in out

    # The API-failure sections must not be reporting the token refusal.
    for marker in ("answered 401", "answered 402", "answered 429", "was unreachable"):
        section = out.split(marker, 1)[1].split("\n\n", 1)[0]
        assert "not_submitted_transport_error" in section, marker
        assert "no_token_count" not in section, marker

    # The guarantee the example exists to show.
    assert out.count("chain still answered : 'Paris.'") == 6


@pytest.mark.parametrize("name", ["01_minimal.py", "02_refusals.py"])
def test_example_is_syntactically_valid(name):
    source = (EXAMPLES / name).read_text(encoding="utf-8")
    compile(source, name, "exec")


def test_minimal_example_refuses_without_a_key():
    """It must fail loudly rather than reach for a default host or key.

    The key check has to come *before* the provider import. CI caught the
    opposite ordering: without the optional `langchain-ollama` installed,
    the example died on ModuleNotFoundError (exit 1) and never reached its
    own error message -- so a developer missing a key was told the wrong
    thing.
    """
    import os

    env = {k: v for k, v in os.environ.items() if k != "NEOMUNDI_API_KEY"}
    result = subprocess.run(
        [sys.executable, str(EXAMPLES / "01_minimal.py")],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert result.returncode == 2, result.stderr
    assert "NEOMUNDI_API_KEY" in result.stderr
    assert "ModuleNotFoundError" not in result.stderr


def test_minimal_example_explains_a_missing_optional_provider():
    """A missing optional provider must produce an instruction, not a trace."""
    import os

    env = dict(os.environ)
    env["NEOMUNDI_API_KEY"] = "ct_test_0000000000000000000000000000"
    # Make the optional provider unimportable without uninstalling it.
    env["PYTHONPATH"] = (
        str(Path(__file__).parent / "_blocked") + os.pathsep + env.get("PYTHONPATH", "")
    )
    result = subprocess.run(
        [sys.executable, str(EXAMPLES / "01_minimal.py"), "--dry-run"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert result.returncode == 3, (result.returncode, result.stderr)
    assert "pip install" in result.stderr
    assert "Traceback" not in result.stderr
