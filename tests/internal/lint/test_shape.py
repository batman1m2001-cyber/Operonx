"""C13–C16, the project's shape, and ``operonx lint --baseline``."""

from __future__ import annotations

from pathlib import Path

import pytest

from operonx.cli.main import main
from operonx.lint.rules import lint_path

pytestmark = pytest.mark.unit


def _write(root: Path, files: dict) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


@pytest.fixture
def project(tmp_path) -> Path:
    """A clean two-feature project: report uses billing through its front door."""
    return _write(
        tmp_path / "shop",
        {
            "operonx.toml": '[app]\nentry = "app.main:APP"\n',
            "README.md": "",
            "app/main.py": "from billing.jobs import nightly\nfrom report.routes import api\n",
            "src/_shared/config.py": 'import os\n\nMODEL = os.getenv("MODEL", "m")\n',
            "src/billing/__init__.py": "from billing.ops import charge\n",
            "src/billing/ops.py": "from _shared.config import MODEL\n",
            "src/billing/graph.py": "",
            "src/report/__init__.py": "summary = 1\n",
            "src/report/ops.py": "from billing import charge\nfrom src.billing.graph import bill\n",
            "datasets/cases.jsonl": "{}\n",
            "tests/test_x.py": 'import os\nfrom billing.ops import charge\nos.environ["A"]\n',
            "scripts/seed.py": 'import os\nos.getenv("A")\n',
        },
    )


def _rules(root: Path) -> list:
    return sorted(f.rule for f in lint_path(root) if f.rule in {"C13", "C14", "C15", "C16"})


def test_a_clean_project_has_no_shape_findings(project):
    assert _rules(project) == []


def test_each_rule_finds_its_case(project):
    _write(
        project,
        {
            "src/report/ops.py": "from billing.ops import charge\nimport src.billing._rates\n",
            "src/report/routes.py": (
                "import os\n"
                'TOKEN = os.environ["TOKEN"]\n'
                'KEY = os.environ.get("KEY")\n'
                '@app.get("/health")\ndef health(): ...\n'
                'r = Route("/x", endpoint=health)\n'
                'd = {}.get("/not-a-route")\n'
                'client.post("/api/send", json={})\n'
            ),
            "notes.md": "",
            "logs/run.log": "",
        },
    )
    assert _rules(project) == ["C13", "C13", "C14", "C14", "C15", "C15", "C16", "C16"]


def test_an_opt_out_holds_one_line(project):
    _write(project, {"src/report/ops.py": "import billing.ops  # operonx: allow[C13]\n"})
    assert _rules(project) == []


def test_a_folder_without_operonx_toml_is_not_a_project(project):
    (project / "operonx.toml").unlink()
    (project / "notes.md").write_text("")
    assert _rules(project) == []


# ── the baseline ─────────────────────────────────────────────────────────


def _lint(root: Path, *args: str) -> int:
    return main(["lint", str(root), "--no-manifest", *args])


def test_one_deep_import_turns_lint_red(project, capsys):
    assert _lint(project) == 0
    _write(project, {"src/report/ops.py": "from billing.ops import charge\n"})
    assert _lint(project) == 1
    assert "[C13] billing.ops reaches inside feature billing" in capsys.readouterr().out


def test_a_baseline_holds_todays_count_and_goes_red_above_it(project, capsys):
    _write(project, {"src/report/ops.py": "from billing.ops import charge\n", "notes.md": ""})
    before = (project / "operonx.toml").read_text()
    assert _lint(project) == 1
    assert (project / "operonx.toml").read_text() == before  # plain lint never writes

    assert _lint(project, "--baseline") == 0
    toml = (project / "operonx.toml").read_text()
    assert toml.endswith("\n[lint]\nbaseline = {C13 = 1, C16 = 1}\n")
    capsys.readouterr()
    assert _lint(project) == 0
    out = capsys.readouterr().out
    assert "[C13] 1 held by the baseline" in out and "clean (on its baseline)" in out
    assert _lint(project, "--all") == 1

    # one more: red, and only the rule that went up shows its findings
    _write(project, {"src/report/_more.py": "import billing.ops\n"})
    capsys.readouterr()
    assert _lint(project) == 1
    out = capsys.readouterr().out
    assert "[C13] 2 over the baseline of 1" in out and "reaches inside" in out
    assert "notes.md" not in out  # C16 is still held

    # both fixed: green, and lint says the baseline can drop
    for rel in ("src/report/_more.py", "notes.md"):
        (project / rel).unlink()
    _write(project, {"src/report/ops.py": "from billing import charge\n"})
    capsys.readouterr()
    assert _lint(project) == 0
    assert "it was 1, run `operonx lint --baseline` to lower it" in capsys.readouterr().out
    assert _lint(project, "--baseline") == 0
    assert (project / "operonx.toml").read_text() == before  # a clean project: no [lint] left


def test_the_baseline_joins_a_lint_table_already_there(project):
    toml = project / "operonx.toml"
    toml.write_text(toml.read_text() + "\n[lint]\n\n[tracing]\nsinks = []\n")
    (project / "notes.md").write_text("")
    assert _lint(project, "--baseline") == 0
    text = toml.read_text()
    assert text.count("[lint]") == 1 and "[lint]\nbaseline = {C16 = 1}\n" in text
    assert "[tracing]\nsinks = []" in text
