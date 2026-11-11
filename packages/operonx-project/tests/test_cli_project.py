"""``new`` makes a project; ``board`` prints the cards and moves one; ``status`` reads the board."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout

from operonx_project import board
from operonx_project.cli import main


def run(*argv, code: int = 0) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        got = main([str(a) for a in argv])
    assert got == code, buf.getvalue()
    return buf.getvalue()


def test_new_then_the_board_through_the_cli(tmp_path):
    repo = tmp_path / "fresh"
    out = run("new", repo, "--name", "fresh")
    assert "docs/brd/" in out and (repo / "docs" / "brd" / "README.md").exists()
    assert "no board yet" in run("board", "--repo", repo)

    board.put(repo, title="add", files=["src/calc/ops.py"], done=["tests/calc/test_ops.py::test_add"])
    board.put(repo, title="double", after=["C1"])
    assert "after C1" in run("board", "--repo", repo)
    assert "waits on C1" in run("board", "C2", "doing", "--repo", repo, code=1)
    assert "C1 · add · doing" in run("board", "C1", "doing", "--repo", repo)
    shown = json.loads(run("board", "--repo", repo, "--json"))
    assert shown["columns"]["doing"] == ["C1"] and shown["cards"][0]["lane"] == "core"
    doc = json.loads(run("status", "--repo", repo, "--json"))
    assert doc["board"]["columns"]["todo"] == ["C2"] and doc["working_on"][0]["card"] == "C1"
    assert "C1   doing" in run("status", "--repo", repo)
