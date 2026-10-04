"""`operonx init` makes a project a coding assistant can build on at once.

The acceptance test for every template is the one a user runs next: the
generated project's own tests pass, and the CLIs load its application and
list every service and job it declares. Each runs in its own process, with
this repo's interpreter, in the generated directory — exactly what the
README tells the user to run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import operonx
from operonx import guide
from operonx.cli.main import TEMPLATES, main

pytestmark = pytest.mark.unit

BIN = Path(sys.executable).parent

#: What each template's application declares — the test fails if a
#: template drops one, and if the CLIs do not list one.
DECLARED = {
    "hello": {"services": ["greet"], "jobs": ["greet_people"]},
    "http": {"services": ["stats"], "jobs": []},
    "chat": {"services": ["chat"], "jobs": []},
    "agent": {"services": ["assistant"], "jobs": []},
}


def _env(project: Path) -> dict:
    env = {**os.environ, "OPERONX_RUNS_DIR": str(project / ".operonx" / "runs")}
    for var in ("VIRTUAL_ENV", "PYTHONPATH", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        env.pop(var, None)
    return env


def _run(argv, cwd: Path, timeout: float = 180) -> subprocess.CompletedProcess:
    got = subprocess.run(
        argv, cwd=cwd, env=_env(cwd), capture_output=True, text=True, timeout=timeout
    )
    assert got.returncode == 0, (
        f"{' '.join(map(str, argv))} failed ({got.returncode}):\n--- stdout\n"
        f"{got.stdout[-4000:]}\n--- stderr\n{got.stderr[-4000:]}"
    )
    return got


def _init(capsys, *argv) -> tuple:
    code = main(["init", *map(str, argv)])
    return code, capsys.readouterr()


def _snapshot(root: Path) -> dict:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".pytest_cache" not in p.parts and "__pycache__" not in p.parts
    }


def test_every_template_is_declared_here():
    assert sorted(TEMPLATES) == sorted(DECLARED)


@pytest.fixture(scope="module", params=sorted(DECLARED))
def project(request, tmp_path_factory):
    """One fresh project per template, made through the CLI's entry point."""
    root = tmp_path_factory.mktemp(request.param) / "myapp"
    assert main(["init", str(root), "--template", request.param]) == 0
    return request.param, root


class TestTheGeneratedProject:
    def test_its_own_tests_pass(self, project):
        _, root = project
        got = _run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], root)
        assert " passed" in got.stdout and "failed" not in got.stdout

    def test_the_clis_list_every_service_and_job(self, project):
        template, root = project
        # what the app declares, read the way the CLIs load it
        described = _run(
            [
                sys.executable,
                "-c",
                "import json; from operonx.app import Application; "
                "d = Application.find('.').describe(); "
                "print(json.dumps({'name': d['name'], "
                "'services': [s['name'] for s in d['services']], "
                "'jobs': [j['name'] for j in d['jobs']]}))",
            ],
            root,
        )
        got = json.loads(described.stdout.strip().splitlines()[-1])
        assert got == {"name": "myapp", **DECLARED[template]}

        served = _run([str(BIN / "operonx"), "serve", "--list"], root).stdout
        jobs = _run([str(BIN / "operonx"), "run", "--list"], root).stdout
        assert served.splitlines()[0] == "myapp"
        for name in DECLARED[template]["services"]:
            assert f"    {name} " in served, served
        for name in DECLARED[template]["jobs"]:
            assert f"  {name} " in jobs, jobs
        if not DECLARED[template]["jobs"]:
            assert "no jobs" in jobs

    def test_it_follows_the_layout(self, project):
        template, root = project
        manifest = (root / "operonx.toml").read_text()
        assert 'app  = "app.main:APP"' in manifest
        assert 'src  = ["src", "."]' in manifest
        assert 'overlay = "resources.yaml"' in manifest
        assert '[tracing]\nsinks = ["local"]' in manifest
        assert "[[serve]]" not in manifest and "[[job]]" not in manifest
        assert "APP = Application(" in (root / "app" / "main.py").read_text()
        features = [p for p in (root / "src").iterdir() if p.is_dir()]
        assert len(features) == 1
        for name in ("__init__.py", "graph.py", "ops.py"):
            assert (features[0] / name).is_file()
        assert list((root / "tests").glob("test_*.py"))
        pyproject = (root / "pyproject.toml").read_text()
        assert f">={operonx.__version__}" in pyproject
        ignored = (root / ".gitignore").read_text().split()
        for entry in (".env", ".operonx/runs/", ".venv/", "__pycache__/"):
            assert entry in ignored
        assert not (root / ".env").exists()  # secrets are the user's to write

    def test_secrets_are_variables_and_listed_in_env_example(self, project):
        _, root = project
        resources = (root / "resources.yaml").read_text()
        example = (root / ".env.example").read_text()
        assert "llm:" in resources and "${LLM_API_KEY}" in resources
        assert "LLM_API_KEY=" in example
        assert "sk-" not in resources

    def test_no_print_in_generated_code(self, project):
        _, root = project
        for path in root.rglob("*.py"):
            assert "print(" not in path.read_text(), path

    def test_ruff_finds_nothing(self, project):
        _, root = project
        if subprocess.run([sys.executable, "-m", "ruff", "--version"]).returncode != 0:
            pytest.skip("ruff is not installed")
        _run([sys.executable, "-m", "ruff", "check", "--no-cache", "."], root)
        _run([sys.executable, "-m", "ruff", "format", "--check", "--no-cache", "."], root)


