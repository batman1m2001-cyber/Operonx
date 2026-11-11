"""The models the extraction runs on, from settings.toml, read at each session's start."""

from __future__ import annotations

from pathlib import Path

from operonx_project.project import new_project


def _project(tmp_path: Path):
    from operonx_project.project import Project

    new_project(tmp_path / "p", name="p")
    return Project.open(tmp_path / "p")


def test_the_requirements_extraction_reads_them_too(tmp_path):
    from operonx_project.flows.brd_flow import BrdRuntime

    project = _project(tmp_path)
    brt = BrdRuntime(coder=None, data_dir=tmp_path / "brd", settings_dir=project.state)
    assert brt.model_for("hunter") == "fable"
    (project.state / "settings.toml").write_text('model = "sonnet"\n', encoding="utf-8")
    assert brt.model_for("hunter") == "sonnet" and brt.model_for("extractor") == "sonnet"
    assert BrdRuntime(coder=None, data_dir=tmp_path / "brd").model_for("hunter") == "fable"  # no project: as given
