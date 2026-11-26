#!/usr/bin/env python3
"""Verify the built wheel ships what it promises.

A separate file rather than an inline shell snippet: the CI matrix runs on
Windows too, where the step's shell is PowerShell and a bash heredoc is a
parse error. CI caught exactly that.
"""

from __future__ import annotations

import glob
import sys
import zipfile

REQUIRED = (
    "langchain_neomundi/__init__.py",
    "langchain_neomundi/callbacks.py",
    # Declared `Typing :: Typed`, so the marker has to actually be there.
    "langchain_neomundi/py.typed",
)


def main() -> int:
    wheels = sorted(glob.glob("dist/*.whl"))
    if not wheels:
        print("no wheel in dist/", file=sys.stderr)
        return 1

    wheel = wheels[-1]
    names = zipfile.ZipFile(wheel).namelist()
    missing = [name for name in REQUIRED if name not in names]
    if missing:
        print(f"{wheel} is missing: {missing}", file=sys.stderr)
        print(f"contents: {names}", file=sys.stderr)
        return 1

    print(f"ok: {wheel} carries {len(REQUIRED)} required entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