class TestForCodingAssistants:
    def test_agents_md_and_claude_md(self, project):
        _, root = project
        agents = (root / "AGENTS.md").read_text()
        for must in (
            ".operonx/guide/README.md",
            "app/main.py",
            "src/<feature>/graph.py",
            "resources.yaml",
            ".env",
            "print()",
            "LOGGER",
            "operonx guide --sync",
            "pytest",
            "operonx serve",
        ):
            assert must in agents, must
        assert (root / "CLAUDE.md").read_text().strip() == "@AGENTS.md"

    def test_agents_md_names_every_guide_page(self, project):
        """It listed pages 01-05 and left out 06, failures, when it shipped."""
        from operonx.guide import pages

        _, root = project
        agents = (root / "AGENTS.md").read_text()
        missing = [p.name for p in pages() if p.name != "README.md" and p.name not in agents]
        assert missing == []

    def test_the_guide_copy_is_the_installed_guide(self, project):
        _, root = project
        copy = root / ".operonx" / "guide"
        for page in guide.pages():
            assert (copy / page.name).read_bytes() == page.read_bytes(), page.name
        assert (copy / "VERSION").read_text().strip() == operonx.__version__


class TestInit:
    def test_prints_what_it_created_and_the_next_commands(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        code, out = _init(capsys, "demo")
        assert code == 0
        assert "operonx.toml" in out.out and "AGENTS.md" in out.out
        assert "cd demo" in out.out
        assert "pytest" in out.out and "operonx serve" in out.out

    def test_dir_defaults_to_here_and_name_to_its_basename(self, tmp_path, capsys, monkeypatch):
        here = tmp_path / "shop"
        here.mkdir()
        monkeypatch.chdir(here)
        code, out = _init(capsys)
        assert code == 0
        assert 'name = "shop"' in (here / "operonx.toml").read_text()
        assert "cd " not in out.out

    def test_name_flag(self, tmp_path, capsys):
        assert _init(capsys, tmp_path / "d", "--name", "billing_bot")[0] == 0
        assert 'name = "billing_bot"' in (tmp_path / "d" / "operonx.toml").read_text()
        assert 'name = "billing-bot"' in (tmp_path / "d" / "pyproject.toml").read_text()

    def test_a_bad_name_is_refused(self, tmp_path, capsys):
        code, out = _init(capsys, tmp_path / "d", "--name", "my app!")
        assert code == 2 and "name" in out.err
        assert not (tmp_path / "d").exists()

    def test_an_unknown_template_is_refused_with_the_choices(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["init", str(tmp_path / "d"), "--template", "nope"])
        assert exc.value.code == 2
        assert "hello" in capsys.readouterr().err

    def test_a_second_init_creates_nothing_and_says_so(self, tmp_path, capsys):
        root = tmp_path / "app1"
        _init(capsys, root)
        before = _snapshot(root)
        code, out = _init(capsys, root)
        assert code == 0
        assert "nothing to create" in out.out
        assert _snapshot(root) == before

    def test_an_existing_project_only_gets_what_is_missing(self, tmp_path, capsys):
        root = tmp_path / "app1"
        _init(capsys, root)
        (root / "AGENTS.md").unlink()
        (root / "app" / "main.py").write_text("# mine\n")
        code, out = _init(capsys, root)
        assert code == 0
        assert (root / "AGENTS.md").is_file()
        assert (root / "app" / "main.py").read_text() == "# mine\n"  # never overwritten
        created = [line for line in out.out.splitlines() if line.startswith("  + ")]
        assert created == ["  + AGENTS.md"]
        assert "app/main.py" in out.out  # reported as kept

    def test_force_overwrites(self, tmp_path, capsys):
        root = tmp_path / "app1"
        _init(capsys, root)
        (root / "app" / "main.py").write_text("# mine\n")
        assert _init(capsys, root, "--force")[0] == 0
        assert "APP = Application(" in (root / "app" / "main.py").read_text()

    def test_a_file_where_the_directory_should_be_is_refused(self, tmp_path, capsys):
        (tmp_path / "f").write_text("x")
        code, out = _init(capsys, tmp_path / "f")
        assert code == 2 and "not a directory" in out.err


class TestGuide:
    def test_prints_the_index(self, capsys):
        assert main(["guide"]) == 0
        assert capsys.readouterr().out == (guide.path() / "README.md").read_text(encoding="utf-8")

    def test_path(self, capsys):
        assert main(["guide", "--path"]) == 0
        assert capsys.readouterr().out.strip() == str(guide.path())

    def test_sync_copies_the_guide_and_writes_the_version(self, tmp_path, capsys):
        root = tmp_path / "p"
        _init(capsys, root)
        copy = root / ".operonx" / "guide"
        (copy / "VERSION").write_text("0.0.1\n")
        (copy / "README.md").write_text("stale")
        (copy / "99-gone.md").write_text("a page the new version dropped")
        assert main(["guide", "--sync", str(root)]) == 0
        assert (copy / "VERSION").read_text().strip() == operonx.__version__
        assert (copy / "README.md").read_bytes() == (guide.path() / "README.md").read_bytes()
        assert not (copy / "99-gone.md").exists()
        assert sorted(p.name for p in copy.iterdir()) == sorted(
            [p.name for p in guide.pages()] + ["VERSION"]
        )
        assert str(copy) in capsys.readouterr().out

    def test_sync_defaults_to_the_project_above_here(self, tmp_path, capsys, monkeypatch):
        root = tmp_path / "p"
        _init(capsys, root)
        (root / ".operonx" / "guide" / "VERSION").write_text("0.0.1\n")
        monkeypatch.chdir(root / "src")
        assert main(["guide", "--sync"]) == 0
        assert (root / ".operonx" / "guide" / "VERSION").read_text().strip() == (
            operonx.__version__
        )

    def test_path_and_sync_together_are_refused(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["guide", "--path", "--sync"])
        assert exc.value.code == 2
