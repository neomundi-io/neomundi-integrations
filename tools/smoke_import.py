#!/usr/bin/env python3
"""Import an installed integration from a clean environment.

    python tools/smoke_import.py integrations/langchain

Imports by the package's real name, checks the typing marker survived
packaging, and refuses to import from the source tree -- which would prove
nothing about the wheel.
"""

from __future__ import annotations

import importlib
import importlib.resources
import inspect
import sys
import tomllib
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: smoke_import.py <integration directory>", file=sys.stderr)
        return 2

    directory = Path(sys.argv[1]).resolve()
    config = tomllib.loads((directory / "pyproject.toml").read_text(encoding="utf-8"))
    project = config["project"]
    import_name = project["name"].replace("-", "_")

    module = importlib.import_module(import_name)
    origin = inspect.getfile(module)
    if "site-packages" not in origin:
        print(
            f"REFUSED: {import_name} imported from {origin}, not from site-packages: "
            "this proves nothing about the built wheel",
            file=sys.stderr,
        )
        return 1

    print(f"import   : {import_name} from site-packages")
    print(f"version  : {getattr(module, '__version__', '(none declared)')}")

    if any("Typing :: Typed" in c for c in project.get("classifiers", [])):
        marker = importlib.resources.files(import_name).joinpath("py.typed")
        if not marker.is_file():
            print("REFUSED: py.typed did not survive packaging", file=sys.stderr)
            return 1
        print("py.typed : present")

    print("clean-environment import ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
