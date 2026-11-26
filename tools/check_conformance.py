#!/usr/bin/env python3
"""Refuse an integration that does not keep its own promises.

Run by CI on every pull request. Three classes of check:

1. **Skeleton.** Required files exist and are not empty. A library of a
   hundred integrations stays navigable only if every one of them has the
   same shape, and that is enforced here rather than hoped for.

2. **Evidence.** This is the one that matters. An integration claiming
   ``e2`` must carry a proof under ``evidence/`` that this script can open
   and read, reporting at least one submitted observation. **The badge is
   therefore a fact, not a claim** -- which is the whole reason a developer
   should trust the catalogue.

3. **Honesty of the install line.** A package marked ``published_on: none``
   may not be documented with a ``pip install <name>`` that would fail.

Exit code 0 when everything holds, 1 otherwise, with every problem listed
rather than only the first.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from registry import Integration, RegistryError, load_all  # noqa: E402

#: Every integration carries these, in this spelling. Uniformity is what
#: makes a hundred of them navigable.
REQUIRED_FILES = (
    "INTEGRATION.yaml",
    "README.md",
    "README_FR.md",
    "LIMITATIONS.md",
    "VALUE.md",
    "CHANGELOG.md",
    "pyproject.toml",
)

REQUIRED_DIRS = ("tests", "examples", "notebooks")

#: Below this, a file is a placeholder rather than a document.
MIN_BYTES = 400

#: A live key prefix must never reach a public repository.
LIVE_KEY = re.compile(r"ct_live_[A-Za-z0-9]{8,}")

SCANNED_SUFFIXES = {".py", ".md", ".toml", ".yml", ".yaml", ".json", ".ipynb", ".txt"}


def check_skeleton(integration: Integration) -> list[str]:
    problems: list[str] = []
    for name in REQUIRED_FILES:
        path = integration.path / name
        if not path.is_file():
            problems.append(f"missing {name}")
        elif name.endswith(".md") and path.stat().st_size < MIN_BYTES:
            problems.append(
                f"{name} is {path.stat().st_size} bytes, below {MIN_BYTES}: "
                "a placeholder is worse than an honest absence"
            )
    for name in REQUIRED_DIRS:
        directory = integration.path / name
        if not directory.is_dir() or not any(directory.iterdir()):
            problems.append(f"missing or empty {name}/")

    notebooks = list((integration.path / "notebooks").glob("*.ipynb"))
    if (integration.path / "notebooks").is_dir() and not notebooks:
        problems.append("notebooks/ contains no .ipynb: a reproducible notebook is required")
    return problems


def check_package_name(integration: Integration) -> list[str]:
    """The package must be named ``<slug>-neomundi``.

    Not cosmetic: the release workflow builds the PyPI project URL from the
    slug, and a trusted publisher is registered per project name. A package
    named otherwise would publish under a name nobody registered.
    """
    expected = f"{integration.slug}-neomundi"
    actual = integration.package_name
    if actual and actual != expected:
        return [
            f"package.name is {actual!r} but the convention is {expected!r}: "
            "the release workflow and the PyPI trusted publisher both derive "
            "from the slug"
        ]
    return []


def check_evidence(integration: Integration) -> list[str]:
    """The invariant: evidence is computed, never taken on trust."""
    problems: list[str] = []
    level = integration.evidence_level
    evidence = integration.data.get("evidence") or {}
    evidence_dir = integration.path / "evidence"

    if level in {"e0", "e1"}:
        # Nothing to verify beyond the test suite, which CI runs anyway.
        return problems

    if not evidence_dir.is_dir():
        return [f"claims evidence level {level} but has no evidence/ directory"]

    proof_rel = evidence.get("proof")
    if not proof_rel:
        return [f"claims {level} but evidence.proof names no file"]

    proof_path = integration.path / str(proof_rel)
    if not proof_path.is_file():
        return [f"claims {level} but evidence.proof points at a missing file: {proof_rel}"]

    try:
        payload = json.loads(proof_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"evidence.proof is not readable JSON ({type(exc).__name__}): {proof_rel}"]

    records = payload.get("records")
    if not isinstance(records, list) or not records:
        problems.append(f"evidence.proof carries no records: {proof_rel}")
        return problems

    submitted = [r for r in records if isinstance(r, dict) and r.get("submitted")]
    if not submitted:
        problems.append(
            f"claims {level} but evidence.proof reports no submitted observation: {proof_rel}"
        )

    declared = evidence.get("real_observations")
    if isinstance(declared, int) and declared != len(submitted):
        problems.append(
            f"evidence.real_observations says {declared} but the proof contains "
            f"{len(submitted)} submitted observation(s)"
        )

    verified = evidence.get("observations_verified_persisted")
    if isinstance(verified, int):
        readback = payload.get("readback")
        persisted = (
            sum(1 for r in readback if isinstance(r, dict) and r.get("persisted"))
            if isinstance(readback, list)
            else 0
        )
        if verified != persisted:
            problems.append(
                f"evidence.observations_verified_persisted says {verified} but the proof "
                f"shows {persisted} re-read as persisted"
            )

    if level == "e3" and not evidence.get("independent_review"):
        problems.append(
            "claims e3 but names no independent_review: e3 means reproduced by a third "
            "party, and the reference must be citable"
        )

    # A proof must never carry content or key material.
    raw = proof_path.read_text(encoding="utf-8")
    if LIVE_KEY.search(raw):
        problems.append(f"evidence.proof contains something shaped like a live key: {proof_rel}")
    return problems


def _fenced_blocks(markdown: str) -> list[str]:
    """Return the contents of every fenced code block.

    Only code blocks matter here: a reader copies those. Prose that says
    "`pip install x` does not work yet" is the honest disclosure we want to
    encourage, so flagging it would train people to disable this check.
    """
    blocks: list[str] = []
    inside = False
    current: list[str] = []
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            if inside:
                blocks.append("\n".join(current))
                current = []
            inside = not inside
            continue
        if inside:
            current.append(line)
    return blocks


def check_install_honesty(integration: Integration) -> list[str]:
    """An unpublished package may not offer a copyable install that fails."""
    problems: list[str] = []
    if integration.is_published:
        return problems
    package = integration.package_name
    if not package:
        return problems
    pattern = re.compile(rf"pip install\s+(?:-[\w-]+\s+)*{re.escape(package)}(?![\w\-/])")
    for name in ("README.md", "README_FR.md"):
        path = integration.path / name
        if not path.is_file():
            continue
        for block in _fenced_blocks(path.read_text(encoding="utf-8")):
            if pattern.search(block):
                problems.append(
                    f"{name} offers a copyable `pip install {package}` but "
                    f"package.published_on is {integration.published_on!r}: it would fail"
                )
                break
    return problems


def check_no_live_key(integration: Integration) -> list[str]:
    problems: list[str] = []
    for path in integration.path.rglob("*"):
        if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
            continue
        if "__pycache__" in path.parts or ".venv" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover
            continue
        if LIVE_KEY.search(text):
            problems.append(f"live key prefix found in {path.relative_to(integration.path)}")
    return problems


def main() -> int:
    try:
        active, archived = load_all()
    except RegistryError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1

    if not active and not archived:
        print("REFUSED: the library contains no integration", file=sys.stderr)
        return 1

    failed = False
    for integration in active + archived:
        problems = list(integration.problems)
        problems += check_skeleton(integration)
        problems += check_package_name(integration)
        problems += check_evidence(integration)
        problems += check_install_honesty(integration)
        problems += check_no_live_key(integration)

        label = f"{integration.slug} ({integration.maturity}, {integration.evidence_level})"
        if problems:
            failed = True
            print(f"FAIL  {label}")
            for problem in problems:
                print(f"        - {problem}")
        else:
            print(f"ok    {label}")

    if failed:
        print(
            "\nREFUSED: at least one integration does not keep its own promises.",
            file=sys.stderr,
        )
        return 1

    print(f"\nok: {len(active)} active, {len(archived)} archived, every claim verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
