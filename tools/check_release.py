#!/usr/bin/env python3
"""Refuse to publish anything that is not exactly the named, reviewed tag.

Reads RELEASE_SLUG and RELEASE_TAG from the environment, never as
arguments, so neither can be confused with a path.

Tag convention: ``<slug>-v<version>``, so the tag names both which
integration is being released and which version, and the two cannot drift
from the manifest.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402


def fail(message: str) -> int:
    print(f"REFUSED: {message}", file=sys.stderr)
    return 1


def main() -> int:
    slug = os.environ.get("RELEASE_SLUG", "").strip()
    tag = os.environ.get("RELEASE_TAG", "").strip()
    if not slug or not tag:
        return fail("RELEASE_SLUG and RELEASE_TAG must both be set")

    directory = Path("integrations") / slug
    if not directory.is_dir():
        return fail(f"no such integration: {slug}")

    prefix = f"{slug}-v"
    if not tag.startswith(prefix):
        return fail(f"tag {tag!r} does not start with {prefix!r}")
    version = tag[len(prefix) :]

    config = tomllib.loads((directory / "pyproject.toml").read_text(encoding="utf-8"))
    project = config["project"]
    if project["version"] != version:
        return fail(
            f"tag {tag!r} implies version {version!r}, "
            f"but pyproject declares {project['version']!r}"
        )

    manifest = yaml.safe_load((directory / "INTEGRATION.yaml").read_text(encoding="utf-8")) or {}
    declared = str((manifest.get("package") or {}).get("version", ""))
    if declared != version:
        return fail(
            f"INTEGRATION.yaml declares version {declared!r}, not {version!r}: "
            "the manifest and the package must agree before publication"
        )

    # An explicit, removable block, so publication is never a side effect
    # of merging something.
    if "Private :: Do Not Upload" in project.get("classifiers", []):
        return fail(
            "publication is intentionally blocked by the 'Private :: Do Not Upload' "
            "classifier; remove it only in a separate, approved activation commit"
        )

    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    try:
        at_tag = subprocess.check_output(["git", "rev-list", "-n", "1", tag], text=True).strip()
    except subprocess.CalledProcessError:
        return fail(f"tag {tag!r} does not exist")
    if head != at_tag:
        return fail(f"HEAD {head[:12]} is not the commit tagged {tag!r} ({at_tag[:12]})")

    print(f"ok: {project['name']} {version} at {tag} ({head[:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
