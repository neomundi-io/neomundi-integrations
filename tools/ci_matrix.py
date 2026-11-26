#!/usr/bin/env python3
"""Emit the CI test matrix, discovered from the registry.

Printed as a ``GITHUB_OUTPUT`` line so the workflow never carries a
hand-written list of integrations. A new integration is tested as soon as
its directory exists.

Each integration declares the Python versions and platforms it supports in
its own ``INTEGRATION.yaml``; the matrix is their union, trimmed so the job
count stays sane: every declared Python on Linux, plus the newest declared
Python on each other platform.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from registry import RegistryError, load_all  # noqa: E402

RUNNER = {"linux": "ubuntu-latest", "macos": "macos-latest", "windows": "windows-latest"}


def main() -> int:
    try:
        active, _ = load_all()
    except RegistryError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1

    include: list[dict[str, str]] = []
    slugs: list[str] = []

    for integration in active:
        if integration.maturity == "archived":
            continue
        slugs.append(integration.slug)
        compat = integration.data.get("compatibility") or {}
        pythons = [str(v) for v in compat.get("python", [])] or ["3.12"]
        platforms = [str(p) for p in compat.get("platforms", [])] or ["linux"]

        for python in pythons:
            include.append({"slug": integration.slug, "python": python, "os": RUNNER["linux"]})
        newest = pythons[-1]
        for platform in platforms:
            if platform == "linux":
                continue
            runner = RUNNER.get(platform)
            if runner:
                include.append({"slug": integration.slug, "python": newest, "os": runner})

    # Two separate outputs on purpose. A matrix carrying both a bare list
    # and `include` makes GitHub spawn an extra job with half its keys
    # unset, which fails for a reason that has nothing to do with the code.
    print(f"matrix={json.dumps({'include': include}, separators=(',', ':'))}")
    print(f"slugs={json.dumps(slugs, separators=(',', ':'))}")
    print(f"discovered {len(slugs)} integration(s), {len(include)} job(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
