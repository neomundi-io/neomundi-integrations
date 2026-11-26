#!/usr/bin/env python3
"""Refuse to publish anything that is not exactly the named, reviewed tag.

Run by the release workflow before a build. A separate file rather than an
inline heredoc, for the same reason as `check_wheel.py`: a step's shell is
not guaranteed to be bash.

Reads `RELEASE_TAG` from the environment. Never takes it as an argument,
so it cannot be confused with a path.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path


def fail(message: str) -> int:
    print(f"REFUSED: {message}", file=sys.stderr)
    return 1


def main() -> int:
    tag = os.environ.get("RELEASE_TAG", "").strip()
    if not tag:
        return fail("RELEASE_TAG is not set")

    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    project = config["project"]

    if project["name"] != "langchain-neomundi":
        return fail(f"unexpected project name {project['name']!r}")

    # The tag names the version, so the two cannot drift apart.
    expected = tag.removeprefix("v")
    if project["version"] != expected:
        return fail(
            f"tag {tag!r} implies version {expected!r}, "
            f"but pyproject declares {project['version']!r}"
        )

    # An explicit, removable block so that publication is never a side
    # effect of merging something.
    if "Private :: Do Not Upload" in project.get("classifiers", []):
        return fail(
            "publication is intentionally blocked by the "
            "'Private :: Do Not Upload' classifier; remove it only in a "
            "separate, approved activation commit"
        )

    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    try:
        at_tag = subprocess.check_output(["git", "rev-list", "-n", "1", tag], text=True).strip()
    except subprocess.CalledProcessError:
        return fail(f"tag {tag!r} does not exist")

    if head != at_tag:
        return fail(f"HEAD {head[:12]} is not the commit tagged {tag!r} ({at_tag[:12]})")

    print(f"ok: {project['name']} {project['version']} at {tag} ({head[:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
