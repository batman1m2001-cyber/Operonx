"""`operonx project`: handed to operonx-project, untouched; never syncs the guide first."""

from __future__ import annotations

import sys
import types

import pytest

from operonx.cli import main as cli_main
from operonx.cli.main import main

pytestmark = pytest.mark.unit


def test_without_operonx_project_it_says_how_to_install(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "operonx_project.cli", None)  # import fails
    assert main(["project", "status"]) == 2
    assert "packages/operonx-project" in capsys.readouterr().err


def test_hands_the_rest_of_the_command_line_over_and_never_syncs(monkeypatch):
    got = []
    fake = types.ModuleType("operonx_project.cli")
    fake.main = lambda argv: got.append(argv) or 0
    monkeypatch.setitem(sys.modules, "operonx_project.cli", fake)
    monkeypatch.setattr(cli_main, "_autosync", lambda: (_ for _ in ()).throw(AssertionError("synced")))
    assert main(["project", "show", "m1", "C2", "--json"]) == 0
    assert got == [["show", "m1", "C2", "--json"]]
