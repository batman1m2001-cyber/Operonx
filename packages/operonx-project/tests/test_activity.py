"""The board's live line (plan D10): a session's tool calls become ``activity``
events, at most one per card every 2 s, whatever the stream's rate."""

from __future__ import annotations

from pathlib import Path

from operonx_project.coder.base import CoderRequest
from operonx_project.coder.claude import _Reader, activity_label
from operonx_project.model import Milestone
from operonx_project.runtime import Runtime
from operonx_project.store import Store


def test_a_tool_call_is_one_short_line(tmp_path):
    assert activity_label("Edit", {"file_path": str(tmp_path / "src" / "x.py")}, tmp_path) == "Edit src/x.py"
    assert activity_label("Bash", {"command": "uv run   pytest -q\n tests"}) == "Bash uv run pytest -q tests"
    assert activity_label("Grep", {"pattern": "def add"}) == "Grep def add"
    assert len(activity_label("Bash", {"command": "x" * 500})) <= 85


def test_a_stream_of_tool_calls_is_throttled_per_card(tmp_path, monkeypatch):
    store = Store(tmp_path / "project.db")
    store.add_milestone(Milestone("m1", str(tmp_path), "m"))
    rt = Runtime.__new__(Runtime)  # only the store and the clock are used
    rt.store, rt._activity_at = store, {}
    clock = [1000.0]
    monkeypatch.setattr("operonx_project.runtime.time.time", lambda: clock[0])
    req = CoderRequest(prompt="", cwd=Path(tmp_path), on_activity=rt.activity_for("m1", "C1", "implementer"))
    reader = _Reader(req, session=None)
    for n in range(100):  # 100 tool calls in 10 s
        reader.event(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "id": f"t{n}", "name": "Edit", "input": {"file_path": f"f{n}.py"}}]
                },
            },
            None,
        )
        clock[0] += 0.1
    got = [e for e in store.events("m1") if e["kind"] == "activity"]
    assert len(got) == 5 and got[0]["data"] == {"text": "Edit f0.py", "role": "implementer"}
    assert got[0]["card"] == "C1" and reader.tools == {"Edit": 100}
