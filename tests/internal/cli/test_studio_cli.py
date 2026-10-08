"""`operonx studio`: find the project, then hand it to operonx-studio."""

from __future__ import annotations

from pathlib import Path

import pytest

from operonx.cli import studio
from operonx.cli.main import main

pytestmark = pytest.mark.unit


def _project(tmp_path: Path) -> Path:
    (tmp_path / "operonx.toml").write_text('[project]\nname = "p"\n', encoding="utf-8")
    (tmp_path / "src").mkdir()
    return tmp_path


def test_outside_a_project_it_says_so(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["studio"]) == 2
    assert "no operonx.toml" in capsys.readouterr().err


def test_without_the_studio_it_says_how_to_install(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(_project(tmp_path))
    monkeypatch.setattr(studio, "studio_command", lambda: None)
    assert main(["studio"]) == 2
    assert "operonx-studio/install.sh" in capsys.readouterr().err


def test_hands_the_project_root_to_the_studio(tmp_path, monkeypatch):
    root = _project(tmp_path)
    monkeypatch.chdir(root / "src")  # from inside: the project is found above
    calls = []
    monkeypatch.setattr(studio, "studio_command", lambda: ["operonx-studio"])
    monkeypatch.setattr(studio.subprocess, "call", lambda cmd: calls.append(cmd) or 0)
    assert main(["studio", "--port", "9000", "--no-open"]) == 0
    assert calls == [["operonx-studio", str(root.resolve()), "--port", "9000", "--no-open"]]


def test_the_studio_found_as_a_module(monkeypatch):
    monkeypatch.setattr(studio.shutil, "which", lambda name: None)
    monkeypatch.setattr(studio.importlib.util, "find_spec", lambda name: object())
    cmd = studio.studio_command()
    assert cmd is not None and cmd[1:] == ["-m", "operonx_studio.cli"]
