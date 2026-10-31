"""The models the sessions run on, from settings.toml: set by the owner (the
api's ``settings`` op, the studio's assistant), read at each session's start."""

from __future__ import annotations

import os
import time
from pathlib import Path

from operonx_project.api import act
from operonx_project.project import load_settings, new_project
from operonx_project.runtime import DEFAULT_MODELS, Runtime


def _project(tmp_path: Path):
    from operonx_project.project import Project

    new_project(tmp_path / "p", name="p")
    return Project.open(tmp_path / "p")


def test_settings_set_every_role_then_one_and_keep_the_sandbox(tmp_path):
    project = _project(tmp_path)
    (project.state / "settings.toml").write_text("sandbox = false\n", encoding="utf-8")
    got = act(project, {"op": "settings"})
    assert got["ok"] and got["status"] == "read" and got["effective"] == DEFAULT_MODELS

    got = act(project, {"op": "settings", "model": "sonnet"}, actor="studio:me/assistant")
    assert got["ok"] and set(got["effective"].values()) == {"sonnet"}
    got = act(project, {"op": "settings", "models": {"implementer": "opus"}})
    assert got["effective"]["implementer"] == "opus" and got["effective"]["reviewer"] == "sonnet"
    saved = load_settings(project.state)
    assert saved == {"sandbox": False, "model": "sonnet", "models": {"implementer": "opus"}}

    got = act(project, {"op": "settings", "model": "", "models": {"implementer": ""}})
    assert got["effective"] == DEFAULT_MODELS and load_settings(project.state) == {"sandbox": False}


def test_a_bad_role_or_model_changes_nothing(tmp_path):
    project = _project(tmp_path)
    for req in ({"model": "gpt-5"}, {"models": {"poet": "sonnet"}}, {"models": ["sonnet"]}):
        got = act(project, {"op": "settings", **req})
        assert not got["ok"] and got["status"] == "invalid", req
    assert not (project.state / "settings.toml").exists()
    assert act(project, {"op": "settings", "model": "claude-sonnet-5-5"})["ok"]


def test_a_session_reads_the_models_when_it_starts(tmp_path):
    project = _project(tmp_path)
    rt = Runtime(store=project.store, git=project.git, coder=None, data_dir=project.state)
    assert rt.model_for("reviewer") == DEFAULT_MODELS["reviewer"]
    # Sonnet codes; a high-risk card's implementer is Opus (PROJECT_SPEED_PLAN V4)
    assert (rt.model_for("implementer"), rt.model_for("implementer", "low")) == ("sonnet", "sonnet")
    assert rt.model_for("implementer", "high") == "opus" and rt.model_for("triage", "high") == "haiku"
    act(project, {"op": "settings", "models": {"implementer:high": "fable"}})
    assert rt.model_for("implementer", "high") == "fable" and rt.model_for("implementer", "low") == "sonnet"
    act(project, {"op": "settings", "model": "haiku"})
    # the owner's one model beats the defaults, and the owner's own role:risk beats it
    assert (rt.model_for("implementer", "high"), rt.model_for("implementer", "low")) == ("fable", "haiku")
    act(project, {"op": "settings", "model": "sonnet"})
    assert rt.model_for("reviewer") == "sonnet" and rt.model_for("planner") == "sonnet"
    path = project.state / "settings.toml"
    act(project, {"op": "settings", "models": {"planner": "haiku"}})
    os.utime(path, (time.time() + 5, time.time() + 5))  # the same second: still seen as a change
    assert rt.model_for("planner") == "haiku" and rt.model_for("reviewer") == "sonnet"
    path.write_text("model = [", encoding="utf-8")  # broken: the defaults, never a crash
    os.utime(path, (time.time() + 10, time.time() + 10))
    assert rt.model_for("reviewer") == DEFAULT_MODELS["reviewer"]


def test_the_requirements_extraction_reads_them_too(tmp_path):
    from operonx_project.flows.brd_flow import BrdRuntime

    project = _project(tmp_path)
    brt = BrdRuntime(coder=None, data_dir=tmp_path / "brd", settings_dir=project.state)
    assert brt.model_for("hunter") == "fable"
    act(project, {"op": "settings", "model": "sonnet"})
    assert brt.model_for("hunter") == "sonnet" and brt.model_for("extractor") == "sonnet"
    assert BrdRuntime(coder=None, data_dir=tmp_path / "brd").model_for("hunter") == "fable"  # no project: as given
