"""The package's declarations about itself, confronted with their sources.

Every test here guards a drift that shipped once. ``langchain-neomundi``
0.2.0, installed from PyPI on 2026-11-27, reported ``__version__ == "0.1.0"``
and ``INTEGRATION_CONTRACT["neomundi_min"] == "2.1.0"`` under a
``neomundi>=2.2.0`` floor, and its quickstart notebook stopped with an
``AttributeError`` on a method 0.2.0 had removed. The suite was green
throughout, because it asserted the old values and nothing ran the notebook.

So each declaration is read back against the file that owns it, rather than
against a literal that has to be remembered.
"""

from __future__ import annotations

import ast
import json
import re
import tomllib
from pathlib import Path

import langchain_neomundi
from langchain_neomundi import NeoMundiCallbackHandler

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
BUILDER = ROOT / "notebooks" / "build_quickstart.py"
NOTEBOOK = ROOT / "notebooks" / "quickstart.ipynb"


def sdk_floor() -> str:
    for requirement in PYPROJECT["project"]["dependencies"]:
        match = re.fullmatch(r"neomundi>=([0-9.]+),<[0-9.]+", requirement.replace(" ", ""))
        if match:
            return match.group(1)
    raise AssertionError("pyproject.toml declares no `neomundi>=` floor")


def test_dunder_version_is_the_declared_version():
    assert langchain_neomundi.__version__ == PYPROJECT["project"]["version"]


def test_contract_states_the_real_sdk_floor():
    contract = langchain_neomundi.INTEGRATION_CONTRACT
    assert contract["neomundi_min"] == sdk_floor()


def test_manifest_states_the_real_sdk_floor():
    # Read as text: the suite runs with pytest alone, and PyYAML is not a
    # dependency of this package.
    manifest = (ROOT / "INTEGRATION.yaml").read_text(encoding="utf-8")
    match = re.search(r"^  neomundi_sdk:\n    min: \"([0-9.]+)\"", manifest, re.MULTILINE)
    assert match, "INTEGRATION.yaml has no compatibility.neomundi_sdk.min"
    assert match.group(1) == sdk_floor()


def builder_cells() -> list[tuple[str, str]]:
    """The ``(kind, source)`` of every cell, read from the builder's AST.

    The builder is parsed, not imported: importing it needs ``nbformat``,
    which only the ``notebook`` extra installs.
    """
    tree = ast.parse(BUILDER.read_text(encoding="utf-8"))
    cells_node = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "CELLS" for t in node.targets)
    )
    cells = []
    for call in cells_node.elts:
        assert isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        kind = {"md": "markdown", "code": "code"}[call.func.id]
        cells.append((kind, ast.literal_eval(call.args[0]).strip()))
    return cells


def test_notebook_calls_only_handler_attributes_that_exist(client):
    handler = NeoMundiCallbackHandler(client)
    missing = []
    for kind, source in builder_cells():
        if kind != "code":
            continue
        for node in ast.walk(ast.parse(source)):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in {"handler", "real_handler"}
                and not hasattr(handler, node.attr)
            ):
                missing.append(node.attr)
    assert not missing, f"the notebook uses handler attributes that do not exist: {missing}"


def comparable(kind: str, source: str) -> tuple[str, object]:
    """What must agree between the builder and the committed notebook.

    ``ruff format`` and ``ruff check`` (isort) run over the committed
    ``.ipynb`` and reshape its code: line breaks move and imports are
    reordered. So code is compared as the multiset of its top-level
    statements' ASTs, which is blind to both and to nothing else. Markdown
    is untouched by ruff and compared verbatim.
    """
    if kind != "code":
        return kind, source
    statements = sorted(ast.dump(node) for node in ast.parse(source).body)
    return kind, statements


def test_committed_notebook_is_the_builders_output():
    """A builder fixed without regenerating would leave the shipped notebook broken."""
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    committed = [comparable(c["cell_type"], "".join(c["source"])) for c in notebook["cells"]]
    expected = [comparable(kind, source) for kind, source in builder_cells()]
    assert committed == expected, "run `python notebooks/build_quickstart.py --execute`"
