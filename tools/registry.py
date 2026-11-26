"""Assemble the library registry by scanning every integration.

There is no central registry file to edit. The source of truth is
``integrations/<slug>/INTEGRATION.yaml``, next to the code it describes, so
adding an integration touches no shared file and cannot conflict with
another integration's pull request.

Everything the library publishes -- the index, the catalogue, the badges,
``registry.json`` -- is derived from here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS_DIR = ROOT / "integrations"
ARCHIVED_DIR_NAME = "_archived"

#: Package lifecycle. Deliberately separate from evidence: a mature package
#: can be poorly evidenced, and a well-evidenced one can still be alpha.
MATURITY_ORDER = ("experimental", "beta", "stable", "maintained", "deprecated", "archived")

#: Evidence levels, reused verbatim from `neomundi-use-cases/CATALOG.md`
#: rather than redefined. Two competing maturity scales in one organisation
#: end up with both of them wrong.
EVIDENCE_LEVELS = {
    "e0": "nothing probative yet",
    "e1": "offline tests, green across platforms",
    "e2": "plus real execution against production, proof committed",
    "e3": "plus independently reproduced by a third party",
}

PUBLISH_TARGETS = ("pypi", "npm", "none")


class RegistryError(Exception):
    """A registry defect that must fail CI rather than be rendered."""


@dataclass
class Integration:
    slug: str
    path: Path
    data: dict[str, Any]
    archived: bool = False
    problems: list[str] = field(default_factory=list)

    # ---------------------------------------------------------------- views

    @property
    def name(self) -> str:
        return str(self.data.get("name") or self.slug)

    @property
    def maturity(self) -> str:
        return str(self.data.get("maturity", "experimental"))

    @property
    def evidence_level(self) -> str:
        return str((self.data.get("evidence") or {}).get("level", "e0")).lower()

    @property
    def package_name(self) -> str | None:
        return (self.data.get("package") or {}).get("name")

    @property
    def published_on(self) -> str:
        return str((self.data.get("package") or {}).get("published_on", "none"))

    @property
    def is_published(self) -> bool:
        return self.published_on in {"pypi", "npm"}

    @property
    def install_line(self) -> str:
        """Never claim an install command that does not work.

        The index renders this verbatim, so an unpublished package shows its
        repository path instead of a `pip install` that would fail.
        """
        if self.published_on == "pypi":
            return f"pip install {self.package_name}"
        if self.published_on == "npm":
            return f"npm install {self.package_name}"
        return f"pip install 'git+https://github.com/neomundi-io/neomundi-integrations.git#subdirectory=integrations/{self.slug}'"

    @property
    def rel_dir(self) -> str:
        prefix = f"integrations/{ARCHIVED_DIR_NAME}/" if self.archived else "integrations/"
        return f"{prefix}{self.slug}"

    def link(self, key: str) -> str | None:
        target = (self.data.get("links") or {}).get(key)
        return f"{self.rel_dir}/{target}" if target else None


def _require(data: dict[str, Any], dotted: str, problems: list[str]) -> Any:
    cursor: Any = data
    for part in dotted.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            problems.append(f"missing required field: {dotted}")
            return None
        cursor = cursor[part]
    if cursor in (None, "", [], {}):
        problems.append(f"required field is empty: {dotted}")
        return None
    return cursor


REQUIRED_FIELDS = (
    "slug",
    "name",
    "technology.name",
    "package.name",
    "package.version",
    "package.published_on",
    "maturity",
    "evidence.level",
    "compatibility.python",
    "value.host_keeps",
    "value.neomundi_adds",
    "entrypoint.snippet",
    "refusals",
    "links.readme",
    "links.limitations",
    "links.value",
)


def load_one(directory: Path, *, archived: bool = False) -> Integration:
    manifest = directory / "INTEGRATION.yaml"
    problems: list[str] = []
    if not manifest.is_file():
        raise RegistryError(f"{directory.name}: INTEGRATION.yaml is missing")

    data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise RegistryError(f"{directory.name}: INTEGRATION.yaml is not a mapping")

    for dotted in REQUIRED_FIELDS:
        _require(data, dotted, problems)

    if data.get("slug") != directory.name:
        problems.append(f"slug {data.get('slug')!r} does not match directory {directory.name!r}")
    if data.get("maturity") not in MATURITY_ORDER:
        problems.append(f"maturity {data.get('maturity')!r} is not one of {MATURITY_ORDER}")

    level = str((data.get("evidence") or {}).get("level", "")).lower()
    if level not in EVIDENCE_LEVELS:
        problems.append(f"evidence.level {level!r} is not one of {sorted(EVIDENCE_LEVELS)}")

    published = str((data.get("package") or {}).get("published_on", ""))
    if published not in PUBLISH_TARGETS:
        problems.append(f"package.published_on {published!r} is not one of {PUBLISH_TARGETS}")

    return Integration(
        slug=directory.name, path=directory, data=data, archived=archived, problems=problems
    )


def load_all() -> tuple[list[Integration], list[Integration]]:
    """Return (active, archived), each sorted by name."""
    if not INTEGRATIONS_DIR.is_dir():
        raise RegistryError("integrations/ does not exist")

    active: list[Integration] = []
    archived: list[Integration] = []

    for child in sorted(INTEGRATIONS_DIR.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if child.name == ARCHIVED_DIR_NAME:
            for grandchild in sorted(child.iterdir()):
                if grandchild.is_dir() and not grandchild.name.startswith("."):
                    archived.append(load_one(grandchild, archived=True))
            continue
        active.append(load_one(child))

    active.sort(key=lambda i: i.name.lower())
    archived.sort(key=lambda i: i.name.lower())
    return active, archived


def as_json(active: list[Integration], archived: list[Integration]) -> dict[str, Any]:
    """The machine-readable registry a website or a badge can consume."""

    def entry(integration: Integration) -> dict[str, Any]:
        payload = dict(integration.data)
        payload["directory"] = integration.rel_dir
        payload["archived"] = integration.archived
        payload["install"] = integration.install_line
        return payload

    return {
        "library": "neomundi-integrations",
        "generated_by": "tools/build_registry.py",
        "counts": {
            "active": len(active),
            "archived": len(archived),
            "evidence_e2_or_better": sum(1 for i in active if i.evidence_level in {"e2", "e3"}),
            "published": sum(1 for i in active if i.is_published),
        },
        "evidence_levels": EVIDENCE_LEVELS,
        "maturity_levels": list(MATURITY_ORDER),
        "integrations": [entry(i) for i in active],
        "archived": [entry(i) for i in archived],
    }


def write_json(path: Path | None = None) -> Path:
    active, archived = load_all()
    target = path or (ROOT / "registry.json")
    payload = as_json(active, archived)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target
