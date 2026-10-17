"""``operonx-project brd``: init, register, read, check."""

from __future__ import annotations

import json
import subprocess

from operonx_project.cli import main
from tests.brd_files import SAMPLE_BLOCKS, make_docx


def test_init_register_read_check(tmp_path, capsys, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    assert main(["brd", "init"]) == 0
    doc = make_docx(repo / "client.docx", SAMPLE_BLOCKS)
    assert main(["brd", "register", str(doc), "--private", "--title", "Client BRD"]) == 0
    assert main(["brd", "read", str(doc), "--out", str(tmp_path / "read")]) == 0
    out = capsys.readouterr().out
    assert "SRC-01 Client BRD (authoritative, private)" in out
    read = json.loads(out[out.index("{") :])
    assert [s["id"] for s in read["sections"]][:3] == ["S01", "§1", "§2"]
    assert main(["brd", "check", "--source", f"SRC-01={doc}"]) == 0
    assert "0 error(s)" in capsys.readouterr().out
    (repo / "client.docx").write_bytes(b"changed")
    assert main(["brd", "check", "--source", f"SRC-01={doc}"]) == 1
    assert "a new version must be registered" in capsys.readouterr().out
