"""The prompt store: published prompts copied to Postgres, one way, and
served from there to a process whose files are older.

The reads and writes run against a real Postgres when
``OPERONX_TEST_PG_DSN`` is set (tests/internal/telemetry/_stores.py says how
to start one) and are skipped otherwise; what needs no database is not.
"""

import os
import uuid

import pytest

from operonx import prompt_store
from operonx import prompts as P
from operonx.cli import prompt as cli

pytestmark = pytest.mark.unit

PG = os.environ.get("OPERONX_TEST_PG_DSN", "")
needs_pg = pytest.mark.skipif(not PG, reason="OPERONX_TEST_PG_DSN is not set")


@pytest.fixture
def project(tmp_path, monkeypatch):
    key = f"t-{uuid.uuid4().hex[:10]}"
    (tmp_path / "operonx.toml").write_text(
        f'[project]\nname = "{key}"\n\n[prompts]\nstore = "${{PROMPT_STORE_DSN}}"\n'
    )
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "guard.md").write_text("Answer briefly.\n")
    (tmp_path / "prompts" / "intake.md").write_text("You help {{who}}.\n{{> guard}}\n")
    monkeypatch.chdir(tmp_path)
    for var in (
        "OPERONX_PROMPT_LABEL",
        "OPERONX_PROMPT_STORE",
        "OPERONX_PROMPT_PROJECT",
        "PROMPT_STORE_DSN",
    ):
        monkeypatch.delenv(var, raising=False)
    from operonx.core import workflow_trace

    monkeypatch.setattr(workflow_trace, "_PROJECT_ROOT", [None])
    monkeypatch.setattr(prompt_store, "_SERVED", {})
    monkeypatch.setattr(prompt_store, "_DSN_SEEN", {})
    return tmp_path


def test_the_store_is_a_setting_of_the_project(project, monkeypatch):
    # ${PROMPT_STORE_DSN} is set nowhere: no store, and prompts are the files
    assert prompt_store.dsn_of(project) == "" and not prompt_store.configured(project)
    assert P.prompt("guard").text == "Answer briefly."
    assert prompt_store.generation(project) == 0 and prompt_store.status(project) == {
        "configured": False
    }
    with pytest.raises(prompt_store.StoreError):
        prompt_store.push(project)
    # the project's .env sets it; the environment names another for this process
    (project / ".env").write_text("PROMPT_STORE_DSN=postgresql://u:p@db.example/prompts\n")
    assert prompt_store.dsn_of(project) == "postgresql://u:p@db.example/prompts"
    monkeypatch.setenv("OPERONX_PROMPT_STORE", "postgresql://other/x")
    assert prompt_store.dsn_of(project) == "postgresql://other/x"
    assert prompt_store.project_key(project).startswith("t-")
    monkeypatch.setenv("OPERONX_PROMPT_PROJECT", "callbot")
    assert prompt_store.project_key(project) == "callbot"


def test_a_store_that_cannot_be_reached_serves_the_files(project, monkeypatch):
    monkeypatch.setenv("OPERONX_PROMPT_STORE", "postgresql://nobody@127.0.0.1:9/none")
    P.publish("guard")
    P.set_label("guard", "production", 1)
    got = P.prompt("guard")
    assert (got.text, got.version, got.label) == ("Answer briefly.", 1, "production")
    assert prompt_store.status(project)["error"] and prompt_store.generation(project) == 0
    with pytest.raises(prompt_store.StoreError):
        prompt_store.push(project)


@needs_pg
def test_what_is_published_is_served_from_the_store(project, monkeypatch, capsys):
    monkeypatch.setenv("PROMPT_STORE_DSN", PG)
    assert cli.main(["publish", "guard", "--label", "production"]) == 0
    assert "1 new version(s), 1 label(s) moved" in capsys.readouterr().out
    assert prompt_store.push(project) == {
        "project": prompt_store.project_key(project),
        "versions": [],
        "labels": [],
    }

    # a service built before v2: its files know v1 only — the store serves v2, and the include follows
    (project / "prompts" / "guard.md").write_text("Answer in one line.\n")
    assert cli.main(["publish", "guard", "--label", "production"]) == 0
    before = prompt_store.refresh(project)
    (project / "prompts" / ".versions" / "guard" / "2.md").unlink()
    (project / "prompts" / "labels.toml").write_text('["guard"]\nproduction = 1\n')
    got = P.prompt("guard")
    assert (got.text, got.version, got.label) == ("Answer in one line.", 2, "production")
    assert P.prompt("intake").text == "You help {{who}}.\nAnswer in one line."
    assert (
        P.prompt("guard", version=1).text == "Answer briefly."
    )  # a version asked for is the file's

    # rolling back is moving the label; the process sees it at its next read, and the generation says so
    (project / "prompts" / ".versions" / "guard" / "2.md").write_text("Answer in one line.\n")
    assert cli.main(["label", "guard", "production", "1"]) == 0
    assert prompt_store.refresh(project) == before + 1
    assert P.prompt("guard").version == 1 and prompt_store.refresh(project) == before + 1
    assert cli.main(["store"]) == 0
    assert "guard" in capsys.readouterr().out


@needs_pg
def test_two_checkouts_cannot_publish_different_texts_under_one_number(project, monkeypatch):
    monkeypatch.setenv("PROMPT_STORE_DSN", PG)
    P.publish("guard")
    prompt_store.push(project)
    (project / "prompts" / ".versions" / "guard" / "1.md").write_text("Something else.\n")
    (project / "prompts" / "intake.md").write_text("New.\n")
    P.publish("intake")
    with pytest.raises(prompt_store.StoreError, match="different text for guard v1"):
        prompt_store.push(project)
    assert (
        prompt_store.refresh(project) >= 0 and P.prompt("intake").version is None
    )  # nothing was pushed
