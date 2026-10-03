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
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on the 3.10 CI leg
    import tomli as tomllib

PYPROJECT = Path(__file__).resolve().parents[3] / "pyproject.toml"
BIN = Path(sys.executable).parent


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


ALIASES = ("run", "serve", "play")


def test_the_operonx_command_lists_every_subcommand():
    """`operonx` came back with real work to dispatch. The 1.1.0 entry
    pointed at a module that did not exist; this one resolves, and its
    --help lists every subcommand."""
    assert _scripts()["operonx"] == "operonx.cli.main:main"
    got = subprocess.run(
        [str(BIN / "operonx"), "--help"], capture_output=True, text=True, timeout=60
    )
    assert got.returncode == 0
    for name in ("init", "guide", *ALIASES):
        assert f"    {name} " in got.stdout, got.stdout


@pytest.mark.parametrize("command", ALIASES)
def test_each_old_script_is_a_deprecated_alias(command: str):
    """`operonx-<command>` stays for one release, pointing at the alias
    that warns and then calls the subcommand's own main."""
    assert _scripts()[f"operonx-{command}"] == f"operonx.cli.aliases:{command}"


@pytest.fixture(scope="module")
def sample_project(tmp_path_factory):
    """A project with a service and a job: what `--list` has to show."""
    from operonx.cli.main import main

    root = tmp_path_factory.mktemp("cli") / "sample"
    assert main(["init", str(root), "--template", "hello"]) == 0
    return root


def _cli(argv, cwd) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "PYTHONPATH")}
    env["OPERONX_RUNS_DIR"] = str(Path(cwd) / ".operonx" / "runs")
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize("command", ["run", "serve"])
def test_list_is_the_same_both_ways(command: str, sample_project: Path):
    new = _cli([str(BIN / "operonx"), command, "--list"], sample_project)
    old = _cli([str(BIN / f"operonx-{command}"), "--list"], sample_project)
    assert new.returncode == old.returncode == 0, new.stderr + old.stderr
    assert new.stdout == old.stdout
    assert "sample" in new.stdout and ("greet" in new.stdout)
    assert new.stderr == ""


@pytest.mark.parametrize("command", ALIASES)
def test_each_alias_warns_once_and_behaves_the_same(command: str, tmp_path: Path):
    new = _cli([str(BIN / "operonx"), command, "--help"], tmp_path)
    old = _cli([str(BIN / f"operonx-{command}"), "--help"], tmp_path)
    assert new.returncode == old.returncode == 0, new.stderr + old.stderr
    assert new.stdout == old.stdout  # one parser: same usage, same flags
    assert f"usage: operonx {command}" in new.stdout
    warning = old.stderr.strip().splitlines()
    assert len(warning) == 1, old.stderr
    assert warning[0].startswith("DeprecationWarning:")
    assert f"use `operonx {command}`" in warning[0]
    assert new.stderr == ""


class TestToolsNamespaceMoved:
    """`operonx.tools` → `operonx.cli` (1.2.0). No shim: leaving one
    would keep `tools` occupied, which is the whole reason for the move
    — `operonx.agents` needs `tools` to mean *agent tools*."""

    def test_old_namespace_is_gone(self):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("operonx.tools")


def test_pack_is_gone():
    """`operonx pack` serialised graphs for the Rust runtime, which is
    dropped; it raised on any looping graph and nothing read its output.
    The command, its deprecated script and its module are gone together."""
    got = subprocess.run(
        [str(BIN / "operonx"), "--help"], capture_output=True, text=True, timeout=60
    )
    assert got.returncode == 0
    assert "    pack " not in got.stdout, got.stdout

    refused = subprocess.run(
        [str(BIN / "operonx"), "pack"], capture_output=True, text=True, timeout=60
    )
    assert refused.returncode == 2
    assert "invalid choice: 'pack'" in refused.stderr

    assert "operonx-pack" not in _scripts()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("operonx.cli.pack")
