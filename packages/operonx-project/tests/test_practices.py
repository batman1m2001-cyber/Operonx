"""The project's practices (PROJECT_PRACTICES_PLAN D1, D5, D6): the file, what every session is told,
and the checks discovery adds."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from operonx_project.practices import FILE, PRESETS, Practices, PracticesError, load, parse, render, tomllib
from operonx_project.rules import standing_rules


def _write(root, text):
    (root / FILE).parent.mkdir(parents=True, exist_ok=True)
    (root / FILE).write_text(text, encoding="utf-8")


def test_no_file_is_the_defaults_and_says_nothing(tmp_path):
    p = load(tmp_path)
    assert p == Practices() and p.tdd == "acceptance" and not p.enforced
    assert render(p, tmp_path) == ""


@pytest.mark.parametrize("name", sorted(PRESETS))
def test_every_preset_parses_and_declares_rules(name):
    p = parse(tomllib.loads(PRESETS[name]), source=name)
    assert p.enforced and p.layers and p.private == "forbid"
    assert p.glossary == "docs/brd/GLOSSARY.md" and p.lessons == "docs/brd/LESSONS.md"


@pytest.mark.parametrize(
    "text, says",
    [
        ("[sprint]\nlength = 2\n", "sprint"),
        ('[tdd]\nmode = "red"\n', "red"),
        ('[architecture]\nlayers = [{ name = "app" }]\n', "paths"),
        ('[architecture]\nprivate = "maybe"\n', "maybe"),
        ('[done]\nchecks = [{ name = "types" }]\n', "cmd"),
        ("[tdd\n", FILE),
    ],
)
def test_a_wrong_file_is_refused_with_what_is_wrong(tmp_path, text, says):
    _write(tmp_path, text)
    with pytest.raises(PracticesError, match=says):
        load(tmp_path)


def test_every_session_is_told_the_practices_the_glossary_and_the_lessons(tmp_path, monkeypatch):
    monkeypatch.setenv("OPERONX_OWNER_RULES", str(tmp_path / "none.md"))
    _write(tmp_path, PRESETS["operonx"].replace('mode = "acceptance"', 'mode = "strict"'))
    (tmp_path / "docs/brd/GLOSSARY.md").write_text("- **Split**: one diner's share of the bill.\n")
    (tmp_path / "docs/brd/LESSONS.md").write_text("- A direct op call never raises (C3, Q-04).\n")
    text = standing_rules(tmp_path)
    assert f"This project's practices ({FILE})" in text
    assert "must fail before it (red)" in text  # strict TDD, in words
    assert "app (app/**) → graph (src/**/graph.py)" in text
    assert "never import another package's _private names" in text
    assert "one diner's share" in text and "A direct op call never raises" in text


def test_a_file_that_does_not_parse_never_breaks_a_session(tmp_path, monkeypatch):
    monkeypatch.setenv("OPERONX_OWNER_RULES", str(tmp_path / "none.md"))
    _write(tmp_path, "[tdd\n")
    assert "does not parse" in standing_rules(tmp_path)


def test_the_test_author_and_the_examiner_never_pin_incidentals():
    from operonx_project.model import Card
    from operonx_project.prompts import INCIDENTALS_RULE, examiner_prompt, test_author_prompt

    card = Card(id="C1", milestone="m1", title="t", intent="i")
    for prompt in (test_author_prompt(card, "", "tests/acceptance/C1"),
                   test_author_prompt(card, "", "tests/acceptance/C1", wrong="pinned a count"),
                   examiner_prompt(card, "", "/h"), examiner_prompt(card, "", "/h", wrong="x")):  # fmt: skip
        assert INCIDENTALS_RULE in prompt
    assert "how many rows a table has" in INCIDENTALS_RULE and "docstring" in INCIDENTALS_RULE


def _cli(*args, cwd):
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}  # this checkout's engine
    return subprocess.run([sys.executable, "-m", "operonx_project.cli", *args], cwd=cwd, capture_output=True,
                          text=True, env=env)  # fmt: skip


def test_the_cli_writes_a_preset_validates_it_and_checks_the_code(tmp_path):
    repo = tmp_path / "r"
    (repo / "src/tip").mkdir(parents=True)
    (repo / "src/tip/ops.py").write_text("import json\n\nx = json._default_decoder\n")
    r = _cli("practices", "--repo", str(repo), "--init", "operonx", cwd=tmp_path)
    assert r.returncode == 0 and (repo / FILE).exists(), r.stderr
    assert _cli("practices", "--repo", str(repo), "--init", "clean", cwd=tmp_path).returncode == 2  # no overwrite
    shown = json.loads(_cli("practices", "--repo", str(repo), "--json", cwd=tmp_path).stdout)
    assert shown["private"] == "forbid" and [x["name"] for x in shown["layers"]][0] == "app"
    r = _cli("arch", "--repo", str(repo), cwd=tmp_path)
    assert r.returncode == 1
    assert "FAILED arch:private:src/tip/ops.py->json._default_decoder - " in r.stdout and "1 violation(s)" in r.stdout
    # no file: nothing to check, and that passes
    assert _cli("arch", "--repo", str(tmp_path / "empty"), cwd=tmp_path).returncode == 0


def test_discovery_adds_the_architecture_and_done_checks_only_from_a_file(tmp_path):
    from operonx_project.flows.discover_flow.ops import practice_checks

    class RT:
        check_timeout_s = 60

    assert practice_checks(Practices(), RT()) == []
    p = parse({"architecture": {"private": "forbid"}, "done": {"checks": [{"name": "types", "cmd": "mypy ."}]}},
              source=FILE)  # fmt: skip
    got = practice_checks(p, RT())
    assert [c.name for c in got] == ["architecture", "types"]
    assert "-m operonx_project.cli arch --repo ." in got[0].cmd and got[1].cmd == "mypy ."


def test_a_milestone_checks_the_architecture_and_an_old_violation_costs_nothing(tmp_path):
    """The check joins the baseline: the repo's violation there is the past; the implementer's new one fails
    its round, as a failing test would, and the next round without it passes."""
    from operonx_project.model import MilestoneState as M
    from tests.flow_env import RIGHT, git, impl, make_repo
    from tests.life_env import Life, base_script

    repo = make_repo(tmp_path)
    (repo / "legacy.py").write_text("import json\n\nD = json._default_decoder\n")
    _write(repo, '[architecture]\nprivate = "forbid"\n\n[done]\nchecks = [{ name = "compiles", cmd = "'
           + sys.executable + ' -m compileall -q ." }]\n')  # fmt: skip
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "practices")
    hack = RIGHT + "\nimport json\n\n\ndef peek():\n    return json._default_encoder\n"
    life = Life(tmp_path, base_script(implementer=[impl(hack), impl(RIGHT)]), mode="w1", repo=repo)
    life.command("start")
    assert life.serve() == M.AWAITING_APPROVAL
    base = life.store.ms_get("m1", "baseline")
    assert [c["name"] for c in base["checks"]] == ["tests", "architecture", "compiles"]
    arch = next(r for r in base["results"] if r["name"] == "architecture")
    assert not arch["ok"] and arch["failures"] == ["arch:private:legacy.py->json._default_decoder"]
    life.command("approve")
    assert life.serve() == M.DONE, life.store.get_milestone("m1").reason
    rounds = [e["data"] for e in life.store.events("m1", limit=10_000) if e["kind"] == "round" and e["card"] == "C1"]
    assert rounds[0]["new_failures"] == ["architecture: arch:private:calc.py->json._default_encoder"]
    assert rounds[-1]["checks_ok"] and (life.integ() / "calc.py").read_text() == RIGHT


def test_the_api_reads_writes_and_commits_only_the_practices(tmp_path):
    from operonx_project.api import act
    from operonx_project.project import Project
    from tests.flow_env import git, make_repo

    repo = make_repo(tmp_path)
    (repo / "src/tip").mkdir(parents=True)
    (repo / "src/tip/ops.py").write_text("import json\n\nx = json._default_decoder\n")
    (repo / "notes.txt").write_text("the owner's own, unstaged\n")
    proj = Project.open(repo, worktrees=tmp_path / "wt")
    got = act(proj, {"op": "practices"})
    assert got["ok"] and got["status"] == "read" and not got["exists"] and got["message"] == "no architecture rules"
    seen = act(proj, {"op": "practices", "preview": "operonx"})["preview"]  # the evidence, before choosing
    assert seen["violation_count"] == 1 and "json._default_decoder" in seen["violations"][0]["id"]
    assert not (repo / FILE).exists() and act(proj, {"op": "practices", "preview": "x"})["status"] == "invalid"
    got = act(proj, {"op": "practices", "preset": "operonx"}, actor="assistant")
    assert got["ok"] and got["commit"] and got["violation_count"] == 1
    assert got["violations"][0]["id"] == "arch:private:src/tip/ops.py->json._default_decoder"
    assert git(repo, "show", "--name-only", "--format=%s", "HEAD").split() == [
        "practices:",
        "operonx",
        "(assistant)",
        FILE,
    ]
    assert "?? notes.txt" in git(repo, "status", "--porcelain")  # nothing else was committed
    assert act(proj, {"op": "practices", "preset": "clean"})["status"] == "refused"
    bad = act(proj, {"op": "practices", "toml": '[tdd]\nmode = "red"\n'})
    assert not bad["ok"] and bad["status"] == "invalid" and "red" in bad["message"]
    good = act(proj, {"op": "practices", "toml": got["text"].replace('mode = "acceptance"', 'mode = "strict"')})
    assert good["ok"] and good["practices"]["tdd"] == "strict" and good["commit"] != got["commit"]


def test_operonx_init_writes_the_operonx_preset_and_its_projects_pass_it(tmp_path):
    """The template is the preset, and every template `operonx init` makes is clean under it: a new project's
    baseline starts with no violation."""
    from operonx.cli import init

    from operonx_project.contracts import check

    assert (init.TEMPLATE_DIR / init.PRACTICES / "practices.toml.tmpl").read_text() == PRESETS["operonx"]
    for template in init.TEMPLATES:
        root = tmp_path / template
        for rel, data in init.plan(template, "demo", root=root).items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(data)
        p = load(root)
        assert p.enforced and render(p, root).count("Lessons from earlier milestones") == 0  # header only: silent
        assert [v.id for v in check(root, p)] == [], template
