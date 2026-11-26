#!/usr/bin/env python3
"""Print the upstream packages to force to their newest release.

Used by the weekly drift watch. Reads the integration's declared target and
SDK rather than guessing, so a new integration is covered automatically.

    python tools/upstream_targets.py integrations/langchain
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: upstream_targets.py <integration directory>", file=sys.stderr)
        return 2
    manifest = Path(sys.argv[1]) / "INTEGRATION.yaml"
    data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    compat = data.get("compatibility") or {}

    targets: list[str] = []
    for key in ("target", "neomundi_sdk"):
        block = compat.get(key) or {}
        name = block.get("name")
        if name:
            targets.append(str(name))
    # The SDK block names no package, by convention: it is always `neomundi`.
    if (compat.get("neomundi_sdk") or {}) and "neomundi" not in targets:
        targets.append("neomundi")

    print(" ".join(targets))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
