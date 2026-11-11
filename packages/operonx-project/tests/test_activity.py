"""A session's tool call, as one short line."""

from __future__ import annotations

from operonx_project.coder.claude import activity_label


def test_a_tool_call_is_one_short_line(tmp_path):
    assert activity_label("Edit", {"file_path": str(tmp_path / "src" / "x.py")}, tmp_path) == "Edit src/x.py"
    assert activity_label("Bash", {"command": "uv run   pytest -q\n tests"}) == "Bash uv run pytest -q tests"
    assert activity_label("Grep", {"pattern": "def add"}) == "Grep def add"
    assert len(activity_label("Bash", {"command": "x" * 500})) <= 85

