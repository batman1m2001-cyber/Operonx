"""Every ``[project.scripts]`` target must actually resolve.

Regression guard. From the April 2026 Hush→Operon migration through
1.1.0, ``pyproject.toml`` declared ``operonx = "operonx.cli:main"``
pointing at a scaffolding CLI that the same migration deleted. It never
resolved — ``operonx --help`` was a ``ModuleNotFoundError`` in every
published release — and nothing caught it, because a console-script
target is only exercised when a human runs the shell command.

These tests import each declared target the way the generated script
wrapper does, so a dangling entry fails in CI instead of on a user's
first ``pip install``.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on the 3.10 CI leg
    import tomli as tomllib

PYPROJECT = Path(__file__).resolve().parents[3] / "pyproject.toml"


def _scripts() -> dict[str, str]:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh).get("project", {}).get("scripts", {})


def test_pyproject_is_where_we_think_it_is():
    """Guard the parents[3] hop — a moved test file must not silently
    turn every assertion below into a vacuous pass over ``{}``."""
    assert PYPROJECT.is_file(), f"pyproject.toml not found at {PYPROJECT}"
    assert _scripts(), "[project.scripts] is empty — did the table move?"


@pytest.mark.parametrize("name,target", sorted(_scripts().items()))
def test_console_script_target_resolves(name: str, target: str):
    """Import ``module:attr`` exactly as the script wrapper does."""
    module_path, _, attr = target.partition(":")
    assert attr, f"{name} = {target!r} — target needs a `module:callable` form"

    module = importlib.import_module(module_path)
    fn = getattr(module, attr, None)

    assert fn is not None, f"{name} = {target!r} — {module_path} has no `{attr}`"
    assert callable(fn), f"{name} = {target!r} — `{attr}` is not callable"


def test_no_umbrella_operonx_command():
    """Operonx is a library. The only shell surface it owes anyone is
    handing graph specs to the Rust runtime, so `operonx-pack` is the
    whole CLI. A dispatcher with nothing to dispatch to would be API
    surface we owe compatibility on forever — see
    operonx/agents/CONTRIBUTING.md, rung 2 of the Footprint Ladder.

    If a real second command ever lands, delete this test with the PR
    that adds it. Do not resurrect the 1.1.0 entry, which pointed at
    nothing.
    """
    assert "operonx" not in _scripts()
    assert importlib.util.find_spec("operonx.cli") is not None
    assert not hasattr(importlib.import_module("operonx.cli"), "main")


class TestPackMovedNamespace:
    """`operonx.tools` → `operonx.cli` (1.2.0). No shim: leaving one
    would keep `tools` occupied, which is the whole reason for the move
    — `operonx.agents` needs `tools` to mean *agent tools*."""

    def test_pack_lives_under_cli(self):
        from operonx.cli.pack import main

        assert callable(main)

    def test_old_namespace_is_gone(self):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("operonx.tools")
