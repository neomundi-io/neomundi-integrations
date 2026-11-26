#!/usr/bin/env python3
"""Verify a built wheel ships what its package promises.

Takes the integration directory, so one script serves every integration:

    python tools/check_wheel.py integrations/langchain

A separate file rather than an inline shell snippet: the CI matrix includes
Windows, where a step's shell is PowerShell and a bash heredoc is a parse
error. That was learned the hard way.
"""

from __future__ import annotations

import glob
import sys
import tomllib
import zipfile
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check_wheel.py <integration directory>", file=sys.stderr)
        return 2

    directory = Path(sys.argv[1])
    config = tomllib.loads((directory / "pyproject.toml").read_text(encoding="utf-8"))
    project = config["project"]
    import_name = project["name"].replace("-", "_")

    wheels = sorted(glob.glob(str(directory / "dist" / "*.whl")))
    if not wheels:
        print(f"no wheel under {directory}/dist/", file=sys.stderr)
        return 1

    wheel = wheels[-1]
    names = zipfile.ZipFile(wheel).namelist()

    required = [f"{import_name}/__init__.py"]
    # A package that advertises `Typing :: Typed` must actually ship the
    # marker, or every consumer silently loses type information.
    if any("Typing :: Typed" in c for c in project.get("classifiers", [])):
        required.append(f"{import_name}/py.typed")

    missing = [name for name in required if name not in names]
    if missing:
        print(f"{wheel} is missing {missing}", file=sys.stderr)
        print(f"contents: {names}", file=sys.stderr)
        return 1

    print(f"ok: {Path(wheel).name} carries {len(required)} required entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
