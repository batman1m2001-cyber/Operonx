"""`operonx lint` as a command: exit codes, the build check, and that it
never writes to the project it checks."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from operonx.cli.main import TEMPLATES, main
from operonx.lint.build import no_network

pytestmark = pytest.mark.unit


def _snapshot(root: Path) -> dict:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


@pytest.fixture(scope="module", params=sorted(TEMPLATES))
def template(request, tmp_path_factory):
    root = tmp_path_factory.mktemp(request.param) / "myapp"
    assert main(["init", str(root), "--template", request.param]) == 0
    return request.param, root


def test_every_init_template_lints_clean(template, capsys):
    _, root = template
    assert main(["lint", str(root)]) == 0
    assert "clean" in capsys.readouterr().out


def test_every_init_template_builds_offline(template):
    name, root = template
    if name == "agent":
        import importlib.util

        if importlib.util.find_spec("operonx_agents") is None:
            pytest.skip("the agent template imports operonx_agents, which is not installed")
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("VIRTUAL_ENV", "LLM_API_KEY", "LLM_BASE_URL")
    }
    env["PYTHONPATH"] = str(root / "src")
    got = subprocess.run(
        [sys.executable, "-m", "operonx.cli.main", "lint", "--build", "."],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert got.returncode == 0, got.stdout + got.stderr
    assert "graph(s) build offline" in got.stdout and "[C1]" not in got.stdout


def test_lint_never_writes_to_the_project_not_even_a_stale_guide(tmp_path, monkeypatch):
    """Delegated commands sync a stale guide copy first; lint must not: a
    checker that edits what it checks (run from another environment, the
    sync deletes the pages of packages that environment lacks)."""
    root = tmp_path / "app"
    assert main(["init", str(root), "--template", "hello"]) == 0
    readme = root / ".operonx" / "guide" / "README.md"
    readme.write_text(readme.read_text() + "\nstale\n")
    before = _snapshot(root)
    monkeypatch.chdir(root)
    main(["lint", "."])
    assert _snapshot(root) == before


def test_errors_exit_one_and_warnings_alone_exit_zero(tmp_path, capsys):
    (tmp_path / "ops.py").write_text('import os\nk = os.getenv("API_KEY")\n')
    (tmp_path / ".env.example").write_text("API_KEY=\n")
    assert main(["lint", str(tmp_path), "--no-manifest"]) == 0
    (tmp_path / "ops.py").write_text('def f():\n    print("x")\n')
    assert main(["lint", str(tmp_path), "--no-manifest"]) == 1
    assert "[C8]" in capsys.readouterr().out


def test_a_missing_manifest_is_c1(tmp_path, capsys):
    (tmp_path / "ops.py").write_text("x = 1\n")
    assert main(["lint", str(tmp_path)]) == 1
    assert "[C1]" in capsys.readouterr().out


def test_a_missing_path_exits_two(tmp_path):
    assert main(["lint", str(tmp_path / "nope")]) == 2


def test_rules_lists_every_rule(capsys):
    assert main(["lint", "--rules"]) == 0
    out = capsys.readouterr().out
    assert all(f"C{n}" in out for n in (1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12))


def test_no_network_records_and_refuses_but_allows_loopback():
    import socket

    seen = []
    with no_network(seen):
        with pytest.raises(OSError, match="C2"):
            socket.create_connection(("example.com", 80), timeout=1)
        with pytest.raises(OSError):
            # nothing listens there, but loopback is not refused by the check
            socket.create_connection(("127.0.0.1", 9), timeout=1)
    assert [a.host for a in seen] == ["example.com"]


def test_a_build_that_reaches_the_network_is_c2(tmp_path):
    root = tmp_path / "net"
    assert main(["init", str(root), "--template", "hello"]) == 0
    ops = root / "src" / "greeting" / "ops.py"
    ops.write_text("import socket\nsocket.getaddrinfo('example.com', 80)\n" + ops.read_text())
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    env["PYTHONPATH"] = str(root / "src")
    got = subprocess.run(
        [sys.executable, "-m", "operonx.cli.main", "lint", "--build", "."],
        cwd=root, env=env, capture_output=True, text=True, timeout=180,
    )  # fmt: skip
    assert got.returncode == 1 and "example.com" in got.stdout, got.stdout + got.stderr
